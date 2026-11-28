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
import os
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


def parse_app_incoming(text: str | None) -> bool | None:
    """``socketfilterfw --getappblocked <path>``: "Incoming connection to <path> is permitted." / "... is blocked"."""
    if not text:
        return None
    lowered = text.lower()
    if "is permitted" in lowered:
        return True
    if "is blocked" in lowered:
        return False
    return None


def parse_ssh_listener(text: str | None) -> bool | None:
    """``netstat -an -p tcp``: whether anything listens on port 22 (``None``: no answer)."""
    if not text or "Proto" not in text:  # a real listing always has its header; anything else is no answer
        return None
    return any(
        line.split()[-1:] == ["LISTEN"] and re.search(r"[.:]22$", line.split()[3] if len(line.split()) > 3 else "")
        for line in text.splitlines()
    )


async def remote_login() -> bool | None:
    """Whether Remote Login (SSH) is on; ``None`` when it cannot be told without root.

    ``systemsetup -getremotelogin`` needs root, so this reads two things any user may read: a listener on
    port 22 (macOS runs sshd from a launchd socket, so the port is open exactly while Remote Login is on),
    and whether launchd has the ``com.openssh.sshd`` service loaded. Both are read-only.
    """
    listening = parse_ssh_listener(await _run("/usr/sbin/netstat", "-an", "-p", "tcp"))
    if listening:
        return True
    proc_out = await _run_any("/bin/launchctl", "print", "system/com.openssh.sshd")
    if proc_out is None:
        return None
    code, text = proc_out
    if code == 0:
        return True
    if "could not find service" in text.lower():
        return False if listening is not None else None
    return None


async def _run_any(*argv: str) -> tuple[int, str] | None:
    """Like :func:`_run` but also returns a non-zero exit's output."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), _TIMEOUT_SEC)
    except (OSError, TimeoutError, asyncio.TimeoutError):
        return None
    return proc.returncode or 0, out.decode("utf-8", "replace")


def remote_login_advice(status: dict[str, Any]) -> list[str]:
    """One line when FileVault is on and Remote Login is known to be off: a power cut then needs a local unlock."""
    if status.get("filevault") is True and status.get("remote_login") is False:
        return [
            "FileVault is on and Remote Login is off: after a power cut this Mac waits for a local unlock.",
            "  Turn on Remote Login to unlock over SSH, or restart on purpose with: sudo fdesetup authrestart",
            "  See \"FileVault and unattended restarts\" in the help page \"Serve models on a Mac\".",
        ]
    return []


async def app_incoming(path: str) -> bool | None:
    """Whether the application firewall lets *path* accept incoming connections (``None``: unknown).

    Read-only and answered to an unprivileged user. A launchd daemon cannot
    answer the firewall's "accept incoming connections?" dialog, so an
    unlisted binary that the firewall does not allow by itself stays blocked.
    """
    return parse_app_incoming(await _run(_SOCKETFILTERFW, "--getappblocked", path))


def own_binary() -> str | None:
    """The packaged agent's real executable path (what the firewall keys on); ``None`` from source."""
    if not getattr(sys, "frozen", False):
        return None
    return os.path.realpath(sys.executable)


async def collect(binary: str | None = None) -> dict[str, Any]:
    """The Mac's FileVault and firewall state; an empty dict off macOS.

    *binary* is the agent's own executable: the firewall decides per
    program, so "the firewall is on" alone does not say whether the model
    port and the metrics port are reachable.
    """
    if sys.platform != "darwin":
        return {}
    binary = binary or own_binary()
    vault, state, stealth, block_all, app, ssh = await asyncio.gather(
        _run(_FDESETUP, "status"),
        _run(_SOCKETFILTERFW, "--getglobalstate"),
        _run(_SOCKETFILTERFW, "--getstealthmode"),
        _run(_SOCKETFILTERFW, "--getblockall"),
        app_incoming(binary) if binary else asyncio.sleep(0),
        remote_login(),
    )
    firewall: dict[str, Any] = {
        "enabled": parse_firewall(state),
        "stealth": parse_stealth(stealth),
        "block_all": parse_block_all(block_all),
    }
    if binary:
        firewall["agent_allowed"] = app
    return {"filevault": parse_filevault(vault), "remote_login": ssh, "firewall": firewall}


def firewall_advice(firewall: dict[str, Any], binary: str) -> list[str]:
    """What an operator should read when the firewall may stop the gateway or Prometheus; empty when fine."""
    if firewall.get("enabled") is not True:
        return []
    fw = "/usr/libexec/ApplicationFirewall/socketfilterfw"
    if firewall.get("block_all") is True:
        return [
            "Firewall: ON with \"Block all incoming connections\": the gateway and Prometheus cannot reach this Mac.",
            "  Turn that option off in System Settings > Network > Firewall > Options.",
        ]
    if firewall.get("agent_allowed") is True:
        return []
    verdict = "blocks" if firewall.get("agent_allowed") is False else "may block"
    return [
        f"Firewall: ON, and it {verdict} incoming connections to the agent (model port 8100-8999, metrics 9468).",
        "  A remote gateway or Prometheus will time out; this Mac then looks offline for serving.",
        "  Allow it once per build (a new build has a new path), as an administrator:",
        f"    sudo {fw} --add {binary} && sudo {fw} --unblockapp {binary}",
    ]


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
