"""Re-seal the backend database's secrets with the current settings master key.

Run by ``faden secrets rotate`` from the backend image, with the new key in
``FADEN_BACKEND_SETTINGS_MASTER_KEY`` and the old one in ``FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS``::

    python -m faden_backend.tools.rotate_keys [--skip-unreadable]

The last line it prints is JSON: ``{"ok": true, "columns": {...counts...}}``, or ``{"ok": false, "error":
...}`` with exit code 1. It prints counts and column names, never a value.

Everything sealed with the master key is here, so a rotation leaves nothing behind that only the old key
opens: the secrets the console stores (``system_setting_secret``: the Hugging Face token, the TLS key
material, the RAG module's secrets, the sign-in secret, the Ray cluster tokens), the remote providers' API
keys, and the private keys of the machine CA, the gateway's machine certificate and each cluster's Ray CA.
The machine credential hashes are not here: they are keyed with the pepper, which a rotation never changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from faden_backend.services.machine_tls import authority
from faden_backend.services.machine_tls.ray_ca import KEY_PURPOSE as RAY_CA_PURPOSE
from faden_backend.services.system_settings.crypto import fernet_for
from faden_backend.settings import settings
from faden_backend.tools.key_rotation import RotationError, Scheme, Target, rotate


def targets(current: str, previous: str = "") -> list[Target]:
    """Every column of the backend database that is sealed with the settings master key."""
    old = previous if previous and previous != current else ""

    def labelled(purpose: str) -> Scheme:
        return Scheme(
            authority.sealing_fernet(current, purpose),
            authority.sealing_fernet(old, purpose) if old else None,
        )

    plain = Scheme(fernet_for(current), fernet_for(old) if old else None)
    return [
        Target("system_setting_secret", ("key",), "ciphertext", plain),
        # Pre-migration rows may still hold the key as typed; the readers accept that.
        Target("llm_providers", ("id",), "api_key_encrypted", plain, plaintext_ok=True),
        Target("machine_ca", ("id",), "key_sealed", labelled(authority.KEY_PURPOSE_CA)),
        Target("machine_tls_client", ("name",), "key_sealed", labelled(authority.KEY_PURPOSE_CLIENT)),
        Target("ray_cluster_ca", ("environment_id",), "key_sealed", labelled(RAY_CA_PURPOSE)),
        # Written by the enterprise sign-in providers, not by core: sealed as the settings crypto does when
        # it is theirs to open, left alone when it is not.
        Target("auth_providers", ("id",), "client_secret_encrypted", plain, plaintext_ok=True, optional=True),
    ]


def _engine() -> AsyncEngine:
    return create_async_engine(str(settings.db_url))


async def run(*, skip_unreadable: bool, engine: AsyncEngine | None = None) -> dict:
    """The job: re-seal, verify, commit. Returns the JSON document the command prints."""
    if not settings.settings_master_key:
        return {"ok": False, "error": "FADEN_BACKEND_SETTINGS_MASTER_KEY is empty."}
    own = engine is None
    engine = engine or _engine()
    try:
        report = await rotate(
            engine,
            targets(settings.settings_master_key, settings.settings_master_key_previous),
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
    parser = argparse.ArgumentParser(prog="python -m faden_backend.tools.rotate_keys", description=__doc__)
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
