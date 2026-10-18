"""The provenance engine's keys: the certificate it signs C2PA manifests with, and the keys of its hashes.

``faden-provenance`` signs a C2PA manifest onto the pictures the platform generates (EU AI Act Art. 50(2))
and keys the hashes of what it records (HMAC), so a record cannot be found by guessing its content. The
keys are kept in ``.env`` with the install's other secrets, one line each:

* ``FADEN_PROVENANCE_SIGNING_KEY``: the signing key (EC P-256), base64 of its PKCS#8 DER encoding;
* ``FADEN_PROVENANCE_SIGNING_CERTS``: base64 of the PEM chain, the signer's certificate first, then the
  authority that issued it -- the install's own (:mod:`faden_cli.core.tls`), the one HTTPS and the machines
  trust;
* ``FADEN_PROVENANCE_HASH_KEYS``: ``<version>:<base64 of 32 random bytes>``, comma-separated, the highest
  version the current one. Every version ever used stays: what was hashed with it is checked with it.
* ``FADEN_PROVENANCE_WATERMARK_KEYS``: ``<version>:<random whole number below 2^63>``, kept the same way:
  the key of the text watermark the model runtimes put into answers (vLLM's ``--watermark-config``). The
  backend gives the current one to the deployments it starts, the engine checks texts with every version.

``faden`` writes them into ``secrets/`` like the other keys (:mod:`faden_cli.core.secret_files`), and
compose mounts them under ``/run/secrets`` of ``faden-provenance``, and the watermark keys of the backend
too. ``faden deploy`` makes them; ``faden upgrade`` and ``faden up`` make what is missing and issue the
certificate again (a new key with it) when it has fewer than :data:`RENEW_BEFORE_DAYS` days left or its
authority is no longer the install's current one. ``faden secrets rotate`` gives the signer a new key and
certificate and appends a hash-key and a watermark-key version (a deployment takes the new watermark key
when it next starts). A backup carries them in its ``.env``; a restore keeps the versions the install had
beside the backup's, and a ``.env`` written anew keeps every version of the one it replaces
(:func:`keep_versions`).

A signer's certificate needs nothing kept once it is replaced: it travels inside every manifest it signed.
"""

from __future__ import annotations

import base64
import binascii
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptography import x509

SIGNING_KEY = "FADEN_PROVENANCE_SIGNING_KEY"
SIGNING_CERTS = "FADEN_PROVENANCE_SIGNING_CERTS"
HASH_KEYS = "FADEN_PROVENANCE_HASH_KEYS"
WATERMARK_KEYS = "FADEN_PROVENANCE_WATERMARK_KEYS"
#: The signer: replaced together, never one without the other.
SIGNER_KEYS: tuple[str, ...] = (SIGNING_KEY, SIGNING_CERTS)
#: Kept in versions: every version ever used stays, the highest is the current one.
VERSIONED_KEYS: tuple[str, ...] = (HASH_KEYS, WATERMARK_KEYS)
KEYS: tuple[str, ...] = (*SIGNER_KEYS, *VERSIONED_KEYS)
#: What the console calls each versioned key.
LABELS = {HASH_KEYS: "hash key", WATERMARK_KEYS: "text watermark key"}

#: Extended key usages of the signer's certificate: C2PA claim signing, document signing, e-mail protection.
C2PA_CLAIM_SIGNING = "1.3.6.1.4.1.62558.2.1"
DOCUMENT_SIGNING = "1.3.6.1.5.5.7.3.36"
SIGNER_DAYS = 365
#: Issued again this many days before it runs out.
RENEW_BEFORE_DAYS = 30
HASH_KEY_BYTES = 32
#: The watermark keys are whole numbers below this: vLLM seeds the watermark's random function with one.
WATERMARK_KEY_LIMIT = 2**63
#: Where a key line written here goes in .env.
ENV_HEADER = "Provenance keys (faden deploy, upgrade, up)"


class ProvenanceKeyError(ValueError):
    """A provenance key in ``.env`` does not read; the message names the key, never its value."""


# ── Versioned keys: the hash keys and the watermark keys ──────────


def _shape(name: str) -> str:
    return "<version>:<whole number>" if name == WATERMARK_KEYS else "<version>:<base64 key>"


def _check(name: str, number: int, key: str) -> None:
    """Raise :class:`ProvenanceKeyError` when *key*, version *number* of *name*, is not a key of its kind."""
    if name == WATERMARK_KEYS:
        if not key.isdigit() or not 0 < int(key) < WATERMARK_KEY_LIMIT:
            raise ProvenanceKeyError(f"{name} version {number} is not a whole number from 1 to 2^63 - 1.")
        return
    try:
        decoded = base64.b64decode(key, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProvenanceKeyError(f"{name} version {number} is not base64.") from exc
    if not decoded:
        raise ProvenanceKeyError(f"{name} version {number} is empty.")


def parse_versions(value: str, name: str = HASH_KEYS) -> dict[int, str]:
    """The versions in a :data:`HASH_KEYS` or :data:`WATERMARK_KEYS` value (*name*), by number.

    Raises :class:`ProvenanceKeyError` when one does not read.
    """
    versions: dict[int, str] = {}
    for entry in value.split(","):
        if not entry.strip():
            continue
        raw_number, sep, raw_key = entry.partition(":")
        digits, key = raw_number.strip(), raw_key.strip()
        if not sep or not digits.isdigit() or int(digits) < 1 or not key:
            raise ProvenanceKeyError(f"{name} has an entry that is not {_shape(name)}.")
        number = int(digits)
        _check(name, number, key)
        if versions.get(number, key) != key:
            raise ProvenanceKeyError(f"{name} holds version {number} twice, with two different keys.")
        versions[number] = key
    return versions


def parse_hash_keys(value: str) -> dict[int, str]:
    """The versions in a :data:`HASH_KEYS` value, by number."""
    return parse_versions(value, HASH_KEYS)


def parse_watermark_keys(value: str) -> dict[int, str]:
    """The versions in a :data:`WATERMARK_KEYS` value, by number (the keys as their decimal digits)."""
    return parse_versions(value, WATERMARK_KEYS)


def format_versions(versions: dict[int, str]) -> str:
    """*versions* as one ``.env`` value, oldest first."""
    return ",".join(f"{number}:{versions[number]}" for number in sorted(versions))


def new_key(name: str = HASH_KEYS) -> str:
    """A new key for *name*: 32 random bytes in base64; for the watermark, a whole number below 2^63."""
    if name == WATERMARK_KEYS:
        return str(secrets.randbelow(WATERMARK_KEY_LIMIT - 1) + 1)
    return base64.b64encode(secrets.token_bytes(HASH_KEY_BYTES)).decode("ascii")


def new_hash_key() -> str:
    """A new hash key: 32 random bytes, in base64."""
    return new_key(HASH_KEYS)


def add_version(value: str, name: str = HASH_KEYS) -> str:
    """*value* with a new version after its highest: the one used from now on."""
    versions = parse_versions(value, name)
    versions[max(versions, default=0) + 1] = new_key(name)
    return format_versions(versions)


def add_hash_key(value: str) -> str:
    """*value* with a new hash-key version after its highest: the one hashes are made with from now on."""
    return add_version(value, HASH_KEYS)


def merge_versions(first: str, second: str, name: str = HASH_KEYS) -> str:
    """Every version of *first* under its number, then every key of *second* that *first* lacks.

    A key of *second* under a number *first* gives another key is added as a new version after the
    highest: no key is lost. Raises :class:`ProvenanceKeyError` when either does not read.
    """
    merged = parse_versions(first, name)
    known = set(merged.values())
    for number, key in sorted(parse_versions(second, name).items()):
        if key in known:
            continue
        merged[max(merged) + 1 if number in merged else number] = key
        known.add(key)
    return format_versions(merged)


def merge_hash_keys(first: str, second: str) -> str:
    """:func:`merge_versions` of two :data:`HASH_KEYS` values."""
    return merge_versions(first, second, HASH_KEYS)


def keep_versions(kept: str, new: str, name: str = HASH_KEYS) -> str:
    """What a ``.env`` written anew holds as *name*: *new*, with every version of *kept* it lacks.

    When either does not read, *kept* as it was: a value that is not understood is never thrown away.
    """
    if not kept or not new or kept == new:
        return kept or new
    try:
        return merge_versions(new, kept, name)
    except ProvenanceKeyError:
        return kept


# ── The signer ────────────────────────────────────────────────────


def installation_id(ca_cert: x509.Certificate) -> str:
    """What names the installation in the signer's certificate: its authority's key identifier (8 bytes, hex).

    The CLI keeps no other id of an installation, and the authority is the install's own. Never a host
    name, a company or a person.
    """
    from cryptography import x509  # noqa: PLC0415

    return x509.SubjectKeyIdentifier.from_public_key(ca_cert.public_key()).digest[:8].hex()


def issue_signer(install_dir: Path, *, now: datetime | None = None) -> tuple[str, str]:
    """A new signing key and its certificate from the install's authority: the two ``.env`` values.

    ``(FADEN_PROVENANCE_SIGNING_KEY, FADEN_PROVENANCE_SIGNING_CERTS)``. The authority is the one
    :func:`faden_cli.core.tls.ensure_ca` keeps, made when the install has none. Raises
    :class:`faden_cli.core.tls.TlsError` when it cannot be read.
    """
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: PLC0415

    from faden_cli.core import tls  # noqa: PLC0415

    install_dir = Path(install_dir)
    now = now or datetime.now(UTC)
    ca_cert, ca_key, created = tls._load_or_make_ca(install_dir, now)  # noqa: SLF001
    if created:
        # A new or renewed authority: published for the machines, as ensure_ca does.
        tls.publish_trust(install_dir, now=now)
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key()
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Fadenstack"),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Provenance"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Fadenstack provenance signer {installation_id(ca_cert)}"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=SIGNER_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.ObjectIdentifier(C2PA_CLAIM_SIGNING),
                x509.ObjectIdentifier(DOCUMENT_SIGNING),
                ExtendedKeyUsageOID.EMAIL_PROTECTION,
            ]),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key_der = key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    chain = cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM)
    return base64.b64encode(key_der).decode("ascii"), base64.b64encode(chain).decode("ascii")


def signer_certificates(value: str) -> list[x509.Certificate]:
    """The chain in a :data:`SIGNING_CERTS` value, the signer's certificate first."""
    from cryptography import x509  # noqa: PLC0415

    try:
        return x509.load_pem_x509_certificates(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise ProvenanceKeyError(f"{SIGNING_CERTS} does not hold a PEM chain in base64.") from exc


def _signer_in(env: dict[str, str]) -> tuple[x509.Certificate, bool] | None:
    """The signer's certificate in *env*, and whether the key there belongs to it; ``None`` if unreadable."""
    from cryptography.exceptions import UnsupportedAlgorithm  # noqa: PLC0415
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    try:
        certs = signer_certificates(env[SIGNING_CERTS])
        der = base64.b64decode(env[SIGNING_KEY], validate=True)
        key = serialization.load_der_private_key(der, password=None)
    except (ProvenanceKeyError, binascii.Error, ValueError, TypeError, UnsupportedAlgorithm):
        return None
    if not certs:
        return None
    encoding, spki = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    signer = certs[0].public_key().public_bytes(encoding, spki)
    return certs[0], signer == key.public_key().public_bytes(encoding, spki)


def signer_due(install_dir: Path, env: dict[str, str], *, now: datetime | None = None) -> str | None:
    """Why the signer needs a new key and certificate, or ``None`` while the one in *env* serves."""
    from faden_cli.core import tls  # noqa: PLC0415

    now = now or datetime.now(UTC)
    if not env.get(SIGNING_KEY) or not env.get(SIGNING_CERTS):
        return "there was none"
    found = _signer_in(env)
    if found is None:
        return "the one in .env does not read"
    cert, matches = found
    left = (cert.not_valid_after_utc - now).days
    if not matches:
        why = "the key in .env does not belong to its certificate"
    elif left < RENEW_BEFORE_DAYS:
        why = f"it had {max(left, 0)} days left"
    elif not tls._issued_by_current_ca(Path(install_dir), cert):  # noqa: SLF001
        why = "its authority is no longer the install's current one"
    else:
        why = None
    return why


# ── In .env ───────────────────────────────────────────────────────


@dataclass
class Ensured:
    """What :func:`ensure` did: never a key, only what it is and why."""

    #: Why the signer was issued, when it was.
    issued: str | None = None
    #: The day the signer's certificate runs out, when one was issued.
    valid_until: datetime | None = None
    #: The versioned keys made (version 1), by their .env name.
    made: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def hash_key_made(self) -> bool:
        """Whether the hash keys were made."""
        return HASH_KEYS in self.made


def ensure(install_dir: Path, env_path: Path, *, now: datetime | None = None) -> Ensured:
    """Give the install's ``.env`` the provenance keys it lacks, and a signer when the one there is due.

    Every other line of ``.env`` stays as it is. Existing hash and watermark keys are never touched; a value
    of them that does not read is reported, not replaced. Raises :class:`faden_cli.core.tls.TlsError` when
    the authority cannot be read, before ``.env`` changes.
    """
    from faden_cli.core.env_gen import read_env_file, rewrite_env  # noqa: PLC0415

    done = Ensured()
    env_path = Path(env_path)
    if not env_path.is_file():
        return done
    now = now or datetime.now(UTC)
    env = read_env_file(env_path)
    values: dict[str, str] = {}
    why = signer_due(install_dir, env, now=now)
    if why is not None:
        values[SIGNING_KEY], values[SIGNING_CERTS] = issue_signer(install_dir, now=now)
        done.issued, done.valid_until = why, now + timedelta(days=SIGNER_DAYS)
    for name in VERSIONED_KEYS:
        if not env.get(name):
            values[name] = add_version("", name)
            done.made.append(name)
            continue
        try:
            parse_versions(env[name], name)
        except ProvenanceKeyError as exc:
            done.problems.append(f"{exc} It is left as it is; the provenance engine cannot use it so.")
    if values:
        rewrite_env(env_path, set_values=values, header=ENV_HEADER)
    return done


def ensure_step(install_dir: Path, env_path: Path) -> None:
    """:func:`ensure`, said on the console; a warning, never an abort, when it cannot be done."""
    from faden_cli.core import tls  # noqa: PLC0415
    from faden_cli.core.console import info, success, warning  # noqa: PLC0415

    try:
        done = ensure(install_dir, env_path)
    except tls.TlsError as exc:
        warning(f"The provenance signing certificate could not be issued: {exc}")
        return
    if done.issued is not None and done.valid_until is not None:
        success(
            f"Provenance signing certificate issued from the install's authority ({done.issued}); "
            f"valid until {done.valid_until:%Y-%m-%d}."
        )
    for name in done.made:
        info(f"Provenance {LABELS[name]} made (version 1).")
    for problem in done.problems:
        warning(problem)


def rotated(install_dir: Path, env: dict[str, str], *, now: datetime | None = None) -> dict[str, str]:
    """What ``faden secrets rotate`` writes: a new signer, and a new version of the hash and watermark keys.

    The versions there stay as they are. Raises :class:`ProvenanceKeyError` when they do not read, and
    :class:`faden_cli.core.tls.TlsError` when the authority cannot be read.
    """
    values = {name: add_version(env.get(name, ""), name) for name in VERSIONED_KEYS}
    values[SIGNING_KEY], values[SIGNING_CERTS] = issue_signer(install_dir, now=now)
    return values


def keep_after_restore(env_path: Path, before: dict[str, str]) -> list[str]:
    """After a restore wrote a backup's ``.env`` over the install's: the install's key versions, kept.

    *before* is the install's ``.env`` before the restore. The backup's versions keep their numbers; a key
    only the install had is added after them. A value that does not read holds no key that can be used: when
    one of the two does not, the other is kept, and a warning returned says so.
    """
    from faden_cli.core.env_gen import read_env_file, rewrite_env  # noqa: PLC0415

    restored_env = read_env_file(env_path)
    values: dict[str, str] = {}
    notes: list[str] = []
    for name in VERSIONED_KEYS:
        kept, restored = before.get(name, ""), restored_env.get(name, "")
        if not kept:
            continue
        if not restored:
            merged: str | None = kept  # nothing restored in its place: the install's value, as it was
        else:
            try:
                merged = merge_versions(restored, kept, name)
            except ProvenanceKeyError:
                merged, note = _one_readable(restored, kept, name)
                notes.append(note)
        if merged is not None and merged != restored:
            values[name] = merged
    if values:
        rewrite_env(env_path, set_values=values, header=ENV_HEADER)
    return notes


def _one_readable(restored: str, before: str, name: str) -> tuple[str | None, str]:
    """Of two values of *name* that do not merge, the one that reads, and what to say of the other."""
    try:
        return format_versions(parse_versions(restored, name)), (
            f"{name} before the restore does not read; the backup's is kept."
        )
    except ProvenanceKeyError:
        pass
    try:
        return format_versions(parse_versions(before, name)), (
            f"{name} in the backup's .env does not read; the install's is kept."
        )
    except ProvenanceKeyError:
        return None, f"{name} does not read, in the backup's .env nor before; it is left as restored."
