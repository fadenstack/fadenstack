"""Sealing of remote providers' API keys.

A key is stored as ``SettingsCrypto`` ciphertext -- Fernet under the key
``sha256(FADEN_BACKEND_SETTINGS_MASTER_KEY)``, no purpose label -- in
``llm_providers.api_key_encrypted`` (backend DB) and, copied unchanged by
``gateway_sync``, in ``llm_provider_instance.api_key_encrypted`` (gateway DB).
The gateway holds the same master key (``FADEN_GATEWAY_SETTINGS_MASTER_KEY``,
the compose file feeds it from the backend's) and opens the value with it.

For ``faden secrets rotate``: both columns are sealed with the settings master
key, not with ``FADEN_GATEWAY_ENCRYPTION_KEY`` (which may be empty and only
protects chat columns). Rotation re-encrypts them with the new master key.
"""

from __future__ import annotations

from faden_backend.services.system_settings.crypto import SettingsCrypto
from faden_backend.settings import settings


class ProviderKeyError(RuntimeError):
    """A provider key could not be sealed or opened. Never answered with plaintext."""


def _crypto() -> SettingsCrypto:
    if not settings.settings_master_key:
        raise ProviderKeyError(
            "FADEN_BACKEND_SETTINGS_MASTER_KEY is empty: a provider API key cannot be stored without it.",
        )
    return SettingsCrypto(settings.settings_master_key)


def seal_provider_key(api_key: str | None) -> str | None:
    """*api_key* as it is stored; ``None`` for no key (empty counts as none)."""
    if not api_key:
        return None
    if SettingsCrypto.is_ciphertext(api_key):
        # Sealing twice would make the key unreadable. Real keys never look like this.
        raise ProviderKeyError("The API key looks like an encrypted value; refusing to seal it again.")
    return _crypto().encrypt(api_key)


def open_provider_key(stored: str | None) -> str | None:
    """The key a stored value holds; raises when it is not sealed with this installation's key."""
    if not stored:
        return None
    if not SettingsCrypto.is_ciphertext(stored):
        raise ProviderKeyError("A provider API key is stored unencrypted; run the database migrations.")
    try:
        return _crypto().decrypt(stored)
    except Exception as exc:  # noqa: BLE001
        raise ProviderKeyError("A provider API key cannot be decrypted with the settings master key.") from exc
