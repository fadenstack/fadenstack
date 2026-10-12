"""The dashboard says which clusters' models are still reached over plain HTTP, and which model-TLS certificates do not renew."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEndpoint,
    InferenceEnvironment,
)
from faden_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from faden_backend.db.models.node_control import InfraNode
from faden_backend.services.dashboard import attention
from faden_backend.services.machine_tls import store

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


class _Facts:
    def __init__(self, session: AsyncSession, now: datetime) -> None:
        self.now = now
        self._session = session

    async def on_backend(self, query: Any) -> Any:
        return await query(self._session)


async def _cluster_with(session: AsyncSession, published: dict[str, Any]) -> InferenceEnvironment:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    session.add(cp)
    await session.flush()
    env = InferenceEnvironment(control_plane_id=cp.id, name=f"dgx-{uuid.uuid4().hex[:6]}", desired_state="running")
    model = LLMModel(
        display_name="Qwen", source=ModelSource.HUGGINGFACE, status=ModelStatus.AVAILABLE,
        hf_repo_id="Qwen/Qwen2.5-0.5B-Instruct", hf_revision="main",
    )
    session.add_all([env, model])
    await session.flush()
    dep = InferenceDeployment(
        environment_id=env.id, model_id=model.id, name=f"dep-{uuid.uuid4().hex[:8]}", spec_json={},
        phase="running", desired_state="active", ready_replicas=1, total_replicas=1,
    )
    session.add(dep)
    await session.flush()
    session.add(InferenceEndpoint(
        deployment_id=dep.id, name="openai", address=published["base_url"], path="/v1",
        status="published", published_json=published,
    ))
    await session.flush()
    return env


async def test_a_cluster_still_on_plain_http_is_named(dbsession: AsyncSession) -> None:
    plain = await _cluster_with(dbsession, {"base_url": "http://192.0.2.49:8010/a/v1", "model_tls_pending": True})
    await _cluster_with(dbsession, {"base_url": "https://192.0.2.49:8010/b/v1", "machine_tls": True})
    items = await attention.model_traffic(_Facts(dbsession, NOW))  # type: ignore[arg-type]
    unencrypted = [i for i in items if i.code == "model_traffic_unencrypted"]
    assert [i.params["cluster"] for i in unencrypted] == [plain.name]
    assert unencrypted[0].link == f"/admin/clusters/{plain.id}"


async def test_certificates_that_should_have_renewed(dbsession: AsyncSession) -> None:
    await store.maintain(dbsession, "k", NOW)
    node = InfraNode(agent_id=f"a-{uuid.uuid4().hex[:6]}", host="192.0.2.71")
    dbsession.add(node)
    await dbsession.flush()
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "m")]))
        .sign(ec.generate_private_key(ec.SECP256R1()), hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM).decode()
    )
    await store.issue_for_node(dbsession, "k", node_id=node.id, host=node.host, csr_pem=csr, now=NOW)

    assert not [i for i in await attention.model_traffic(_Facts(dbsession, NOW)) if "certificate" in i.code]  # type: ignore[arg-type]
    late = NOW + timedelta(days=27)  # three days left: renewal (at ten) has been failing for a week
    codes = {i.code: i for i in await attention.model_traffic(_Facts(dbsession, late))}  # type: ignore[arg-type]
    assert codes["machine_certificate_expiring"].params["machine"] == "192.0.2.71"
    assert codes["machine_certificate_expiring"].severity == "warning"
    assert "gateway_machine_certificate_expiring" in codes
