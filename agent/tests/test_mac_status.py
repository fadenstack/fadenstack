"""The Mac status the console shows: parsed from the system tools, never guessed."""

from __future__ import annotations

import pytest

from faden_agent import mac_status


@pytest.mark.parametrize(("text", "want"), [
    ("FileVault is On.\n", True), ("FileVault is Off.\n", False), ("", None), (None, None), ("garbage", None),
])
def test_filevault(text: str | None, want: bool | None) -> None:
    assert mac_status.parse_filevault(text) is want


@pytest.mark.parametrize(("text", "want"), [
    ("Firewall is enabled. (State = 1)", True), ("Firewall is disabled. (State = 0)", False), (None, None),
])
def test_firewall(text: str | None, want: bool | None) -> None:
    assert mac_status.parse_firewall(text) is want


def test_stealth_and_block_all() -> None:
    assert mac_status.parse_stealth("Stealth mode enabled") is True
    assert mac_status.parse_stealth("Stealth mode disabled") is False
    assert mac_status.parse_block_all("Block all DISABLED!") is False
    assert mac_status.parse_block_all("Block all ENABLED!") is True


@pytest.mark.anyio
async def test_nothing_is_collected_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mac_status.sys, "platform", "linux")
    assert await mac_status.collect() == {}


@pytest.mark.anyio
async def test_a_missing_tool_is_none_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mac_status.sys, "platform", "darwin")
    monkeypatch.setattr(mac_status, "_FDESETUP", "/nonexistent/fdesetup")
    monkeypatch.setattr(mac_status, "_SOCKETFILTERFW", "/nonexistent/socketfilterfw")
    got = await mac_status.collect()
    assert got["filevault"] is None and got["firewall"]["enabled"] is None


@pytest.mark.anyio
async def test_a_slow_command_is_killed_not_left_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mac_status, "_TIMEOUT_SEC", 0.3)
    assert await mac_status._run("/bin/sleep", "30") is None  # noqa: SLF001
    import subprocess

    left = subprocess.run(["pgrep", "-f", "sleep 30"], capture_output=True, text=True, check=False)
    assert left.stdout.strip() == ""


@pytest.mark.anyio
async def test_the_status_is_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    calls = iter([{"filevault": True}, {"filevault": False}])

    async def fake() -> dict:
        return next(calls)

    monkeypatch.setattr(mac_status, "collect", fake)
    target: dict = {}
    task = asyncio.create_task(mac_status.refresh_forever(target, interval=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    assert target["mac_status"] in ({"filevault": True}, {"filevault": False})


def test_app_incoming_is_parsed_from_getappblocked() -> None:
    assert mac_status.parse_app_incoming("Incoming connection to /x/faden-agent is permitted.") is True
    assert mac_status.parse_app_incoming("Incoming connection to /x/faden-agent is blocked.") is False
    assert mac_status.parse_app_incoming("") is None


def test_firewall_advice_names_the_exact_commands_only_when_needed() -> None:
    binary = "/Library/Fadenstack/agent/0.0.0-abc/faden-agent"
    assert mac_status.firewall_advice({"enabled": False}, binary) == []
    assert mac_status.firewall_advice({"enabled": None}, binary) == []
    assert mac_status.firewall_advice({"enabled": True, "agent_allowed": True}, binary) == []
    blocked = "\n".join(mac_status.firewall_advice({"enabled": True, "agent_allowed": False}, binary))
    assert "blocks incoming" in blocked
    assert f"--add {binary}" in blocked and f"--unblockapp {binary}" in blocked
    unknown = "\n".join(mac_status.firewall_advice({"enabled": True, "agent_allowed": None}, binary))
    assert "may block" in unknown
    block_all = "\n".join(mac_status.firewall_advice({"enabled": True, "block_all": True}, binary))
    assert "Block all incoming" in block_all and "sudo" not in block_all


@pytest.mark.anyio
async def test_collect_reports_whether_the_agent_binary_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mac_status.sys, "platform", "darwin")
    calls: list[tuple[str, ...]] = []

    async def fake_run(*argv: str) -> str | None:
        calls.append(argv)
        return "Incoming connection to /b is blocked." if "--getappblocked" in argv else None

    monkeypatch.setattr(mac_status, "_run", fake_run)
    out = await mac_status.collect("/b")
    assert out["firewall"]["agent_allowed"] is False
    assert any(a[-2:] == ("--getappblocked", "/b") for a in calls)


def test_parse_ssh_listener() -> None:
    from faden_agent import mac_status

    on = "tcp4       0      0  *.22                   *.*                    LISTEN     \ntcp4  0  0  *.8100  *.*  LISTEN\n"
    off = "tcp4       0      0  *.8100                 *.*                    LISTEN     \ntcp4 0 0 1.2.3.4.5022 1.2.3.4.1 ESTABLISHED\n"
    assert mac_status.parse_ssh_listener(on) is True
    assert mac_status.parse_ssh_listener(off) is False
    assert mac_status.parse_ssh_listener(None) is None


def test_remote_login_advice_only_when_known_off_with_filevault() -> None:
    from faden_agent import mac_status

    warn = mac_status.remote_login_advice({"filevault": True, "remote_login": False})
    assert len(warn) == 3 and "waits for a local unlock" in warn[0] and "fdesetup authrestart" in warn[1]
    for status in ({"filevault": True, "remote_login": True}, {"filevault": True, "remote_login": None},
                   {"filevault": False, "remote_login": False}, {"filevault": None, "remote_login": False}, {}):
        assert mac_status.remote_login_advice(status) == []


@pytest.mark.asyncio
async def test_remote_login_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    from faden_agent import mac_status

    async def fake_run(*argv):
        return "tcp4 0 0 *.22 *.* LISTEN\n"

    monkeypatch.setattr(mac_status, "_run", fake_run)
    assert await mac_status.remote_login() is True

    async def no_listener(*argv):
        return "tcp4 0 0 *.8100 *.* LISTEN\n"

    async def not_found(*argv):
        return 113, 'Could not find service "com.openssh.sshd" in domain for system'

    monkeypatch.setattr(mac_status, "_run", no_listener)
    monkeypatch.setattr(mac_status, "_run_any", not_found)
    assert await mac_status.remote_login() is False

    async def odd(*argv):
        return 1, "something else"

    monkeypatch.setattr(mac_status, "_run_any", odd)
    assert await mac_status.remote_login() is None
