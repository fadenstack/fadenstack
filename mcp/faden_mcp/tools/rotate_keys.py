"""Re-seal the MCP database's secrets with the current MCP encryption key.

Run by ``faden secrets rotate`` from the MCP image, with the new key in ``FADEN_MCP_ENCRYPTION_KEY`` and the
old one in ``FADEN_MCP_ENCRYPTION_KEY_PREVIOUS``::

    python -m faden_mcp.tools.rotate_keys [--skip-unreadable]

It re-seals every column declared with ``EncryptedText`` or ``EncryptedJSON``, found in the models: the
headers and the environment of the MCP servers, which carry bearer tokens and API keys.

The last line it prints is JSON: ``{"ok": true, "columns": {...counts...}}``, or ``{"ok": false, "error":
...}`` with exit code 1. Counts and column names only, never a value.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from faden_mcp.db.crypto import EncryptedJSON, EncryptedText, derive_fernet
from faden_mcp.db.models.mcp import Base
from faden_mcp.settings import settings
from faden_mcp.tools.key_rotation import RotationError, Scheme, Target, rotate


def targets(current: str, previous: str = "") -> list[Target]:
    """Every ``EncryptedText``/``EncryptedJSON`` column, with the purpose label that column's key derives from."""
    old = previous if previous and previous != current else ""
    found: list[Target] = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if not isinstance(column.type, (EncryptedText, EncryptedJSON)):
                continue
            purpose = column.type._purpose  # noqa: SLF001 - the label the type seals with
            scheme = Scheme(derive_fernet(current, purpose), derive_fernet(old, purpose) if old else None)
            keys = tuple(c.name for c in table.primary_key.columns)
            # Rows from before the columns were encrypted are plain text, and the types read them as such.
            found.append(Target(table.name, keys, column.name, scheme, plaintext_ok=True))
    return found


def _engine() -> AsyncEngine:
    return create_async_engine(str(settings.db_url))


async def run(*, skip_unreadable: bool, engine: AsyncEngine | None = None) -> dict:
    """The job: re-seal, verify, commit. Returns the JSON document the command prints."""
    if not settings.encryption_key:
        return {"ok": True, "columns": {}, "note": "MCP encryption is off (no key): nothing is sealed."}
    own = engine is None
    engine = engine or _engine()
    try:
        report = await rotate(
            engine,
            targets(settings.encryption_key, settings.encryption_key_previous),
            skip_unreadable=skip_unreadable,
        )
    except RotationError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if own:
            await engine.dispose()
    return {"ok": True, "columns": report.as_dict()}


def main(argv: list[str] | None = None) -> int:
    """Run the job as a command; the exit code says whether it succeeded."""
    parser = argparse.ArgumentParser(prog="python -m faden_mcp.tools.rotate_keys", description=__doc__)
    parser.add_argument(
        "--skip-unreadable",
        action="store_true",
        help="leave values that neither the new nor the previous key opens as they are, instead of stopping",
    )
    args = parser.parse_args(argv)
    try:
        outcome = asyncio.run(run(skip_unreadable=args.skip_unreadable))
    except Exception as exc:  # the CLI reads one JSON line, whatever went wrong
        outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    print(json.dumps(outcome))  # noqa: T201 - the CLI reads this line
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
