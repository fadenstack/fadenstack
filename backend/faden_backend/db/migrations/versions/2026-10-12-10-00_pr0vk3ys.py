"""Encrypt the API keys of remote providers.

``llm_providers.api_key_encrypted`` held the key as typed (the create path had a
TODO where the encryption belonged), so every database dump carried the keys of
OpenAI, OpenRouter and the like in plain text. This seals each plaintext value
with the settings master key, as ``SettingsCrypto`` does: Fernet under
``sha256(FADEN_BACKEND_SETTINGS_MASTER_KEY)``, no purpose label.

Idempotent: a value that already looks like a Fernet token is left alone, NULL
and empty values stay NULL/empty. An empty master key stops the migration
rather than leaving keys unprotected or sealing them with nothing.

Revision ID: pr0vk3ys
Revises: n0pr0vc4p
"""

from __future__ import annotations

import base64
import hashlib

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet

from faden_backend.settings import settings

revision = "pr0vk3ys"
down_revision = "n0pr0vc4p"
branch_labels = None
depends_on = None

# Every Fernet token starts with this (version byte 0x80 in base64).
_FERNET_PREFIX = "gAAAAA"


def _fernet() -> Fernet:
    master_key = settings.settings_master_key
    if not master_key:
        raise RuntimeError(
            "FADEN_BACKEND_SETTINGS_MASTER_KEY is empty: the provider API keys cannot be encrypted. "
            "Set it (it is the key the console's other secrets use) and run the migration again.",
        )
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(master_key.encode("utf-8")).digest()))


def upgrade() -> None:
    """Encrypt every provider key that is still plaintext."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, api_key_encrypted FROM llm_providers WHERE api_key_encrypted IS NOT NULL"),
    ).fetchall()
    plain = [(row.id, row.api_key_encrypted) for row in rows if row.api_key_encrypted]
    plain = [(pid, key) for pid, key in plain if not key.startswith(_FERNET_PREFIX)]
    if not plain:
        return
    fernet = _fernet()
    for provider_id, key in plain:
        bind.execute(
            sa.text("UPDATE llm_providers SET api_key_encrypted = :key WHERE id = :id"),
            {"key": fernet.encrypt(key.encode("utf-8")).decode("ascii"), "id": provider_id},
        )


def downgrade() -> None:
    """Nothing to undo: the keys stay encrypted.

    Older code wrote and sent the key as typed, so after a downgrade remote
    providers fail to authenticate until their keys are entered again. Decrypting
    here would put the secrets back into the database and its backups, so the
    downgrade does not.
    """
