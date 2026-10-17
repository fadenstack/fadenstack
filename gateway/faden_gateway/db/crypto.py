"""Column-level encryption for sensitive chat data.

Provides two SQLAlchemy ``TypeDecorator`` types:

* **EncryptedText** – encrypts ``str`` values before INSERT/UPDATE and
  decrypts after SELECT.  Stored in the database as ``Text`` containing
  Fernet ciphertext (URL-safe base64).
* **EncryptedJSON** – serializes a Python ``dict`` / ``list`` to a JSON
  string, encrypts, and stores the ciphertext as ``Text``.  On read the
  ciphertext is decrypted and deserialized back to a Python object.

Key management
--------------
A single master key (``FADEN_GATEWAY_ENCRYPTION_KEY``) is stretched via
HKDF-SHA256 into independent per-purpose subkeys.  Each column declares
a *purpose* label (e.g. ``"chat-content"``, ``"chat-memory"``) so that
compromise of one derived key cannot decrypt columns tagged with a
different purpose.

Call :func:`configure` once at application startup (in ``lifespan.py``)
to activate encryption.  When the master key is empty, encryption is
**disabled** and values pass through as plaintext — this is intentional
for local development and to support brownfield migration (existing
plaintext rows are readable while new rows are encrypted).

EE extension point
------------------
Enterprise Edition can replace the key-provider by calling
:func:`set_key_provider` with a callable that, given a *purpose* string,
returns raw 32-byte key material (e.g. from HashiCorp Vault Transit).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

# Module state ----------------------------------------------------------

_key_provider: Callable[[str], bytes] | None = None
"""Return 32 bytes of key material for a given *purpose* label."""

_previous_provider: Callable[[str], bytes] | None = None
"""The same for the key from before ``faden secrets rotate``, while a rotation is unfinished."""

_FERNET_PREFIX = b"gAAAAA"
"""All Fernet tokens start with this prefix (version byte 0x80 → 'gA')."""


# Public API ------------------------------------------------------------


def derive_key(master_key: str, purpose: str) -> bytes:
    """The 32 bytes of key material HKDF-SHA256 derives from *master_key* for *purpose*."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=purpose.encode("utf-8"),
    ).derive(master_key.encode("utf-8"))


def derive_fernet(master_key: str, purpose: str) -> Fernet:
    """The Fernet for *purpose* under *master_key*."""
    return Fernet(base64.urlsafe_b64encode(derive_key(master_key, purpose)))


def configure(master_key: str, previous_key: str = "") -> None:
    """Derive per-purpose encryption keys from *master_key* via HKDF.

    Must be called once during app startup.  If *master_key* is empty,
    encryption is disabled (plaintext passthrough).

    *previous_key* is the key from before a rotation (``faden secrets rotate``): what it sealed still
    opens, and everything new is sealed with *master_key*.
    """
    if not master_key:
        logger.warning(
            "ENCRYPTION_KEY is not set — chat data stored in plaintext. "
            "Set FADEN_GATEWAY_ENCRYPTION_KEY in production.",
        )
        return

    previous = previous_key if previous_key and previous_key != master_key else ""
    set_key_provider(
        lambda purpose: derive_key(master_key, purpose),
        previous=(lambda purpose: derive_key(previous, purpose)) if previous else None,
    )


def set_key_provider(provider: Callable[[str], bytes], previous: Callable[[str], bytes] | None = None) -> None:
    """Replace the key provider (used by EE Vault integration)."""
    global _key_provider, _previous_provider  # noqa: PLW0603
    _key_provider = provider
    _previous_provider = previous


def _fernet_for(purpose: str) -> Fernet | MultiFernet | None:
    """Return a Fernet instance keyed for *purpose* (opening the previous key's values too), or ``None``."""
    if _key_provider is None:
        return None
    current = Fernet(base64.urlsafe_b64encode(_key_provider(purpose)))
    if _previous_provider is None:
        return current
    return MultiFernet([current, Fernet(base64.urlsafe_b64encode(_previous_provider(purpose)))])


def seal(purpose: str, data: bytes) -> bytes | None:
    """*data* encrypted with *purpose*'s key, or ``None`` while encryption is off.

    For chat text kept outside the database -- a reply's events in Redis --
    so it is no plainer there than in the columns.
    """
    f = _fernet_for(purpose)
    return None if f is None else f.encrypt(data)


def unseal(purpose: str, token: bytes) -> bytes:
    """What :func:`seal` encrypted. Raises ``InvalidToken`` for another key, or with encryption off."""
    f = _fernet_for(purpose)
    if f is None:
        raise InvalidToken
    return f.decrypt(token)


def file_keys(purpose: str = "chat-files") -> list[bytes]:
    """The 32-byte keys files are sealed with: the current one first, then the previous one; none when off."""
    if _key_provider is None:
        return []
    keys = [_key_provider(purpose)]
    if _previous_provider is not None:
        keys.append(_previous_provider(purpose))
    return keys


def _is_ciphertext(value: str) -> bool:
    """Heuristic: does *value* look like a Fernet token?"""
    try:
        return value.encode("ascii")[:6] == _FERNET_PREFIX
    except (UnicodeEncodeError, AttributeError):
        return False


# TypeDecorators --------------------------------------------------------


class EncryptedText(TypeDecorator):
    """A ``Text`` column whose value is Fernet-encrypted at rest.

    Usage::

        content = mapped_column(EncryptedText("chat-content"), nullable=False)
    """

    impl = Text
    cache_ok = True

    def __init__(self, purpose: str = "default") -> None:
        super().__init__()
        self._purpose = purpose

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        f = _fernet_for(self._purpose)
        if f is None:
            return value  # encryption disabled
        return f.encrypt(value.encode("utf-8")).decode("ascii")

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if not _is_ciphertext(value):
            return value  # plaintext (legacy or encryption disabled)
        f = _fernet_for(self._purpose)
        if f is None:
            return value  # can't decrypt without key
        try:
            return f.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.error("Failed to decrypt column (purpose=%s)", self._purpose)
            raise


class EncryptedJSON(TypeDecorator):
    """A ``Text`` column storing Fernet-encrypted JSON.

    The Python value is a ``dict`` or ``list``.  On bind it is serialized
    to a JSON string and encrypted; on result it is decrypted and
    deserialized.  The database column is ``Text`` (not ``JSON``),
    because ciphertext is not valid JSON.

    Usage::

        tool_call_json = mapped_column(
            EncryptedJSON("chat-content"), nullable=True,
        )
    """

    impl = Text
    cache_ok = True

    def __init__(self, purpose: str = "default") -> None:
        super().__init__()
        self._purpose = purpose

    def process_bind_param(
        self,
        value: dict[str, Any] | list[Any] | None,
        dialect: Any,
    ) -> str | None:
        if value is None:
            return None
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        f = _fernet_for(self._purpose)
        if f is None:
            return raw  # store as plain JSON text
        return f.encrypt(raw.encode("utf-8")).decode("ascii")

    def process_result_value(
        self,
        value: str | None,
        dialect: Any,
    ) -> dict[str, Any] | list[Any] | None:
        if value is None:
            return None
        if _is_ciphertext(value):
            f = _fernet_for(self._purpose)
            if f is None:
                return None  # can't decrypt
            try:
                value = f.decrypt(value.encode("ascii")).decode("utf-8")
            except InvalidToken:
                logger.error("Failed to decrypt JSON column (purpose=%s)", self._purpose)
                raise
        return json.loads(value)


_warned_plaintext: set[str] = set()
"""Fingerprints of plaintext provider keys already warned about."""


def decrypt_provider_key(stored: str) -> str:
    """The API key in ``LLMProviderInstance.api_key_encrypted``.

    The backend seals the key with ``SettingsCrypto`` (Fernet under
    ``sha256(settings master key)``, no HKDF purpose) and the sync copies it
    unchanged, so it is opened with that same key -- not with the chat-column
    keys above, whose master key (``FADEN_GATEWAY_ENCRYPTION_KEY``) may be
    empty. ``faden secrets rotate`` finds these values by the column and this key.

    A value that is not a Fernet token is a key from before the migration
    ``pr0vk3yg`` sealed the column. It is still used, so a gateway started a
    moment before its migration, or an old row, does not drop auth, but each
    one is warned about once. Ciphertext that cannot be opened raises.
    """
    if not _is_ciphertext(stored):
        fingerprint = hashlib.sha256(stored.encode("utf-8")).hexdigest()[:12]
        if fingerprint not in _warned_plaintext:
            _warned_plaintext.add(fingerprint)
            logger.warning(
                "A provider API key is stored unencrypted (fingerprint %s); "
                "run the gateway migrations or republish the provider from the backend.",
                fingerprint,
            )
        return stored
    from faden_gateway.settings import settings  # noqa: PLC0415

    if not settings.settings_master_key:
        raise InvalidToken("FADEN_GATEWAY_SETTINGS_MASTER_KEY is empty; cannot decrypt the provider API key")
    return settings_fernet().decrypt(stored.encode("ascii")).decode("utf-8")


def settings_fernet_for(master_key: str) -> Fernet:
    """The Fernet the backend's ``SettingsCrypto`` derives from *master_key*: sha256, no purpose label."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(master_key.encode("utf-8")).digest()))


def settings_fernet() -> MultiFernet:
    """Opens what the backend sealed with the settings master key, or with the previous one while a rotation is unfinished."""
    from faden_gateway.settings import settings  # noqa: PLC0415

    keys = [settings_fernet_for(settings.settings_master_key)]
    previous = settings.settings_master_key_previous
    if previous and previous != settings.settings_master_key:
        keys.append(settings_fernet_for(previous))
    return MultiFernet(keys)
