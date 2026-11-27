"""Serving on one Mac from the console: the one-call environment, and what a Mac refuses at deploy."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from fastapi import FastAPI
import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from faden_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from faden_backend.services.inference.service import ConflictError, DeploymentService, EnvironmentService
from tests.test_inference_api import API, authed_fapp, superuser  # noqa: F401  (fixtures)
from tests.test_native_bundles import MAC
from tests.test_standalone_driver import _environment, _member, _node, _spec

pytestmark = pytest.mark.anyio()


async def test_one_call_makes_the_environment_and_a_second_returns_it(dbsession: AsyncSession) -> None:
    service = EnvironmentService(dbsession)
    mac = await _node(dbsession, MAC)
    env, created = await service.create_standalone(mac.id)
    again, created_again = await service.create_standalone(mac.id)
    assert created is True and created_again is False and again.id == env.id
    other = await _node(dbsession, MAC, host="10.0.0.10")
    second, _ = await service.create_standalone(other.id)
    assert second.id != env.id and second.control_plane_id == env.control_plane_id  # one plane, one env per Mac


async def test_a_mac_whose_agent_cannot_serve_gets_a_sentence(dbsession: AsyncSession) -> None:
    old = await _node(dbsession, {k: v for k, v in MAC.items() if k != "native_runtime"})
    with pytest.raises(ConflictError, match="native model runtime"):
        await EnvironmentService(dbsession).create_standalone(old.id)


async def test_a_mac_in_a_ray_cluster_is_refused(dbsession: AsyncSession) -> None:
    ray_env = await _environment(dbsession, driver="ray")
    mac = await _node(dbsession, MAC)
    await _member(dbsession, ray_env, mac)
    with pytest.raises(ConflictError, match="already a member"):
        await EnvironmentService(dbsession).create_standalone(mac.id)


async def test_the_endpoint_is_201_then_200(authed_fapp: FastAPI, client: AsyncClient, dbsession: AsyncSession) -> None:
    mac = await _node(dbsession, MAC)
    await dbsession.commit()
    url = f"{API}/environments/standalone"
    first = await client.post(url, json={"node_id": str(mac.id)})
    second = await client.post(url, json={"node_id": str(mac.id)})
    assert (first.status_code, second.status_code) == (201, 200), first.text
    assert first.json()["id"] == second.json()["id"]


async def _deployer(session: AsyncSession) -> tuple[Any, Any, Any]:
    env = await _environment(session)
    node = await _node(session, {**MAC, "metal": {"unified_memory_bytes": 64 * 2**30}})
    await _member(session, env, node)
    model = LLMModel(display_name="m", source=ModelSource.HUGGINGFACE, status=ModelStatus.AVAILABLE, hf_repo_id="o/m")
    session.add(model)
    await session.flush()
    service = DeploymentService(session)
    create = service.create

    async def guarded(**kw: Any) -> Any:
        return await create(allow_guarded=True, **kw)

    service.create = guarded  # type: ignore[method-assign]
    return env, model, service


@pytest.mark.parametrize(
    ("config", "words"),
    [
        ({"quantization": "gptq"}, "GPTQ"),
        ({"kv_cache_dtype": "fp8"}, "KV cache"),
        ({"enable_prefix_caching": True}, "prefix caching"),
        ({"gpu_memory_utilization": 0.95}, "at most about"),
    ],
)
async def test_a_mac_refuses_what_it_cannot_run(dbsession: AsyncSession, config: dict[str, Any], words: str) -> None:
    env, model, service = await _deployer(dbsession)
    with pytest.raises(ConflictError, match=words):
        await service.create(environment_id=env.id, model_id=model.id, name="a",
                             spec=_spec(engine={"name": "vllm", "config": config}))


async def test_a_mac_runs_one_model_at_a_time(dbsession: AsyncSession) -> None:
    env, model, service = await _deployer(dbsession)
    await service.create(environment_id=env.id, model_id=model.id, name="first", spec=_spec())
    with pytest.raises(ConflictError, match="one model at a time"):
        await service.create(environment_id=env.id, model_id=model.id, name="second", spec=_spec())


async def test_the_downgraded_agent_still_opens_its_environment(dbsession: AsyncSession) -> None:
    service = EnvironmentService(dbsession)
    mac = await _node(dbsession, MAC)
    env, _ = await service.create_standalone(mac.id)
    mac.capabilities_json = {k: v for k, v in MAC.items() if k != "native_runtime"}
    await dbsession.flush()
    again, created = await service.create_standalone(mac.id, name="another")
    assert again.id == env.id and created is False


async def test_two_concurrent_requests_get_the_same_environment(_engine: AsyncEngine) -> None:
    """Real connections: the second request waits for the first and returns its environment."""
    from faden_backend.db.models.inference import (
        InferenceControlPlane,
        InferenceEnvironment,
        InferenceEnvironmentNode,
    )
    from faden_backend.db.models.node_control import InfraNode

    maker = async_sessionmaker(_engine, expire_on_commit=False)
    async with maker() as setup:
        node = InfraNode(agent_id="race-mac", host="10.9.9.9", status="healthy", capabilities_json=dict(MAC))
        setup.add(node)
        await setup.commit()
        node_id = node.id

    async def request(name: str) -> tuple[Any, bool]:
        async with maker() as session:
            result = await EnvironmentService(session).create_standalone(node_id, name)
            await session.commit()
            return result

    try:
        (a, made_a), (b, made_b) = await asyncio.gather(request("first"), request("second"))
        assert a.id == b.id
        assert sorted([made_a, made_b]) == [False, True]
        async with maker() as check:
            envs = (await check.execute(select(InferenceEnvironmentNode).where(InferenceEnvironmentNode.node_id == node_id))).scalars().all()
            assert len(envs) == 1
    finally:
        async with maker() as cleanup:
            member_envs = [m.environment_id for m in (await cleanup.execute(
                select(InferenceEnvironmentNode).where(InferenceEnvironmentNode.node_id == node_id))).scalars()]
            planes = [e.control_plane_id for e in (await cleanup.execute(
                select(InferenceEnvironment).where(InferenceEnvironment.id.in_(member_envs)))).scalars()]
            await cleanup.execute(delete(InferenceEnvironmentNode).where(InferenceEnvironmentNode.node_id == node_id))
            await cleanup.execute(delete(InferenceEnvironment).where(InferenceEnvironment.id.in_(member_envs)))
            await cleanup.execute(delete(InferenceControlPlane).where(InferenceControlPlane.id.in_(planes)))
            await cleanup.execute(delete(InfraNode).where(InfraNode.id == node_id))
            await cleanup.commit()
