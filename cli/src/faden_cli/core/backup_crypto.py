"""Encrypted backups: every file of a backup encrypted to the install's backup key.

The backups are made with the public key alone, which stays on the server, so the nightly backup runs
unattended. The private key, the recovery key, is shown once when it is made and kept off the server
(``faden backup key``, :mod:`faden_cli.core.backup_key`). Without it nobody opens a backup: neither someone
holding the backup disk nor an administrator of this server.

Each file is encrypted the way age does it, with ``cryptography``:

- an X25519 key of its own; the file's key is HKDF-SHA256 over the shared secret, salted with both public
  keys;
- the content in 64 KiB chunks, ChaCha20-Poly1305, the nonce an 11-byte counter and a last-chunk flag: a
  changed, cut-off or reordered file does not open;
- a header: :data:`MAGIC`, the recipient's id (8 bytes of the SHA-256 of its public key, to say which key
  opens it), the file's public key.

A backup made before a key existed stays as it is, and is restored as before.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from faden_cli.core.private_files import open_private, private_dir, write_private

MAGIC = b"FADENSTACK-BACKUP-1\n"
#: Added to each encrypted file's name.
SUFFIX = ".fenc"
#: What an encrypted backup says of itself, in the clear: what it is, when, and which key opens it.
INDEX = "backup.json"
FORMAT = "fadenstack-backup-encrypted-1"
#: Where a restore opens an encrypted backup, inside it (owner-only), removed when the restore ends.
OPENED = ".opened"
_CHUNK = 64 * 1024
_TAG = 16
_INFO = b"fadenstack-backup-v1"
_ID_BYTES = 8

# ── Keys as text: Bech32 (BIP-173), whose checksum catches a mistyped copy ──

SECRET_HRP = "faden-backup-key"
PUBLIC_HRP = "faden-backup-pub"
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_GENERATOR = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)


class KeyTextError(ValueError):
    """A key's text is not a backup key: mistyped, cut short, or another kind of key."""


def _polymod(values: list[int]) -> int:
    check = 1
    for value in values:
        top = check >> 25
        check = (check & 0x1FFFFFF) << 5 ^ value
        for i, generator in enumerate(_GENERATOR):
            if (top >> i) & 1:
                check ^= generator
    return check


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _regroup(data: bytes | list[int], source: int, target: int, *, pad: bool) -> list[int]:
    acc = bits = 0
    out: list[int] = []
    top = (1 << target) - 1
    for value in data:
        acc = (acc << source) | value
        bits += source
        while bits >= target:
            bits -= target
            out.append((acc >> bits) & top)
    if pad and bits:
        out.append((acc << (target - bits)) & top)
    elif not pad and (bits >= source or (acc << (target - bits)) & top):
        raise KeyTextError("the key's last characters do not fit")
    return out


def encode(hrp: str, raw: bytes) -> str:
    """*raw* as Bech32 text under *hrp*, upper case."""
    data = _regroup(raw, 8, 5, pad=True)
    polymod = _polymod([*_hrp_expand(hrp), *data, 0, 0, 0, 0, 0, 0]) ^ 1
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return (hrp + "1" + "".join(_CHARSET[d] for d in data + checksum)).upper()


def decode(hrp: str, text: str) -> bytes:
    """The bytes of the Bech32 text *text* under *hrp*; spaces, dashes in groups and case do not matter."""
    flat = "".join(text.split()).lower()
    prefix = hrp + "1"
    if not flat.startswith(prefix):
        raise KeyTextError(f"a key of this kind starts with {prefix.upper()}")
    body = flat[len(prefix):].replace("-", "")
    if any(c not in _CHARSET for c in body) or len(body) < 7:  # noqa: PLR2004
        raise KeyTextError("the key holds characters a key never has (look for 1, b, i or o)")
    data = [_CHARSET.index(c) for c in body]
    if _polymod([*_hrp_expand(hrp), *data]) != 1:
        raise KeyTextError("the key's checksum does not match: a character is wrong or missing")
    return bytes(_regroup(data[:-6], 5, 8, pad=False))


# ── The key pair ──


@dataclass(frozen=True)
class RecoveryKey:
    """A backup key pair; :attr:`secret` is the recovery key's text."""

    private: X25519PrivateKey

    @classmethod
    def generate(cls) -> RecoveryKey:
        """A new key pair."""
        return cls(X25519PrivateKey.generate())

    @classmethod
    def from_text(cls, text: str) -> RecoveryKey:
        """The key in *text*: as shown, in groups or not, with or without its ``FADEN-BACKUP-KEY1`` start."""
        flat = "".join(text.split()).lower()
        if not flat.startswith(SECRET_HRP + "1"):
            flat = SECRET_HRP + "1" + flat
        raw = decode(SECRET_HRP, flat)
        if len(raw) != 32:  # noqa: PLR2004
            raise KeyTextError("the key is not 32 bytes long")
        return cls(X25519PrivateKey.from_private_bytes(raw))

    @property
    def secret(self) -> str:
        """The recovery key's text."""
        return encode(SECRET_HRP, self.private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))

    @property
    def public(self) -> str:
        """The public key's text."""
        return public_text(self.private.public_key())

    @property
    def id(self) -> str:
        """The key's short name, as backups record it."""
        return key_id(self.private.public_key())


def public_text(key: X25519PublicKey) -> str:
    """The public key as text, as ``backup-key.pub`` holds it."""
    return encode(PUBLIC_HRP, key.public_bytes(Encoding.Raw, PublicFormat.Raw))


def public_key(text: str) -> X25519PublicKey:
    """The public key in *text*."""
    raw = decode(PUBLIC_HRP, text)
    if len(raw) != 32:  # noqa: PLR2004
        raise KeyTextError("the public key is not 32 bytes long")
    return X25519PublicKey.from_public_bytes(raw)


def key_id(key: X25519PublicKey) -> str:
    """A short name for a public key: which key opens a backup, without the key."""
    return hashlib.sha256(key.public_bytes(Encoding.Raw, PublicFormat.Raw)).hexdigest()[: _ID_BYTES * 2]


def groups(secret: str) -> tuple[str, list[str]]:
    """The recovery key's prefix and the rest in groups of five characters, as it is shown and asked for."""
    prefix, _, body = secret.partition("1")
    return prefix + "1", [body[i : i + 5] for i in range(0, len(body), 5)]


# ── One file ──


def _file_key(shared: bytes, ephemeral: bytes, recipient: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=ephemeral + recipient, info=_INFO).derive(shared)


def _nonce(counter: int, *, last: bool) -> bytes:
    return counter.to_bytes(11, "big") + (b"\x01" if last else b"\x00")


def _chunks(source: BinaryIO) -> Iterator[tuple[bytes, bool]]:
    """The content in chunks, each with whether it is the last (an empty file is one empty last chunk)."""
    current = source.read(_CHUNK)
    while True:
        following = source.read(_CHUNK)
        yield current, not following
        if not following:
            return
        current = following


def encrypt_stream(source: BinaryIO, target: BinaryIO, recipient: X25519PublicKey) -> None:
    """Encrypt *source* to *recipient* into *target*."""
    ephemeral = X25519PrivateKey.generate()
    mine = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    theirs = recipient.public_bytes(Encoding.Raw, PublicFormat.Raw)
    aead = ChaCha20Poly1305(_file_key(ephemeral.exchange(recipient), mine, theirs))
    target.write(MAGIC + bytes.fromhex(key_id(recipient)) + mine)
    for counter, (chunk, last) in enumerate(_chunks(source)):
        target.write(aead.encrypt(_nonce(counter, last=last), chunk, None))


class OpenError(RuntimeError):
    """An encrypted file does not open: another key, or the file was changed or cut short."""


def recipient_of(path: Path) -> str:
    """The id of the key an encrypted file was made for."""
    with path.open("rb") as handle:
        head = handle.read(len(MAGIC) + _ID_BYTES)
    if not head.startswith(MAGIC) or len(head) < len(MAGIC) + _ID_BYTES:
        raise OpenError(f"{path.name} is not an encrypted backup file")
    return head[len(MAGIC) :].hex()


def decrypt_stream(source: BinaryIO, target: BinaryIO, key: RecoveryKey) -> None:
    """Decrypt *source* with *key* into *target*; :class:`OpenError` when it does not open."""
    head = source.read(len(MAGIC) + _ID_BYTES + 32)
    if len(head) < len(MAGIC) + _ID_BYTES + 32 or not head.startswith(MAGIC):
        raise OpenError("not an encrypted backup file")
    wanted = head[len(MAGIC) : len(MAGIC) + _ID_BYTES].hex()
    public = key.private.public_key()
    if wanted != key_id(public):
        raise OpenError(f"made for another key ({wanted}); this key is {key_id(public)}")
    mine = head[-32:]
    shared = key.private.exchange(X25519PublicKey.from_public_bytes(mine))
    aead = ChaCha20Poly1305(_file_key(shared, mine, public.public_bytes(Encoding.Raw, PublicFormat.Raw)))
    counter = 0
    sealed = source.read(_CHUNK + _TAG)
    while True:
        following = source.read(_CHUNK + _TAG)
        try:
            target.write(aead.decrypt(_nonce(counter, last=not following), sealed, None))
        except InvalidTag:
            raise OpenError("changed or cut short") from None
        if not following:
            return
        sealed, counter = following, counter + 1


# ── A whole backup ──


def is_encrypted(backup_dir: Path) -> bool:
    """Whether *backup_dir* is an encrypted backup."""
    return (backup_dir / INDEX).is_file()


def read_index(backup_dir: Path) -> dict | None:
    """What an encrypted backup says of itself in the clear, or None for an unencrypted one."""
    try:
        index = json.loads((backup_dir / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return index if isinstance(index, dict) else None


def encrypt_backup(backup_dir: Path, recipient: X25519PublicKey, *, kind: str) -> list[str]:
    """Encrypt every file of the finished backup *backup_dir* in place; the files encrypted, by path.

    One file at a time, the plain file removed once its encrypted copy is written: the backup never needs
    twice its size.
    """
    done: list[str] = []
    for path in sorted(p for p in backup_dir.rglob("*") if p.is_file() and not p.name.endswith(SUFFIX)):
        sealed = path.with_name(path.name + SUFFIX)
        with path.open("rb") as source, open_private(sealed) as target:
            encrypt_stream(source, target, recipient)
        path.unlink()
        done.append(path.relative_to(backup_dir).as_posix())
    index = {
        "format": FORMAT,
        "kind": kind,
        "created_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "key_id": key_id(recipient),
        "files": len(done),
    }
    write_private(backup_dir / INDEX, (json.dumps(index, indent=2) + "\n").encode("utf-8"))
    return done


def open_backup(backup_dir: Path, key: RecoveryKey) -> Path:
    """Decrypt the encrypted backup *backup_dir* into an owner-only folder inside it, and return that folder.

    The caller removes it with :func:`close_backup` when the restore ends. One left by a restore that did
    not end is removed first.
    """
    index = read_index(backup_dir) or {}
    wanted = str(index.get("key_id") or "")
    if wanted and wanted != key_id(key.private.public_key()):
        raise OpenError(
            f"This backup was encrypted to the key {wanted}; the key given is {key_id(key.private.public_key())}."
        )
    opened = backup_dir / OPENED
    close_backup(backup_dir)
    private_dir(opened)
    try:
        sealed_files = (p for p in backup_dir.rglob("*" + SUFFIX) if OPENED not in p.relative_to(backup_dir).parts)
        for sealed in sorted(sealed_files):
            rel = sealed.relative_to(backup_dir)
            plain = opened / rel.with_name(rel.name[: -len(SUFFIX)])
            private_dir(plain.parent)
            with sealed.open("rb") as source, open_private(plain) as target:
                try:
                    decrypt_stream(source, target, key)
                except OpenError as exc:
                    raise OpenError(f"{rel.as_posix()}: {exc}") from None
    except BaseException:
        close_backup(backup_dir)
        raise
    return opened


def close_backup(backup_dir: Path) -> None:
    """Remove the decrypted copy a restore made in *backup_dir*."""
    opened = backup_dir / OPENED
    if opened.exists():
        shutil.rmtree(opened, ignore_errors=os.name == "nt")
