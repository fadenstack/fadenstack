"""Seal what was written in plain text: chats stored without a chat key, or before a column was sealed.

Run by ``faden secrets seal`` and once by ``faden upgrade`` after its health check, in the gateway's
container::

    python -m faden_gateway.tools.seal_plaintext

Every ``EncryptedText``/``EncryptedJSON`` column is found in the models (as the key rotation finds them).
Plain values are sealed with the current chat key, 500 rows at a time, each batch in a transaction of its
own: the gateway keeps serving meanwhile (its columns read plain and sealed values side by side), nothing
waits on a lock for long, and a run that stops is finished by the next. A value that looks like a Fernet
token but opens with no key is counted and left as it is, never overwritten.

The last line it prints is JSON: ``{"ok": true, "columns": {"table.column": {"sealed": n, ...}}}``, or
``{"ok": false, "error": ...}`` with exit code 1. Counts only, never a value.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass

from cryptography.fernet import InvalidToken
from sqlalchemy import column, not_, select, table, tuple_, update
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from faden_gateway.settings import settings
from faden_gateway.tools.key_rotation import FERNET_PREFIX, Target
from faden_gateway.tools.rotate_keys import chat_targets

#: Rows sealed per transaction.
BATCH = 500


@dataclass
class Sealed:
    """What one column had."""

    sealed: int = 0
    already: int = 0
    #: Looks like a token, opens with no key the gateway holds: left as it is.
    unreadable: int = 0


async def _seal_column(engine: AsyncEngine, target: Target) -> Sealed:
    counts = Sealed()
    pk = [column(name) for name in target.primary_key]
    value = column(target.column)
    tbl = table(target.table, *pk, value)
    key_of = tuple_(*pk) if len(pk) > 1 else pk[0]
    last: tuple | None = None
    while True:
        async with engine.begin() as conn:
            query = (
                select(*pk, value)
                .select_from(tbl)
                .where(value.is_not(None), not_(value.like(f"{FERNET_PREFIX}%")))
                .order_by(*pk)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
            if last is not None:
                query = query.where(key_of > (tuple_(*last) if len(pk) > 1 else last[0]))
            rows = (await conn.execute(query)).all()
            if not rows:
                break
            for row in rows:
                keys, plain = tuple(row[: len(pk)]), row[len(pk)]
                sealed = target.scheme.current.encrypt(str(plain).encode("utf-8")).decode("ascii")
                where = [c == k for c, k in zip(pk, keys, strict=True)]
                # Only if no one wrote it meanwhile: a newer value is the gateway's, and sealed already.
                result = await conn.execute(
                    update(tbl).where(*where, value == plain).values({target.column: sealed}),
                )
                counts.sealed += result.rowcount or 0
                last = keys
    async with engine.connect() as conn:
        tokens = (await conn.execute(
            select(value).select_from(tbl).where(value.like(f"{FERNET_PREFIX}%")),
        )).scalars()
        for token in tokens:
            try:
                target.scheme.either.decrypt(token.encode("ascii"))
                counts.already += 1
            except (InvalidToken, UnicodeEncodeError):
                counts.unreadable += 1
    return counts


async def run(engine: AsyncEngine | None = None) -> dict:
    """Seal every plain value of every sealed column; returns the JSON document the command prints."""
    if not settings.encryption_key:
        return {"ok": False, "error": "FADEN_GATEWAY_ENCRYPTION_KEY is empty: there is no key to seal with."}
    own = engine is None
    engine = engine or create_async_engine(str(settings.db_url))
    try:
        report: dict[str, dict[str, int]] = {}
        for target in chat_targets(settings.encryption_key, settings.encryption_key_previous):
            report[target.label] = asdict(await _seal_column(engine, target))
    finally:
        if own:
            await engine.dispose()
    return {"ok": True, "columns": report}


def main(argv: list[str] | None = None) -> int:
    """Run the job as a command; the exit code says whether it succeeded."""
    parser = argparse.ArgumentParser(prog="python -m faden_gateway.tools.seal_plaintext", description=__doc__)
    parser.parse_args(argv)
    try:
        outcome = asyncio.run(run())
    except Exception as exc:  # the CLI reads one JSON line, whatever went wrong
        outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    print(json.dumps(outcome))  # noqa: T201 - the CLI reads this line
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
