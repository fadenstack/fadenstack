"""The machine CA's certificates, without a database: making, signing, sealing.

Everything here is ``cryptography``; the TLS itself is done by nginx on the
machines and by OpenSSL under httpx in the gateway. What this module decides
is what the certificates say:

- the CA may only sign end certificates (``path_length=0``);
- a machine's certificate names exactly the addresses the backend records for
  it -- whatever the request asked for -- and may only act as a server;
- the gateway's may only act as a client, so a machine's key can never be used
  to call another machine's models.

Keys are sealed with a Fernet key derived (HKDF) from the settings master key,
one derivation per purpose, which the gateway repeats to open its own.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

#: The CA's life. The next one is made a year before the end (``ROTATE_BEFORE``).
CA_DAYS = 5 * 365
ROTATE_BEFORE = timedelta(days=365)
#: How long a scheduled next CA is only trusted before it issues: every trust
#: bundle (agents and the gateway refresh theirs every minute) has it by then.
PROPAGATE = timedelta(days=2)
#: End certificates: short, renewed automatically, so nothing needs revoking.
LEAF_DAYS = 30
RENEW_BEFORE = timedelta(days=10)

ORGANIZATION = "Fadenstack"
CA_COMMON_NAME = "Fadenstack machine CA"
#: The one client the machines' proxies let through. nginx matches this name.
GATEWAY_CLIENT = "fadenstack-gateway"

KEY_PURPOSE_CA = "machine-ca-key"
KEY_PURPOSE_CLIENT = "machine-tls-client-key"

_BACKDATE = timedelta(minutes=5)


class AuthorityError(ValueError):
    """A request this CA refuses to sign, or material it cannot read."""


def _fernet(master_key: str, purpose: str) -> Fernet:
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose.encode()).derive(
        master_key.encode(),
    )
    return Fernet(base64.urlsafe_b64encode(raw))


def seal(pem: bytes, master_key: str, purpose: str) -> str:
    """*pem* encrypted with the key derived from *master_key* for *purpose*."""
    return _fernet(master_key, purpose).encrypt(pem).decode("ascii")


def unseal(token: str, master_key: str, purpose: str) -> bytes:
    """The PEM back; ``cryptography.fernet.InvalidToken`` when the master key is another one."""
    return _fernet(master_key, purpose).decrypt(token.encode("ascii"))


def fingerprint(cert: x509.Certificate) -> str:
    """SHA-256 of the certificate, hex."""
    return cert.fingerprint(hashes.SHA256()).hex()


def key_id(cert: x509.Certificate) -> str:
    """The subject key identifier, hex: what an issued certificate's authority key identifier holds."""
    return cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest.hex()


def _pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode("ascii")


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    )


def general_name(name: str) -> x509.GeneralName:
    """An address as an IP SAN, anything else as a DNS name."""
    try:
        return x509.IPAddress(ipaddress.ip_address(name))
    except ValueError:
        return x509.DNSName(name)


def _key_usage(*, cert_sign: bool, key_encipherment: bool = False) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=True, content_commitment=False, key_encipherment=key_encipherment,
        data_encipherment=False, key_agreement=False, key_cert_sign=cert_sign, crl_sign=cert_sign,
        encipher_only=False, decipher_only=False,
    )


@dataclass(frozen=True)
class MadeCA:
    """A new CA: its certificate and its unsealed key."""

    cert: x509.Certificate
    cert_pem: str
    key_pem: bytes


def make_ca(now: datetime, *, days: int = CA_DAYS) -> MadeCA:
    """A new machine CA: EC P-256, signs end certificates only."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key()
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZATION),
        x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _BACKDATE)
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(cert_sign=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .sign(key, hashes.SHA256())
    )
    return MadeCA(cert=cert, cert_pem=_pem(cert), key_pem=_key_pem(key))


def load_ca_key(key_pem: bytes) -> ec.EllipticCurvePrivateKey:
    """The CA's key from its PEM."""
    key = serialization.load_pem_private_key(key_pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise AuthorityError("The machine CA's key is not an EC key.")
    return key


def _leaf(
    *,
    subject: x509.Name,
    public: object,
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    usage: x509.ObjectIdentifier,
    names: list[str],
    now: datetime,
    days: int,
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(public)  # type: ignore[arg-type]
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _BACKDATE)
        .not_valid_after(min(now + timedelta(days=days), ca_cert.not_valid_after_utc))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            _key_usage(cert_sign=False, key_encipherment=isinstance(public, rsa.RSAPublicKey)), critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)  # type: ignore[arg-type]
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False,
        )
    )
    if names:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([general_name(n) for n in names]), critical=False,
        )
    return builder.sign(ca_key, hashes.SHA256())


def _acceptable_key(public: object) -> None:
    if isinstance(public, ec.EllipticCurvePublicKey):
        if public.curve.name not in ("secp256r1", "secp384r1"):
            raise AuthorityError(f"EC curve {public.curve.name} is not accepted; use P-256 or P-384.")
        return
    if isinstance(public, rsa.RSAPublicKey):
        if public.key_size < 2048:
            raise AuthorityError("RSA keys under 2048 bits are not accepted.")
        return
    raise AuthorityError("Only EC (P-256, P-384) and RSA (2048+) keys are accepted.")


def sign_machine(
    csr_pem: bytes | str,
    *,
    names: list[str],
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    now: datetime,
    days: int = LEAF_DAYS,
) -> x509.Certificate:
    """A server certificate for a machine's model-TLS proxy, for its key in *csr_pem*.

    Only the key is taken from the request: its names and subject are the
    backend's, so a machine cannot obtain a certificate for an address that is
    not its own -- the server's, for one.
    """
    if isinstance(csr_pem, str):
        csr_pem = csr_pem.encode("ascii", errors="replace")
    try:
        csr = x509.load_pem_x509_csr(csr_pem)
    except ValueError as exc:
        raise AuthorityError("Not a PEM certificate request.") from exc
    if not csr.is_signature_valid:
        raise AuthorityError("The request is not signed by the key it carries.")
    public = csr.public_key()
    _acceptable_key(public)
    clean = [n.strip() for n in names if n and n.strip()]
    if not clean:
        raise AuthorityError("This machine has no recorded address to put in a certificate.")
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZATION),
        x509.NameAttribute(NameOID.COMMON_NAME, clean[0][:64]),
    ])
    return _leaf(
        subject=subject, public=public, ca_cert=ca_cert, ca_key=ca_key,
        usage=ExtendedKeyUsageOID.SERVER_AUTH, names=clean, now=now, days=days,
    )


@dataclass(frozen=True)
class MadeClient:
    """A client identity: its certificate and its unsealed key."""

    cert: x509.Certificate
    cert_pem: str
    key_pem: bytes


def make_client(
    common_name: str,
    *,
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    now: datetime,
    days: int = LEAF_DAYS,
) -> MadeClient:
    """A client-only certificate named *common_name*, with a key made here."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORGANIZATION),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])
    cert = _leaf(
        subject=subject, public=key.public_key(), ca_cert=ca_cert, ca_key=ca_key,
        usage=ExtendedKeyUsageOID.CLIENT_AUTH, names=[], now=now, days=days,
    )
    return MadeClient(cert=cert, cert_pem=_pem(cert), key_pem=_key_pem(key))


def serial_hex(cert: x509.Certificate) -> str:
    """The serial number, hex."""
    return format(cert.serial_number, "x")


def bundle_sha256(bundle_pem: str) -> str:
    """SHA-256 of a trust bundle, hex: how a machine tells it changed."""
    return hashlib.sha256(bundle_pem.encode("ascii")).hexdigest()
