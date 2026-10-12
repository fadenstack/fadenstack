"""A release that mounts things differently must not break the cluster it finds running.

On the test server, an upgrade changed where the GB10 runtime mounts ``/models``.
The running containers kept the old mounts (a container keeps them until it is
made again), but the reconciler translated each model's host path with the new
ones: the path changed, so did the config hash, and the serving model was
applied again into containers that did not hold it -- DEPLOY_FAILED, fixed only
by a manual stop and start.

Now the backend remembers what it started each machine's container with and
translates with that; the console is told the new settings are pending, and the
operator restarts when it suits them. Nothing restarts by itself.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.node_control_dao import NodeControlDAO
from faden_backend.db.models.inference import InferenceEnvironment
from faden_backend.db.models.node_control import InfraNode, NodeCommandType
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.inference import runtime_choice
from faden_backend.services.inference.bundles import LAUNCH_KEY, default_bundle_registry
from faden_backend.services.inference.drivers.ray import deployment as deployment_module
from faden_backend.services.inference.drivers.ray.deployment import RayDeploymentManager
from faden_backend.services.inference.drivers.ray.environment import RayEnvironmentManager
from faden_backend.web.api.inference.environments import router as environments_router
from tests.test_inference_env_observation import _bound_environment, _FakeNodeControl
from tests.test_inference_ray import deployment_env  # noqa: F401 - a fixture

OLD = {"model_store": "/srv/faden/models", "ray_session": "/var/lib/faden/ray"}
NEW = {
    "model_store": "/home/user/.cache/huggingface",
    "ray_session": "/var/lib/faden/ray",
    "hf_cache": "/home/user/.cache/huggingface/hub",
}
# Where the model sits on the machine: under the Hugging Face hub directory.
MODEL_ROOT = "/home/user/.cache/huggingface/hub/models--Qwen--Qwen3"


def _mounts(paths: dict[str, str], node: InfraNode) -> list[dict[str, str]]:
    shown = InfraNode(capabilities_json={**(node.capabilities_json or {}), "paths": paths})
    bundle = default_bundle_registry.resolve_for_node(shown)
    return [m.model_dump() for m in default_bundle_registry.mounts_for_node(bundle, shown)]


async def _machines(dbsession: AsyncSession, env: InferenceEnvironment) -> list[InfraNode]:
    from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO  # noqa: PLC0415

    return list(await EnvironmentNodeDAO(dbsession).machines(env.id))


async def _move_mounts(dbsession: AsyncSession, nodes: list[InfraNode], paths: dict[str, str]) -> None:
    """What a new release does: the machines report paths the runtime now mounts."""
    for node in nodes:
        node.capabilities_json = {**node.capabilities_json, "paths": paths}
    await dbsession.flush()


# -- the record --------------------------------------------------------------------------


@pytest.mark.anyio()
async def test_ensure_records_the_mounts_it_sent_each_machine(dbsession: AsyncSession) -> None:
    env = await _bound_environment(dbsession)
    fleet = _FakeNodeControl()

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=fleet)

    sent = {
        str(cmd["node_id"]): cmd["payload"]["runtime_bundle"]
        for cmd in fleet.by_type(NodeCommandType.ENSURE_RAY_RUNTIME.value)
    }
    recorded = env.observed_status_json[LAUNCH_KEY]
    assert sent and set(recorded) == set(sent)
    for node_id, payload in sent.items():
        assert recorded[node_id]["mounts"] == payload["mounts"]
        assert recorded[node_id]["bundle_id"]
    assert any(m["container_path"] == "/models" for m in next(iter(recorded.values()))["mounts"])


# -- translating a model's path ---------------------------------------------------------------


@pytest.fixture()
def ready_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The head holds the model at MODEL_ROOT; whether the copy is ready is not what is tested."""

    class _Coordinator:
        def __init__(self, *_: Any, **__: Any) -> None: ...

        async def evaluate(self, *, model: Any, environment: Any) -> Any:
            head = str(environment.head_node_id)
            return SimpleNamespace(root_paths={head: MODEL_ROOT}, all_ready=True, blockers=[], waiting_on=None)

    monkeypatch.setattr(deployment_module, "ModelArtifactCoordinator", _Coordinator)


async def _container_path(dbsession: AsyncSession, deployment: Any) -> str | None:
    facts = await RayDeploymentManager()._resolve(dbsession, deployment)  # noqa: SLF001
    assert not isinstance(facts, deployment_module._ResolveError), facts  # noqa: SLF001
    return facts.availability_root_path


@pytest.mark.anyio()
async def test_a_release_that_mounts_differently_leaves_a_serving_model_as_it_is(
    deployment_env, dbsession: AsyncSession, ready_model: None,  # noqa: F811
) -> None:
    """The incident: the path (and so the config hash) must not move under a container that keeps the old mounts."""
    deployment, node = deployment_env
    env = await dbsession.get(InferenceEnvironment, deployment.environment_id)
    node.capabilities_json = {**node.capabilities_json, "paths": NEW | {"model_store": OLD["model_store"]}}
    # The container was started when /models came from the old store and the cache was mounted as well.
    started = _mounts(node.capabilities_json["paths"], node)
    env.observed_status_json = {LAUNCH_KEY: {str(node.id): {"bundle_id": "x", "mounts": started}}}
    before = await _container_path(dbsession, deployment)
    assert before == "/root/.cache/huggingface/hub/models--Qwen--Qwen3"

    # The upgrade: /models is now the machine's own store, which also contains the model.
    await _move_mounts(dbsession, [node], NEW)
    assert await _container_path(dbsession, deployment) == before  # same path, same hash, no re-apply


@pytest.mark.anyio()
async def test_without_a_record_the_current_mounts_are_used(
    deployment_env, dbsession: AsyncSession, ready_model: None,  # noqa: F811
) -> None:
    deployment, node = deployment_env
    await _move_mounts(dbsession, [node], NEW)

    assert await _container_path(dbsession, deployment) == "/models/hub/models--Qwen--Qwen3"


@pytest.mark.anyio()
async def test_without_a_record_what_the_agent_reports_stands_in(
    deployment_env, dbsession: AsyncSession, ready_model: None,  # noqa: F811
) -> None:
    """A cluster started before the record was kept: its agent says what the container has."""
    deployment, node = deployment_env
    await _move_mounts(dbsession, [node], NEW)
    reported = _mounts(OLD | {"hf_cache": NEW["hf_cache"]}, node)
    await NodeControlDAO(dbsession).upsert_inventory_snapshot(
        node_id=node.id, inventory_json={"ray_runtime": {"running": True, "mounts": reported}}, utilization_json={},
    )

    assert await _container_path(dbsession, deployment) == "/root/.cache/huggingface/hub/models--Qwen--Qwen3"


# -- the pending settings, and applying them ----------------------------------------------------


async def _running_cluster(dbsession: AsyncSession) -> tuple[InferenceEnvironment, list[InfraNode]]:
    """A cluster started under the old mounts, reconciled once so that its record is real."""
    env = await _bound_environment(dbsession)
    nodes = await _machines(dbsession, env)
    await _move_mounts(dbsession, nodes, OLD)
    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=_FakeNodeControl())
    env.observed_generation = env.generation
    return env, nodes


@pytest.mark.anyio()
async def test_nothing_is_pending_while_the_record_matches_this_release(dbsession: AsyncSession) -> None:
    env, _ = await _running_cluster(dbsession)

    status = await runtime_choice.environment_status(dbsession, env.id)

    assert status["settings_pending"] is False and status["settings_reasons"] == []
    assert status["applied"] is True and status["restarting"] is False


@pytest.mark.anyio()
async def test_new_mounts_are_pending_not_restarting(dbsession: AsyncSession) -> None:
    env, nodes = await _running_cluster(dbsession)
    generation = env.generation
    await _move_mounts(dbsession, nodes, NEW)

    status = await runtime_choice.environment_status(dbsession, env.id)

    assert status["settings_pending"] is True and status["settings_reasons"] == ["mounts"]
    # Not a restart under way, so no "Restarting" chip; and no restart by itself.
    assert status["applied"] is True and status["restarting"] is False and status["settings_applying"] is False
    assert env.generation == generation


@pytest.mark.anyio()
async def test_a_stopped_cluster_has_nothing_to_apply(dbsession: AsyncSession) -> None:
    env, nodes = await _running_cluster(dbsession)
    await _move_mounts(dbsession, nodes, NEW)
    env.desired_state = "stopped"

    assert (await runtime_choice.environment_status(dbsession, env.id))["settings_pending"] is False


@pytest.mark.anyio()
async def test_applying_restarts_the_cluster_once_and_the_notice_goes_with_the_new_record(
    dbsession: AsyncSession,
) -> None:
    env, nodes = await _running_cluster(dbsession)
    await _move_mounts(dbsession, nodes, NEW)
    generation = env.generation

    status = await runtime_choice.apply_settings(dbsession, env.id)

    assert env.generation == generation + 1
    assert status["settings_pending"] is True and status["settings_applying"] is True
    # Asked again while it restarts: no second restart.
    await runtime_choice.apply_settings(dbsession, env.id)
    assert env.generation == generation + 1

    # The reconciler's pass: the machines are started with this release's mounts and the record follows.
    fleet = _FakeNodeControl()
    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=fleet)
    env.observed_generation = env.generation
    status = await runtime_choice.environment_status(dbsession, env.id)
    assert status["settings_pending"] is False and status["settings_applying"] is False
    sent = fleet.by_type(NodeCommandType.ENSURE_RAY_RUNTIME.value)
    assert sent and all(
        any(m["host_path"] == NEW["model_store"] for m in cmd["payload"]["runtime_bundle"]["mounts"]) for cmd in sent
    )


@pytest.mark.anyio()
async def test_applying_with_nothing_pending_restarts_nothing(dbsession: AsyncSession) -> None:
    env, _ = await _running_cluster(dbsession)
    generation = env.generation

    await runtime_choice.apply_settings(dbsession, env.id)

    assert env.generation == generation


# -- who may ----------------------------------------------------------------------------------------


def _permission_of(path: str, method: str) -> tuple[str, str]:
    route = next(
        r for r in environments_router.routes
        if isinstance(r, APIRoute) and r.path == path and method in r.methods
    )
    for dep in route.dependant.dependencies:
        call = dep.call
        if getattr(call, "__qualname__", "").endswith("_guard"):
            free = dict(zip(call.__code__.co_freevars, (c.cell_contents for c in call.__closure__), strict=True))
            return free["resource"], free["action"]
    raise AssertionError(f"{method} {path} has no permission guard")


def test_applying_settings_takes_the_permission_a_runtime_switch_takes() -> None:
    base = "/{environment_id}/runtime"
    assert _permission_of(base + "/apply-settings", "POST") == _permission_of(base, "POST")
    assert _permission_of(base + "/apply-settings", "POST")[1] == "update"


@pytest.mark.anyio()
async def test_a_user_without_the_permission_is_refused_and_an_admin_restarts(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    env, nodes = await _running_cluster(dbsession)
    await _move_mounts(dbsession, nodes, NEW)
    generation = env.generation
    url = f"/api/inference/environments/{env.id}/runtime/apply-settings"

    def user(superuser: bool) -> User:
        return User(id=uuid.uuid4(), email=f"u{uuid.uuid4().hex[:6]}@example.com", hashed_password="x",
                    is_active=True, is_verified=True, is_superuser=superuser)

    plain = user(False)
    fastapi_app.dependency_overrides[current_active_user] = lambda: plain
    assert (await client.post(url)).status_code == 403
    assert env.generation == generation

    admin = user(True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    response = await client.post(url)
    assert response.status_code == 200, response.text
    assert response.json()["settings_applying"] is True
    assert env.generation == generation + 1
