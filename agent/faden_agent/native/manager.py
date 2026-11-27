"""The six native-runtime commands (C1 contract) and the workers they run.

A worker is ``vllm serve`` in its own session (process group), started from a
staged runtime with an environment built from nothing. Its record is written
durably before the command returns, so an agent that restarts -- a crash, an
upgrade, launchd's KeepAlive -- finds it again: a worker still running with
the recorded pid, creation time and arguments is adopted (no second cold
start); anything else is marked failed and what is left of its tree ended.
An agent stopping does not stop its workers; ``faden-agent stop`` and
``uninstall`` do (``darwin_service.stop_native_workers``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psutil

from faden_agent.native import proc as procs
from faden_agent.native import sandbox as sandboxing
from faden_agent.native import stage as staging
from faden_agent.native import worker as workers
from faden_agent.native.common import (
    NativeError,
    NativePaths,
    check_digest,
    check_id,
    ensure_private_dir,
    read_json_nofollow,
    write_json_durable,
)

log = logging.getLogger(__name__)

#: The model store M syncs into; snapshot paths must resolve inside it.
MODEL_STORE_ROOT = Path("/var/db/fadenstack/agent/models")
RECORD_VERSION = 1
LIVE = ("starting", "ready")
_HEADROOM = 1 << 30

FetchTar = Callable[..., Awaitable[Path | None]]
ProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]


def _env_hash(env: dict[str, str]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()


@dataclass
class _Handle:
    popen: subprocess.Popen[bytes] | None
    watcher: asyncio.Task[None] | None = None


class NativeRuntimeManager:
    #: SIGTERM→SIGKILL grace when the agent itself ends a tree (timeout, restart cleanup).
    cleanup_grace_s: float = 10.0
    #: ... and when a new generation replaces a running one.
    restart_grace_s: float = 30.0

    def __init__(
        self,
        *,
        paths: NativePaths,
        fetch_tar: FetchTar | None = None,
        model_root: Path = MODEL_STORE_ROOT,
        max_workers: int = 1,
        on_capabilities: Callable[[dict[str, Any]], None] | None = None,
        poll_interval: float = 2.0,
        probe_metal: bool = True,
        sandbox: bool = True,
        sandbox_deny: list[Path] | None = None,
        sandbox_config_error: str | None = None,
    ) -> None:
        self.paths = paths
        self._fetch_tar = fetch_tar
        self._model_root = model_root
        self._max_workers = max(1, int(max_workers))
        self._on_capabilities = on_capabilities
        self._poll = poll_interval
        self._probe_metal = probe_metal
        #: Run the worker under sandbox-exec (native/sandbox.py), denying it the
        #: agent's state (credential, env file, model-TLS key). On by default.
        #: Wanted unless the operator said FADEN_NODE_AGENT_NATIVE_SANDBOX=0; a
        #: missing sandbox-exec then refuses starts (fails closed) instead of
        #: quietly running an unsandboxed worker.
        self._sandbox_wanted = bool(sandbox)
        self._sandbox_available = sandboxing.available()
        #: FADEN_NODE_AGENT_NATIVE_SANDBOX held something unknown: refuse every start.
        self._sandbox_config_error = sandbox_config_error
        self._sandbox = self._sandbox_wanted and self._sandbox_available and not sandbox_config_error
        if sandbox_config_error:
            log.error("Native workers are refused: %s", sandbox_config_error)
        self._sandbox_deny = list(sandbox_deny) if sandbox_deny is not None else [
            paths.root.parent, Path("/Library/Fadenstack"),
        ]
        self._handles: dict[str, _Handle] = {}
        self._last_found = 0
        self._lock = asyncio.Lock()
        self._stage_locks: dict[str, asyncio.Lock] = {}
        self._metal: dict[str, Any] = {"unified_memory_bytes": psutil.virtual_memory().total, "source": "sysctl"}
        for directory in (paths.root, paths.runtimes, paths.workers, paths.logs, paths.home):
            ensure_private_dir(directory)

    # ── records ───────────────────────────────────────────────────

    def _record(self, deployment_id: str) -> dict[str, Any] | None:
        return read_json_nofollow(self.paths.worker_record(deployment_id))

    def _save(self, record: dict[str, Any]) -> None:
        write_json_durable(self.paths.worker_record(record["deployment_id"]), record)

    def _records(self) -> list[dict[str, Any]]:
        return self._scan()[0]

    def model_paths_in_use(self) -> tuple[list[str], int]:
        """Snapshot paths of workers that may still be reading the model store, and how many records are unreadable.

        Used by ``remove_model``: a model is not removed under a worker that is starting or ready, nor
        while a record cannot be read (its snapshot is unknown).
        """
        good, bad = self._scan()
        paths = [
            str((r.get("model") or {}).get("snapshot_path") or "")
            for r in good if r.get("state") in ("starting", "ready")
        ]
        return [p for p in paths if p], len(bad)

    def _scan(self) -> tuple[list[dict[str, Any]], list[Path]]:
        """Valid records, and the paths of the ones that are not (reported, never ignored)."""
        good: list[dict[str, Any]] = []
        bad: list[Path] = []
        for path in sorted(self.paths.workers.glob("*.json")):
            record = read_json_nofollow(path)
            try:
                if not record or record.get("deployment_id") != path.stem:
                    raise procs.BadRecord("unreadable or not this deployment's")
                procs.validate(record)
            except procs.BadRecord as exc:
                # Written atomically, so this is not a crash mid-write: say so loudly.
                log.error("Native worker record %s is corrupt (%s); its processes are found by their marker", path, exc)
                bad.append(path)
                continue
            good.append(record)
        return good, bad

    def sandbox_state(self) -> dict[str, Any]:
        return {"wanted": self._sandbox_wanted, "available": self._sandbox_available, "active": self._sandbox,
                "config_error": self._sandbox_config_error}

    # ── capabilities ──────────────────────────────────────────────

    def capabilities(self) -> dict[str, Any]:
        # native_sandbox false: workers would run (or are refused) without the sandbox.
        return {"native_runtime": 1, "native_sandbox": self._sandbox, "metal": dict(self._metal)}

    async def refresh_metal(self) -> None:
        """``recommended_max_working_set_bytes`` from the staged runtime's mlx; absent before staging."""
        runtime = self._newest_runtime(None)
        if runtime is None or not self._probe_metal:
            return
        venv = runtime / "runtime" / "venv"
        code = "import json, mlx.core as mx; f = getattr(mx, 'device_info', None) or mx.metal.device_info; print(json.dumps(f()))"
        try:
            proc = await asyncio.create_subprocess_exec(
                str(venv / "bin" / "python"), "-I", "-c", code,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=workers.probe_env(self.paths, venv), start_new_session=True,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                raise
            info = json.loads(out.decode().strip().splitlines()[-1])
            value = int(info["max_recommended_working_set_size"])
        except Exception as exc:  # noqa: BLE001 - the estimate stands in
            log.warning("Could not read Metal's recommended working set from the runtime: %s", exc)
            return
        self._metal = {
            "unified_memory_bytes": int(info.get("memory_size") or self._metal["unified_memory_bytes"]),
            "recommended_max_working_set_bytes": value,
            "source": "mlx",
        }
        if self._on_capabilities is not None:
            self._on_capabilities(self.capabilities())

    # ── staging ───────────────────────────────────────────────────

    def _staged(self) -> list[dict[str, Any]]:
        out = []
        if not self.paths.runtimes.is_dir():
            return out
        for bundle in sorted(self.paths.runtimes.iterdir()):
            if bundle.name.startswith(".") or not bundle.is_dir():
                continue
            for layer in sorted(bundle.iterdir()):
                stamp = staging.read_stamp(layer) if not layer.name.startswith(".") else None
                if stamp:
                    out.append({
                        "bundle_id": bundle.name,
                        "layer_digest": stamp["layer_digest"],
                        "runtime_root": str(layer / "runtime"),
                        "staged_at": stamp.get("staged_at"),
                        "engines": stamp.get("engines"),
                    })
        return out

    def _newest_runtime(self, bundle_id: str | None, layer_digest: str | None = None) -> Path | None:
        staged = [s for s in self._staged() if bundle_id is None or s["bundle_id"] == bundle_id]
        if layer_digest:
            staged = [s for s in staged if s["layer_digest"] == layer_digest]
        if not staged:
            return None
        best = max(staged, key=lambda s: s.get("staged_at") or 0)
        return Path(best["runtime_root"]).parent

    async def stage_native_runtime(
        self, payload: dict[str, Any], *, emit_progress: ProgressEmitter | None = None
    ) -> dict[str, Any]:
        bundle_id = check_id(payload.get("bundle_id"), "bundle_id")
        config_digest = (
            check_digest(payload.get("config_digest"), "config_digest") if payload.get("config_digest") else None
        )
        rootfs_digest = (
            check_digest(payload.get("rootfs_digest"), "rootfs_digest") if payload.get("rootfs_digest") else None
        )
        interpreter_path = payload.get("interpreter_path")
        interpreter_path = str(interpreter_path) if interpreter_path else None
        rewrite = payload.get("pyvenv_home_rewrite")
        rewrite = True if rewrite is None else bool(rewrite)
        layer_digest = check_digest(payload.get("layer_digest"), "layer_digest")
        size = workers._int(payload.get("size_bytes"), "size_bytes", 1, staging.MAX_UNPACKED)
        unpacked = payload.get("unpacked_size_bytes")
        unpacked = None if unpacked in (None, "") else workers._int(unpacked, "unpacked_size_bytes", 1, staging.MAX_UNPACKED)
        image = str(payload.get("oci_image") or "")
        tag = str(payload.get("tag") or "")
        if not image:
            raise NativeError("oci_image is required")
        if tag and ":" not in image.rsplit("/", 1)[-1]:
            image = f"{image}:{tag}"
        lock = self._stage_locks.setdefault(bundle_id, asyncio.Lock())
        async with lock:
            final = self.paths.runtime_dir(bundle_id, layer_digest)
            if staging.is_staged(self.paths, bundle_id, layer_digest):
                return {"ok": True, "staged": True, "runtime_root": str(final / "runtime"),
                        "layer_digest": layer_digest, "fetched": False}
            if self._fetch_tar is None:
                raise NativeError("no image transfer configured", code="unsupported")
            ensure_private_dir(self.paths.downloads)
            # docker save may hand over the layer compressed (containerd store) or
            # not (classic): room for the larger tar, the extracted tree, and a margin.
            tree = unpacked or size * 4
            need = max(size, tree) + tree + _HEADROOM
            free = shutil.disk_usage(self.paths.root).free
            if free < need:
                raise NativeError(f"{free >> 20} MiB free, staging needs about {need >> 20} MiB", code="disk_space")
            tar = await self._fetch_tar(
                image=image, expect_rootfs=rootfs_digest, directory=self.paths.downloads, emit_progress=emit_progress
            )
            if tar is None:
                raise NativeError("no room to keep the runtime image for verification", code="disk_space")
            try:
                if emit_progress is not None:
                    await emit_progress({"phase": "extracting", "message": f"Verifying and extracting {image}"})
                root = await asyncio.to_thread(
                    staging.stage_from_tar, Path(tar), paths=self.paths, bundle_id=bundle_id,
                    config_digest=config_digest, layer_digest=layer_digest, size_bytes=size,
                    unpacked_size_bytes=unpacked, rootfs_digest=rootfs_digest,
                    interpreter_path=interpreter_path, pyvenv_home_rewrite=rewrite,
                )
            finally:
                Path(tar).unlink(missing_ok=True)
        log.info("Staged native runtime %s %s at %s", bundle_id, layer_digest, root)
        await self.refresh_metal()
        return {"ok": True, "staged": True, "runtime_root": str(root / "runtime"),
                "layer_digest": layer_digest, "fetched": True}

    async def native_runtime_status(self, _payload: dict[str, Any] | None = None) -> dict[str, Any]:
        good, bad = self._scan()
        return {
            "ok": True,
            "staged": [{k: s[k] for k in ("bundle_id", "layer_digest", "runtime_root")} for s in self._staged()],
            "workers": [self._public(r) for r in good]
            + [{"deployment_id": p.stem, "state": "unknown_record", "path": str(p)} for p in bad],
            "max_workers": self._max_workers,
            "sandbox": self.sandbox_state(),
        }

    # ── workers ───────────────────────────────────────────────────

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        keys = ("deployment_id", "generation", "state", "pid", "port", "served_model_name",
                "started_at", "ready_at", "exit_code", "last_error", "stopped_at", "bundle_id", "layer_digest")
        return {k: record.get(k) for k in keys}

    async def start_native_worker(self, payload: dict[str, Any]) -> dict[str, Any]:
        spec = workers.parse_spec(payload, model_root=self._model_root)
        if self._sandbox_config_error:
            raise NativeError(self._sandbox_config_error, code="sandbox_config_invalid")
        if self._sandbox_wanted and not self._sandbox_available:
            raise NativeError(
                f"{sandboxing.SANDBOX_EXEC} is missing, so the worker cannot be sandboxed away from the "
                "agent's credential; refusing to start it. Set FADEN_NODE_AGENT_NATIVE_SANDBOX=0 to run "
                "unsandboxed deliberately.",
                code="sandbox_unavailable",
            )
        async with self._lock:
            current = self._record(spec.deployment_id)
            if current and current.get("state") in LIVE and self._leader(current) is not None:
                runtime = self._newest_runtime(spec.bundle_id, spec.layer_digest)
                same = (
                    current.get("generation") == spec.generation
                    and runtime is not None
                    and current.get("cmd_sha256") == procs.cmd_hash(workers.argv(spec, runtime / "runtime" / "venv"))
                    and current.get("env_sha256") == _env_hash(spec.env)
                )
                if same:
                    return {"ok": True, "pid": current["pid"], "port": current["port"],
                            "started_at": current["started_at"], "existing": True}
                # A new generation or a changed payload: the old engine goes first (C1 contract).
                log.info("Restarting native worker %s: generation %s -> %s",
                         spec.deployment_id, current.get("generation"), spec.generation)
                await self._stop_locked(spec.deployment_id, current, grace_s=self.restart_grace_s)
            live = [r for r in self._records() if r.get("state") in LIVE and r["deployment_id"] != spec.deployment_id
                    and self._leader(r) is not None]
            if len(live) >= self._max_workers:
                names = ", ".join(r["deployment_id"] for r in live)
                raise NativeError(
                    f"this machine runs at most {self._max_workers} native worker(s); {names} is running",
                    code="worker_exists",
                )
            if any(r.get("port") == spec.port for r in live) or not workers.port_free(spec.port):
                raise NativeError(f"port {spec.port} on 127.0.0.1 is in use", code="port_in_use")
            runtime = self._newest_runtime(spec.bundle_id, spec.layer_digest)
            if runtime is None:
                raise NativeError(f"runtime {spec.bundle_id} is not staged", code="not_staged")
            stamp = staging.read_stamp(runtime)
            if stamp is None:
                raise NativeError(f"runtime {runtime} has no valid staged stamp", code="not_staged")
            venv = runtime / "runtime" / "venv"
            argv = workers.argv(spec, venv)
            if self._sandbox:
                argv = sandboxing.wrap(argv, sandboxing.profile(
                    denied=self._sandbox_deny,
                    readable=[runtime, self._model_root],
                    writable=[self.paths.home, self.paths.tmp(spec.deployment_id)],
                ))
            env = workers.environment(spec, self.paths, venv)
            ensure_private_dir(self.paths.tmp(spec.deployment_id))
            ensure_private_dir(self.paths.home / "hf")
            log_path = self.paths.log_file(spec.deployment_id)
            workers.rotate_if_big(log_path, 0)  # a new start, a fresh log (the last run's stays as .1)
            size = workers.snapshot_bytes(spec.snapshot)
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, f"faden: starting {spec.deployment_id} generation {spec.generation}: "
                             f"{' '.join(workers.vllm_args(spec, venv))}"
                             f"{' (sandboxed)' if self._sandbox else ''}\n".encode())
                popen = subprocess.Popen(  # noqa: S603 - argv list, no shell interpolation
                    argv, stdin=subprocess.DEVNULL, stdout=fd, stderr=subprocess.STDOUT, env=env,
                    cwd=str(self.paths.tmp(spec.deployment_id)), start_new_session=True, close_fds=True,
                )
            finally:
                os.close(fd)
            try:
                created = psutil.Process(popen.pid).create_time()
            except psutil.Error:
                created = time.time()
            record = {
                "v": RECORD_VERSION,
                "deployment_id": spec.deployment_id,
                "generation": spec.generation,
                "bundle_id": spec.bundle_id,
                "layer_digest": stamp["layer_digest"],
                "pid": popen.pid,
                "pgid": popen.pid,
                "started_at": created,
                "argv": argv,
                "sandboxed": self._sandbox,
                "marker": workers.marker(spec.deployment_id, spec.generation),
                "cmd_sha256": procs.cmd_hash(argv),
                "env_sha256": _env_hash(spec.env),
                "port": spec.port,
                "served_model_name": spec.served_model_name,
                "model": {**spec.model_info, "name": spec.model_name, "snapshot_path": str(spec.snapshot),
                          "bytes": size},
                "runtime_root": str(runtime / "runtime"),
                "log_path": str(log_path),
                "timeout_s": workers.ready_timeout(size),
                "state": "starting",
                "ready_at": None,
                "exit_code": None,
                "last_error": None,
                "stopped_at": None,
            }
            self._save(record)
            self._watch(spec.deployment_id, _Handle(popen=popen))
            log.info("Started native worker %s (pid %s, port %s)", spec.deployment_id, popen.pid, spec.port)
            return {"ok": True, "pid": popen.pid, "port": spec.port, "started_at": created}

    def _leader(self, record: dict[str, Any]) -> psutil.Process | None:
        handle = self._handles.get(record["deployment_id"])
        if handle is not None and handle.popen is not None and handle.popen.pid == record.get("pid"):
            # Our own child: its exit is known exactly.
            if handle.popen.poll() is not None:
                return None
            try:
                return psutil.Process(handle.popen.pid)
            except psutil.Error:
                return None
        return procs.matching(record)

    def _watch(self, deployment_id: str, handle: _Handle) -> None:
        old = self._handles.get(deployment_id)
        if old is not None and old.watcher is not None:
            old.watcher.cancel()
        self._handles[deployment_id] = handle
        handle.watcher = asyncio.create_task(self._watcher(deployment_id, handle), name=f"native-{deployment_id}")

    async def _probe_ready(self, port: int, served: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"http://127.0.0.1:{port}/v1/models")
            if response.status_code != 200:
                return False
            return any(m.get("id") == served for m in response.json().get("data") or [] if isinstance(m, dict))
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    async def _watcher(self, deployment_id: str, handle: _Handle) -> None:
        while True:
            record = self._record(deployment_id)
            if not record or record.get("state") not in LIVE or self._handles.get(deployment_id) is not handle:
                return
            log_path = Path(record["log_path"])
            if self._leader(record) is None:
                code = handle.popen.poll() if handle.popen is not None else None
                record.update(state="failed", exit_code=code,
                              last_error=workers.last_error(log_path) or f"the worker exited (code {code})")
                # Its children may outlive it: the recorded ones are still found.
                await asyncio.to_thread(procs.stop_worker, record, None, grace_s=self.cleanup_grace_s / 2)
                self._save(record)
                log.warning("Native worker %s exited: %s", deployment_id, record["last_error"])
                return
            leader = self._leader(record)
            family = procs.family_of(leader) if leader is not None else []
            if family and family != record.get("family"):
                # Kept so a descendant that leaves the group (setsid) is still stopped.
                record["family"] = procs.trim_family([*(record.get("family") or []), *family])
                self._save(record)
            if record["state"] == "starting":
                if await self._probe_ready(int(record["port"]), record["served_model_name"]):
                    record.update(state="ready", ready_at=time.time())
                    self._save(record)
                    log.info("Native worker %s is ready", deployment_id)
                elif time.time() - float(record["started_at"]) > float(record.get("timeout_s") or 300):
                    tail = workers.last_error(log_path)
                    record.update(state="failed",
                                  last_error=f"not ready after {record['timeout_s']} s" + (f": {tail}" if tail else ""))
                    self._save(record)  # before stopping: a status asked meanwhile says why
                    await self._stop_record(record, grace_s=self.cleanup_grace_s)
                    record.update(stopped_at=time.time())
                    self._save(record)
                    return
            workers.rotate_if_big(log_path)
            await asyncio.sleep(self._poll if record["state"] == "starting" else self._poll * 2.5)

    async def native_worker_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        deployment_id = check_id(payload.get("deployment_id"), "deployment_id")
        record = self._record(deployment_id)
        if record is None:
            return {"ok": True, "state": "stopped", "port": None}
        out = {"ok": True, **{k: record.get(k) for k in
                              ("state", "pid", "port", "ready_at", "exit_code", "last_error", "generation", "started_at")}}
        if record.get("state") in LIVE and deployment_id not in self._handles:
            out["state"] = "failed"  # nothing watches it: the agent never adopted it
            out["last_error"] = "the worker is not tracked by this agent"
        elif record.get("state") in LIVE and self._leader(record) is None:
            # Between two watcher passes: say it now; the watcher records it.
            handle = self._handles.get(deployment_id)
            out["state"] = "failed"
            out["exit_code"] = handle.popen.poll() if handle is not None and handle.popen is not None else None
            out["last_error"] = workers.last_error(Path(record["log_path"])) or "the worker exited"
        return out

    async def _stop_record(self, record: dict[str, Any], *, grace_s: float) -> list[int]:
        handle = self._handles.get(record["deployment_id"])
        popen = handle.popen if handle is not None and handle.popen is not None and handle.popen.pid == record.get("pid") else None
        leader = self._leader(record)
        if leader is not None:
            record["family"] = procs.trim_family([*(record.get("family") or []), *procs.family_of(leader)])
        found = await asyncio.to_thread(procs.collect, record, leader)
        self._last_found = len(found)
        return await asyncio.to_thread(
            procs.stop_processes, found, grace_s=grace_s,
            reap=(lambda: popen.poll()) if popen is not None else None,
        )

    async def stop_native_worker(self, payload: dict[str, Any]) -> dict[str, Any]:
        deployment_id = check_id(payload.get("deployment_id"), "deployment_id")
        grace = float(payload.get("grace_s", 30) or 0)
        grace = min(max(grace, 0.0), 600.0)
        async with self._lock:
            record = self._record(deployment_id)
            try:
                if record is not None:
                    procs.validate(record)
            except procs.BadRecord:
                record = None
            if record is None:
                # No readable record: anything still carrying its marker goes.
                found = await asyncio.to_thread(procs.marked, uid=os.getuid(), prefix=f"{deployment_id}:")
                left = await self._stop_unrecorded(deployment_id, grace_s=grace) if found else []
                if left:
                    raise NativeError(f"processes {left} of {deployment_id} survived SIGKILL", code="stop_incomplete")
                corrupt = self.paths.worker_record(deployment_id)
                if corrupt.exists() or corrupt.is_symlink():
                    corrupt.rename(corrupt.with_name(f"{corrupt.name}.corrupt-{int(time.time())}"))
                if not found:
                    # Idempotent (C1): the force-delete cleanup stops what may be long gone.
                    return {"ok": True, "stopped": False, "reason": "not running", "exit_code": None}
                return {"ok": True, "stopped": True, "exit_code": None}
            was_live = record.get("state") in LIVE
            code = await self._stop_locked(deployment_id, record, grace_s=grace)
            ran = was_live or self._last_found > 0
        if not ran:
            return {"ok": True, "stopped": False, "reason": "not running", "exit_code": code}
        return {"ok": True, "stopped": True, "exit_code": code}

    async def _stop_locked(self, deployment_id: str, record: dict[str, Any], *, grace_s: float) -> int | None:
        handle = self._handles.get(deployment_id)
        if handle is not None and handle.watcher is not None:
            handle.watcher.cancel()
        survivors = await self._stop_record(record, grace_s=grace_s)
        self._handles.pop(deployment_id, None)
        if survivors:
            raise NativeError(f"processes {survivors} of {deployment_id} survived SIGKILL", code="stop_incomplete")
        code = handle.popen.poll() if handle is not None and handle.popen is not None else record.get("exit_code")
        record.update(state="stopped", stopped_at=time.time(), exit_code=code)
        self._save(record)
        shutil.rmtree(self.paths.tmp(deployment_id), ignore_errors=True)
        log.info("Stopped native worker %s (exit %s)", deployment_id, code)
        return code

    async def native_worker_logs(self, payload: dict[str, Any]) -> dict[str, Any]:
        deployment_id = check_id(payload.get("deployment_id"), "deployment_id")
        cursor = payload.get("cursor")
        lines, new_cursor = workers.read_logs(
            self.paths.log_file(deployment_id), tail=payload.get("tail") or 200,
            cursor=None if cursor in (None, "") else str(cursor),
        )
        return {"ok": True, "lines": lines, "cursor": new_cursor}

    # ── agent start and stop ──────────────────────────────────────

    async def _stop_unrecorded(self, deployment_id: str, *, grace_s: float) -> list[int]:
        found = await asyncio.to_thread(procs.marked, uid=os.getuid(), prefix=f"{deployment_id}:")
        if found:
            log.warning("Stopping %d process(es) of %s found by their marker, not by a record", len(found), deployment_id)
        return await asyncio.to_thread(procs.stop_processes, found, grace_s=grace_s)

    async def reconcile(self) -> dict[str, list[str]]:
        """Adopt workers that outlived the agent; fail and clean up the rest."""
        adopted: list[str] = []
        failed: list[str] = []
        good, bad = self._scan()
        for path in bad:
            # Cannot be adopted without a record: no ownerless vLLM either.
            await self._stop_unrecorded(path.stem, grace_s=self.cleanup_grace_s)
            failed.append(path.stem)
        for record in good:
            if record.get("state") not in LIVE:
                continue
            deployment_id = record["deployment_id"]
            if procs.matching(record, uid=os.getuid()) is not None:
                self._watch(deployment_id, _Handle(popen=None))
                adopted.append(deployment_id)
                log.info("Adopted native worker %s (pid %s)", deployment_id, record.get("pid"))
                continue
            try:
                left = await asyncio.to_thread(
                    procs.stop_worker, record, None, grace_s=self.cleanup_grace_s, uid=os.getuid(),
                )
            except procs.BadRecord as exc:
                log.error("Native worker record %s is malformed (%s); nothing signalled", deployment_id, exc)
                left = []
            record.update(state="failed", stopped_at=time.time(),
                          last_error="the agent restarted and the worker was gone or not the recorded process"
                          + (f"; {left} survived" if left else ""))
            self._save(record)
            failed.append(deployment_id)
            log.warning("Native worker %s did not survive the agent restart", deployment_id)
        return {"adopted": adopted, "failed": failed}

    async def close(self) -> None:
        """Stop watching. Workers keep running: the next agent adopts them."""
        for handle in self._handles.values():
            if handle.watcher is not None:
                handle.watcher.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await handle.watcher
        self._handles.clear()
