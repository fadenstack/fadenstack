"""``faden audit`` -- the audit trail from the server."""

from __future__ import annotations

import sys

import click

from faden_cli.core.console import console, error, success, warning


@click.group("audit")
def audit_group() -> None:
    """The audit trail: who did what in the console, the API and this CLI."""


@audit_group.command("verify")
def verify_cmd() -> None:
    """Check that every event is as it was written: the hash chain, the events from before it, the file copy."""
    from faden_cli.core.backend_tool import run_backend_tool  # noqa: PLC0415

    outcome = run_backend_tool(["verify"], timeout=600)
    if "events" not in outcome:
        error(f"The check did not run: {outcome.get('error', 'unknown error')}")
        sys.exit(2)
    console.print(f"  Events in the chain: {outcome['events']}")
    console.print(f"  Events from before the chain (covered by its first event): {outcome['legacy_rows']}")
    if outcome.get("ok"):
        success(f"The audit trail is intact (up to {outcome.get('intact_until') or 'now'}).")
        return
    warning("The audit trail was changed:")
    for problem in outcome.get("problems") or []:
        console.print(f"    - {problem}")
    sys.exit(1)
