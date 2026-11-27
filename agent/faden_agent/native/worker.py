"""One ``vllm serve`` worker: checking what the backend asks for, its argv, env and log."""

from __future__ import annotations

import os
import re
import shutil
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from faden_agent.native.common import NativeError, NativePaths, check_id

#: Prints the worker's effective open-file limit into its log, then becomes
#: the worker (exec keeps the pid). A constant: the arguments travel as "$@".
SHIM = 'echo "faden: NOFILE soft=$(ulimit -n) hard=$(ulimit -Hn)"; exec "$@"'

MAX_UTILIZATION = 0.95
LOG_MAX_BYTES = 20 << 20

#: extra_args the backend may pass, with the value each takes (None: a switch).
_EXTRA: dict[str, re.Pattern[str] | None] = {
    "--dtype": re.compile(r"^(auto|float16|bfloat16|float32|half)$"),
    "--max-num-seqs": re.compile(r"^[1-9][0-9]{0,4}$"),
    "--max-num-batched-tokens": re.compile(r"^[1-9][0-9]{0,7}$"),
    "--seed": re.compile(r"^[0-9]{1,10}$"),
    "--tool-call-parser": re.compile(r"^[a-z0-9_]{1,64}$"),
    "--reasoning-parser": re.compile(r"^[a-z0-9_]{1,64}$"),
    "--generation-config": re.compile(r"^(auto|vllm)$"),
    "--enable-prefix-caching": None,
    "--no-enable-prefix-caching": None,
    "--enable-auto-tool-choice": None,
}
_ENV_KEY = re.compile(r"^(VLLM|MLX|METAL)_[A-Z0-9_]{1,64}$")
_ENV_DENY = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
_SERVED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}$")


@dataclass
class WorkerSpec:
    deployment_id: str
    generation: int | str
    bundle_id: str
    layer_digest: str | None
    model_name: str
    snapshot: Path
    served_model_name: str
    port: int
    gpu_memory_utilization: float
    max_model_len: int | None
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    #: The backend's model id, Hub repo and revision, kept in the record for the operator.
    model_info: dict[str, str | None] = field(default_factory=dict)


def _int(value: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(value, bool):
        raise NativeError(f"{what} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise NativeError(f"{what} must be an integer") from exc
    if not lo <= number <= hi or (isinstance(value, float) and value != number):
        raise NativeError(f"{what} must be between {lo} and {hi}, got {value!r}")
    return number


def check_extra_args(args: Any) -> list[str]:
    if args is None:
        return []
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise NativeError("extra_args must be a list of strings")
    out: list[str] = []
    i = 0
    while i < len(args):
        flag, eq, value = args[i].partition("=")
        if flag not in _EXTRA:
            raise NativeError(f"extra_args: {flag!r} is not allowed (allowed: {', '.join(sorted(_EXTRA))})")
        pattern = _EXTRA[flag]
        if pattern is None:
            if eq:
                raise NativeError(f"extra_args: {flag} takes no value")
            out.append(flag)
            i += 1
            continue
        if not eq:
            if i + 1 >= len(args):
                raise NativeError(f"extra_args: {flag} needs a value")
            value = args[i + 1]
            i += 1
        if not pattern.fullmatch(value):
            raise NativeError(f"extra_args: {flag} {value!r} is not allowed")
        out += [flag, value]
        i += 1
    return out


def check_env(env: Any) -> dict[str, str]:
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise NativeError("env must be an object")
    out: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_KEY.fullmatch(key) or any(d in key for d in _ENV_DENY):
            raise NativeError(f"env: {str(key)[:64]!r} is not allowed (VLLM_*, MLX_*, METAL_* only, no secrets)")
        text = str(value)
        if len(text) > 4096 or "\x00" in text:
            raise NativeError(f"env: the value of {key} is too long or invalid")
        out[key] = text
    return out


def check_snapshot(path: Any, model_root: Path) -> Path:
    """An absolute path that resolves, every symlink followed, inside the model store."""
    text = str(path or "")
    if not text.startswith("/") or "\x00" in text:
        raise NativeError("model.snapshot_path must be an absolute path")
    try:
        real = Path(text).resolve(strict=True)
        root = model_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise NativeError(f"model.snapshot_path {text} does not exist", code="model_missing") from exc
    if real != root and root not in real.parents:
        raise NativeError(f"model.snapshot_path {text} is outside the model store {model_root}")
    if not real.is_dir() or not (real / "config.json").is_file():
        raise NativeError(f"model.snapshot_path {text} is not a model snapshot (no config.json)", code="model_missing")
    return real


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # As uvicorn binds: TIME_WAIT from the last run's connections is no conflict, a listener is.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def parse_spec(payload: dict[str, Any], *, model_root: Path) -> WorkerSpec:
    generation = payload.get("generation")
    if isinstance(generation, bool) or generation is None:
        raise NativeError("generation is required")
    if not isinstance(generation, int):
        generation = check_id(generation, "generation")
    host = str(payload.get("host") or "127.0.0.1")
    if host != "127.0.0.1":
        raise NativeError("host must be 127.0.0.1: the model proxy publishes the worker")
    model = payload.get("model")
    if not isinstance(model, dict):
        raise NativeError("model must be an object with name and snapshot_path")
    served = str(payload.get("served_model_name") or model.get("name") or "")
    if not _SERVED.fullmatch(served):
        raise NativeError(f"served_model_name {served[:80]!r} is not allowed")
    try:
        util = float(payload.get("gpu_memory_utilization"))
    except (TypeError, ValueError) as exc:
        raise NativeError("gpu_memory_utilization must be a number") from exc
    if not 0.0 < util <= MAX_UTILIZATION:
        raise NativeError(f"gpu_memory_utilization must be in (0, {MAX_UTILIZATION}], got {util}")
    max_len = payload.get("max_model_len")
    layer = payload.get("layer_digest")
    if layer is not None:
        from faden_agent.native.common import check_digest  # noqa: PLC0415

        layer = check_digest(layer, "layer_digest")
    return WorkerSpec(
        deployment_id=check_id(payload.get("deployment_id"), "deployment_id"),
        generation=generation,
        bundle_id=check_id(payload.get("bundle_id"), "bundle_id"),
        layer_digest=layer,
        model_name=str(model.get("name") or served),
        snapshot=check_snapshot(model.get("snapshot_path"), model_root),
        served_model_name=served,
        port=_int(payload.get("port"), "port", 1024, 65535),
        gpu_memory_utilization=util,
        max_model_len=None if max_len in (None, "") else _int(max_len, "max_model_len", 16, 10_000_000),
        extra_args=check_extra_args(payload.get("extra_args")),
        env=check_env(payload.get("env")),
        model_info={k: (str(model[k])[:256] if model.get(k) is not None else None)
                    for k in ("id", "hf_repo_id", "revision")},
    )


def vllm_args(spec: WorkerSpec, venv: Path) -> list[str]:
    args = [
        str(venv / "bin" / "vllm"), "serve", str(spec.snapshot),
        "--host", "127.0.0.1",
        "--port", str(spec.port),
        "--served-model-name", spec.served_model_name,
        "--gpu-memory-utilization", f"{spec.gpu_memory_utilization:g}",
    ]
    if spec.max_model_len:
        args += ["--max-model-len", str(spec.max_model_len)]
    return args + spec.extra_args


def argv(spec: WorkerSpec, venv: Path) -> list[str]:
    return ["/bin/sh", "-c", SHIM, "faden-vllm", *vllm_args(spec, venv)]


def environment(spec: WorkerSpec, paths: NativePaths, venv: Path) -> dict[str, str]:
    """Built from nothing: the agent's own environment (credentials, env file) never reaches the worker."""
    home = paths.home
    env = {
        "PATH": f"{venv / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "TMPDIR": str(paths.tmp(spec.deployment_id)) + "/",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HOME": str(home / "hf"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "VLLM_NO_USAGE_STATS": "1",
        "DO_NOT_TRACK": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
    }
    env.update(spec.env)
    return env


def probe_env(paths: NativePaths, venv: Path) -> dict[str, str]:
    return {
        "PATH": f"{venv / 'bin'}:/usr/bin:/bin",
        "HOME": str(paths.home),
        "TMPDIR": str(paths.root / "tmp") + "/",
        "LANG": "en_US.UTF-8",
        "PYTHONNOUSERSITE": "1",
    }


def snapshot_bytes(path: Path) -> int:
    total = 0
    for current, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += (Path(current) / name).stat().st_size  # follows the HF blob symlinks
            except OSError:
                pass
    return total


def ready_timeout(snapshot_size: int) -> int:
    """Gate 0b/0d: ~100 s for a 0.6B model; bigger models load longer."""
    gib = snapshot_size / (1 << 30)
    return int(min(3600, max(300, 180 + 120 * gib)))


# ── logs ──────────────────────────────────────────────────────────


def rotate_if_big(path: Path, limit: int = LOG_MAX_BYTES) -> bool:
    """Copy-truncate: the worker holds the file open in append mode."""
    try:
        if path.stat().st_size <= limit:
            return False
        shutil.copyfile(path, path.with_suffix(".log.1"))
        os.truncate(path, 0)
        return True
    except OSError:
        return False


_ERRORISH = re.compile(r"(error|exception|traceback|raise |failed|killed|out of memory)", re.IGNORECASE)


def tail_lines(path: Path, count: int, *, max_bytes: int = 1 << 20) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]  # the first may be cut
    return lines[-count:] if count > 0 else []


def last_error(path: Path) -> str | None:
    lines = tail_lines(path, 200, max_bytes=64 << 10)
    picked = [line for line in lines if _ERRORISH.search(line)][-20:] or lines[-5:]
    text = "\n".join(picked).strip()
    return text[-4000:] or None


def read_logs(path: Path, *, tail: int, cursor: str | None) -> tuple[list[str], str]:
    """From the byte offset *cursor* (new lines only), else the last *tail* lines."""
    tail = max(1, min(int(tail or 200), 2000))
    try:
        size = path.stat().st_size
    except OSError:
        return [], "0"
    if cursor is not None and str(cursor).isdigit():
        offset = int(cursor)
        if offset > size:  # rotated
            offset = 0
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(4 << 20)
        end = data.rfind(b"\n") + 1  # whole lines only; the rest comes next time
        lines = data[:end].decode("utf-8", "replace").splitlines()
        return lines[-tail:], str(offset + end)
    return tail_lines(path, tail), str(size)
