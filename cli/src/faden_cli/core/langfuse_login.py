"""Langfuse's first user: an address Langfuse takes, and whether Langfuse made the user.

Langfuse checks ``LANGFUSE_INIT_USER_EMAIL`` as an e-mail address whose domain ends in at least two letters.
It refused ``admin@localhost``, the administrator ``faden deploy -y`` makes: it then had no user and no API
key, and answered 500 to the project's keys, so no trace was recorded, while the deploy said the user was made
(found 19 November 2026 in the upgrade check to 0.7.1).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from faden_cli.core.env_gen import read_env_file, set_env_values

#: The address check of Langfuse's settings (zod's e-mail pattern); tried against Langfuse 3.227.1: it takes
#: admin@localhost.local and admin@example.com, and refuses admin@localhost and admin@10.0.0.5.
_TAKEN = re.compile(
    r"^(?!\.)(?!.*\.\.)[A-Za-z0-9_'+\-.]*[A-Za-z0-9_+-]@([A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}$"
)
#: Said where Langfuse took another address than the administrator's.
WHY = "Langfuse takes no address without a domain such as example.com"
#: When even the address with ``.local`` added is refused.
FALLBACK = "admin@fadenstack.local"


def address_for_langfuse(email: str) -> str:
    """*email* where Langfuse takes it; else with ``.local`` added to its domain (admin@localhost.local)."""
    if _TAKEN.match(email):
        return email
    local, _, domain = email.rpartition("@")
    if local and domain:
        candidate = f"{local}@{domain}.local"
        if _TAKEN.match(candidate):
            return candidate
    return FALLBACK


def correct_env(env_path: Path) -> str | None:
    """The first user's address in *env_path* made one Langfuse takes; the new one, or ``None`` if it was."""
    if not env_path.exists():
        return None
    current = read_env_file(env_path).get("LANGFUSE_INIT_USER_EMAIL", "")
    if not current or address_for_langfuse(current) == current:
        return None
    fixed = address_for_langfuse(current)
    set_env_values(env_path, {"LANGFUSE_INIT_USER_EMAIL": fixed})
    return fixed


def has_first_user(
    env: dict[str, str], *, docker: str = "docker", wait_s: float = 60, every_s: float = 3,
) -> bool:
    """Whether Langfuse's database holds its first user, made as Langfuse starts: waits up to *wait_s*."""
    from faden_cli.core import restore_swap  # noqa: PLC0415
    from faden_cli.core.langfuse_db import database_of  # noqa: PLC0415

    email = env.get("LANGFUSE_INIT_USER_EMAIL", "")
    url = env.get("LANGFUSE_DATABASE_URL", "")
    if not email or not url:
        return False
    sql = f"SELECT count(*) FROM users WHERE lower(email) = lower({restore_swap.literal(email)});"  # noqa: S608
    deadline = time.monotonic() + wait_s
    while True:
        try:
            if int(restore_swap.psql(sql, db=database_of(url), docker=docker).strip() or 0) > 0:
                return True
        except (restore_swap.PostgresError, ValueError):
            pass  # its table comes with Langfuse's migrations, which may still run
        if time.monotonic() >= deadline:
            return False
        time.sleep(every_s)



def announce(admin_email: str, address: str) -> None:
    """``faden deploy``: whose address Langfuse's first user has."""
    from faden_cli.core.console import info  # noqa: PLC0415

    if address == admin_email:
        info("Langfuse's first user is the administrator's e-mail, with a password of its own in .env.")
    else:
        info(f"Langfuse's first user is {address}, with a password of its own in .env: {WHY}.")


def correct_step(env_path: Path) -> bool:
    """``faden upgrade``: :func:`correct_env`, said on the console; whether it corrected the address."""
    from faden_cli.core.console import info  # noqa: PLC0415

    fixed = correct_env(env_path)
    if fixed:
        info(f"Langfuse's first user is now {fixed}: {WHY}.")
    return fixed is not None


def report_first_user(env_path: Path, *, wanted: bool = True, then: str = "") -> None:
    """Said on the console: whether Langfuse made its first user (nothing when not *wanted*)."""
    from faden_cli.core.console import success, warning  # noqa: PLC0415

    if not wanted:
        return
    if has_first_user(read_env_file(env_path)):
        success("Langfuse has its first user.")
    else:
        warning(
            "Langfuse has not made its first user, so it records no traces: "
            f"see `faden logs faden-web`{then}."
        )
