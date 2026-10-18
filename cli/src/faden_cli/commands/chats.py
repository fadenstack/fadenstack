"""``faden chats`` -- the chat data on the server, as a whole."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import click

from faden_cli.core.console import console, error, info, success


def _orphans(*, erase: bool) -> dict:
    docker = shutil.which("docker")
    if not docker:
        return {"ok": False, "error": "docker not found on PATH"}
    args = [docker, "exec", "faden-gateway", "python", "-m", "faden_gateway.tools.chat_orphans"]
    proc = subprocess.run(  # noqa: S603
        [*args, *(["--erase"] if erase else [])], capture_output=True, text=True, check=False, timeout=3600,
    )
    for line in reversed(proc.stdout.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    detail = (proc.stderr or proc.stdout).strip().splitlines()
    return {"ok": False, "error": detail[-1] if detail else f"exit code {proc.returncode}"}


@click.group("chats")
def chats_group() -> None:
    """The chat data on the server."""


@chats_group.command("orphans")
@click.option("--erase", is_flag=True, help="Erase what is listed, files first (asks first).")
@click.option("-y", "--yes", is_flag=True, help="Erase without asking.")
def orphans_cmd(*, erase: bool, yes: bool) -> None:
    """Chat data of accounts that no longer exist: listed by account id; erased only with --erase.

    Deleting an account erases its chats from this release on. Accounts deleted before left theirs; an upgrade
    erases nothing on its own.
    """
    found = _orphans(erase=False)
    if not found.get("ok"):
        error(f"Not checked: {found.get('error', 'unknown error')}")
        sys.exit(1)
    accounts: dict[str, dict[str, int]] = found.get("accounts") or {}
    if not accounts:
        success("No chat data of deleted accounts.")
        return
    for user_id, counts in accounts.items():
        console.print(f"  {user_id}: " + ", ".join(f"{n} {kind.replace('_', ' ')}" for kind, n in counts.items()))
    if not erase:
        info("Erase it with: faden chats orphans --erase")
        return
    if not yes:
        click.confirm(f"Erase the chat data of {len(accounts)} deleted account(s) for good?", abort=True)
    done = _orphans(erase=True)
    if not done.get("ok"):
        error(f"Not erased: {done.get('error', 'unknown error')}")
        sys.exit(1)
    from faden_cli.core.backend_tool import record_event  # noqa: PLC0415

    record_event("chat.orphans.erase", "install", "chat", metadata={"accounts": list(accounts)})
    success(f"Erased the chat data of {len(accounts)} deleted account(s).")
