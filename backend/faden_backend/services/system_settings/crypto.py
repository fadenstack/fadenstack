"""Secret encryption helpers for system settings."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

_FERNET_PREFIX = "gAAAAA"


def fernet_for(master_key: str) -> Fernet:
    """The Fernet ``SettingsCrypto`` derives from *master_key*: sha256, no purpose label."""
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class SettingsCrypto:
    """Encrypt/decrypt helper based on Fernet symmetric crypto.

    *previous_key* is the master key from before a rotation (``faden secrets rotate``): a value sealed
    with either key opens, and everything new is sealed with *master_key*.
    """

    def __init__(self, master_key: str, previous_key: str = "") -> None:
        keys = [fernet_for(master_key)]
        if previous_key and previous_key != master_key:
            keys.append(fernet_for(previous_key))
        self._fernet = MultiFernet(keys)
        self._current_only = keys[0]

    @classmethod
    def from_settings(cls) -> SettingsCrypto:
        """The helper for this server's master key, and the previous one while a rotation is unfinished."""
        from faden_backend.settings import settings  # noqa: PLC0415

        return cls(settings.settings_master_key, settings.settings_master_key_previous)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string into opaque ciphertext."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext into plaintext string."""
        return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    def rotate(self, ciphertext: str) -> str:
        """*ciphertext* sealed again with the current key; it may have been sealed with either."""
        return self._fernet.rotate(ciphertext.encode("utf-8")).decode("utf-8")

    def opens_with_current_key(self, ciphertext: str) -> bool:
        """Whether the current key alone opens *ciphertext*: what a rotation checks before it commits."""
        try:
            self._current_only.decrypt(ciphertext.encode("utf-8"))
        except (InvalidToken, ValueError):
            return False
        return True

    @staticmethod
    def is_ciphertext(value: str | None) -> bool:
        """Whether *value* looks like a Fernet token (they all start ``gAAAAA``).

        Lets a migration or a sync tell sealed values from legacy plaintext
        without trying to decrypt. API keys never start this way.
        """
        return bool(value) and value.startswith(_FERNET_PREFIX)

    @staticmethod
    def mask(value: str) -> str:
        """Return a non-sensitive preview of a secret value."""
        if not value:
            return ""
        if len(value) <= 6:
            return "*" * len(value)
        return f"{value[:2]}***{value[-2:]}"
