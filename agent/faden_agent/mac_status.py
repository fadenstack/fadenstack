"""What an operator should know about a Mac before it serves models (darwin only, read-only).

Reported in ``static_capabilities["mac_status"]`` so the console can show it:
the application firewall (a firewall that blocks incoming connections stops
the gateway reaching the model port), and FileVault (an unattended reboot only
comes back by itself after ``fdesetup authrestart``). Both commands answer an
unprivileged user; neither changes anything. Anything that cannot be read is
``None``, never guessed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import sys
from typing import Any

log = logging.getLogger(__name__)

_FDESETUP = "/usr/bin/fdesetup"
_SOCKETFILTERFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"
_TIMEOUT_SEC = 5.0


async def _run(*argv: str) -> str | None:
    """The command's output, or ``None`` when it is missing, fails or is slow (a slow one is killed)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError:
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), _TIMEOUT_SEC)
    except (TimeoutError, asyncio.TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 2.0)
        return None
    return out.decode("utf-8", "replace") if proc.returncode == 0 else None


def parse_filevault(text: str | None) -> bool | None:
    """``fdesetup status``: "FileVault is On." / "FileVault is Off."."""
    if not text:
        return None
    match = re.search(r"FileVault is (On|Off)", text)
    return None if match is None else match.group(1) == "On"


def parse_firewall(text: str | None) -> bool | None:
    """``socketfilterfw --getglobalstate``: "Firewall is enabled. (State = 1)" or "disabled"."""
    if not text:
        return None
    lowered = text.lower()
    if "firewall is enabled" in lowered:
        return True
    if "firewall is disabled" in lowered:
        return False
    return None


def parse_stealth(text: str | None) -> bool | None:
    """``socketfilterfw --getstealthmode``: "Stealth mode enabled" or "disabled"."""
    if not text:
        return None
    lowered = text.lower()
    if "stealth mode enabled" in lowered:
        return True
    if "stealth mode disabled" in lowered:
        return False
    return None


def parse_block_all(text: str | None) -> bool | None:
    """``socketfilterfw --getblockall``: "Block all DISABLED!" or "Block all ENABLED!"."""
    if not text:
        return None
    lowered = text.lower()
    if "block all enabled" in lowered:
        return True
    if "block all disabled" in lowered:
        return False
    return None


async def collect() -> dict[str, Any]:
    """The Mac's FileVault and firewall state; an empty dict off macOS."""
    if sys.platform != "darwin":
        return {}
    vault, state, stealth, block_all = await asyncio.gather(
        _run(_FDESETUP, "status"),
        _run(_SOCKETFILTERFW, "--getglobalstate"),
        _run(_SOCKETFILTERFW, "--getstealthmode"),
        _run(_SOCKETFILTERFW, "--getblockall"),
    )
    return {
        "filevault": parse_filevault(vault),
        "firewall": {
            "enabled": parse_firewall(state),
            "stealth": parse_stealth(stealth),
            "block_all": parse_block_all(block_all),
        },
    }


#: How often the state is read again: FileVault and the firewall change while the agent runs.
REFRESH_SEC = 300.0


async def refresh_forever(target: dict[str, Any], *, interval: float = REFRESH_SEC) -> None:
    """Keep ``target["mac_status"]`` current (the dict the agent sends with every inventory)."""
    while True:
        await asyncio.sleep(interval)
        try:
            target["mac_status"] = await collect()
        except Exception:  # noqa: BLE001 - a status read never stops the agent
            log.exception("Could not refresh the Mac status")
