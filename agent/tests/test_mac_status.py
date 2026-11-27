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
