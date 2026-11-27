"""Native workers against a fake runtime: start, ready, logs, stop, restart, isolation."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

import psutil
import pytest

from faden_agent import darwin_service as ds
from faden_agent.native import NativeError, NativePaths
from faden_agent.native import proc as procs
from faden_agent.native import stage as st
from faden_agent.native import worker as workers
from faden_agent.native.manager import NativeRuntimeManager
from tests.native_fakes import build_runtime, carrier, layer_from_dir

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

BUNDLE = "faden-native-vllm-metal-darwin-arm64-vllm0.34.0"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def env(tmp_path: Path):
    paths = NativePaths(tmp_path / "state" / "native")
    build_runtime(tmp_path / "src")
    c = carrier(tmp_path / "img.tar", layer_from_dir(tmp_path / "src"))
    st.stage_from_tar(c.tar, paths=paths, bundle_id=BUNDLE, config_digest=c.config_digest,
                      layer_digest=c.layer_digest, size_bytes=c.size_bytes)
    models = tmp_path / "models"
    snap = models / "hub" / "models--Qwen--Qwen3-0.6B" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")
    yield paths, models, snap
    # Nothing a failed test started may outlive it.
    for record_path in paths.workers.glob("*.json"):
        record = json.loads(record_path.read_text())
        procs.stop_tree(record.get("pid"), record.get("pgid"), grace_s=0.5,
                        not_before=float(record.get("started_at") or 0))


def _manager(paths: NativePaths, models: Path, **kw) -> NativeRuntimeManager:
    manager = NativeRuntimeManager(paths=paths, model_root=models, poll_interval=0.05, probe_metal=False, **kw)
    manager.cleanup_grace_s = 1.0
    manager.restart_grace_s = 1.0
    return manager


def _payload(snap: Path, port: int, **kw) -> dict:
    payload = {
        "deployment_id": "dep-1", "generation": 1, "bundle_id": BUNDLE,
        "model": {"name": "Qwen/Qwen3-0.6B", "snapshot_path": str(snap)},
        "served_model_name": "qwen3-0.6b", "port": port, "host": "127.0.0.1",
        "gpu_memory_utilization": 0.5, "max_model_len": 4096, "extra_args": ["--max-num-seqs", "8"], "env": {},
    }
    payload.update(kw)
    return payload


async def _settle(manager: NativeRuntimeManager, deployment_id: str = "dep-1", timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await manager.native_worker_status({"deployment_id": deployment_id})
        if status["state"] != "starting":
            return status
        await asyncio.sleep(0.05)
    raise AssertionError("worker never left starting")


def _kids(paths: NativePaths, port: int) -> list[int]:
    deadline = time.monotonic() + 10
    path = paths.home / f"kids-{port}.txt"
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return [int(p) for p in path.read_text().split()]


def _gone(pids: list[int]) -> bool:
    for pid in pids:
        try:
            if psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                return False
        except psutil.NoSuchProcess:
            pass
    return True


@pytest.mark.asyncio
async def test_start_ready_logs_and_stop_takes_the_whole_tree(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    port = _free_port()
    started = await manager.start_native_worker(_payload(snap, port))
    assert started["ok"] and started["port"] == port
    status = await _settle(manager)
    assert status["state"] == "ready", status
    kids = _kids(paths, port)
    assert os.getpgid(started["pid"]) == started["pid"]  # its own session and group

    record = json.loads(paths.worker_record("dep-1").read_text())
    assert record["v"] == 1 and record["pgid"] == record["pid"] and record["state"] == "ready"
    assert record["cmd_sha256"] == procs.cmd_hash(record["argv"])
    assert procs.matching(record) is not None

    logs = await manager.native_worker_logs({"deployment_id": "dep-1", "tail": 50})
    assert any(line.startswith("faden: NOFILE soft=") for line in logs["lines"])
    assert any("fake vllm starting" in line for line in logs["lines"])
    more = await manager.native_worker_logs({"deployment_id": "dep-1", "tail": 50, "cursor": logs["cursor"]})
    assert more["lines"] == []

    # Same deployment and generation: the running one.
    again = await manager.start_native_worker(_payload(snap, port))
    assert again["existing"] and again["pid"] == started["pid"]

    stopped = await manager.stop_native_worker({"deployment_id": "dep-1", "grace_s": 1})
    assert stopped["stopped"]
    assert _gone([started["pid"], *kids])  # including the child that ignores SIGTERM
    status = await manager.native_worker_status({"deployment_id": "dep-1"})
    assert status["state"] == "stopped"
    await manager.close()


@pytest.mark.asyncio
async def test_the_worker_sees_none_of_the_agents_secrets(env, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, models, snap = env
    monkeypatch.setenv("FADEN_NODE_AGENT_ENROLLMENT_TOKEN", "enroll-secret-123")
    monkeypatch.setenv("HF_TOKEN", "hf_secret_456")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret-789")
    manager = _manager(paths, models)
    port = _free_port()
    await manager.start_native_worker(_payload(snap, port, env={"VLLM_LOGGING_LEVEL": "DEBUG"}))
    assert (await _settle(manager))["state"] == "ready"
    seen = json.loads((paths.home / f"env-{port}.json").read_text())
    text = json.dumps(seen)
    for secret in ("enroll-secret-123", "hf_secret_456", "aws-secret-789"):
        assert secret not in text
    assert not [k for k in seen if k.startswith("FADEN_")]
    assert seen["HF_HUB_OFFLINE"] == "1" and seen["HOME"] == str(paths.home)
    assert seen["TMPDIR"].startswith(str(paths.root / "tmp" / "dep-1"))
    assert seen["VLLM_LOGGING_LEVEL"] == "DEBUG"
    await manager.stop_native_worker({"deployment_id": "dep-1", "grace_s": 1})
    await manager.close()


@pytest.mark.asyncio
async def test_a_worker_that_exits_is_failed_with_its_error(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    await manager.start_native_worker(_payload(snap, _free_port(), env={"VLLM_FAKE_MODE": "exit"}))
    status = await _settle(manager)
    assert status["state"] == "failed" and status["exit_code"] == 3
    assert "boom" in status["last_error"]
    await manager.close()


@pytest.mark.asyncio
async def test_never_ready_times_out_and_is_stopped(env, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, models, snap = env
    monkeypatch.setattr(workers, "ready_timeout", lambda _size: 1)
    manager = _manager(paths, models)
    port = _free_port()
    started = await manager.start_native_worker(_payload(snap, port, env={"VLLM_FAKE_MODE": "never"}))
    kids = _kids(paths, port)
    status = await _settle(manager, timeout=30)
    assert status["state"] == "failed" and "not ready after 1 s" in status["last_error"]
    assert _gone([started["pid"], *kids])
    await manager.close()


@pytest.mark.asyncio
async def test_one_worker_per_mac_and_generations(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    port = _free_port()
    await manager.start_native_worker(_payload(snap, port))
    with pytest.raises(NativeError) as err:
        await manager.start_native_worker(_payload(snap, _free_port(), deployment_id="dep-2"))
    assert err.value.error_code == "worker_exists" and "at most 1" in str(err.value)
    await manager.stop_native_worker({"deployment_id": "dep-1", "grace_s": 1})
    await manager.close()


@pytest.mark.asyncio
async def test_a_new_generation_or_changed_payload_restarts(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    manager.cleanup_grace_s = 1.0
    port = _free_port()
    first = await manager.start_native_worker(_payload(snap, port))
    assert (await _settle(manager))["state"] == "ready"
    old_kids = _kids(paths, port)
    (paths.home / f"kids-{port}.txt").unlink()
    second = await manager.start_native_worker(_payload(snap, port, generation=2))
    assert second["pid"] != first["pid"] and not second.get("existing")
    assert _gone([first["pid"], *old_kids])
    assert (await _settle(manager))["state"] == "ready"
    record = json.loads(paths.worker_record("dep-1").read_text())
    assert record["generation"] == 2 and record["model"]["name"] == "Qwen/Qwen3-0.6B"
    # Same generation, a changed argument: a restart too.
    _kids(paths, port)
    third = await manager.start_native_worker(_payload(snap, port, generation=2, extra_args=["--max-num-seqs", "4"]))
    assert third["pid"] != second["pid"]
    await manager.stop_native_worker({"deployment_id": "dep-1", "grace_s": 1})
    await manager.close()


@pytest.mark.asyncio
async def test_agent_restart_adopts_a_live_worker(env) -> None:
    paths, models, snap = env
    first = _manager(paths, models)
    port = _free_port()
    started = await first.start_native_worker(_payload(snap, port))
    assert (await _settle(first))["state"] == "ready"
    await first.close()  # the agent goes; the worker stays

    second = _manager(paths, models)
    result = await second.reconcile()
    assert result == {"adopted": ["dep-1"], "failed": []}
    status = await second.native_worker_status({"deployment_id": "dep-1"})
    assert status["state"] == "ready" and status["pid"] == started["pid"]
    kids = _kids(paths, port)
    await second.stop_native_worker({"deployment_id": "dep-1", "grace_s": 1})
    assert _gone([started["pid"], *kids])
    await second.close()


@pytest.mark.asyncio
async def test_agent_restart_fails_and_cleans_up_a_mismatched_worker(env) -> None:
    paths, models, snap = env
    first = _manager(paths, models)
    port = _free_port()
    started = await first.start_native_worker(_payload(snap, port))
    assert (await _settle(first))["state"] == "ready"
    kids = _kids(paths, port)
    await first.close()
    record = json.loads(paths.worker_record("dep-1").read_text())
    record["cmd_sha256"] = "sha256:" + "0" * 64
    record["runtime_root"] = "/nowhere"
    paths.worker_record("dep-1").write_text(json.dumps(record))

    second = _manager(paths, models)
    assert await second.reconcile() == {"adopted": [], "failed": ["dep-1"]}
    assert _gone([started["pid"], *kids])  # no ownerless vLLM
    status = await second.native_worker_status({"deployment_id": "dep-1"})
    assert status["state"] == "failed"
    await second.close()


@pytest.mark.asyncio
async def test_agent_restart_marks_a_dead_worker_failed(env) -> None:
    paths, models, snap = env
    first = _manager(paths, models)
    port = _free_port()
    started = await first.start_native_worker(_payload(snap, port))
    assert (await _settle(first))["state"] == "ready"
    kids = _kids(paths, port)
    await first.close()
    os.kill(started["pid"], signal.SIGKILL)
    await asyncio.sleep(0.2)

    second = _manager(paths, models)
    assert (await second.reconcile())["failed"] == ["dep-1"]
    assert _gone(kids)  # its orphaned children too
    await second.close()


@pytest.mark.asyncio
async def test_root_stop_ends_verified_workers_only(tmp_path: Path, env) -> None:
    _paths, models, snap = env
    layout = ds.Layout(tmp_path / "root")
    paths = NativePaths(layout.state_dir / "native")
    build_runtime(tmp_path / "src2")
    c = carrier(tmp_path / "img2.tar", layer_from_dir(tmp_path / "src2"))
    st.stage_from_tar(c.tar, paths=paths, bundle_id=BUNDLE, config_digest=c.config_digest,
                      layer_digest=c.layer_digest, size_bytes=c.size_bytes)
    manager = _manager(paths, models)
    port = _free_port()
    started = await manager.start_native_worker(_payload(snap, port))
    assert (await _settle(manager))["state"] == "ready"
    kids = _kids(paths, port)
    await manager.close()
    # A forged record naming a process that is not a worker (this test).
    me = psutil.Process()
    (paths.workers / "forged.json").write_text(json.dumps({
        "deployment_id": "forged", "state": "ready", "pid": me.pid, "pgid": me.pid,
        "started_at": me.create_time(), "cmd_sha256": "sha256:" + "2" * 64,
    }))
    said: list[str] = []
    left = ds.stop_native_workers(layout, os.getuid(), say=said.append, grace_s=1)
    assert left == []
    assert _gone([started["pid"], *kids])
    assert me.is_running()
    assert any("Stopped the model worker dep-1" in s for s in said)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"gpu_memory_utilization": 0.96}, "gpu_memory_utilization"),
        ({"gpu_memory_utilization": 0}, "gpu_memory_utilization"),
        ({"port": 80}, "port"),
        ({"host": "0.0.0.0"}, "host"),
        ({"extra_args": ["--trust-remote-code"]}, "not allowed"),
        ({"extra_args": ["--api-key", "x"]}, "not allowed"),
        ({"extra_args": ["--dtype", "$(id)"]}, "not allowed"),
        ({"env": {"HF_TOKEN": "x"}}, "not allowed"),
        ({"env": {"VLLM_API_KEY": "x"}}, "not allowed"),
        ({"deployment_id": "../etc"}, "deployment_id"),
        ({"served_model_name": "a b"}, "served_model_name"),
        ({"model": {"name": "m", "snapshot_path": "relative/path"}}, "absolute"),
    ],
)
@pytest.mark.asyncio
async def test_inputs_are_checked(env, change: dict, message: str) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    payload = _payload(snap, _free_port())
    payload.update(change)
    with pytest.raises(NativeError) as err:
        await manager.start_native_worker(payload)
    assert message in str(err.value)
    assert err.value.error_code == "invalid_payload"


@pytest.mark.asyncio
async def test_a_snapshot_linking_out_of_the_store_is_refused(env, tmp_path: Path) -> None:
    paths, models, _snap = env
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "config.json").write_text("{}")
    sneaky = models / "hub" / "sneaky"
    sneaky.symlink_to(outside)
    manager = _manager(paths, models)
    with pytest.raises(NativeError) as err:
        await manager.start_native_worker(_payload(sneaky, _free_port()))
    assert "outside the model store" in str(err.value)


@pytest.mark.asyncio
async def test_an_unstaged_bundle(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    with pytest.raises(NativeError) as err:
        await manager.start_native_worker(_payload(snap, _free_port(), bundle_id="not-there"))
    assert err.value.error_code == "not_staged"


@pytest.mark.asyncio
async def test_status_of_an_unknown_deployment_and_runtime_status(env) -> None:
    paths, models, _snap = env
    manager = _manager(paths, models)
    assert (await manager.native_worker_status({"deployment_id": "nope"}))["state"] == "stopped"
    status = await manager.native_runtime_status({})
    assert status["staged"][0]["bundle_id"] == BUNDLE and status["workers"] == []
    assert manager.capabilities()["native_runtime"] == 1
    assert manager.capabilities()["metal"]["unified_memory_bytes"] > 0
    assert "recommended_max_working_set_bytes" not in manager.capabilities()["metal"]


def test_log_cursor_survives_rotation(tmp_path: Path) -> None:
    log = tmp_path / "w.log"
    log.write_text("a\nb\nc\n")
    lines, cursor = workers.read_logs(log, tail=2, cursor=None)
    assert lines == ["b", "c"] and cursor == "6"
    with log.open("a") as fh:
        fh.write("d\npartial")
    lines, cursor = workers.read_logs(log, tail=10, cursor=cursor)
    assert lines == ["d"] and cursor == "8"
    assert workers.rotate_if_big(log, limit=1)
    assert (tmp_path / "w.log.1").exists() and log.stat().st_size == 0
    lines, cursor = workers.read_logs(log, tail=10, cursor=cursor)
    assert lines == [] and cursor == "0"


def test_ready_timeout_scales_with_model_size() -> None:
    assert workers.ready_timeout(600 << 20) == 300
    assert workers.ready_timeout(40 << 30) == 3600
    assert 300 < workers.ready_timeout(5 << 30) < 3600
