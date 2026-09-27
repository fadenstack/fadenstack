"""The certificate nginx serves HTTPS with: made on this machine, or brought by the operator.

nginx serves HTTPS whenever ``<install>/tls`` holds ``edge.crt`` and
``edge.key`` (``nginx/select-config.sh``), with ``edge-chain.pem`` sent after
the certificate when present. The backend's TLS admin API writes the same
three files, so whichever wrote last is what nginx serves.

A certificate made here is signed by a small certificate authority of this
install rather than by itself. Browsers, API clients and the agents on other
machines are told to trust the authority once (``tls/ca.crt``); a new
certificate -- another address, a renewal -- then needs nothing new on their
side. The authority's key stays in ``<install>/tls-ca``, which no container
mounts.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import socket
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric.types import CertificateIssuerPrivateKeyTypes

TLS_DIR = "tls"
CERT_FILE = "edge.crt"
KEY_FILE = "edge.key"
CHAIN_FILE = "edge-chain.pem"
#: The authority's certificate, next to the material: what everyone is told to trust.
CA_PUBLIC_FILE = "ca.crt"
#: The authority's own files, outside every container mount.
CA_DIR = "tls-ca"
CA_CERT_FILE = "ca.crt"
CA_KEY_FILE = "ca.key"
#: The authority before the last rotation, published beside the new one while
#: certificates it signed may still be served.
CA_PREVIOUS_FILE = "ca-previous.crt"
#: The operator's own issuers (``tls use --ca``), published to the machines too.
ROOTS_FILE = "roots.pem"
#: Where the operator's certificate came from, so ``tls renew`` can take the renewed one.
SOURCE_FILE = "source.json"
#: A certificate made here is re-issued this many days before it runs out.
RENEW_BEFORE_DAYS = 30

#: Apple's limit for certificates under an authority the user added; the others accept it.
LEAF_DAYS = 825
CA_DAYS = 3650
#: A certificate brought along that runs out sooner than this gets a warning.
EXPIRY_WARNING_DAYS = 30
_MAX_PEM = 64 * 1024

# Interfaces whose addresses no other machine reaches this one by (loopback
# goes by its address: "lo" as a prefix would also match "Local Area Connection").
_VIRTUAL_INTERFACES = ("docker", "br-", "veth", "virbr", "cni", "flannel", "vethernet")


class TlsError(Exception):
    """The material cannot be used; the message says why and what to do."""


@dataclass
class CertSummary:
    """What an operator needs to know about a certificate."""

    subject: str
    issuer: str
    names: list[str]
    not_before: datetime
    not_after: datetime
    fingerprint_sha256: str
    self_signed: bool
    made_here: bool = False
    warnings: list[str] = field(default_factory=list)
    #: The machines have a new issuer to fetch before this certificate is served.
    trust_changed: bool = False


# ── names ─────────────────────────────────────────────────────────


def machine_names() -> list[str]:
    """The names and addresses this machine is reached by, loopback first.

    Docker's bridges and other virtual interfaces are left out: no other
    machine reaches the console through them.
    """
    import psutil  # noqa: PLC0415

    names = ["localhost", "127.0.0.1", "::1"]
    host = socket.gethostname()
    for candidate in (host, socket.getfqdn()):
        if candidate and candidate not in names and candidate != "localhost":
            names.append(candidate)
    try:
        interfaces = psutil.net_if_addrs()
    except Exception:  # never block on inventory
        interfaces = {}
    for name, addresses in interfaces.items():
        if name.lower().startswith(_VIRTUAL_INTERFACES):
            continue
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            ip = ipaddress.ip_address(address.address)
            if ip.is_loopback or ip.is_link_local:
                continue
            if address.address not in names:
                names.append(address.address)
    return names


def _general_name(name: str) -> x509.GeneralName:
    from cryptography import x509  # noqa: PLC0415

    try:
        return x509.IPAddress(ipaddress.ip_address(name))
    except ValueError:
        return x509.DNSName(name)


def _cert_names(cert: x509.Certificate) -> list[str]:
    from cryptography import x509  # noqa: PLC0415
    from cryptography.x509.oid import ExtensionOID  # noqa: PLC0415

    try:
        ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
    except x509.ExtensionNotFound:
        return []
    return [str(value.value) for value in ext.value if hasattr(value, "value")]


def covers(cert_names: Iterable[str], name: str) -> bool:
    """Whether a certificate listing *cert_names* is valid for *name* (one-label wildcards)."""
    wanted = name.lower().rstrip(".")
    for entry in cert_names:
        listed = entry.lower().rstrip(".")
        if listed == wanted:
            return True
        wildcard = listed.startswith("*.") and "." in wanted and not _is_ip(wanted)
        if wildcard and wanted.split(".", 1)[1] == listed[2:]:
            return True
    return False


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


# ── reading and summarising ───────────────────────────────────────


def _pem_blocks(text: str, kind: str = "CERTIFICATE") -> list[str]:
    begin, end = f"-----BEGIN {kind}-----", f"-----END {kind}-----"
    blocks: list[str] = []
    rest = text
    while begin in rest:
        start = rest.index(begin)
        stop = rest.find(end, start)
        if stop < 0:
            break
        blocks.append(rest[start : stop + len(end)] + "\n")
        rest = rest[stop + len(end) :]
    return blocks


def summarise(cert: x509.Certificate, *, made_here: bool = False) -> CertSummary:
    """What :class:`CertSummary` shows of *cert*."""
    from cryptography.hazmat.primitives import hashes  # noqa: PLC0415

    return CertSummary(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        names=_cert_names(cert),
        not_before=cert.not_valid_before_utc,
        not_after=cert.not_valid_after_utc,
        fingerprint_sha256=cert.fingerprint(hashes.SHA256()).hex(),
        self_signed=cert.subject == cert.issuer,
        made_here=made_here,
    )


def material_present(install_dir: Path) -> bool:
    """Whether the install holds what nginx serves HTTPS with."""
    tls = Path(install_dir) / TLS_DIR
    return (tls / CERT_FILE).is_file() and (tls / KEY_FILE).is_file()


def current(install_dir: Path) -> CertSummary | None:
    """The certificate nginx serves, or ``None`` while the install is plain HTTP."""
    from cryptography import x509  # noqa: PLC0415

    install_dir = Path(install_dir)
    if not material_present(install_dir):
        return None
    try:
        cert = x509.load_pem_x509_certificate((install_dir / TLS_DIR / CERT_FILE).read_bytes())
    except (ValueError, OSError) as exc:
        raise TlsError(f"{install_dir / TLS_DIR / CERT_FILE} cannot be read: {exc}") from exc
    return summarise(cert, made_here=_issued_by_our_ca(install_dir, cert))


def _issued_by_our_ca(install_dir: Path, cert: x509.Certificate) -> bool:
    """Signed by the install's authority, the current one or the one before a rotation."""
    for name in (CA_CERT_FILE, CA_PREVIOUS_FILE):
        for ca in _valid_certs(install_dir / CA_DIR / name, datetime.now(UTC)):
            try:
                cert.verify_directly_issued_by(ca)
            except Exception:  # noqa: S112 - InvalidSignature, another key type: not this issuer
                continue
            return True
    return False


def _issued_by_current_ca(install_dir: Path, cert: x509.Certificate) -> bool:
    for ca in _valid_certs(install_dir / CA_DIR / CA_CERT_FILE, datetime.now(UTC)):
        try:
            cert.verify_directly_issued_by(ca)
        except Exception:  # noqa: S112 - InvalidSignature, another key type: not this issuer
            continue
        return True
    return False


# ── writing ───────────────────────────────────────────────────────


def _write(path: Path, data: bytes, *, mode: int) -> None:
    """Write *data* with *mode* from the start, so a key is never readable by others."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # O_BINARY: Windows' C runtime would otherwise turn every newline into CRLF.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp), flags, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    tmp.chmod(mode)
    tmp.replace(path)


def _install(install_dir: Path, cert_pem: bytes, key_pem: bytes, chain_pem: bytes | None) -> None:
    tls = Path(install_dir) / TLS_DIR
    # The key last: nginx switches on the pair, so it must never find a key
    # next to the previous certificate.
    _write(tls / CERT_FILE, cert_pem, mode=0o644)
    if chain_pem:
        _write(tls / CHAIN_FILE, chain_pem, mode=0o644)
    else:
        (tls / CHAIN_FILE).unlink(missing_ok=True)
    _write(tls / KEY_FILE, key_pem, mode=0o600)


def remove(install_dir: Path) -> bool:
    """Take the material away, so nginx goes back to plain HTTP; ``True`` if there was any.

    The authority stays: switched on again, a new certificate is trusted
    wherever the old one was.
    """
    tls = Path(install_dir) / TLS_DIR
    had = material_present(install_dir)
    for name in (KEY_FILE, CERT_FILE, CHAIN_FILE):
        (tls / name).unlink(missing_ok=True)
    return had


# ── made here ─────────────────────────────────────────────────────


def _load_or_make_ca(
    install_dir: Path, now: datetime
) -> tuple[x509.Certificate, CertificateIssuerPrivateKeyTypes, bool]:
    """This install's authority, made on first use; ``(cert, key, created)``."""
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    ca_dir = install_dir / CA_DIR
    cert_path, key_path = ca_dir / CA_CERT_FILE, ca_dir / CA_KEY_FILE
    if cert_path.is_file() and key_path.is_file():
        try:
            cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise TypeError("not the EC key this CLI makes")
        except (ValueError, TypeError, OSError) as exc:
            raise TlsError(
                f"The certificate authority in {ca_dir} cannot be read ({exc}). Move the directory "
                "away to make a new one; everything that trusted the old one must then trust the new."
            ) from exc
        # Reused while it outlives the certificate it is about to sign.
        if cert.not_valid_after_utc > now + timedelta(days=LEAF_DAYS + 1):
            return cert, key, False
        # Rotated: the old one stays published while what it signed may be served.
        _write(ca_dir / CA_PREVIOUS_FILE, cert_path.read_bytes(), mode=0o644)

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Fadenstack"),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Fadenstack CA on {socket.gethostname()}"),
    ])
    public = key.public_key()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .sign(key, hashes.SHA256())
    )
    ca_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):  # Windows has no such bits
        ca_dir.chmod(0o700)
    _write(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        mode=0o600,
    )
    _write(cert_path, cert.public_bytes(serialization.Encoding.PEM), mode=0o644)
    return cert, key, True


def _valid_certs(path: Path, now: datetime) -> list[x509.Certificate]:
    from cryptography import x509  # noqa: PLC0415

    if not path.is_file():
        return []
    try:
        certs = x509.load_pem_x509_certificates(path.read_bytes())
    except ValueError:
        return []
    return [c for c in certs if c.not_valid_after_utc > now]


def publish_trust(install_dir: Path, *, now: datetime | None = None) -> tuple[Path, bool]:
    """Write what the machines are to trust into ``tls/ca.crt``: ``(path, changed)``.

    The install's authority, the one before it while still valid (a rotation
    never leaves a served certificate without its issuer), and the operator's
    own issuers. The backend hands this file to every machine as it is.
    """
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    install_dir = Path(install_dir)
    now = now or datetime.now(UTC)
    ca_dir = install_dir / CA_DIR
    seen: set[bytes] = set()
    parts: list[bytes] = []
    for name in (CA_CERT_FILE, CA_PREVIOUS_FILE, ROOTS_FILE):
        for cert in _valid_certs(ca_dir / name, now):
            der = cert.public_bytes(serialization.Encoding.DER)
            if der not in seen:
                seen.add(der)
                parts.append(cert.public_bytes(serialization.Encoding.PEM))
    public = install_dir / TLS_DIR / CA_PUBLIC_FILE
    data = b"".join(parts)
    if not data:
        public.unlink(missing_ok=True)
        return public, False
    if public.is_file() and public.read_bytes() == data:
        return public, False
    _write(public, data, mode=0o644)
    return public, True


def ensure_ca(install_dir: Path, *, now: datetime | None = None) -> tuple[Path, bool]:
    """The install's authority, made when missing, published in ``tls/``: ``(path, changed)``.

    Deploy and upgrade call this with HTTPS still off. The backend offers the
    authority to every machine (``/api/node-files/server-trust``), so they
    hold it before a certificate it signed is served: once HTTPS is on, the
    plain-HTTP address only redirects to what they could not check otherwise.
    ``changed`` says the machines have something new to fetch.
    """
    install_dir = Path(install_dir)
    now = now or datetime.now(UTC)
    _load_or_make_ca(install_dir, now)
    return publish_trust(install_dir, now=now)


@dataclass
class Generated:
    """A certificate made here: what it is, and the authority to trust for it."""

    summary: CertSummary
    ca_public: Path
    #: The machines have a new authority to fetch before this certificate is served.
    trust_changed: bool


def generate(install_dir: Path, extra_names: Iterable[str] = (), *, now: datetime | None = None) -> Generated:
    """Make a certificate for this machine, signed by the install's authority, and install it.

    It names ``localhost``, the machine's host names and every address it
    has outside Docker's bridges, plus *extra_names* -- a DNS name or an
    address the console is also reached by.
    """
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: PLC0415

    install_dir = Path(install_dir)
    now = now or datetime.now(UTC)
    names = machine_names()
    for raw in extra_names:
        extra = raw.strip()
        if not extra:
            continue
        with_port = ":" in extra and not _is_ip(extra)  # IPv6 addresses have colons of their own
        if extra.startswith(("http://", "https://")) or "/" in extra or " " in extra or with_port:
            raise TlsError(f"{extra!r} is not a host name or an address (no scheme, port or path).")
        if extra not in names:
            names.append(extra)

    ca_cert, ca_key, _created = _load_or_make_ca(install_dir, now)
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key()
    loopback = ("localhost", "127.0.0.1", "::1")
    primary = next((n for n in names if n not in loopback and not _is_ip(n)), "localhost")
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, primary)]))
        .issuer_name(ca_cert.subject)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=LEAF_DAYS))
        .add_extension(x509.SubjectAlternativeName([_general_name(n) for n in names]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    _install(
        install_dir,
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        None,
    )
    # The served certificate is made here now: nothing to take from the operator's source.
    (install_dir / CA_DIR / SOURCE_FILE).unlink(missing_ok=True)
    ca_public, changed = publish_trust(install_dir, now=now)
    return Generated(summary=summarise(cert, made_here=True), ca_public=ca_public, trust_changed=changed)


# ── brought by the operator ───────────────────────────────────────


def _read_pem(path: Path, label: str) -> str:
    path = Path(path).expanduser()
    if not path.is_file():
        raise TlsError(f"{label} {path} does not exist.")
    raw = path.read_bytes()
    if len(raw) > _MAX_PEM:
        raise TlsError(f"{label} {path} is larger than {_MAX_PEM // 1024} KB; that is not a PEM file.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TlsError(f"{label} {path} is not PEM text (DER? convert it with openssl first).") from exc
    if "-----BEGIN " not in text:
        raise TlsError(f"{label} {path} is not PEM-encoded.")
    return text


def _chains_to(leaf: x509.Certificate, chain: list[x509.Certificate], roots: list[x509.Certificate]) -> bool:
    """Whether *leaf*, through *chain*, was issued by one of *roots*."""
    current = leaf
    for _ in range(len(chain) + 1):
        for root in roots:
            try:
                current.verify_directly_issued_by(root)
            except Exception:  # noqa: S112 - InvalidSignature, another key type: not this issuer
                continue
            return True
        issuer = next((c for c in chain if c.subject == current.issuer and c is not current), None)
        if issuer is None:
            return False
        current = issuer
    return False


def _sha256_file(path: Path) -> str | None:
    import hashlib  # noqa: PLC0415

    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def source(install_dir: Path) -> dict[str, str | None] | None:
    """Where the served certificate was imported from, when it was the operator's."""
    import json  # noqa: PLC0415

    try:
        data = json.loads((Path(install_dir) / CA_DIR / SOURCE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("cert") and data.get("key") else None


def use_own(
    install_dir: Path,
    cert_path: Path,
    key_path: Path,
    chain_path: Path | None = None,
    *,
    ca_path: Path | None = None,
    now: datetime | None = None,
) -> CertSummary:
    """Check the operator's certificate and key, then install them.

    The certificate file may carry its chain after it (a "fullchain" file);
    a separate *chain_path* is added after that. Problems that make nginx or
    every client refuse the pair raise :class:`TlsError`; a certificate that
    does not name the addresses of this machine only warns, since the console
    may well be reached by a name only DNS knows.

    A private issuer -- *ca_path*, or a self-signed root the chain ends in --
    is published to the machines with the install's own authority, so they
    trust the certificate without anyone copying files to them. Where the
    files came from is kept, so ``tls renew`` takes the renewed ones from
    there (certbot, acme.sh and corporate tooling renew in place).
    """
    import json  # noqa: PLC0415

    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: PLC0415

    now = now or datetime.now(UTC)
    cert_text = _read_pem(cert_path, "Certificate")
    key_text = _read_pem(key_path, "Key")
    blocks = _pem_blocks(cert_text)
    if not blocks:
        raise TlsError(f"Certificate {cert_path} holds no CERTIFICATE block.")
    chain_blocks = blocks[1:]
    if chain_path is not None:
        chain_blocks += _pem_blocks(_read_pem(chain_path, "Chain"))

    try:
        cert = x509.load_pem_x509_certificate(blocks[0].encode())
    except ValueError as exc:
        raise TlsError(f"Certificate {cert_path} cannot be read: {exc}") from exc
    try:
        key = serialization.load_pem_private_key(key_text.encode(), password=None)
    except TypeError as exc:
        raise TlsError(
            f"Key {key_path} is protected by a passphrase, which nginx cannot ask for. "
            "Write it out without one, for example: openssl pkey -in <key> -out <key>.plain"
        ) from exc
    except ValueError as exc:
        raise TlsError(f"Key {key_path} cannot be read: {exc}") from exc
    if not isinstance(key, (rsa.RSAPrivateKey, ec.EllipticCurvePrivateKey)):
        raise TlsError("Only RSA and EC keys are served; browsers do not accept the others.")

    der = serialization.Encoding.DER
    spki = serialization.PublicFormat.SubjectPublicKeyInfo
    if cert.public_key().public_bytes(der, spki) != key.public_key().public_bytes(der, spki):
        raise TlsError("The key does not belong to the certificate.")
    if cert.not_valid_before_utc > now:
        raise TlsError(f"The certificate is not valid before {cert.not_valid_before_utc:%Y-%m-%d %H:%M} UTC.")
    if cert.not_valid_after_utc < now:
        raise TlsError(f"The certificate expired on {cert.not_valid_after_utc:%Y-%m-%d}.")
    chain: list[x509.Certificate] = []
    for index, block in enumerate(chain_blocks, start=1):
        try:
            chain.append(x509.load_pem_x509_certificate(block.encode()))
        except ValueError as exc:
            raise TlsError(f"Chain certificate #{index} cannot be read: {exc}") from exc

    # The issuer the machines are to trust: given, or the root the chain ends in.
    roots: list[x509.Certificate] = []
    if ca_path is not None:
        try:
            roots = x509.load_pem_x509_certificates(_read_pem(ca_path, "Authority").encode())
        except ValueError as exc:
            raise TlsError(f"Authority {ca_path} cannot be read: {exc}") from exc
        if not _chains_to(cert, chain, roots):
            msg = f"The certificate was not issued by {ca_path}, through the chain given or directly."
            raise TlsError(msg)
    elif chain and chain[-1].subject == chain[-1].issuer and chain[-1] is not cert:
        roots = [chain[-1]]

    summary = summarise(cert)
    reachable = [n for n in machine_names() if n not in ("localhost", "127.0.0.1", "::1")]
    if reachable and not any(covers(summary.names, n) for n in reachable):
        summary.warnings.append(
            "It names none of this machine's own names or addresses "
            f"({', '.join(reachable)}); clients must reach the console by a name it lists: "
            f"{', '.join(summary.names) or 'it lists none'}."
        )
    if summary.self_signed:
        summary.warnings.append("It is self-signed: every browser and client has to be told to trust it.")
    if (summary.not_after - now).days < EXPIRY_WARNING_DAYS:
        summary.warnings.append(f"It expires on {summary.not_after:%Y-%m-%d}.")

    install_dir = Path(install_dir)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    _install(
        install_dir,
        blocks[0].encode(),
        key_pem,
        "".join(chain_blocks).encode() if chain_blocks else None,
    )
    roots_file = install_dir / CA_DIR / ROOTS_FILE
    if roots:
        _write(roots_file, b"".join(r.public_bytes(serialization.Encoding.PEM) for r in roots), mode=0o644)
    else:
        roots_file.unlink(missing_ok=True)
    absolute = {
        "cert": str(Path(cert_path).expanduser().resolve()),
        "key": str(Path(key_path).expanduser().resolve()),
        "chain": str(Path(chain_path).expanduser().resolve()) if chain_path else None,
        "ca": str(Path(ca_path).expanduser().resolve()) if ca_path else None,
    }
    _write(
        install_dir / CA_DIR / SOURCE_FILE,
        json.dumps({**absolute, "cert_sha256": _sha256_file(Path(absolute["cert"]))}, indent=1).encode(),
        mode=0o600,
    )
    _, summary.trust_changed = publish_trust(install_dir, now=now)
    return summary


# ── renewing ──────────────────────────────────────────────────────


@dataclass
class Renewal:
    """What ``tls renew`` did, or why it did nothing."""

    #: off | current | renewed | reimported | expiring
    action: str
    message: str
    summary: CertSummary | None = None
    trust_changed: bool = False


def renew(
    install_dir: Path, *, days: int = RENEW_BEFORE_DAYS, force: bool = False, now: datetime | None = None
) -> Renewal:
    """Keep the served certificate current; safe to run every day.

    * Made here: issued again, by the same authority, ``days`` before it runs
      out -- or when this machine has an address it does not list, or it was
      signed by the authority before a rotation. It keeps every name it had.
      Nothing changes for the machines or the browsers that trust the authority.
    * The operator's own: taken again from the files it was imported from when
      they changed there (a renewal by certbot and the like), after the same
      checks as the first time. Otherwise a warning once it is close to its end.
    """
    install_dir = Path(install_dir)
    now = now or datetime.now(UTC)
    served = current(install_dir)
    if served is None:
        return Renewal("off", "HTTPS is off; nothing to renew.")
    left = (served.not_after - now).days

    if served.made_here:
        reachable = [n for n in machine_names() if n not in ("localhost", "127.0.0.1", "::1")]
        missing = [n for n in reachable if not covers(served.names, n)]
        from cryptography import x509  # noqa: PLC0415

        cert = x509.load_pem_x509_certificate((install_dir / TLS_DIR / CERT_FILE).read_bytes())
        stale_issuer = not _issued_by_current_ca(install_dir, cert)
        if not (force or left < days or missing or stale_issuer):
            return Renewal("current", f"Made here, valid for {left} more days.", served)
        why = (
            "as asked" if force
            else f"it had {left} days left" if left < days
            else f"it did not list {', '.join(missing)}" if missing
            else "the authority was renewed"
        )
        made = generate(install_dir, served.names, now=now)
        return Renewal("renewed", f"Issued again ({why}).", made.summary, made.trust_changed)

    origin = source(install_dir)
    if origin is not None:
        changed = _sha256_file(Path(str(origin["cert"]))) != origin.get("cert_sha256")
        if force or changed:
            try:
                summary = use_own(
                    install_dir,
                    Path(str(origin["cert"])),
                    Path(str(origin["key"])),
                    Path(str(origin["chain"])) if origin.get("chain") else None,
                    ca_path=Path(str(origin["ca"])) if origin.get("ca") else None,
                    now=now,
                )
            except TlsError as exc:
                why = f"The files at {origin['cert']} changed but cannot be used: {exc}"
                return Renewal("expiring", why, served)
            taken = f"Taken again from {origin['cert']}."
            return Renewal("reimported", taken, summary, summary.trust_changed)
        if left < days:
            return Renewal(
                "expiring",
                f"It runs out in {left} days and {origin['cert']} still holds the same one: "
                "renew it there, and the next run takes it.",
                served,
            )
        return Renewal(
            "current", f"Valid for {left} more days; taken from {origin['cert']} when it changes.", served
        )

    if left < days:
        return Renewal(
            "expiring",
            f"It runs out in {left} days. Install the renewed one: llmport tls use CERT KEY.",
            served,
        )
    return Renewal("current", f"Valid for {left} more days.", served)


# ── what the backend is told about the front door ─────────────────


def write_edge_info(install_dir: Path, *, http_port: int, https_port: int, renews: str) -> None:
    """``tls/edge.json``: this host's addresses and ports, for the backend in its container.

    The backend sees only the container's own interfaces, so the addresses it
    offered a machine being added were ones no other machine could use.
    """
    import json  # noqa: PLC0415

    hosts = [n for n in machine_names() if n not in ("localhost", "127.0.0.1", "::1")]
    info = {"hosts": hosts, "http_port": http_port, "https_port": https_port, "renews": renews}
    path = Path(install_dir) / TLS_DIR / "edge.json"
    data = (json.dumps(info, indent=1) + "\n").encode()
    if not path.is_file() or path.read_bytes() != data:
        _write(path, data, mode=0o644)
