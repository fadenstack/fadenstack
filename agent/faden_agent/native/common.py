"""Shared pieces of the native runtime: errors, paths, durable JSON."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: deployment ids, bundle ids and generations end up in file names.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class NativeError(RuntimeError):
    """A native-runtime command failure with a code the backend can act on."""

    def __init__(self, message: str, *, code: str = "invalid_payload") -> None:
        super().__init__(message)
        self.error_code = code


def check_id(value: Any, what: str) -> str:
    text = str(value or "")
    if not _ID.fullmatch(text) or text in {".", ".."}:
        raise NativeError(f"{what} must match {_ID.pattern}, got {text[:80]!r}.")
    return text


def check_digest(value: Any, what: str) -> str:
    text = str(value or "").strip().lower()
    if not _DIGEST.fullmatch(text):
        raise NativeError(f"{what} must be sha256:<64 hex>, got {str(value)[:80]!r}.")
    return text


def ensure_private_dir(path: Path) -> Path:
    """A directory only the service account can read (0700)."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def write_json_durable(path: Path, payload: dict[str, Any]) -> None:
    """tmp → fsync → rename → fsync(dir): a crash leaves the old or the new file, never half."""
    directory = path.parent
    ensure_private_dir(directory)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    fsync_dir(directory)


def fsync_dir(directory: Path) -> None:
    try:
        dfd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def read_json_nofollow(
    path: Path, *, limit: int = 1 << 20, owner: int | None = None, dir_fd: int | None = None
) -> dict[str, Any] | None:
    """Read a small JSON object: no symlink, no fifo, one link, optionally a given owner.

    Root reads worker records in the service's directory when it stops the
    service: a planted symlink, fifo or hardlink must not make it read (or
    hang on) anything else.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags, dir_fd=dir_fd)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or (owner is not None and st.st_uid != owner):
        os.close(fd)
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None
    if len(data) > limit:
        return None
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):  # a deep nest is not a record either
        return None
    return value if isinstance(value, dict) else None


@dataclass(frozen=True)
class NativePaths:
    """Everything native lives under ``<state_dir>/native``, owned by the service account."""

    root: Path

    @classmethod
    def for_state_path(cls, state_path: str | os.PathLike[str]) -> NativePaths:
        return cls(Path(state_path).parent / "native")

    @property
    def runtimes(self) -> Path:
        return self.root / "runtimes"

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    @property
    def workers(self) -> Path:
        return self.root / "workers"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def home(self) -> Path:
        return self.root / "home"

    def tmp(self, deployment_id: str) -> Path:
        """The worker's TMPDIR and ZMQ IPC directory. Kept SHORT: vLLM puts a 36-character uuid socket in it
        and a unix socket path may be at most 103 bytes on macOS (sockaddr_un.sun_path)."""
        return self.root / "t" / hashlib.sha256(deployment_id.encode()).hexdigest()[:12]

    def ipc_path_length(self, deployment_id: str) -> int:
        return len(str(self.tmp(deployment_id))) + 1 + 36

    def bundle_dir(self, bundle_id: str) -> Path:
        return self.runtimes / bundle_id

    def runtime_dir(self, bundle_id: str, layer_digest: str) -> Path:
        return self.bundle_dir(bundle_id) / layer_digest.split(":", 1)[1]

    def worker_record(self, deployment_id: str) -> Path:
        return self.workers / f"{deployment_id}.json"

    def log_file(self, deployment_id: str) -> Path:
        return self.logs / f"{deployment_id}.log"
