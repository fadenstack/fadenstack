"""File store abstraction for chat attachment storage.

Ships with ``LocalFileStore`` — files on the local filesystem at a
configurable root path.  The ``FileStore`` protocol enables alternative
backends (e.g. MinIO) without changing consumers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiofiles
import aiofiles.os
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

#: How a sealed file starts; a file without it is from before files were sealed and is read as it is.
SEALED_MAGIC = b"FDNENC1"
_NONCE = 12


class FileSealError(ValueError):
    """A sealed file that no key the gateway holds opens."""


def seal_bytes(data: bytes, key: bytes) -> bytes:
    """*data* sealed with AES-256-GCM under *key*: magic, nonce, ciphertext and tag."""
    nonce = os.urandom(_NONCE)
    return SEALED_MAGIC + nonce + AESGCM(key).encrypt(nonce, data, SEALED_MAGIC)


def is_sealed(raw: bytes) -> bool:
    """Whether *raw* is a sealed file."""
    return raw.startswith(SEALED_MAGIC)


def open_bytes(raw: bytes, keys: list[bytes]) -> bytes:
    """What :func:`seal_bytes` sealed, with the first of *keys* that opens it; a plain file as it is."""
    if not is_sealed(raw):
        return raw
    nonce = raw[len(SEALED_MAGIC):len(SEALED_MAGIC) + _NONCE]
    body = raw[len(SEALED_MAGIC) + _NONCE:]
    for key in keys:
        try:
            return AESGCM(key).decrypt(nonce, body, SEALED_MAGIC)
        except InvalidTag:
            pass  # sealed with another key: try the next one
    raise FileSealError("The file is sealed with a key the gateway does not hold.")


@runtime_checkable
class FileStore(Protocol):
    """Minimal async file storage interface."""

    async def put_bytes(self, key: str, data: bytes) -> str:
        """Store *data* under *key*.  Returns the key."""
        ...

    async def get_bytes(self, key: str) -> bytes:
        """Retrieve previously stored bytes."""
        ...

    async def delete(self, key: str) -> bool:
        """Delete a stored object.  Returns ``True`` if it existed."""
        ...

    async def exists(self, key: str) -> bool:
        """Check whether *key* exists."""
        ...


class LocalFileStore:
    """Store files on the local filesystem under *root*."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _resolve(self, key: str) -> Path:
        safe = Path(key)
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError(f"Invalid key: {key}")
        return self.root / safe

    async def put_bytes(self, key: str, data: bytes) -> str:
        path = self._resolve(key)
        await aiofiles.os.makedirs(path.parent, exist_ok=True)
        async with aiofiles.open(path, "wb") as fh:
            await fh.write(data)
        log.debug("Stored %d bytes at %s", len(data), path)
        return key

    async def get_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        async with aiofiles.open(path, "rb") as fh:
            return await fh.read()

    async def delete(self, key: str) -> bool:
        path = self._resolve(key)
        if not path.exists():
            return False
        await aiofiles.os.remove(path)
        parent = path.parent
        while parent != self.root:
            try:
                os.rmdir(parent)
            except OSError:
                break
            parent = parent.parent
        return True

    async def exists(self, key: str) -> bool:
        path = self._resolve(key)
        return await aiofiles.os.path.exists(path)


class SealedFileStore:
    """A file store whose files are sealed at rest with the chat key (purpose ``chat-files``).

    Attachments and pictures were the one part of a chat stored readable: the messages, titles and the text
    read from files were sealed, the files themselves not. Files from before are read as they are and sealed
    by ``faden secrets seal``; while the chat key is off (development), files are stored as they are.
    """

    def __init__(self, inner: FileStore) -> None:
        self.inner = inner

    async def put_bytes(self, key: str, data: bytes) -> str:
        from faden_gateway.db.crypto import file_keys  # noqa: PLC0415 - crypto is configured at start

        keys = file_keys()
        return await self.inner.put_bytes(key, seal_bytes(data, keys[0]) if keys else data)

    async def get_bytes(self, key: str) -> bytes:
        from faden_gateway.db.crypto import file_keys  # noqa: PLC0415

        return open_bytes(await self.inner.get_bytes(key), file_keys())

    async def delete(self, key: str) -> bool:
        return await self.inner.delete(key)

    async def exists(self, key: str) -> bool:
        return await self.inner.exists(key)
