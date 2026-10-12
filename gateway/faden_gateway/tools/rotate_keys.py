"""Re-seal the gateway database's secrets with a new key.

Run by ``faden secrets rotate`` from the gateway image, once per key::

    python -m faden_gateway.tools.rotate_keys --scope settings [--skip-unreadable]
    python -m faden_gateway.tools.rotate_keys --scope chat [--skip-unreadable]

``settings`` is the settings master key (``FADEN_GATEWAY_SETTINGS_MASTER_KEY``, the backend's key): it seals
the provider API keys the backend copies here. ``chat`` is the chat key (``FADEN_GATEWAY_ENCRYPTION_KEY``):
it seals every column declared with ``EncryptedText`` or ``EncryptedJSON`` -- messages, titles, memory,
attachment notes, the outbound TLS material and the governance keys. The columns are found in the models,
so a new encrypted column is rotated without anyone remembering to list it. Each key has its previous key
in ``..._PREVIOUS`` while a rotation is unfinished.

The last line it prints is JSON: ``{"ok": true, "columns": {...counts...}}``, or ``{"ok": false, "error":
...}`` with exit code 1. Counts and column names only, never a value.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from faden_gateway.db.crypto import EncryptedJSON, EncryptedText, derive_fernet, settings_fernet_for
from faden_gateway.db.meta import meta
from faden_gateway.db.models import load_all_models
from faden_gateway.settings import settings
from faden_gateway.tools.key_rotation import RotationError, Scheme, Target, rotate


def settings_targets(current: str, previous: str = "") -> list[Target]:
    """The provider API keys: sealed by the backend with the settings master key, copied here unchanged."""
    old = previous if previous and previous != current else ""
    scheme = Scheme(settings_fernet_for(current), settings_fernet_for(old) if old else None)
    # A key stored before the migration that sealed the column is still plain text; the gateway uses it.
    return [Target("llm_provider_instance", ("id",), "api_key_encrypted", scheme, plaintext_ok=True)]


def chat_targets(current: str, previous: str = "") -> list[Target]:
    """Every ``EncryptedText``/``EncryptedJSON`` column, with the purpose label that column's key derives from."""
    load_all_models()
    old = previous if previous and previous != current else ""
    found: list[Target] = []
    for table in meta.tables.values():
        for column in table.columns:
            if not isinstance(column.type, (EncryptedText, EncryptedJSON)):
                continue
            purpose = column.type._purpose  # noqa: SLF001 - the label the type seals with
            scheme = Scheme(derive_fernet(current, purpose), derive_fernet(old, purpose) if old else None)
            keys = tuple(c.name for c in table.primary_key.columns)
            # Rows from before chats were encrypted are plain text, and the types read them as such.
            found.append(Target(table.name, keys, column.name, scheme, plaintext_ok=True))
    return found


def _engine() -> AsyncEngine:
    return create_async_engine(str(settings.db_url))


async def run(scope: str, *, skip_unreadable: bool, engine: AsyncEngine | None = None) -> dict:
    """The job: re-seal, verify, commit. Returns the JSON document the command prints."""
    if scope == "settings":
        if not settings.settings_master_key:
            return {"ok": False, "error": "FADEN_GATEWAY_SETTINGS_MASTER_KEY is empty."}
        targets = settings_targets(settings.settings_master_key, settings.settings_master_key_previous)
    elif scope == "chat":
        if not settings.encryption_key:
            return {"ok": True, "columns": {}, "note": "Chat encryption is off (no key): nothing is sealed."}
        targets = chat_targets(settings.encryption_key, settings.encryption_key_previous)
    else:
        return {"ok": False, "error": f"Unknown scope {scope!r}."}
    own = engine is None
    engine = engine or _engine()
    try:
        report = await rotate(engine, targets, skip_unreadable=skip_unreadable)
    except RotationError as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if own:
            await engine.dispose()
    return {"ok": True, "columns": report.as_dict()}


def main(argv: list[str] | None = None) -> int:
    """Run the job as a command; the exit code says whether it succeeded."""
    parser = argparse.ArgumentParser(prog="python -m faden_gateway.tools.rotate_keys", description=__doc__)
    parser.add_argument("--scope", choices=("settings", "chat"), required=True)
    parser.add_argument(
        "--skip-unreadable",
        action="store_true",
        help="leave values that neither the new nor the previous key opens as they are, instead of stopping",
    )
    args = parser.parse_args(argv)
    try:
        outcome = asyncio.run(run(args.scope, skip_unreadable=args.skip_unreadable))
    except Exception as exc:  # the CLI reads one JSON line, whatever went wrong
        outcome = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
    print(json.dumps(outcome))  # noqa: T201 - the CLI reads this line
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
