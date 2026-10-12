"""Ray's mutual TLS between a cluster's machines: the cluster CA and the node endpoint.

``services/machine_tls/ray_ca.py`` keeps one CA per cluster and signs each
member's certificate for ``RAY_USE_TLS``; ``POST /api/node-files/ray-tls/certificate``
hands it out, only to members of a cluster that has Ray TLS switched on.
"""

from __future__ import annotations

import contextlib
import ipaddress
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.machine_tls import RayClusterCA
from faden_backend.db.models.node_control import InfraNode
from faden_backend.services.machine_tls import authority as auth
from faden_backend.services.machine_tls import ray_ca
from faden_backend.settings import settings
from faden_backend.web.api.node_files import views

MASTER = "test-master-key"
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
#: What a DGX Spark reports, enough for a certified runtime bundle to cover it.
GB10 = {"machine": "aarch64", "gpu_count": 1, "gpu": {"vendor": "nvidia", "family": "GB10"}}


def _csr(key: object | None = None) -> str:
    key = key or ec.generate_private_key(ec.SECP256R1())
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ray-node")]))
        .sign(key, hashes.SHA256())  # type: ignore[arg-type]
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )


def _load(pem: str) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pem.encode())


async def _cluster(session: AsyncSession, *, ray_tls: bool = True, fabric_ip: str | None = None) -> tuple[InferenceEnvironment, InfraNode]:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    session.add(cp)
    await session.flush()
    node = InfraNode(
        id=uuid.uuid4(), agent_id=f"agent-{uuid.uuid4().hex[:8]}", host="192.0.2.49",
        capabilities_json={"hostname": "spark-a"},
    )
    session.add(node)
    await session.flush()
    observed = {}
    if fabric_ip:
        observed = {"resolved_fabric": {"node_bindings": {str(node.id): {"ip": fabric_ip}}}}
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
        config_json={"ray_tls": ray_tls}, observed_status_json=observed,
    )
    session.add(env)
    await session.flush()
    session.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))
    await session.flush()
    return env, node


# ── the cluster CA ───────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_node_certificate_serves_and_dials_under_every_name_ray_uses(dbsession: AsyncSession) -> None:
    env, _ = await _cluster(dbsession)
    issued = await ray_ca.issue(
        dbsession, MASTER, environment_id=env.id, csr_pem=_csr(),
        names=["192.168.100.10", "192.0.2.49", "", "spark-a", "127.0.0.1", "localhost", "192.0.2.49"],
        now=NOW,
    )
    cert, ca = _load(issued.cert_pem), _load(issued.ca_pem)
    cert.verify_directly_issued_by(ca)
    usage = list(cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
    assert set(usage) == {ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH}
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert san.get_values_for_type(x509.IPAddress) == [
        ipaddress.ip_address("192.168.100.10"), ipaddress.ip_address("192.0.2.49"), ipaddress.ip_address("127.0.0.1"),
    ]
    # Ray's own generator names the addresses as DNS entries too.
    assert san.get_values_for_type(x509.DNSName) == [
        "192.168.100.10", "192.0.2.49", "spark-a", "127.0.0.1", "localhost",
    ]
    assert issued.names == ["192.168.100.10", "192.0.2.49", "spark-a", "127.0.0.1", "localhost"]
    assert not cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert cert.not_valid_after_utc - NOW == timedelta(days=ray_ca.LEAF_DAYS)


@pytest.mark.anyio
async def test_one_ca_per_cluster_kept_sealed(dbsession: AsyncSession) -> None:
    env, _ = await _cluster(dbsession)
    other, _ = await _cluster(dbsession)
    first = await ray_ca.issue(dbsession, MASTER, environment_id=env.id, csr_pem=_csr(), names=["10.0.0.1"], now=NOW)
    again = await ray_ca.issue(dbsession, MASTER, environment_id=env.id, csr_pem=_csr(), names=["10.0.0.2"], now=NOW)
    elsewhere = await ray_ca.issue(dbsession, MASTER, environment_id=other.id, csr_pem=_csr(), names=["10.0.0.3"], now=NOW)
    assert first.ca_pem == again.ca_pem != elsewhere.ca_pem
    row = await dbsession.get(RayClusterCA, env.id)
    assert row is not None
    assert "PRIVATE KEY" not in row.key_sealed
    # Another cluster's CA does not vouch for this one's machines.
    with pytest.raises(Exception):  # noqa: B017
        _load(first.cert_pem).verify_directly_issued_by(_load(elsewhere.ca_pem))


@pytest.mark.anyio
async def test_a_ca_the_master_key_cannot_open_is_replaced(dbsession: AsyncSession) -> None:
    env, _ = await _cluster(dbsession)
    before = await ray_ca.issue(dbsession, MASTER, environment_id=env.id, csr_pem=_csr(), names=["10.0.0.1"], now=NOW)
    after = await ray_ca.issue(dbsession, "another-key", environment_id=env.id, csr_pem=_csr(), names=["10.0.0.1"], now=NOW)
    assert before.ca_pem != after.ca_pem
    _load(after.cert_pem).verify_directly_issued_by(_load(after.ca_pem))


@pytest.mark.anyio
async def test_requests_it_will_not_sign(dbsession: AsyncSession) -> None:
    env, _ = await _cluster(dbsession)
    with pytest.raises(auth.AuthorityError):
        await ray_ca.issue(dbsession, MASTER, environment_id=env.id, csr_pem="not a request", names=["10.0.0.1"])
    rsa_csr = _csr(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    with pytest.raises(auth.AuthorityError, match="EC"):
        await ray_ca.issue(dbsession, MASTER, environment_id=env.id, csr_pem=rsa_csr, names=["10.0.0.1"])
    with pytest.raises(auth.AuthorityError, match="No address"):
        await ray_ca.issue(dbsession, MASTER, environment_id=env.id, csr_pem=_csr(), names=["", "  "])


# ── the node endpoint ────────────────────────────────────────────────────


@contextlib.contextmanager
def _as_node(app: FastAPI, session: AsyncSession, node: object):  # noqa: ANN202
    @contextlib.asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield session

    previous = getattr(app.state, "db_session_factory", None)
    app.state.db_session_factory = factory
    app.dependency_overrides[views._authenticate_node] = lambda: node
    try:
        yield
    finally:
        app.dependency_overrides.pop(views._authenticate_node, None)
        app.state.db_session_factory = previous


@pytest.mark.anyio
async def test_a_member_gets_its_fabric_address_and_its_own(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "settings_master_key", MASTER)
    env, node = await _cluster(dbsession, fabric_ip="192.168.100.10")
    with _as_node(fastapi_app, dbsession, node):
        response = await client.post(
            "/api/node-files/ray-tls/certificate",
            json={"environment_id": str(env.id), "csr_pem": _csr(), "names": ["evil.example"]},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["names"] == ["192.168.100.10", "192.0.2.49", "spark-a", "127.0.0.1", "localhost"]
    _load(body["certificate_pem"]).verify_directly_issued_by(_load(body["ca_pem"]))


@pytest.mark.anyio
async def test_only_members_of_a_cluster_with_ray_tls_on(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "settings_master_key", MASTER)
    env, _ = await _cluster(dbsession)
    stranger = SimpleNamespace(id=uuid.uuid4(), host="192.0.2.99", capabilities_json={})
    with _as_node(fastapi_app, dbsession, stranger):
        response = await client.post(
            "/api/node-files/ray-tls/certificate", json={"environment_id": str(env.id), "csr_pem": _csr()},
        )
    assert response.status_code == 403

    off, node = await _cluster(dbsession, ray_tls=False)
    with _as_node(fastapi_app, dbsession, node):
        response = await client.post(
            "/api/node-files/ray-tls/certificate", json={"environment_id": str(off.id), "csr_pem": _csr()},
        )
        assert response.status_code == 409
        response = await client.post("/api/node-files/ray-tls/certificate", json={"csr_pem": _csr()})
        assert response.status_code == 400


# ── the switch ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_switching_on_restarts_the_cluster_once(dbsession: AsyncSession) -> None:
    from faden_backend.services.inference import ray_tls

    env, node = await _cluster(dbsession, ray_tls=False)
    node.capabilities_json = {**node.capabilities_json, **GB10, "ray_tls": 2}
    env.observed_generation = env.generation
    await dbsession.flush()
    before = env.generation

    state = await ray_tls.switch(dbsession, env.id, on=True)
    assert state["on"] is True and state["applied"] is False and state["restarting"] is True
    assert state["running"] == {"192.0.2.49": None}, "nothing reported yet"
    env.observed_status_json = {**(env.observed_status_json or {}), "ray_tls": {str(node.id): True}}
    await dbsession.flush()
    assert (await ray_tls.environment_status(dbsession, env.id))["applied"] is True
    assert env.config_json["ray_tls"] is True and env.generation == before + 1
    # Said again: nothing to restart.
    await ray_tls.switch(dbsession, env.id, on=True)
    assert env.generation == before + 1

    state = await ray_tls.switch(dbsession, env.id, on=False)
    assert env.config_json["ray_tls"] is False and env.generation == before + 2
    assert state["applied"] is False, "a machine still runs it"

    # Stopped, it waits for its start rather than restarting: nothing to watch.
    env.desired_state = "stopped"
    await dbsession.flush()
    state = await ray_tls.environment_status(dbsession, env.id)
    assert state["applied"] is False and state["restarting"] is False


@pytest.mark.anyio
async def test_a_lab_build_that_does_not_report_cannot_switch_it_on(dbsession: AsyncSession) -> None:
    from faden_backend.services.inference import ray_tls

    env, node = await _cluster(dbsession, ray_tls=False)
    node.capabilities_json = {**node.capabilities_json, "ray_tls": 1}
    await dbsession.flush()
    assert (await ray_tls.environment_status(dbsession, env.id))["can_switch_on"] is False
    with pytest.raises(ray_tls.RayTlsError, match=r"0\.1\.18"):
        await ray_tls.switch(dbsession, env.id, on=True)


@pytest.mark.anyio
async def test_not_on_while_an_agent_cannot(dbsession: AsyncSession) -> None:
    from faden_backend.services.inference import ray_tls

    env, _node = await _cluster(dbsession, ray_tls=False)
    before = env.generation
    state = await ray_tls.environment_status(dbsession, env.id)
    assert state["old_agents"] == ["192.0.2.49"] and state["can_switch_on"] is False
    with pytest.raises(ray_tls.RayTlsError, match=r"192.0.2.49"):
        await ray_tls.switch(dbsession, env.id, on=True)
    assert env.generation == before
    # Off is always possible.
    await ray_tls.switch(dbsession, env.id, on=False)
