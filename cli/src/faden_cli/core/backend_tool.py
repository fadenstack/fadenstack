"""Run the backend's account and audit tool (``faden_backend.tools.admin``) in the backend container.

What the CLI does to accounts and what it does to the install (backups, restores, key rotations, upgrades) is
recorded in the audit trail like what the console does, with ``source: cli``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from faden_cli.core.console import warning

#: The backend's container, as the compose file names it.
BACKEND_CONTAINER = "faden-backend"


def run_backend_tool(args: list[str], *, stdin: str | None = None, timeout: int = 120) -> dict[str, Any]:
    """``python -m faden_backend.tools.admin <args>`` in the running backend; its last JSON line.

    Returns ``{"ok": False, "error": ...}`` when docker or the container is not there, or the tool failed.
    """
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "error": "docker not found on PATH"}
    try:
        proc = subprocess.run(  # noqa: S603
            [docker, "exec", "-i", BACKEND_CONTAINER, "python", "-m", "faden_backend.tools.admin", *args],
            input=stdin,
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


def record_event(
    action: str, target_type: str, target_id: str, *, metadata: dict[str, Any] | None = None,
    result: str = "allow", severity: str = "high",
) -> bool:
    """Record what the CLI did in the audit trail; a warning when it cannot (the backend is not running)."""
    args = [
        "record", "--action", action, "--target-type", target_type, "--target-id", target_id,
        "--result", result, "--severity", severity,
    ]
    if metadata:
        args += ["--meta", json.dumps(metadata)]
    outcome = run_backend_tool(args)
    if not outcome.get("ok"):
        warning(f"Could not record {action} in the audit trail: {outcome.get('error', 'unknown error')}")
        return False
    return True
