"""Ray's mutual TLS between a cluster's machines: each cluster's own CA, and its node certificates.

Ray (2.58) with ``RAY_USE_TLS=1`` uses one certificate per node as both gRPC
server and client, demands a client certificate from the cluster CA on every
connection, and checks the server's name against the address it dialled.
So a node certificate:

- may act as server and client (both extended key usages);
- names every address Ray reaches the node by -- the fabric address the
  cluster runs on, the machine's own, ``127.0.0.1`` and ``localhost`` -- as
  IP entries and, as Ray's own generator does, as DNS entries too;
- lives as long as the node's container: Ray reads its certificates when a
  process starts and never again, so the agent asks for one only when it
  (re)creates the runtime container, and a cluster started again gets new ones.

The CA is per cluster and apart from the machine CA, whose rotations must not
reach a running cluster. Its key is sealed with the settings master key.
"""

from __future__ import annotations

import contextlib
import ipaddress
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.machine_tls import RayClusterCA
from faden_backend.services.machine_tls import authority as auth

KEY_PURPOSE = "ray-cluster-ca-key"
_CURVES = ("secp256r1", "secp384r1")
CA_DAYS = 10 * 365
#: Longer than any cluster runs without its containers being made again.
LEAF_DAYS = 2 * 365


@dataclass(frozen=True)
class RayCertificate:
    """A node's signed Ray certificate, the cluster CA's, and the names it carries."""

    cert_pem: str
    ca_pem: str
    not_after: datetime
    names: list[str]


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def _ensure_ca(
    session: AsyncSession, master_key: str, environment_id: uuid.UUID, now: datetime,
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    row = await session.get(RayClusterCA, environment_id)
    if row is not None:
        try:
            cert = x509.load_pem_x509_certificate(row.cert_pem.encode("ascii"))
            key = auth.load_ca_key(auth.unseal(row.key_sealed, master_key, KEY_PURPOSE))
            if cert.not_valid_after_utc - now > timedelta(days=LEAF_DAYS):
                return cert, key
        except (InvalidToken, ValueError, auth.AuthorityError):
            pass  # unreadable (another master key) or too old: a new one, and the cluster restarts on it
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, auth.ORGANIZATION),
        x509.NameAttribute(NameOID.COMMON_NAME, f"Fadenstack Ray CA {str(environment_id)[:8]}"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    )
    sealed = auth.seal(key_pem, master_key, KEY_PURPOSE)
    if row is None:
        session.add(RayClusterCA(
            environment_id=environment_id, cert_pem=pem, key_sealed=sealed,
            not_after=cert.not_valid_after_utc, created_at=now,
        ))
    else:
        row.cert_pem, row.key_sealed, row.not_after = pem, sealed, cert.not_valid_after_utc
    await session.flush()
    return cert, key


def _san(names: list[str]) -> x509.SubjectAlternativeName:
    entries: list[x509.GeneralName] = []
    for name in names:
        with contextlib.suppress(ValueError):
            entries.append(x509.IPAddress(ipaddress.ip_address(name)))
        entries.append(x509.DNSName(name))
    return x509.SubjectAlternativeName(entries)


async def issue(
    session: AsyncSession,
    master_key: str,
    *,
    environment_id: uuid.UUID,
    csr_pem: str,
    names: list[str],
    now: datetime | None = None,
) -> RayCertificate:
    """Sign a node's request for its Ray certificate in *environment_id*'s cluster. The caller commits."""
    now = _now(now)
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode("ascii", errors="replace"))
    except ValueError as exc:
        raise auth.AuthorityError("Not a PEM certificate request.") from exc
    if not csr.is_signature_valid:
        raise auth.AuthorityError("The request is not signed by the key it carries.")
    public = csr.public_key()
    if not isinstance(public, ec.EllipticCurvePublicKey) or public.curve.name not in _CURVES:
        raise auth.AuthorityError("Ray certificates take an EC P-256 or P-384 key.")
    clean = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    if not clean:
        raise auth.AuthorityError("No address to put in the certificate.")
    ca_cert, ca_key = await _ensure_ca(session, master_key, environment_id, now)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, auth.ORGANIZATION),
            x509.NameAttribute(NameOID.COMMON_NAME, f"ray-node {clean[0]}"[:64]),
        ]))
        .issuer_name(ca_cert.subject).public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(min(now + timedelta(days=LEAF_DAYS), ca_cert.not_valid_after_utc))
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
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(_san(clean), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(public), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return RayCertificate(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        ca_pem=ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        not_after=cert.not_valid_after_utc,
        names=clean,
    )
