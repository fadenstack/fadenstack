"""Seal what the gateway stored in plain text: ``faden secrets seal``, and once after ``faden upgrade``.

Runs ``python -m faden_gateway.tools.seal_plaintext`` in the running gateway (its chat key, its database).
Chats written before the install had a key, or before a column was sealed, are sealed in batches while the
platform is in use; a run that stops is finished by the next.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from faden_cli.core.console import console, info, success, warning

#: The gateway's container, as the compose file names it.
GATEWAY_CONTAINER = "faden-gateway"


def run_seal(timeout: int = 6 * 3600) -> dict[str, Any]:
    """The seal job's JSON line, or ``{"ok": False, "error": ...}``."""
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "error": "docker not found on PATH"}
    try:
        proc = subprocess.run(  # noqa: S603
            [docker, "exec", GATEWAY_CONTAINER, "python", "-m", "faden_gateway.tools.seal_plaintext"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)}
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            outcome = json.loads(line)
        except ValueError:
            continue
        if isinstance(outcome, dict):
            return outcome
    detail = (proc.stderr or proc.stdout).strip().splitlines()
    return {"ok": False, "error": detail[-1] if detail else f"exit code {proc.returncode}"}


def seal_and_report() -> bool:
    """Run the job and say what it did; ``False`` when it did not finish (a warning, never an abort)."""
    info("Sealing chats stored in plain text…")
    outcome = run_seal()
    if not outcome.get("ok"):
        warning(f"Not sealed yet: {outcome.get('error', 'unknown error')} -- run `faden secrets seal` later.")
        return False
    columns: dict[str, dict[str, int]] = outcome.get("columns") or {}
    sealed = sum(c.get("sealed", 0) for c in columns.values())
    unreadable = {name: c["unreadable"] for name, c in columns.items() if c.get("unreadable")}
    success(f"Sealed {sealed} value(s) stored in plain text." if sealed else "Nothing stored in plain text.")
    for name, count in unreadable.items():
        console.print(
            f"  [yellow]{name}: {count} value(s) look sealed but open with no key; left as they are[/yellow]",
        )
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    record_event("secrets.seal", "install", "chat", metadata={"sealed": sealed, "unreadable": unreadable})
    return True
