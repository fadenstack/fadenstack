"""Native workers against a fake runtime: start, ready, logs, stop, restart, isolation."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
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

#: This run's deployment ids. Workers are found by an environment marker naming the
#: deployment, so two suites running at once (other worktrees) must never share one.
RUN = f"{os.getpid()}-{os.urandom(3).hex()}"
DEP = f"dep-1-{RUN}"
DEP2 = f"dep-2-{RUN}"
BUNDLE = "faden-native-vllm-metal-darwin-arm64-vllm0.34.0"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def _fake_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake's own switches, beside the production allowlist.
    monkeypatch.setattr(workers, "ENV_ALLOWED", workers.ENV_ALLOWED | {"VLLM_FAKE_MODE", "VLLM_FAKE_SETSID"})
    # A marker of this run's own: suites in other worktrees (on older code) sweep
    # every FADEN_NATIVE_WORKER process of this user, and would end this run's fakes.
    monkeypatch.setattr(workers, "MARKER_ENV", f"FADEN_NATIVE_WORKER_T{RUN.replace('-', '_')}")
    monkeypatch.setattr(procs, "MARKER_ENV", workers.MARKER_ENV)


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
        try:
            leader = psutil.Process(record["pid"])
            if abs(leader.create_time() - record["started_at"]) > 1:
                leader = None
        except (psutil.Error, KeyError):
            leader = None
        try:
            procs.stop_worker(record, leader, grace_s=0.5)
        except procs.BadRecord:
            pass


def _manager(paths: NativePaths, models: Path, **kw) -> NativeRuntimeManager:
    manager = NativeRuntimeManager(paths=paths, model_root=models, poll_interval=0.05, probe_metal=False, **kw)
    manager.cleanup_grace_s = 1.0
    manager.restart_grace_s = 1.0
    return manager


def _payload(snap: Path, port: int, **kw) -> dict:
    payload = {
        "deployment_id": DEP, "generation": 1, "bundle_id": BUNDLE,
        "model": {"name": "Qwen/Qwen3-0.6B", "snapshot_path": str(snap)},
        "served_model_name": "qwen3-0.6b", "port": port, "host": "127.0.0.1",
        "gpu_memory_utilization": 0.5, "max_model_len": 4096, "extra_args": ["--max-num-seqs", "8"], "env": {},
    }
    payload.update(kw)
    return payload


#: How long a test waits for the fake runtime to get somewhere. Generous on purpose:
#: under load (several suites at once) a Python start alone can take seconds. The
#: waits poll, so a fast machine never waits this long.
WAIT_S = 30.0


async def _until(condition, timeout: float = WAIT_S, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval)
    return True


async def _settle(manager: NativeRuntimeManager, deployment_id: str = DEP, timeout: float = WAIT_S) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await manager.native_worker_status({"deployment_id": deployment_id})
        if status["state"] != "starting":
            return status
        await asyncio.sleep(0.02)
    raise AssertionError("worker never left starting")


async def _settle_state(manager: NativeRuntimeManager, state: str, timeout: float = WAIT_S) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await manager.native_worker_status({"deployment_id": DEP})
        if status["state"] == state:
            return status
        await asyncio.sleep(0.05)
    return status


def _kids(paths: NativePaths, port: int) -> list[int]:
    deadline = time.monotonic() + WAIT_S
    path = paths.home / f"kids-{port}.txt"
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
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

    record = json.loads(paths.worker_record(DEP).read_text())
    assert record["v"] == 1 and record["pgid"] == record["pid"] and record["state"] == "ready"
    assert record["cmd_sha256"] == procs.cmd_hash(record["argv"])
    assert procs.matching(record) is not None

    logs = await manager.native_worker_logs({"deployment_id": DEP, "tail": 50})
    assert any(line.startswith("faden: NOFILE soft=") for line in logs["lines"])
    assert any("fake vllm starting" in line for line in logs["lines"])
    more = await manager.native_worker_logs({"deployment_id": DEP, "tail": 50, "cursor": logs["cursor"]})
    assert more["lines"] == []

    # Same deployment and generation: the running one.
    again = await manager.start_native_worker(_payload(snap, port))
    assert again["existing"] and again["pid"] == started["pid"]

    stopped = await manager.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
    assert stopped["stopped"]
    assert _gone([started["pid"], *kids])  # including the child that ignores SIGTERM
    status = await manager.native_worker_status({"deployment_id": DEP})
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
    assert [k for k in seen if k.startswith("FADEN_")] == [workers.MARKER_ENV]
    assert seen[workers.MARKER_ENV] == f"{DEP}:1"  # the marker, nothing of the agent's
    assert seen["HF_HUB_OFFLINE"] == "1" and seen["HOME"] == str(paths.home)
    assert seen["TMPDIR"].startswith(str(paths.root / "tmp" / DEP))
    assert seen["VLLM_LOGGING_LEVEL"] == "DEBUG"
    await manager.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
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
    status = await _settle(manager)
    assert status["state"] == "failed" and "not ready after 1 s" in status["last_error"]
    # Under load the 1 s may pass before the fake has started its children, so they
    # are not awaited; whatever it did start must be gone once the stop finishes
    # (the reason is recorded before the stop).
    def nothing_left() -> bool:
        kids_file = paths.home / f"kids-{port}.txt"
        kids = [int(p) for p in kids_file.read_text().split()] if kids_file.exists() else []
        return _gone([started["pid"], *kids]) and not procs.marked(uid=os.getuid(), prefix=f"{DEP}:")
    assert await _until(nothing_left)
    await manager.close()


@pytest.mark.asyncio
async def test_one_worker_per_mac_and_generations(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    port = _free_port()
    await manager.start_native_worker(_payload(snap, port))
    with pytest.raises(NativeError) as err:
        await manager.start_native_worker(_payload(snap, _free_port(), deployment_id=DEP2))
    assert err.value.error_code == "worker_exists" and "at most 1" in str(err.value)
    await manager.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
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
    record = json.loads(paths.worker_record(DEP).read_text())
    assert record["generation"] == 2 and record["model"]["name"] == "Qwen/Qwen3-0.6B"
    # Same generation, a changed argument: a restart too.
    _kids(paths, port)
    third = await manager.start_native_worker(_payload(snap, port, generation=2, extra_args=["--max-num-seqs", "4"]))
    assert third["pid"] != second["pid"]
    await manager.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
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
    assert result == {"adopted": [DEP], "failed": []}
    status = await second.native_worker_status({"deployment_id": DEP})
    assert status["state"] == "ready" and status["pid"] == started["pid"]
    kids = _kids(paths, port)
    await second.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
    assert _gone([started["pid"], *kids])
    await second.close()


@pytest.mark.asyncio
async def test_a_stale_record_never_signals_a_process_that_reused_its_pid(env) -> None:
    paths, models, snap = env
    first = _manager(paths, models)
    port = _free_port()
    started = await first.start_native_worker(_payload(snap, port))
    assert (await _settle(first))["state"] == "ready"
    kids = _kids(paths, port)
    await first.close()
    os.kill(started["pid"], signal.SIGKILL)  # the leader dies, its children stay
    # The recorded number now names an unrelated group leader of the same user.
    stranger = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        record = json.loads(paths.worker_record(DEP).read_text())
        record.update(pid=stranger.pid, pgid=stranger.pid)  # started_at and cmd stay the worker's
        paths.worker_record(DEP).write_text(json.dumps(record))

        second = _manager(paths, models)
        assert await second.reconcile() == {"adopted": [], "failed": [DEP]}
        assert stranger.poll() is None  # untouched
        assert _gone(kids)  # the recorded children, verified by creation time, are not
        await second.close()
    finally:
        stranger.kill()
        stranger.wait()


@pytest.mark.asyncio
async def test_a_setsid_helper_that_outlives_the_leader_is_stopped(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    port = _free_port()
    started = await manager.start_native_worker(_payload(snap, port, env={"VLLM_FAKE_SETSID": "1"}))
    assert (await _settle(manager))["state"] == "ready"
    kids = _kids(paths, port)
    helper = kids[-1]
    assert os.getpgid(helper) == helper != started["pid"]  # its own session
    deadline = time.monotonic() + WAIT_S
    while helper not in {e["pid"] for e in json.loads(paths.worker_record(DEP).read_text()).get("family", [])}:
        assert time.monotonic() < deadline, "the helper was never recorded"
        await asyncio.sleep(0.05)
    os.kill(started["pid"], signal.SIGKILL)
    status = await _settle_state(manager, "failed")
    assert status["state"] == "failed"
    deadline = time.monotonic() + WAIT_S
    while not _gone(kids) and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    assert _gone(kids)  # including the setsid helper
    await manager.close()


def test_the_agents_own_process_and_group_are_never_signalled() -> None:
    me = psutil.Process()
    record = {"pid": 999_999, "pgid": 999_999, "started_at": 1.0, "cmd_sha256": "sha256:" + "0" * 64,
              "family": [{"pid": me.pid, "ct": me.create_time()}]}
    assert procs.collect(record, None) == []
    assert procs.stop_worker(record, None, grace_s=0.1) == []
    with pytest.raises(AssertionError):
        procs.collect(record, me)  # a "leader" in the agent's own group


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
    assert await _until(lambda: _gone([started["pid"]]))

    second = _manager(paths, models)
    assert (await second.reconcile())["failed"] == [DEP]
    assert _gone(kids)  # its orphaned children too
    await second.close()


BAD_RECORDS = {
    "started_at_text": {"started_at": "x"},
    "started_at_list": {"started_at": [1]},
    "started_at_dict": {"started_at": {"a": 1}},
    "started_at_none": {"started_at": None},
    "started_at_nan": {"started_at": float("nan")},
    "pid_text": {"pid": "123"},
    "pid_bool": {"pid": True},
    "pid_one": {"pid": 1, "pgid": 1},
    "pid_negative": {"pid": -5, "pgid": -5},
    "pid_huge": {"pid": 1 << 40, "pgid": 1 << 40},
    "pgid_other": {"pgid": 1},
    "cmd_hash_bad": {"cmd_sha256": 5},
    "family_not_list": {"family": "x"},
    "family_bad_entry": {"family": [{"pid": "x", "ct": 1}]},
    "family_huge": {"family": [{"pid": 2, "ct": 1.0}] * 5000},
    "runtime_root_relative": {"runtime_root": "rel"},
    "port_text": {"port": "80"},
}


@pytest.mark.asyncio
async def test_root_stop_ends_verified_workers_and_skips_every_forged_record(tmp_path: Path, env) -> None:
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
    me = psutil.Process()
    good = json.loads(paths.worker_record(DEP).read_text())
    workers_dir = paths.workers
    # Forged: names sorting before the real one, so they are read first.
    for name, change in BAD_RECORDS.items():
        (workers_dir / f"a-{name}.json").write_text(json.dumps({**good, "deployment_id": f"a-{name}", **change}))
    (workers_dir / "a-not-json.json").write_text("{nope")
    (workers_dir / "a-array.json").write_text("[1, 2]")
    (workers_dir / "a-deep.json").write_text("[" * 100_000 + "]" * 100_000)
    (workers_dir / "a-self.json").write_text(json.dumps({
        **good, "deployment_id": "a-self", "pid": me.pid, "pgid": me.pid, "started_at": me.create_time(),
    }))
    (workers_dir / "a-\x1b[31mred.json").write_text(json.dumps({**good, "state": "ready", "started_at": "x"}))
    os.symlink(paths.worker_record(DEP), workers_dir / "a-symlink.json")
    os.link(paths.worker_record(DEP), tmp_path / "hardlink-of-dep-1")  # dep-1 itself now has 2 links
    os.mkfifo(workers_dir / "a-fifo.json")

    said: list[str] = []
    left = ds.stop_native_workers(layout, os.getuid(), say=said.append, grace_s=1)
    assert left == []
    assert me.is_running()
    # dep-1 has two links now: root refuses to read it (a hardlink could be anything);
    # its processes still go, by the marker they carry (a forged record that copies the
    # marker can only ever reach processes carrying it: the worker's own).
    assert any("a-started_at_text" in s and "Skipped" in s for s in said)
    assert not any("\x1b" in s for s in said)  # no terminal escapes reach root's terminal
    assert _gone([started["pid"], *kids])
    assert me.is_running()


def test_root_reads_nothing_through_a_symlinked_native_dir(tmp_path: Path) -> None:
    layout = ds.Layout(tmp_path / "root")
    layout.state_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere" / "workers"
    elsewhere.mkdir(parents=True)
    (elsewhere / "x.json").write_text(json.dumps({"state": "ready", "pid": os.getpid()}))
    os.symlink(tmp_path / "elsewhere", layout.state_dir / "native")
    said: list[str] = []
    assert ds._worker_records(layout, os.getuid(), say=said.append) == ([], set())


def test_a_record_of_another_owner_is_not_read(tmp_path: Path) -> None:
    from faden_agent.native.common import read_json_nofollow

    path = tmp_path / "r.json"
    path.write_text("{}")
    assert read_json_nofollow(path, owner=os.getuid()) == {}
    assert read_json_nofollow(path, owner=os.getuid() + 1) is None


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
        ({"env": {"VLLM_PLUGINS": "evil"}}, "not allowed"),
        ({"env": {"VLLM_LOGGING_LEVEL": "/etc/passwd"}}, "not allowed"),
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


@pytest.mark.asyncio
async def test_the_sandboxed_worker_cannot_read_or_write_the_agents_state(env) -> None:
    paths, models, snap = env
    if not (await _sandbox_ok()):
        pytest.skip("sandbox-exec not available")
    state = paths.root.parent / "state.json"
    state.write_text('{"credential": "the-node-credential"}')
    for sandbox, port in ((True, _free_port()), (False, _free_port())):
        manager = _manager(paths, models, sandbox=sandbox)
        await manager.start_native_worker(_payload(snap, port))
        assert (await _settle(manager))["state"] == "ready"
        probe = json.loads((paths.home / f"probe-{port}.json").read_text())
        record = json.loads(paths.worker_record(DEP).read_text())
        await manager.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
        await manager.close()
        if sandbox:
            assert record["sandboxed"] is True and record["argv"][0] == "/usr/bin/sandbox-exec"
            assert probe["read"].startswith("denied") and probe["write"].startswith("denied")
            assert probe["signal_agent"].startswith("denied")  # not even kill -0 of the agent
            assert not (paths.root.parent / "planted").exists()
        else:  # the test can tell: without the sandbox the same worker reads it
            assert "the-node-credential" in probe["read"] and probe["signal_agent"] == "ok"


async def _sandbox_ok() -> bool:
    from faden_agent.native import sandbox

    return sys.platform == "darwin" and sandbox.available()


def test_a_program_from_the_runtime_alone_is_not_the_worker() -> None:
    # This pytest runs a Python from the agent's venv: "exe inside runtime_root" holds,
    # but it serves nothing on the recorded port, so it is not adopted or signalled.
    me = psutil.Process()
    record = {"pid": me.pid, "pgid": me.pid, "started_at": me.create_time(), "cmd_sha256": "sha256:" + "0" * 64,
              "runtime_root": os.path.dirname(os.path.dirname(os.path.realpath(me.exe()))), "port": 38999}
    assert procs.matching(record) is None


@pytest.mark.asyncio
async def test_a_corrupt_record_hides_nothing(tmp_path: Path, env) -> None:
    _paths, models, snap = env
    layout = ds.Layout(tmp_path / "root")
    paths = NativePaths(layout.state_dir / "native")
    build_runtime(tmp_path / "src3")
    c = carrier(tmp_path / "img3.tar", layer_from_dir(tmp_path / "src3"))
    st.stage_from_tar(c.tar, paths=paths, bundle_id=BUNDLE, config_digest=c.config_digest,
                      layer_digest=c.layer_digest, size_bytes=c.size_bytes)
    manager = _manager(paths, models)
    port = _free_port()
    started = await manager.start_native_worker(_payload(snap, port))
    assert (await _settle(manager))["state"] == "ready"
    kids = _kids(paths, port)
    await manager.close()
    paths.worker_record(DEP).write_text("{corrupt")

    second = _manager(paths, models)
    status = await second.native_runtime_status({})
    assert {"deployment_id": DEP, "state": "unknown_record", "path": str(paths.worker_record(DEP))} in status["workers"]
    said: list[str] = []
    assert ds.stop_native_workers(layout, os.getuid(), say=said.append, grace_s=1) == []
    assert any("found by their marker" in s for s in said), said
    assert _gone([started["pid"], *kids])


@pytest.mark.asyncio
async def test_the_agent_stops_a_worker_whose_record_is_corrupt(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    port = _free_port()
    started = await manager.start_native_worker(_payload(snap, port))
    assert (await _settle(manager))["state"] == "ready"
    kids = _kids(paths, port)
    await manager.close()
    paths.worker_record(DEP).write_text("[]")
    second = _manager(paths, models)
    result = await second.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
    assert result["ok"] and _gone([started["pid"], *kids])
    assert not paths.worker_record(DEP).exists()  # set aside as .corrupt-<time>
    assert list(paths.workers.glob(f"{DEP}.json.corrupt-*"))


@pytest.mark.asyncio
async def test_stop_is_idempotent_for_a_deployment_not_running(env) -> None:
    # C1 force-delete cleanup: on reconnect the backend stops deployments the agent may never have had.
    paths, models, snap = env
    manager = _manager(paths, models)
    assert await manager.stop_native_worker({"deployment_id": "never-ran", "grace_s": 30}) == {
        "ok": True, "stopped": False, "reason": "not running", "exit_code": None,
    }
    await manager.start_native_worker(_payload(snap, _free_port()))
    assert (await _settle(manager))["state"] == "ready"
    first = await manager.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
    assert first["stopped"] is True
    again = await manager.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
    assert again["ok"] and again["stopped"] is False and again["reason"] == "not running"
    await manager.close()


@pytest.mark.asyncio
async def test_after_a_restart_the_cleanup_stop_ends_an_adopted_worker(env) -> None:
    # The Mac was offline during a force-delete: the worker survived, the agent adopts it on
    # start, and the cleanup stop that follows the reconnect ends it.
    paths, models, snap = env
    first = _manager(paths, models)
    port = _free_port()
    started = await first.start_native_worker(_payload(snap, port))
    assert (await _settle(first))["state"] == "ready"
    kids = _kids(paths, port)
    await first.close()
    second = _manager(paths, models)
    assert (await second.reconcile())["adopted"] == [DEP]
    result = await second.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
    assert result["ok"] and result["stopped"] is True
    assert _gone([started["pid"], *kids])
    assert (await second.stop_native_worker({"deployment_id": "unknown-dep", "grace_s": 30}))["reason"] == "not running"
    await second.close()


@pytest.mark.asyncio
async def test_a_helper_detaching_after_the_leader_dies_is_found_by_its_marker(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models)
    manager.cleanup_grace_s = 0.2  # the watcher's own cleanup may well miss it
    port = _free_port()
    started = await manager.start_native_worker(_payload(snap, port, env={"VLLM_FAKE_SETSID": "late"}))
    assert (await _settle(manager))["state"] == "ready"
    kids = _kids(paths, port)
    os.kill(started["pid"], signal.SIGKILL)
    late_file = paths.home / f"late-{port}.txt"
    deadline = time.monotonic() + WAIT_S
    while not late_file.exists() or not late_file.read_text():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.02)
    late = int(late_file.read_text())
    assert os.getpgid(late) == late
    await _settle_state(manager, "failed")
    result = await manager.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
    assert result["ok"]
    assert _gone([started["pid"], *kids, late])
    await manager.close()


def test_the_family_cap_keeps_the_newest() -> None:
    entries = [{"pid": 100_000 - i, "ct": float(i)} for i in range(procs.MAX_FAMILY + 50)]
    kept = procs.trim_family(entries)
    assert len(kept) == procs.MAX_FAMILY
    assert min(e["ct"] for e in kept) == 50.0  # the oldest went, not the highest pids


@pytest.mark.asyncio
async def test_without_sandbox_exec_starts_are_refused_unless_disabled(env, monkeypatch: pytest.MonkeyPatch) -> None:
    from faden_agent.native import sandbox

    paths, models, snap = env
    monkeypatch.setattr(sandbox, "available", lambda: False)
    manager = _manager(paths, models)
    assert manager.capabilities()["native_sandbox"] is False
    assert (await manager.native_runtime_status({}))["sandbox"] == {
        "wanted": True, "available": False, "active": False, "config_error": None,
    }
    with pytest.raises(NativeError) as err:
        await manager.start_native_worker(_payload(snap, _free_port()))
    assert err.value.error_code == "sandbox_unavailable" and "FADEN_NODE_AGENT_NATIVE_SANDBOX=0" in str(err.value)
    await manager.close()

    deliberate = _manager(paths, models, sandbox=False)
    await deliberate.start_native_worker(_payload(snap, _free_port()))
    assert (await _settle(deliberate))["state"] == "ready"
    assert json.loads(paths.worker_record(DEP).read_text())["sandboxed"] is False
    await deliberate.stop_native_worker({"deployment_id": DEP, "grace_s": 1})
    await deliberate.close()


@pytest.mark.asyncio
async def test_an_unknown_sandbox_setting_refuses_starts(env) -> None:
    paths, models, snap = env
    manager = _manager(paths, models, sandbox_config_error="FADEN_NODE_AGENT_NATIVE_SANDBOX='' is not ...")
    assert manager.capabilities()["native_sandbox"] is False
    state = (await manager.native_runtime_status({}))["sandbox"]
    assert state["active"] is False and "NATIVE_SANDBOX" in state["config_error"]
    with pytest.raises(NativeError) as err:
        await manager.start_native_worker(_payload(snap, _free_port()))
    assert err.value.error_code == "sandbox_config_invalid"
    assert not list(paths.workers.glob("*.json"))  # nothing started
    await manager.close()


def test_the_production_marker_name() -> None:
    # The fixture renames it per run; the name the agent ships is this one.
    import inspect

    for module in (workers, procs):
        assert 'MARKER_ENV = "FADEN_NATIVE_WORKER"' in inspect.getsource(module)
