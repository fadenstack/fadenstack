"""The Mac idle-sleep guard (power.py), with a fake caffeinate."""

from __future__ import annotations

import asyncio
import os
import sys
import types
import time
from pathlib import Path

import psutil
import pytest

from faden_agent import power
from faden_agent.config import AgentConfig

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX processes")


@pytest.fixture()
def fake(tmp_path: Path) -> Path:
    """A caffeinate that records its argv, then waits like the real one (until -w's pid ends)."""
    script = tmp_path / "caffeinate"
    log = tmp_path / "argv.log"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'while kill -0 "$3" 2>/dev/null; do sleep 0.05; done\n'
    )
    script.chmod(0o755)
    return script


def _wait(cond, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


@pytest.mark.asyncio
async def test_holds_the_assertion_for_the_agent_and_reports_it(fake: Path) -> None:
    guard = power.IdleSleepGuard(binary=str(fake), check_every_s=0.05)
    await guard.start()
    try:
        assert guard.running()
        assert _wait(lambda: (fake.parent / "argv.log").exists())
        assert (fake.parent / "argv.log").read_text().split() == ["-i", "-w", str(os.getpid())]
        status = guard.status()
        assert status["prevent_idle_sleep"] is True and status["active"] is True
        assert status["pid"] and status["error"] is None and status["method"] == "caffeinate -i -w"
        # Same process group as the agent: launchd's job cleanup takes it too.
        assert os.getpgid(status["pid"]) == os.getpgrp()
    finally:
        await guard.stop()
    assert not guard.running() and guard.status()["active"] is False


@pytest.mark.asyncio
async def test_it_is_started_again_when_it_dies(fake: Path) -> None:
    guard = power.IdleSleepGuard(binary=str(fake), check_every_s=0.05, restart_min_s=0.1)
    await guard.start()
    try:
        first = guard.status()["pid"]
        psutil.Process(first).kill()
        for _ in range(600):
            await asyncio.sleep(0.05)
            if guard.running() and guard.status()["pid"] != first:
                break
        assert guard.running() and guard.status()["pid"] != first
        assert guard.status()["restarts"] == 1
    finally:
        await guard.stop()


def test_restarts_are_rate_limited(fake: Path) -> None:
    now = [100.0]
    guard = power.IdleSleepGuard(binary=str(fake), restart_min_s=10, clock=lambda: now[0])
    guard._spawn()
    first = guard._proc
    first.kill()
    first.wait()
    guard.check()
    assert guard._proc is first  # too soon: not again yet
    now[0] += 11
    guard.check()
    assert guard.running() and guard._proc is not first
    guard._proc.kill()
    guard._proc.wait()


def test_it_ends_with_the_agent_it_watches(fake: Path) -> None:
    # -w <pid>: when the watched process goes, caffeinate goes; never left behind.
    import subprocess

    agent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    guard = power.IdleSleepGuard(binary=str(fake), pid=agent.pid)
    guard._spawn()
    holder = guard._proc
    agent.kill()
    agent.wait()
    assert _wait(lambda: holder.poll() is not None)


@pytest.mark.asyncio
async def test_off_and_misconfigured_hold_nothing(fake: Path) -> None:
    off = power.IdleSleepGuard(enabled=False, binary=str(fake))
    await off.start()
    assert not off.running() and off.status()["prevent_idle_sleep"] is False
    bad = power.IdleSleepGuard(config_error="FADEN_NODE_AGENT_PREVENT_IDLE_SLEEP='' is not ...", binary=str(fake))
    await bad.start()
    assert not bad.running() and "PREVENT_IDLE_SLEEP" in bad.status()["error"]
    assert not (fake.parent / "argv.log").exists()


@pytest.mark.asyncio
async def test_a_missing_binary_is_reported_not_fatal(tmp_path: Path) -> None:
    guard = power.IdleSleepGuard(binary=str(tmp_path / "nope"), check_every_s=0.05)
    await guard.start()
    try:
        assert not guard.running() and "could not start" in guard.status()["error"]
    finally:
        await guard.stop()


def test_holder_of_finds_the_real_caffeinate_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    class P:
        def __init__(self, pid, cmdline):
            self.info = {"pid": pid, "cmdline": cmdline}

    procs = [P(1, ["/sbin/launchd"]), P(7, [power.CAFFEINATE, "-i", "-w", "4242"]), P(8, [power.CAFFEINATE, "-d"])]
    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: iter(procs))
    assert power.IdleSleepGuard.holder_of(4242) == 7
    assert power.IdleSleepGuard.holder_of(1) is None


@pytest.mark.parametrize(
    ("raw", "on", "error"),
    [(None, True, False), ("1", True, False), ("off", False, False), ("NO", False, False),
     ("", True, True), ("maybe", True, True)],
)
def test_the_flag_is_strict(monkeypatch: pytest.MonkeyPatch, raw, on, error) -> None:
    if raw is None:
        monkeypatch.delenv("FADEN_NODE_AGENT_PREVENT_IDLE_SLEEP", raising=False)
    else:
        monkeypatch.setenv("FADEN_NODE_AGENT_PREVENT_IDLE_SLEEP", raw)
    config = AgentConfig.from_env()
    assert config.prevent_idle_sleep is on and (config.prevent_idle_sleep_error is not None) is error


@pytest.mark.asyncio
async def test_linux_starts_no_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from faden_agent.service import NodeAgentService

    monkeypatch.setattr(sys, "platform", "linux")
    host = SimpleNamespace(_power=None, _config=SimpleNamespace(prevent_idle_sleep=True, prevent_idle_sleep_error=None))
    await NodeAgentService._start_power_guard(host)  # type: ignore[arg-type]
    assert host._power is None


def test_holder_of_finds_caffeinate_watching_a_child_of_the_launchd_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Onefile build: launchd's pid is the bootloader; its child, the Python agent, is what caffeinate watches."""

    class P:
        def __init__(self, pid, ppid, cmdline):
            self.info = {"pid": pid, "ppid": ppid, "cmdline": cmdline}

    procs = [
        P(6312, 1, ["faden-agent", "run"]),
        P(6314, 6312, ["faden-agent", "run"]),
        P(6400, 1, [power.CAFFEINATE, "-i", "-w", "6314"]),
        P(6500, 1, [power.CAFFEINATE, "-i", "-w", "9999"]),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: iter(procs))
    assert power.IdleSleepGuard.holder_of(6312) == 6400
    assert power.IdleSleepGuard.holder_of(6314) == 6400
    assert power.IdleSleepGuard.holder_of(7777) is None


def test_on_battery_is_reported_for_the_console(monkeypatch) -> None:
    import types

    import psutil

    from faden_agent.power import IdleSleepGuard

    monkeypatch.setattr(psutil, "sensors_battery", lambda: types.SimpleNamespace(power_plugged=False))
    assert IdleSleepGuard.on_battery() is True
    monkeypatch.setattr(psutil, "sensors_battery", lambda: types.SimpleNamespace(power_plugged=True))
    assert IdleSleepGuard.on_battery() is False
    monkeypatch.setattr(psutil, "sensors_battery", lambda: None)
    assert IdleSleepGuard.on_battery() is None
    assert "on_battery" in IdleSleepGuard().status()


def test_holder_of_reads_ps_when_psutil_cannot_see_another_users_cmdline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without root psutil gets AccessDenied (cmdline None) for the _fadenagent caffeinate; ps can read it."""

    class P:
        def __init__(self, pid, ppid, name, cmdline):
            self.info = {"pid": pid, "ppid": ppid, "name": name, "cmdline": cmdline}

    procs = [P(62301, 1, "faden-agent", None), P(62312, 62301, "caffeinate", None)]
    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: iter(procs))
    ps_out = "62301 /Library/Fadenstack/agent/current/faden-agent run\n62312 /usr/bin/caffeinate -i -w 62301\n"
    monkeypatch.setattr(power.subprocess, "run", lambda *a, **k: types.SimpleNamespace(stdout=ps_out))
    assert power.IdleSleepGuard.find_holder(62301) == (62312, True)


def test_unreadable_caffeinate_is_inconclusive_not_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    class P:
        def __init__(self, pid, ppid, name, cmdline):
            self.info = {"pid": pid, "ppid": ppid, "name": name, "cmdline": cmdline}

    procs = [P(62312, 62301, "caffeinate", None)]
    monkeypatch.setattr(psutil, "process_iter", lambda *_a, **_k: iter(procs))

    def boom(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(power.subprocess, "run", boom)
    assert power.IdleSleepGuard.find_holder(62301) == (None, False)
    from faden_agent.__main__ import _sleep_line

    assert "run with sudo" in _sleep_line(None, False) and "NOT prevented" not in _sleep_line(None, False)
    assert "NOT prevented" in _sleep_line(None, True)
