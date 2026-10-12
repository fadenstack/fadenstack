"""A new Ray cluster encrypts its traffic from the start; clusters made before keep what they run.

A cluster made with Ray TLS off keeps running unencrypted between its machines
even after a later release changes the default, so the default has to be right
at creation. The model's own default stays off: it is what a stored config
without the key means.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import InferenceControlPlane, InferenceEnvironment
from faden_backend.db.models.node_control import InfraNode
from faden_backend.services.inference import ray_tls
from faden_backend.services.inference.drivers.ray.schemas import RayEnvironmentConfig
from faden_backend.services.inference.service import ConflictError, EnvironmentService
from tests.test_inference_api import API, authed_fapp, make_control_plane, superuser  # noqa: F401  (fixtures)

GB10 = {"machine": "aarch64", "gpu_count": 1, "gpu": {"vendor": "nvidia", "family": "GB10"}, "ray_tls": 2}


async def _control_plane(session: AsyncSession) -> InferenceControlPlane:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:12]}", driver="ray")
    session.add(cp)
    await session.flush()
    return cp


async def _node(session: AsyncSession, **caps: object) -> InfraNode:
    node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex[:8]}", host="192.0.2.49", capabilities_json=dict(caps))
    session.add(node)
    await session.flush()
    return node


async def test_a_new_cluster_has_ray_tls_on(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession)
    env = await EnvironmentService(dbsession).create(control_plane_id=cp.id, name="fresh")
    assert env.config_json["ray_tls"] is True
    assert RayEnvironmentConfig.model_validate(env.config_json).ray_tls is True


async def test_a_new_cluster_made_through_the_api_has_ray_tls_on(client: AsyncClient, authed_fapp) -> None:  # noqa: ANN001, F811
    """The console wizard sends no config, so this is its path."""
    cp = await make_control_plane(client, name=f"cp-{uuid.uuid4().hex[:6]}")
    response = await client.post(f"{API}/environments", json={"control_plane_id": cp["id"], "name": "wizard"})
    assert response.status_code == 201, response.text
    assert response.json()["config"]["ray_tls"] is True


async def test_an_admin_can_create_a_cluster_with_it_off_on_purpose(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession)
    env = await EnvironmentService(dbsession).create(control_plane_id=cp.id, name="open", config={"ray_tls": False})
    assert env.config_json["ray_tls"] is False


async def test_a_stored_config_without_the_key_stays_off() -> None:
    """Clusters made before the setting existed run without TLS; reading their config must not turn it on."""
    assert RayEnvironmentConfig.model_validate({}).ray_tls is False
    assert RayEnvironmentConfig.model_validate({"head_port": 6379}).ray_tls is False


async def test_a_taken_over_cluster_is_not_given_tls(dbsession: AsyncSession) -> None:
    """Takeover writes its own config and never goes through the creation defaults."""
    from faden_backend.services.inference import takeover  # noqa: PLC0415

    assert "ray_tls" not in Path(takeover.__file__).read_text(encoding="utf-8")
    env = InferenceEnvironment(control_plane_id=(await _control_plane(dbsession)).id, name="found", config_json={})
    dbsession.add(env)
    await dbsession.flush()
    assert RayEnvironmentConfig.model_validate(env.config_json).ray_tls is False


async def test_a_machine_in_the_runtime_container_can_apply_it(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession)
    env = await EnvironmentService(dbsession).create(control_plane_id=cp.id, name="ok")
    assert await ray_tls.member_refusal(dbsession, env, await _node(dbsession, **GB10)) is None


async def test_a_host_mode_machine_is_refused_and_told_what_to_do(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession)
    env = await EnvironmentService(dbsession).create(control_plane_id=cp.id, name="strict")
    host_mode = await _node(dbsession, ray_tls=2)  # reports no platform: no runtime bundle covers it
    service = EnvironmentService(dbsession)
    with pytest.raises(ConflictError, match=r"runtime container.*switch Ray TLS off"):
        await service.add_node(env.id, host_mode.id, "worker")
    state = await ray_tls.environment_status(dbsession, env.id)
    assert state["on"] is True


async def test_an_old_agent_is_refused(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession)
    env = await EnvironmentService(dbsession).create(control_plane_id=cp.id, name="old")
    old = await _node(dbsession, **{**GB10, "ray_tls": 1})
    with pytest.raises(ConflictError, match=r"0\.1\.18"):
        await EnvironmentService(dbsession).add_node(env.id, old.id, "worker")


async def test_a_cluster_with_it_off_accepts_a_host_mode_machine(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession)
    env = await EnvironmentService(dbsession).create(control_plane_id=cp.id, name="off", config={"ray_tls": False})
    host_mode = await _node(dbsession)
    await EnvironmentService(dbsession).add_node(env.id, host_mode.id, "worker")


async def test_it_cannot_be_switched_on_while_a_machine_runs_ray_on_the_host(dbsession: AsyncSession) -> None:
    cp = await _control_plane(dbsession)
    env = await EnvironmentService(dbsession).create(control_plane_id=cp.id, name="sw", config={"ray_tls": False})
    host_mode = await _node(dbsession, ray_tls=2)
    await EnvironmentService(dbsession).add_node(env.id, host_mode.id, "worker")
    state = await ray_tls.environment_status(dbsession, env.id)
    assert state["host_mode"] == ["192.0.2.49"] and state["can_switch_on"] is False
    with pytest.raises(ray_tls.RayTlsError, match="runtime container"):
        await ray_tls.switch(dbsession, env.id, on=True)
