"""Column-level encryption for sensitive MCP configuration data.

Provides EncryptedText and EncryptedJSON TypeDecorators that encrypt
values at rest using Fernet with HKDF-derived per-purpose subkeys.

Mirrors the pattern from faden_gateway.db.crypto.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

_key_provider: Callable[[str], bytes] | None = None
# The key from before `faden secrets rotate`, while a rotation is unfinished.
_previous_provider: Callable[[str], bytes] | None = None
_FERNET_PREFIX = b"gAAAAA"


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

    *previous_key* is the key from before a rotation (``faden secrets rotate``): what it sealed still
    opens, and everything new is sealed with *master_key*.
    """
    if not master_key:
        logger.warning(
            "ENCRYPTION_KEY is not set — MCP secrets stored in plaintext. "
            "Set FADEN_MCP_ENCRYPTION_KEY in production.",
        )
        return

    previous = previous_key if previous_key and previous_key != master_key else ""
    set_key_provider(
        lambda purpose: derive_key(master_key, purpose),
        previous=(lambda purpose: derive_key(previous, purpose)) if previous else None,
    )


def set_key_provider(provider: Callable[[str], bytes], previous: Callable[[str], bytes] | None = None) -> None:
    """Replace the key provider."""
    global _key_provider, _previous_provider  # noqa: PLW0603
    _key_provider = provider
    _previous_provider = previous


def _fernet_for(purpose: str) -> Fernet | MultiFernet | None:
    if _key_provider is None:
        return None
    current = Fernet(base64.urlsafe_b64encode(_key_provider(purpose)))
    if _previous_provider is None:
        return current
    return MultiFernet([current, Fernet(base64.urlsafe_b64encode(_previous_provider(purpose)))])


def _is_ciphertext(value: str) -> bool:
    try:
        return value.encode("ascii")[:6] == _FERNET_PREFIX
    except (UnicodeEncodeError, AttributeError):
        return False


class EncryptedText(TypeDecorator):
    """A Text column whose value is Fernet-encrypted at rest."""

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
            return value
        return f.encrypt(value.encode("utf-8")).decode("ascii")

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        f = _fernet_for(self._purpose)
        if f is None or not _is_ciphertext(value):
            return value
        try:
            return f.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.warning("Failed to decrypt EncryptedText — returning raw value")
            return value


class EncryptedJSON(TypeDecorator):
    """A Text column storing JSON encrypted at rest."""

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
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True)
        f = _fernet_for(self._purpose)
        if f is None:
            return raw
        return f.encrypt(raw.encode("utf-8")).decode("ascii")

    def process_result_value(
        self,
        value: str | None,
        dialect: Any,
    ) -> dict[str, Any] | list[Any] | None:
        if value is None:
            return None
        f = _fernet_for(self._purpose)
        if f is not None and _is_ciphertext(value):
            try:
                value = f.decrypt(value.encode("ascii")).decode("utf-8")
            except InvalidToken:
                logger.warning(
                    "Failed to decrypt EncryptedJSON — attempting JSON parse",
                )
        try:
            return json.loads(value)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            logger.warning("Failed to parse EncryptedJSON — returning None")
            return None
