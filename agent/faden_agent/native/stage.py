"""Stage a native runtime from a ``docker save`` tar of the C1 carrier image.

The carrier is FROM scratch with exactly one layer holding ``runtime/``
(a relocatable CPython, a venv, ``RUNTIME-MANIFEST.json``). The agent never
loads it into Docker: it reads the tar the backend's ``/images/save`` serves,
checks the config and layer digests, and extracts the layer itself.

Extraction is the attack surface. ``tarfile``'s ``data`` filter does the base
rules (absolute names, ``..``, links leaving the destination, device files);
on top of it every member must sit under ``runtime/``, be a regular file, a
directory, an in-tree symlink or a hardlink to a file already extracted, and
carry no setuid/setgid bit, and the archive is capped in size and members.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import posixpath
import secrets
import shutil
import tarfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from faden_agent.native.common import NativeError, NativePaths, fsync_dir, write_json_durable

log = logging.getLogger(__name__)

STAMP = ".faden-staged.json"
MANIFEST = "RUNTIME-MANIFEST.json"
MAX_MEMBERS = 200_000
MAX_NAME = 1024
#: Without an unpacked size from the backend, a layer may unpack to this many
#: times its compressed size (a gzip bomb stops here).
MAX_RATIO = 8
MAX_UNPACKED = 64 << 30
#: ... but never less than this (a small layer's tar padding compresses far better).
MIN_LIMIT = 256 << 20
_CHUNK = 1 << 20


def _unsafe(message: str) -> NativeError:
    return NativeError(message, code="unsafe_archive")


def _mismatch(message: str) -> NativeError:
    return NativeError(message, code="digest_mismatch")


# ── reading the docker-save tar ───────────────────────────────────


def _outer_member(outer: tarfile.TarFile, name: str) -> tarfile.TarInfo:
    norm = posixpath.normpath(name)
    if norm.startswith(("/", "../")) or norm == "..":
        raise _unsafe(f"image tar names {name!r} outside itself")
    try:
        member = outer.getmember(norm)
    except KeyError:
        try:
            member = outer.getmember(name)
        except KeyError as exc:
            raise NativeError(f"image tar lacks {name!r}", code="invalid_image") from exc
    if not member.isreg():
        raise _unsafe(f"image tar entry {name!r} is not a regular file")
    return member


def _read_small(outer: tarfile.TarFile, member: tarfile.TarInfo, limit: int = 4 << 20) -> bytes:
    if member.size > limit:
        raise NativeError(f"{member.name} is {member.size} bytes; at most {limit}", code="invalid_image")
    handle = outer.extractfile(member)
    assert handle is not None
    return handle.read()


def read_image(outer: tarfile.TarFile) -> tuple[tarfile.TarInfo, tarfile.TarInfo]:
    """The config and the one layer named by ``manifest.json``."""
    raw = _read_small(outer, _outer_member(outer, "manifest.json"))
    try:
        entries = json.loads(raw)
    except ValueError as exc:
        raise NativeError("manifest.json is not JSON", code="invalid_image") from exc
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict):
        raise NativeError("the image tar must hold exactly one image", code="invalid_image")
    layers = entries[0].get("Layers")
    if not isinstance(layers, list) or len(layers) != 1:
        raise NativeError("the runtime carrier must have exactly one layer", code="invalid_image")
    return _outer_member(outer, str(entries[0].get("Config") or "")), _outer_member(outer, str(layers[0]))


def rootfs_digest_of(diff_ids: list[str]) -> str:
    """The backend's ``compute_rootfs_digest``: the image's content identity across Docker stores."""
    return "sha256:" + hashlib.sha256("\n".join(diff_ids).encode()).hexdigest()


def check_config(
    data: bytes, *, config_digest: str | None, layer_digest: str, rootfs_digest: str | None = None
) -> dict[str, Any]:
    """The pin is the content: diff_ids (and their rootfs digest), the layer hashed after.

    The config's own digest is the image Id, which differs between Docker's
    classic and containerd stores; a mismatch is only logged.
    """
    got = "sha256:" + hashlib.sha256(data).hexdigest()
    if config_digest and got != config_digest:
        log.warning("Runtime image config is %s, the bundle names %s; checking its content instead", got, config_digest)
    try:
        config = json.loads(data)
    except ValueError as exc:
        raise NativeError("the image config is not JSON", code="invalid_image") from exc
    diff_ids = (config.get("rootfs") or {}).get("diff_ids")
    if diff_ids != [layer_digest]:
        raise _mismatch(f"image rootfs is {diff_ids}, expected [{layer_digest}]")
    if rootfs_digest and rootfs_digest_of(diff_ids) != rootfs_digest:
        raise _mismatch(f"image rootfs digest is {rootfs_digest_of(diff_ids)}, expected {rootfs_digest}")
    labels = (config.get("config") or {}).get("Labels") or {}
    if config.get("os") != "darwin" and labels.get("org.fadenstack.runtime.os") != "darwin":
        raise NativeError("the image is not a darwin runtime carrier", code="invalid_image")
    return config


class _Capped:
    """A read-only stream that refuses to yield more than *limit* bytes."""

    def __init__(self, raw: IO[bytes], limit: int) -> None:
        self._raw = raw
        self._limit = limit
        self.count = 0
        self.sha = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self._raw.read(size if size is not None and size >= 0 else _CHUNK)
        self.count += len(data)
        if self.count > self._limit:
            raise _unsafe(f"the layer unpacks to more than {self._limit} bytes")
        self.sha.update(data)
        return data


@contextmanager
def _layer_stream(outer: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> Iterator[_Capped]:
    handle = outer.extractfile(member)
    assert handle is not None
    head = handle.read(2)
    handle.seek(0)
    raw: IO[bytes] = gzip.GzipFile(fileobj=handle, mode="rb") if head == b"\x1f\x8b" else handle  # type: ignore[assignment]
    try:
        yield _Capped(raw, limit)
    finally:
        raw.close()


def unpacked_limit(member: tarfile.TarInfo, size_bytes: int, unpacked_size_bytes: int | None) -> int:
    if unpacked_size_bytes:
        # C1's figure is the layer tar's size; 1 % plus 1 MiB of slack in case
        # it is ever the files' sum (headers and padding add ~512 B a member).
        return min(int(unpacked_size_bytes) * 101 // 100 + (1 << 20), MAX_UNPACKED)
    return min(max(max(member.size, int(size_bytes)) * MAX_RATIO, MIN_LIMIT), MAX_UNPACKED)


def hash_layer(outer: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> str:
    with _layer_stream(outer, member, limit) as stream:
        while stream.read(_CHUNK):
            pass
        return "sha256:" + stream.sha.hexdigest()


# ── extraction ────────────────────────────────────────────────────


def _inside(norm: str) -> bool:
    return norm == "runtime" or norm.startswith("runtime/")


def check_member(member: tarfile.TarInfo, files: set[str]) -> str:
    """The extra rules on top of the ``data`` filter; returns the normalised name."""
    name = member.name
    if len(name) > MAX_NAME or "\x00" in name:
        raise _unsafe(f"member name too long or invalid: {name[:80]!r}")
    if name.startswith("/"):
        raise _unsafe(f"absolute member name {name!r}")
    norm = posixpath.normpath(name)
    if norm == ".." or norm.startswith("../") or ".." in name.split("/"):
        raise _unsafe(f"member {name!r} climbs out with ..")
    if not _inside(norm):
        raise _unsafe(f"member {name!r} is outside runtime/")
    if member.mode & 0o6000:
        raise _unsafe(f"member {name!r} is setuid or setgid")
    if member.isreg() or member.isdir():
        return norm
    if member.issym():
        link = member.linkname
        if link.startswith("/"):
            raise _unsafe(f"symlink {name!r} points at the absolute path {link!r}")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(norm), link))
        if not _inside(target):
            raise _unsafe(f"symlink {name!r} → {link!r} leaves runtime/")
        return norm
    if member.islnk():
        target = posixpath.normpath(member.linkname)
        if target not in files:
            raise _unsafe(f"hardlink {name!r} → {member.linkname!r} is not a file of this layer")
        return norm
    raise _unsafe(f"member {name!r} is a device, fifo or other special file")


def extract_layer(stream: IO[bytes], dest: Path, *, limit: int) -> int:
    """Extract an uncompressed layer tar into *dest*; returns the member count."""
    if not hasattr(tarfile, "data_filter"):
        raise NativeError("this Python has no tarfile data filter; refusing to extract", code="unsupported")
    files: set[str] = set()
    links: list[str] = []
    total = 0
    count = 0
    with tarfile.open(fileobj=stream, mode="r|") as layer:
        for member in layer:
            count += 1
            if count > MAX_MEMBERS:
                raise _unsafe(f"the layer has more than {MAX_MEMBERS} members")
            norm = check_member(member, files)
            total += max(member.size, 0)
            if total > limit:
                raise _unsafe(f"the layer's files add up to more than {limit} bytes")
            try:
                layer.extract(member, dest, filter="data", set_attrs=True)
            except tarfile.FilterError as exc:
                raise _unsafe(f"refused {member.name!r}: {exc}") from exc
            if member.isreg():
                files.add(norm)
            elif member.issym():
                links.append(norm)
                _check_link(dest, norm)
    # A chain of links made later may change where an earlier one leads.
    for norm in links:
        _check_link(dest, norm)
    return count


def _check_link(dest: Path, norm: str) -> None:
    """Resolved on disk, through every link before it, a symlink stays under ``runtime/``."""
    root = os.path.realpath(dest / "runtime")
    real = os.path.realpath(dest / norm)
    if real != root and not real.startswith(root + os.sep):
        raise _unsafe(f"symlink {norm!r} resolves to {real}, outside runtime/")


# ── the runtime tree ──────────────────────────────────────────────


def _rel_inside(root: Path, rel: str, what: str) -> Path:
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise NativeError(f"{what} {rel!r} must be relative inside runtime/", code="invalid_runtime")
    return root / rel


def check_runtime(runtime: Path, *, interpreter_path: str | None = None) -> dict[str, Any]:
    """Validate ``RUNTIME-MANIFEST.json`` against what was extracted."""
    path = runtime / MANIFEST
    if not path.is_file() or path.is_symlink():
        raise NativeError(f"{MANIFEST} is missing", code="invalid_runtime")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise NativeError(f"{MANIFEST} is not JSON", code="invalid_runtime") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != "python-venv":
        raise NativeError(f"{MANIFEST} is not a python-venv runtime", code="invalid_runtime")
    if (manifest.get("target") or {}).get("os", "darwin") != "darwin":
        raise NativeError(f"{MANIFEST} targets {manifest['target'].get('os')}", code="invalid_runtime")
    if not (manifest.get("engines") or {}).get("vllm"):
        raise NativeError(f"{MANIFEST} names no vllm engine", code="invalid_runtime")
    if (manifest.get("patches") or {}).get("mlx_lm_fd_cap_raise_only") is not True:
        raise NativeError("the runtime lacks the raise-only mlx_lm open-file patch", code="invalid_runtime")
    named = str((manifest.get("interpreter") or {}).get("path") or "")
    if interpreter_path and interpreter_path != named:
        raise NativeError(
            f"the bundle names the interpreter {interpreter_path}, the runtime {named}", code="invalid_runtime"
        )
    interp = _rel_inside(runtime, named, "interpreter.path")
    venv = _rel_inside(runtime, str(manifest.get("venv") or "venv"), "venv")
    real_root = runtime.resolve()
    if not interp.is_file() or real_root not in interp.resolve().parents:
        raise NativeError(f"interpreter {interp} is missing", code="invalid_runtime")
    link = venv / "bin" / "python"
    if not link.is_symlink() or os.readlink(link).startswith("/"):
        raise NativeError("venv/bin/python must be a relative symlink", code="invalid_runtime")
    if real_root not in link.resolve().parents:
        raise NativeError("venv/bin/python resolves outside the runtime", code="invalid_runtime")
    if not (venv / "pyvenv.cfg").is_file() or not (venv / "bin" / "vllm").is_file():
        raise NativeError("the venv lacks pyvenv.cfg or bin/vllm", code="invalid_runtime")
    return manifest


def relocate(runtime: Path, final_runtime: Path, manifest: dict[str, Any]) -> None:
    """Gate 0d: ``pyvenv.cfg`` ``home`` names where the interpreter will live."""
    venv = runtime / str(manifest.get("venv") or "venv")
    cfg = venv / "pyvenv.cfg"
    home = final_runtime / posixpath.dirname(str(manifest["interpreter"]["path"]))
    lines = cfg.read_text(encoding="utf-8").splitlines()
    out = [f"home = {home}" if line.split("=", 1)[0].strip() == "home" else line for line in lines]
    if not any(line.startswith("home = ") for line in out):
        out.insert(0, f"home = {home}")
    tmp = cfg.with_name(".pyvenv.cfg.tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, cfg)


def read_stamp(directory: Path) -> dict[str, Any] | None:
    try:
        stamp = json.loads((directory / STAMP).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stamp, dict) or "sha256:" + directory.name != stamp.get("layer_digest"):
        return None
    return stamp


def is_staged(paths: NativePaths, bundle_id: str, layer_digest: str) -> bool:
    directory = paths.runtime_dir(bundle_id, layer_digest)
    return read_stamp(directory) is not None and (directory / "runtime" / MANIFEST).is_file()


def _fsync_tree(root: Path) -> None:
    for current, _dirs, names in os.walk(root):
        for name in names:
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            try:
                fd = os.open(path, os.O_RDONLY)
            except OSError:
                continue
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fsync_dir(Path(current))


def stage_from_tar(
    tar_path: Path,
    *,
    paths: NativePaths,
    bundle_id: str,
    config_digest: str | None,
    layer_digest: str,
    size_bytes: int,
    unpacked_size_bytes: int | None = None,
    rootfs_digest: str | None = None,
    interpreter_path: str | None = None,
    pyvenv_home_rewrite: bool = True,
) -> Path:
    """Verify and extract; returns ``<runtimes>/<bundle>/<layer_hex>``."""
    final = paths.runtime_dir(bundle_id, layer_digest)
    if is_staged(paths, bundle_id, layer_digest):
        return final
    bundle_dir = paths.bundle_dir(bundle_id)
    bundle_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for stale in bundle_dir.glob(".staging-*"):
        shutil.rmtree(stale, ignore_errors=True)
    staging = bundle_dir / f".staging-{secrets.token_hex(6)}"
    staging.mkdir(mode=0o700)
    try:
        with tarfile.open(tar_path, mode="r:") as outer:
            config_member, layer_member = read_image(outer)
            config = check_config(
                _read_small(outer, config_member), config_digest=config_digest, layer_digest=layer_digest,
                rootfs_digest=rootfs_digest,
            )
            limit = unpacked_limit(layer_member, size_bytes, unpacked_size_bytes)
            got = hash_layer(outer, layer_member, limit)
            if got != layer_digest:
                raise _mismatch(f"layer is {got}, expected {layer_digest}")
            with _layer_stream(outer, layer_member, limit) as stream:
                members = extract_layer(stream, staging, limit=limit)  # type: ignore[arg-type]
                while stream.read(_CHUNK):  # the tar's end blocks, so the hash covers it all
                    pass
                if "sha256:" + stream.sha.hexdigest() != layer_digest:
                    raise _mismatch("the layer changed between verification and extraction")
        manifest = check_runtime(staging / "runtime", interpreter_path=interpreter_path)
        if pyvenv_home_rewrite:
            relocate(staging / "runtime", final / "runtime", manifest)
        stamp = {
            "bundle_id": bundle_id,
            "layer_digest": layer_digest,
            "config_digest": config_digest,
            "members": members,
            "staged_at": time.time(),
            "engines": manifest.get("engines"),
            "interpreter": manifest.get("interpreter"),
            "image_created": config.get("created"),
        }
        write_json_durable(staging / STAMP, stamp)
        _fsync_tree(staging)
        if final.exists():
            # Present without a valid stamp: an interrupted earlier stage.
            trash = bundle_dir / f".staging-old-{secrets.token_hex(6)}"
            os.rename(final, trash)
            shutil.rmtree(trash, ignore_errors=True)
        os.rename(staging, final)
        fsync_dir(bundle_dir)
        return final
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
