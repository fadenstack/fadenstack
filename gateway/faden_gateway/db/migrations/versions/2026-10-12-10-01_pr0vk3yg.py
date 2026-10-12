"""Encrypt the API keys of provider instances.

The backend copied each remote provider's key into ``llm_provider_instance`` as
typed, so the gateway database and its backups held them in plain text. This
seals every plaintext value with the settings master key (the same Fernet key
``SettingsCrypto`` derives in the backend, which the gateway already shares as
``FADEN_GATEWAY_SETTINGS_MASTER_KEY``), so ``decrypt_provider_key`` opens it.

Idempotent: values that already look like Fernet tokens are left alone, NULL and
empty values stay as they are. An empty master key stops the migration rather
than leaving the keys in plain text.

Revision ID: pr0vk3yg
Revises: n0pr0jc4p
"""

from __future__ import annotations

import base64
import hashlib

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet

from faden_gateway.settings import settings

revision = "pr0vk3yg"
down_revision = "n0pr0jc4p"
branch_labels = None
depends_on = None

# Every Fernet token starts with this (version byte 0x80 in base64).
_FERNET_PREFIX = "gAAAAA"


def upgrade() -> None:
    """Encrypt every provider instance key that is still plaintext."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, api_key_encrypted FROM llm_provider_instance WHERE api_key_encrypted IS NOT NULL"),
    ).fetchall()
    plain = [
        (row.id, row.api_key_encrypted)
        for row in rows
        if row.api_key_encrypted and not row.api_key_encrypted.startswith(_FERNET_PREFIX)
    ]
    if not plain:
        return
    master_key = settings.settings_master_key
    if not master_key:
        raise RuntimeError(
            "FADEN_GATEWAY_SETTINGS_MASTER_KEY is empty: the provider API keys cannot be encrypted. "
            "Set it (it must equal the backend's FADEN_BACKEND_SETTINGS_MASTER_KEY) and run the migration again.",
        )
    fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(master_key.encode("utf-8")).digest()))
    for instance_id, key in plain:
        bind.execute(
            sa.text("UPDATE llm_provider_instance SET api_key_encrypted = :key WHERE id = :id"),
            {"key": fernet.encrypt(key.encode("utf-8")).decode("ascii"), "id": instance_id},
        )


def downgrade() -> None:
    """Nothing to undo: the keys stay encrypted.

    Older gateways send the stored text as the key, so after a downgrade remote
    providers fail to authenticate until the backend publishes them again with
    keys entered anew. Decrypting here would put the secrets back into the
    database and its backups, so the downgrade does not.
    """
