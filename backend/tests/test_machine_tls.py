"""The machine CA: what its certificates say, and how it looks after itself.

Trusted only by the gateway and the machines' model-TLS proxies (see
services/machine_tls). The rules that make holding its key in the backend
acceptable are checked here: a machine gets a server certificate for its own
recorded address whatever it asks for, the gateway a client-only one, and the
CA can sign nothing that signs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.machine_tls import MachineCA, MachineTlsClient
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.machine_tls import authority as auth
from llm_port_backend.services.machine_tls import store

MASTER = "test-master-key"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _csr(names: list[str], key: object | None = None) -> str:
    key = key or ec.generate_private_key(ec.SECP256R1())
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    )
    builder = builder.add_extension(
        x509.SubjectAlternativeName([auth.general_name(n) for n in names]), critical=False,
    )
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()  # type: ignore[arg-type]


def _usage(cert: x509.Certificate) -> list[x509.ObjectIdentifier]:
    return list(cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)


def _names(cert: x509.Certificate) -> list[str]:
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return [str(v.value) for v in san]


def _ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    made = auth.make_ca(NOW)
    return made.cert, auth.load_ca_key(made.key_pem)


# ── certificates ─────────────────────────────────────────────────────────


def test_the_ca_can_sign_only_end_certificates() -> None:
    cert, _ = _ca()
    constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert constraints.ca and constraints.path_length == 0
    assert cert.not_valid_after_utc - NOW > timedelta(days=4 * 365)


def test_a_machine_gets_its_recorded_address_whatever_it_asks_for() -> None:
    ca_cert, ca_key = _ca()
    csr = _csr(["10.250.10.61", "console.example.org"])  # the server's address, for one
    cert = auth.sign_machine(csr, names=["10.250.10.49"], ca_cert=ca_cert, ca_key=ca_key, now=NOW)
    assert _names(cert) == ["10.250.10.49"]
    assert _usage(cert) == [ExtendedKeyUsageOID.SERVER_AUTH], "a machine's key must not work as a client"
    cert.verify_directly_issued_by(ca_cert)
    assert cert.not_valid_after_utc - NOW <= timedelta(days=auth.LEAF_DAYS)


def test_the_gateway_is_a_client_and_nothing_else() -> None:
    ca_cert, ca_key = _ca()
    made = auth.make_client(auth.GATEWAY_CLIENT, ca_cert=ca_cert, ca_key=ca_key, now=NOW)
    assert _usage(made.cert) == [ExtendedKeyUsageOID.CLIENT_AUTH]
    assert made.cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "fadenstack-gateway"
    made.cert.verify_directly_issued_by(ca_cert)


def test_requests_it_will_not_sign() -> None:
    ca_cert, ca_key = _ca()
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505 - refused, as it should be
    with pytest.raises(auth.AuthorityError, match="2048"):
        auth.sign_machine(_csr(["m"], weak), names=["10.0.0.1"], ca_cert=ca_cert, ca_key=ca_key, now=NOW)
    with pytest.raises(auth.AuthorityError, match="PEM"):
        auth.sign_machine("not a request", names=["10.0.0.1"], ca_cert=ca_cert, ca_key=ca_key, now=NOW)
    with pytest.raises(auth.AuthorityError, match="no recorded address"):
        auth.sign_machine(_csr(["m"]), names=[" "], ca_cert=ca_cert, ca_key=ca_key, now=NOW)
    # A request whose signature is not by the key it carries (RSA: same-length signatures to splice).
    def _rsa() -> rsa.RSAPrivateKey:
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)

    good = x509.load_pem_x509_csr(_csr(["m"], _rsa()).encode())
    other = x509.load_pem_x509_csr(_csr(["m"], _rsa()).encode())
    spliced = good.public_bytes(serialization.Encoding.DER).replace(good.signature, other.signature)
    forged_pem = x509.load_der_x509_csr(spliced).public_bytes(serialization.Encoding.PEM)
    with pytest.raises(auth.AuthorityError, match="not signed"):
        auth.sign_machine(forged_pem, names=["10.0.0.1"], ca_cert=ca_cert, ca_key=ca_key, now=NOW)


def test_a_sealed_key_opens_only_with_the_same_master_key_and_purpose() -> None:
    sealed = auth.seal(b"key material", MASTER, auth.KEY_PURPOSE_CA)
    assert auth.unseal(sealed, MASTER, auth.KEY_PURPOSE_CA) == b"key material"
    with pytest.raises(InvalidToken):
        auth.unseal(sealed, "another master key", auth.KEY_PURPOSE_CA)
    with pytest.raises(InvalidToken):
        auth.unseal(sealed, MASTER, auth.KEY_PURPOSE_CLIENT)


# ── its life in the database ─────────────────────────────────────────────


async def _node(session: AsyncSession, host: str = "10.250.10.49") -> InfraNode:
    node = InfraNode(id=uuid.uuid4(), agent_id=f"agent-{uuid.uuid4().hex[:8]}", host=host)
    session.add(node)
    await session.flush()
    return node


async def _gateway(session: AsyncSession) -> tuple[x509.Certificate, MachineTlsClient]:
    row = await session.get(MachineTlsClient, auth.GATEWAY_CLIENT)
    assert row is not None
    return x509.load_pem_x509_certificate(row.cert_pem.encode()), row


@pytest.mark.anyio
async def test_the_first_pass_makes_the_ca_and_the_gateway_certificate(dbsession: AsyncSession) -> None:
    done = await store.maintain(dbsession, MASTER, NOW)
    assert done.created and done.gateway_renewed and not done.scheduled
    again = await store.maintain(dbsession, MASTER, NOW + timedelta(hours=1))
    assert not (again.created or again.gateway_renewed or again.scheduled or again.retired)
    bundle = await store.trust_bundle(dbsession, NOW)
    ca = x509.load_pem_x509_certificates(bundle.encode())
    assert len(ca) == 1
    gateway, _ = await _gateway(dbsession)
    gateway.verify_directly_issued_by(ca[0])


@pytest.mark.anyio
async def test_a_machine_is_issued_for_its_host_and_paced(dbsession: AsyncSession) -> None:
    node = await _node(dbsession, host="host=10.250.10.49")
    issued = await store.issue_for_node(
        dbsession, MASTER, node_id=node.id, host=node.host, csr_pem=_csr(["evil.example"]), now=NOW,
    )
    cert = x509.load_pem_x509_certificate(issued.cert_pem.encode())
    assert _names(cert) == ["10.250.10.49"] == issued.names
    with pytest.raises(store.TooSoonError):
        await store.issue_for_node(dbsession, MASTER, node_id=node.id, host=node.host, csr_pem=_csr(["m"]), now=NOW + timedelta(seconds=5))
    # A new address is a reason to issue at once.
    moved = await store.issue_for_node(
        dbsession, MASTER, node_id=node.id, host="10.250.10.50", csr_pem=_csr(["m"]), now=NOW + timedelta(seconds=6),
    )
    assert moved.names == ["10.250.10.50"]
    assert (await store.status(dbsession, NOW))["machines"][str(node.id)]["names"] == ["10.250.10.50"]


@pytest.mark.anyio
async def test_a_scheduled_rotation_is_trusted_first_signs_later_and_retires_the_old(dbsession: AsyncSession) -> None:
    await store.maintain(dbsession, MASTER, NOW)
    first = (await dbsession.execute(select(MachineCA))).scalars().one()
    old_key_id = await store.issuer_key_id(dbsession, NOW)

    # A year before the end: the next CA appears, trusted but not yet signing.
    late = first.not_after - auth.ROTATE_BEFORE + timedelta(hours=1)
    done = await store.maintain(dbsession, MASTER, late)
    assert done.scheduled
    assert len(x509.load_pem_x509_certificates((await store.trust_bundle(dbsession, late)).encode())) == 2
    assert await store.issuer_key_id(dbsession, late) == old_key_id
    assert not (await store.maintain(dbsession, MASTER, late + timedelta(hours=1))).scheduled

    # Two days on it signs, and the gateway moves to it straight away.
    switched = late + auth.PROPAGATE + timedelta(minutes=1)
    done = await store.maintain(dbsession, MASTER, switched)
    assert done.gateway_renewed and not done.retired
    assert await store.issuer_key_id(dbsession, switched) != old_key_id
    gateway, _row = await _gateway(dbsession)
    assert gateway.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier.hex() != old_key_id

    # Once everything the old one signed has expired, it is retired.
    done = await store.maintain(dbsession, MASTER, switched + timedelta(days=auth.LEAF_DAYS + 2))
    assert done.retired == 1
    assert len(x509.load_pem_x509_certificates(
        (await store.trust_bundle(dbsession, switched + timedelta(days=auth.LEAF_DAYS + 2))).encode()
    )) == 1


@pytest.mark.anyio
async def test_an_administrators_rotation_distrusts_the_old_at_once(dbsession: AsyncSession) -> None:
    await store.maintain(dbsession, MASTER, NOW)
    before = await store.issuer_key_id(dbsession, NOW)
    await store.rotate(dbsession, MASTER, NOW + timedelta(minutes=1))
    after = NOW + timedelta(minutes=2)
    bundle = x509.load_pem_x509_certificates((await store.trust_bundle(dbsession, after)).encode())
    assert len(bundle) == 1 and auth.key_id(bundle[0]) != before
    gateway, _ = await _gateway(dbsession)
    gateway.verify_directly_issued_by(bundle[0])


@pytest.mark.anyio
async def test_a_restore_without_the_master_key_replaces_the_ca(dbsession: AsyncSession) -> None:
    await store.maintain(dbsession, MASTER, NOW)
    before = await store.issuer_key_id(dbsession, NOW)
    done = await store.maintain(dbsession, "the new install's key", NOW + timedelta(minutes=1))
    assert done.created and done.gateway_renewed
    later = NOW + timedelta(minutes=2)
    bundle = x509.load_pem_x509_certificates((await store.trust_bundle(dbsession, later)).encode())
    assert [auth.key_id(c) for c in bundle] != [before]
    assert len(bundle) == 1
    reasons = [r.reason for r in (await dbsession.execute(select(MachineCA))).scalars().all()]
    assert "unreadable" in reasons
