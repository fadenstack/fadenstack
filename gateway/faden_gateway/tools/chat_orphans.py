"""Chat data of accounts that no longer exist: listed, and erased only when asked (``faden chats orphans``).

Until 18 October 2026 deleting an account left its chats, projects, files and memory in the gateway. An
upgrade erases nothing on its own, so this finds them, and ``--erase`` erases them through the same code as
every other erase (files first)::

    python -m faden_gateway.tools.chat_orphans            # list
    python -m faden_gateway.tools.chat_orphans --erase    # erase what it lists

The last line it prints is JSON: ``{"ok": true, "accounts": {"<id>": {"chats": n, ...}}, "erased": ...}``.
Ids and counts only, never a name or a word of what was written.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from faden_gateway.db.models.gateway import ChatAttachment, ChatProject, ChatSession, MemoryFact
from faden_gateway.services.gateway import erasure
from faden_gateway.services.gateway.file_store import LocalFileStore
from faden_gateway.settings import settings

_TABLES = {"chats": ChatSession, "projects": ChatProject, "files": ChatAttachment, "memory_facts": MemoryFact}


async def backend_user_ids() -> set[str]:
    """Every account id in the backend's database."""
    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_pass}"
        f"@{settings.db_host}:{settings.db_port}/{settings.backend_db_base}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        return {str(row["id"]) for row in await conn.fetch('SELECT id FROM "user"')}
    finally:
        await conn.close()


async def find(session: AsyncSession, known: set[str]) -> dict[str, dict[str, int]]:
    """Per account that no longer exists, how many of each kind of chat data it left."""
    found: dict[str, dict[str, int]] = {}
    for label, model in _TABLES.items():
        rows = (await session.execute(select(model.user_id, func.count()).group_by(model.user_id))).all()
        for user_id, count in rows:
            if str(user_id) not in known:
                found.setdefault(str(user_id), dict.fromkeys(_TABLES, 0))[label] = int(count)
    return found


async def run(
    *,
    erase: bool,
    engine: AsyncEngine | None = None,
    known_ids: Callable[[], Awaitable[set[str]]] = backend_user_ids,
) -> dict[str, Any]:
    """The job; returns the JSON document the command prints."""
    known = await known_ids()
    own = engine is None
    engine = engine or create_async_engine(str(settings.db_url))
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            accounts = await find(session, known)
            erased: dict[str, int] = {}
            if erase:
                store = LocalFileStore(settings.chat_file_store_root)
                for user_id in accounts:
                    done = await erasure.erase_user(session, store, user_id)
                    erased[user_id] = done.chats
                await session.commit()
    finally:
        if own:
            await engine.dispose()
    return {"ok": True, "accounts": accounts, "erased": erased if erase else None}


def main(argv: list[str] | None = None) -> int:
    """Run the job as a command; the exit code says whether it succeeded."""
    parser = argparse.ArgumentParser(prog="python -m faden_gateway.tools.chat_orphans", description=__doc__)
    parser.add_argument("--erase", action="store_true", help="erase what is listed (files first)")
    args = parser.parse_args(argv)
    try:
        outcome = asyncio.run(run(erase=args.erase))
    except Exception as exc:  # the CLI reads one JSON line, whatever went wrong
        outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    print(json.dumps(outcome))  # noqa: T201 - the CLI reads this line
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
