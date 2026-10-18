"""How long deleted chats are kept before they are erased: asked once by ``faden deploy`` and ``faden upgrade``.

The answer goes into ``.env`` (the gateway's own default) and, once the backend runs, into the console's
setting (Settings > Chat), where an administrator changes it later. A setting an administrator already chose is
never overwritten.
"""

from __future__ import annotations

from pathlib import Path

import click

from faden_cli.core.console import console, info, success, warning
from faden_cli.core.env_gen import read_env_file, write_env_file

#: The gateway's default, read when the console's setting is not stored.
ENV_KEY = "FADEN_GATEWAY_CHAT_DELETED_RETENTION_DAYS"
#: The console's setting.
SETTING_KEY = "chat.deleted_retention_days"
DEFAULT_DAYS = 30


def ask_retention(env_path: Path, *, days: int | None, yes: bool, heading: str) -> int:
    """The number of days, from the option, from ``.env``, from the person, or 30; written to ``.env``."""
    env_vars = read_env_file(env_path)
    if days is None:
        stored = env_vars.get(ENV_KEY, "").strip()
        if stored.isdigit():
            return int(stored)
        if yes:
            days = DEFAULT_DAYS
            info(
                f"Chats people delete are erased for good after {DEFAULT_DAYS} days "
                "(change it under Settings > Chat, or with --chat-retention-days).",
            )
        else:
            console.print(f"\n[bold cyan]{heading}[/bold cyan]")
            console.print(
                "  A chat someone deletes disappears for them at once. It is kept on the server for a while\n"
                "  (an administrator can still erase it at once), then erased for good with its files.\n"
                "  0 erases within the hour. Administrators change it later under Settings > Chat.",
            )
            days = click.prompt(
                "  Days to keep deleted chats", type=click.IntRange(min=0), default=DEFAULT_DAYS,
            )
    if env_vars.get(ENV_KEY) != str(days):
        env_vars[ENV_KEY] = str(days)
        write_env_file(env_path, env_vars)
    return days


def apply_retention(days: int) -> None:
    """Store *days* as the console's setting unless an administrator set one already."""
    from faden_cli.core.backend_tool import run_backend_tool  # noqa: PLC0415

    stored = run_backend_tool(["get-setting", "--key", SETTING_KEY])
    if not stored.get("ok"):
        warning(f"The chat retention was not stored in the console: {stored.get('error', 'unknown error')}")
        return
    if stored.get("value") is not None:
        return
    done = run_backend_tool(["set-setting", "--key", SETTING_KEY, "--value", str(days)])
    if done.get("ok"):
        success(f"Deleted chats are erased after {days} day(s) (Settings > Chat).")
    else:
        warning(f"The chat retention was not stored in the console: {done.get('error', 'unknown error')}")
