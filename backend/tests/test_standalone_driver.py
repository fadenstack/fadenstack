"""The ``standalone`` driver: one Mac, its native runtime, one engine per deployment.

The agent side (C2) is not here: the commands are answered by a script, and
what is checked is what the backend asks for and how it reads the answers --
the order (start, ready, route, publish; unroute, stop, deleted), the ports
(P public, P+20000 on loopback), the refusals, and that Ray is untouched.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEndpoint,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from faden_backend.db.models.node_control import InfraNode, NodeCommandType
from faden_backend.services.inference import reconciliation
from faden_backend.services.inference.bundles import default_bundle_registry
from faden_backend.services.inference.drivers.standalone.commands import NativeCommands, StepOutcome
from faden_backend.services.inference.registry import registry
from faden_backend.services.inference.service import ConflictError, DeploymentService, EnvironmentService
from tests.test_inference_api import (  # noqa: F401  (fixtures)
    API,
    authed_fapp,
    superuser,
)
from tests.test_native_bundles import GB10, MAC, X86, native_bundle

pytestmark = pytest.mark.anyio()


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mac_runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The catalogue with a certified Mac runtime in it (none is minted in the test tree)."""
    bundle = native_bundle()
    monkeypatch.setitem(default_bundle_registry._bundles, bundle.bundle_id, bundle)  # noqa: SLF001
    return bundle


class Script:
    """Answers NativeCommands.step/ask from a table and records what was asked."""

    def __init__(self) -> None:
        self.steps: dict[str, StepOutcome] = {}
        self.status: dict[str, Any] = {"state": "starting"}
        self.asked: list[tuple[str, str, dict[str, Any]]] = []

    async def step(self, commands: NativeCommands, step: str, *, command_type: str, payload: dict[str, Any], **_: Any) -> StepOutcome:
        self.asked.append((step, command_type, payload))
        commands._record[step] = {"id": str(uuid.uuid4()), "payload": "x", "type": command_type}  # noqa: SLF001
        return self.steps.get(step, StepOutcome("running"))

    async def ask(self, commands: NativeCommands, *, command_type: str, payload: dict[str, Any], **_: Any) -> dict[str, Any] | None:
        self.asked.append(("ask", command_type, payload))
        return self.status

    def types(self) -> list[str]:
        return [t for _, t, _ in self.asked]


@pytest.fixture()
def script(monkeypatch: pytest.MonkeyPatch) -> Script:
    s = Script()

    async def step(self: NativeCommands, step: str, **kw: Any) -> StepOutcome:
        return await s.step(self, step, **kw)

    async def ask(self: NativeCommands, **kw: Any) -> dict[str, Any] | None:
        return await s.ask(self, **kw)

    monkeypatch.setattr(NativeCommands, "step", step)
    monkeypatch.setattr(NativeCommands, "ask", ask)
    return s


async def _node(session: AsyncSession, caps: dict[str, Any], host: str = "10.0.0.9") -> InfraNode:
    node = InfraNode(agent_id=f"mac-{uuid.uuid4().hex[:6]}", host=host, status="healthy", capabilities_json=dict(caps))
    session.add(node)
    await session.flush()
    return node


async def _environment(session: AsyncSession, driver: str = "standalone") -> InferenceEnvironment:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver=driver)
    session.add(cp)
    await session.flush()
    env = InferenceEnvironment(control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running")
    session.add(env)
    await session.flush()
    return env


async def _member(session: AsyncSession, env: InferenceEnvironment, node: InfraNode) -> None:
    session.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))
    await session.flush()


def _spec(**over: Any) -> dict[str, Any]:
    spec = {
        "api_version": "inference.fadenstack.com/v1alpha1",
        "engine": {"name": "vllm", "config": {}},
        "scale": {"replicas": 1},
        "artifacts": {"source": "local_path", "root_path": "/var/db/fadenstack/agent/models/qwen3-0.6b"},
        "service": {"path": "/v1"},
    }
    spec.update(over)
    return spec


async def _deployment(session: AsyncSession, env: InferenceEnvironment, **spec: Any) -> InferenceDeployment:
    model = LLMModel(
        display_name="Qwen/Qwen3-0.6B", source=ModelSource.HUGGINGFACE, status=ModelStatus.AVAILABLE,
        hf_repo_id="Qwen/Qwen3-0.6B", hf_revision="main",
    )
    session.add(model)
    await session.flush()
    dep = InferenceDeployment(environment_id=env.id, model_id=model.id, name=f"dep-{uuid.uuid4().hex[:8]}", spec_json=_spec(**spec))
    session.add(dep)
    await session.flush()
    return dep


class _Ctx:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.node_control = object()
        self.gateway_sync = None
        self.deployments = DeploymentService(session)
        self.environments = EnvironmentService(session)


# ── registration and membership ──────────────────────────────────────────


async def test_the_standalone_driver_is_registered() -> None:
    assert "standalone" in registry.keys()
    driver = registry.get("standalone")()
    assert driver.environment_manager is not None and driver.deployment_manager is not None


async def test_a_standalone_environment_takes_one_capable_mac(dbsession: AsyncSession) -> None:
    env = await _environment(dbsession)
    service = EnvironmentService(dbsession)

    old_agent = await _node(dbsession, {k: v for k, v in MAC.items() if k != "native_runtime"})
    with pytest.raises(ConflictError, match="native model runtime"):
        await service.add_node(env.id, old_agent.id)

    mac = await _node(dbsession, MAC)
    await service.add_node(env.id, mac.id)

    second = await _node(dbsession, MAC, host="10.0.0.10")
    with pytest.raises(ConflictError, match="exactly one machine"):
        await service.add_node(env.id, second.id)


async def test_a_deployment_a_mac_cannot_serve_is_refused_at_once(dbsession: AsyncSession) -> None:
    env = await _environment(dbsession)
    model = LLMModel(display_name="m", source=ModelSource.HUGGINGFACE, status=ModelStatus.AVAILABLE, hf_repo_id="o/m")
    dbsession.add(model)
    await dbsession.flush()
    service = DeploymentService(dbsession)
    service_create = service.create

    async def create(**kw: Any) -> Any:  # a superuser: the model-store path guard is not what this is about
        return await service_create(allow_guarded=True, **kw)

    service.create = create  # type: ignore[method-assign]

    with pytest.raises(ConflictError, match="has no machine yet"):
        await service.create(environment_id=env.id, model_id=model.id, name="a", spec=_spec())

    node = await _node(dbsession, {k: v for k, v in MAC.items() if k != "model_tls_native"})
    await _member(dbsession, env, node)
    with pytest.raises(ConflictError, match="model-TLS proxy"):
        await service.create(environment_id=env.id, model_id=model.id, name="b", spec=_spec())

    node.capabilities_json = {**MAC, "metal": {"recommended_max_working_set_bytes": 40 * 2**30}}
    await dbsession.flush()
    with pytest.raises(ConflictError, match="one copy"):
        await service.create(environment_id=env.id, model_id=model.id, name="c", spec=_spec(scale={"replicas": 2}))
    with pytest.raises(ConflictError, match="asks for"):
        await service.create(
            environment_id=env.id, model_id=model.id, name="d",
            spec=_spec(resources={"replica": {"memory": "64Gi"}}),
        )
    created = await service.create(environment_id=env.id, model_id=model.id, name="e", spec=_spec())
    assert created.id


# ── the environment ──────────────────────────────────────────────────────


async def test_the_environment_is_ready_once_its_runtime_is_staged(
    dbsession: AsyncSession, script: Script, _mac_runtime: Any,
) -> None:
    env = await _environment(dbsession)
    mac = await _node(dbsession, MAC)
    await _member(dbsession, env, mac)
    ctx = _Ctx(dbsession)

    await reconciliation.reconcile_environment(ctx, env)
    assert env.status == EnvironmentStatus.PREPARING
    step, kind, payload = script.asked[0]
    assert (step, kind) == ("stage", NodeCommandType.STAGE_NATIVE_RUNTIME.value)
    assert payload["oci_image"] == "ghcr.io/fadenstack/mac-runtime-arm64"
    assert payload["tag"] == "vllm0.34.0-metal0.34.0-1"
    assert payload["layer_digest"] == _mac_runtime.native.layer_digest
    assert payload["rootfs_digest"] == _mac_runtime.native.rootfs_digest

    script.steps["stage"] = StepOutcome("done", {"staged": True, "runtime_root": "/var/db/fadenstack/runtimes/x"})
    await reconciliation.reconcile_environment(ctx, env)
    assert env.status == EnvironmentStatus.READY
    assert env.head_node_id == mac.id
    member = (await dbsession.execute(select(InferenceEnvironmentNode).where(InferenceEnvironmentNode.environment_id == env.id))).scalar_one()
    assert member.member_status == "alive"


async def test_a_failed_stage_fails_the_environment_with_the_reason(dbsession: AsyncSession, script: Script) -> None:
    env = await _environment(dbsession)
    await _member(dbsession, env, await _node(dbsession, MAC))
    script.steps["stage"] = StepOutcome("failed", {}, "layer digest mismatch")
    await reconciliation.reconcile_environment(_Ctx(dbsession), env)
    assert env.status == EnvironmentStatus.FAILED
    assert "layer digest mismatch" in env.status_message


async def test_a_mac_below_the_minimum_os_fails_the_environment(dbsession: AsyncSession, script: Script) -> None:
    env = await _environment(dbsession)
    await _member(dbsession, env, await _node(dbsession, {**MAC, "os": "macOS-15.6-arm64-arm-64bit"}))
    await reconciliation.reconcile_environment(_Ctx(dbsession), env)
    assert env.status == EnvironmentStatus.FAILED
    assert "No certified native runtime" in env.status_message
    assert script.asked == []


# ── a deployment, start to delete ────────────────────────────────────────


async def _ready_environment(dbsession: AsyncSession) -> tuple[InferenceEnvironment, InfraNode]:
    env = await _environment(dbsession)
    mac = await _node(dbsession, {**MAC, "metal": {"unified_memory_bytes": 64 * 2**30}})
    await _member(dbsession, env, mac)
    env.status = EnvironmentStatus.READY
    await dbsession.flush()
    return env, mac


async def test_a_deployment_starts_routes_and_publishes_in_that_order(dbsession: AsyncSession, script: Script) -> None:
    env, mac = await _ready_environment(dbsession)
    dep = await _deployment(dbsession, env)
    ctx = _Ctx(dbsession)

    await reconciliation.reconcile_deployment(ctx, dep)
    assert dep.phase == DeploymentPhase.APPLYING.value
    start = next(p for s, _, p in script.asked if s == "start")
    public = (dep.observed_status_json or {})["native"]["public_port"]
    assert 8100 <= public < 9000
    assert start["port"] == public + 20000
    assert start["host"] == "127.0.0.1"
    assert start["model"]["snapshot_path"] == "/var/db/fadenstack/agent/models/qwen3-0.6b"
    assert start["gpu_memory_utilization"] == 0.8
    assert (dep.observed_status_json or {})["native"]["memory"]["estimated"] is True

    script.steps["start"] = StepOutcome("done", {"pid": 4242, "port": public + 20000})
    await reconciliation.reconcile_deployment(ctx, dep)
    assert dep.phase == DeploymentPhase.APPLYING.value
    assert not any(s == "route" for s, _, _ in script.asked), "no route before the engine is ready"

    script.status = {"state": "ready", "port": public + 20000}
    await reconciliation.reconcile_deployment(ctx, dep)
    route = next(p for s, _, p in script.asked if s == "route")
    assert route == {
        "action": "ensure", "public_port": public, "internal_port": public + 20000,
        "owner": str(dep.id), "generation": dep.generation,
    }
    assert dep.phase == DeploymentPhase.APPLYING.value, "not published before the route is confirmed"

    script.steps["route"] = StepOutcome("done", {"protected": True})
    await reconciliation.reconcile_deployment(ctx, dep)
    assert dep.phase == DeploymentPhase.RUNNING.value
    endpoint = (await dbsession.execute(select(InferenceEndpoint).where(InferenceEndpoint.deployment_id == dep.id))).scalar_one()
    assert endpoint.status == EndpointStatus.PUBLISHED.value
    assert endpoint.published_json["base_url"] == f"https://10.0.0.9:{public}/v1"
    assert endpoint.published_json["machine_tls"] is True
    assert endpoint.published_json["driver"] == "standalone"


async def test_an_engine_that_died_fails_with_its_error(dbsession: AsyncSession, script: Script) -> None:
    env, _ = await _ready_environment(dbsession)
    dep = await _deployment(dbsession, env)
    script.steps["start"] = StepOutcome("done", {"pid": 1})
    script.status = {"state": "failed", "last_error": "Metal out of memory"}
    await reconciliation.reconcile_deployment(_Ctx(dbsession), dep)
    assert dep.phase == DeploymentPhase.FAILED.value
    assert "Metal out of memory" in dep.phase_message


async def test_two_deployments_get_two_ports(dbsession: AsyncSession, script: Script) -> None:
    env, _ = await _ready_environment(dbsession)
    a = await _deployment(dbsession, env)
    b = await _deployment(dbsession, env)
    ctx = _Ctx(dbsession)
    await reconciliation.reconcile_deployment(ctx, a)
    await dbsession.flush()
    await reconciliation.reconcile_deployment(ctx, b)
    ports = {d.observed_status_json["native"]["public_port"] for d in (a, b)}
    assert len(ports) == 2


async def test_a_delete_unroutes_then_stops_then_sets_deleted_itself(dbsession: AsyncSession, script: Script) -> None:
    env, _ = await _ready_environment(dbsession)
    dep = await _deployment(dbsession, env)
    ctx = _Ctx(dbsession)
    script.steps.update(start=StepOutcome("done", {}), route=StepOutcome("done", {}))
    script.status = {"state": "ready"}
    await reconciliation.reconcile_deployment(ctx, dep)
    assert dep.phase == DeploymentPhase.RUNNING.value
    public = dep.observed_status_json["native"]["public_port"]

    dep.desired_state = DeploymentDesiredState.DELETED.value
    script.asked.clear()
    await reconciliation.reconcile_deployment(ctx, dep)
    assert dep.phase == DeploymentPhase.STOPPING.value
    assert script.asked[0][0] == "unroute"
    assert script.asked[0][2]["action"] == "remove" and script.asked[0][2]["public_port"] == public

    script.steps["unroute"] = StepOutcome("done", {})
    await reconciliation.reconcile_deployment(ctx, dep)
    assert [s for s, _, _ in script.asked] == ["unroute", "unroute", "stop"]
    assert dep.phase == DeploymentPhase.STOPPING.value

    script.steps["stop"] = StepOutcome("done", {"stopped": True})
    dep_id = dep.id
    result = await reconciliation.reconcile_deployment(ctx, dep)
    assert result["reason"] == "deleted", "the manager sets DELETED and the reconciler purges the row"
    await dbsession.flush()
    assert await dbsession.get(InferenceDeployment, dep_id) is None


async def test_a_deployment_on_a_mac_that_lost_its_capability_fails_instead_of_waiting(
    dbsession: AsyncSession, script: Script,
) -> None:
    env, mac = await _ready_environment(dbsession)
    dep = await _deployment(dbsession, env)
    mac.capabilities_json = {k: v for k, v in MAC.items() if k != "native_runtime"}
    await dbsession.flush()
    await reconciliation.reconcile_deployment(_Ctx(dbsession), dep)
    assert dep.phase == DeploymentPhase.FAILED.value
    assert "Update the agent" in dep.phase_message
    assert dep.observed_generation == dep.generation


# ── the four places that said "ray", with a Ray environment ──────────────


async def test_reconcile_runs_the_ray_only_steps_for_ray_and_only_for_ray(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch, script: Script,
) -> None:
    calls: list[tuple[str, Any]] = []

    async def member_status(ctx: Any, env: Any) -> None:
        calls.append(("member", env.id))

    async def prometheus(ctx: Any, env: Any, driver: Any = None) -> None:
        calls.append(("prometheus", getattr(driver, "key", None)))

    class _RayEnv:
        async def reconcile_environment(self, session: Any, environment: Any, **_: Any) -> None:
            return None

    ray_cls = registry.get("ray")
    monkeypatch.setattr(ray_cls, "__init__", lambda self: setattr(self, "environment_manager", _RayEnv()))
    monkeypatch.setattr(reconciliation, "_sync_member_status", member_status)
    monkeypatch.setattr(reconciliation, "_sync_prometheus_targets", prometheus)

    ray_env = await _environment(dbsession, driver="ray")
    await reconciliation.reconcile_environment(_Ctx(dbsession), ray_env)
    assert calls == [("member", ray_env.id), ("prometheus", "ray")]

    calls.clear()
    mac_env = await _environment(dbsession)
    await _member(dbsession, mac_env, await _node(dbsession, MAC))
    await reconciliation.reconcile_environment(_Ctx(dbsession), mac_env)
    assert calls == []


async def test_the_hardware_view_resolves_each_cluster_under_its_own_driver(dbsession: AsyncSession) -> None:
    from faden_backend.services.marketplace.hardware import cluster_hardware

    ray_env = await _environment(dbsession, driver="ray")
    await _member(dbsession, ray_env, await _node(dbsession, GB10, host="10.0.1.1"))
    mac_env = await _environment(dbsession)
    await _member(dbsession, mac_env, await _node(dbsession, MAC, host="10.0.1.2"))

    by_name = {c.name: c for c in await cluster_hardware(dbsession)}
    ray_bundle = default_bundle_registry.resolve_for_node(
        (await dbsession.execute(select(InfraNode).where(InfraNode.host == "10.0.1.1"))).scalar_one(), driver="ray",
    )
    assert by_name[ray_env.name].vllm_version == ray_bundle.compatibility_matrix.vllm_version
    assert by_name[mac_env.name].vllm_version == "0.34.0"


async def test_the_planner_asks_for_the_environments_driver(dbsession: AsyncSession) -> None:
    from faden_backend.services.inference.planner import MultiNodeFabricPlanner as InferenceEnvironmentPlanner

    gb10 = await _node(dbsession, GB10, host="10.0.2.1")
    mac = await _node(dbsession, MAC, host="10.0.2.2")
    ray = InferenceEnvironmentPlanner._runtime_readiness(None, [gb10])  # noqa: SLF001 - default is Ray, as before
    assert ray.compatible
    assert InferenceEnvironmentPlanner._runtime_readiness(None, [gb10], driver="ray") == ray  # noqa: SLF001
    native = InferenceEnvironmentPlanner._runtime_readiness(None, [mac], driver="standalone")  # noqa: SLF001
    assert native.compatible
    refused = InferenceEnvironmentPlanner._runtime_readiness(None, [mac], driver="ray")  # noqa: SLF001
    assert not refused.compatible
    assert "No certified Ray runtime bundle" in refused.detail or any(
        "No certified Ray runtime bundle" in v for v in refused.node_results.values()
    )


async def test_the_runtime_list_asks_each_bundle_under_its_driver(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession,  # noqa: F811
) -> None:
    mac = await _node(dbsession, MAC, host="10.0.3.1")
    x86 = await _node(dbsession, X86, host="10.0.3.2")
    r = await client.get(f"{API}/runtime-bundles", params={"node_id": [str(mac.id), str(x86.id)]})
    assert r.status_code == 200, r.text
    rows = {b["bundle_id"]: b for b in r.json()}
    native = rows["faden-native-vllm-metal-darwin-arm64-vllm0.34.0"]
    assert native["image"] == "ghcr.io/fadenstack/mac-runtime-arm64:vllm0.34.0-metal0.34.0-1"
    assert str(mac.id) in native["compatible_node_ids"]
    assert str(x86.id) in native["incompatible"]
    x86_default = default_bundle_registry.resolve_for_node(x86, driver="ray")
    assert str(x86.id) in rows[x86_default.bundle_id]["compatible_node_ids"]
    assert str(mac.id) in rows[x86_default.bundle_id]["incompatible"]


async def test_a_machine_may_fetch_the_native_carrier(dbsession: AsyncSession) -> None:
    from faden_backend.web.api.node_files.views import _images_a_node_may_fetch

    allowed = await _images_a_node_may_fetch(dbsession)
    assert "ghcr.io/fadenstack/mac-runtime-arm64:vllm0.34.0-metal0.34.0-1" in allowed
    for bundle in default_bundle_registry.bundles_for_driver("ray"):
        assert bundle.container.image in allowed or f"{bundle.container.image}:latest" in allowed


async def test_stopping_the_environment_takes_the_engine_down_and_waits(dbsession: AsyncSession, script: Script) -> None:
    env, _ = await _ready_environment(dbsession)
    dep = await _deployment(dbsession, env)
    ctx = _Ctx(dbsession)
    script.steps.update(start=StepOutcome("done", {}), route=StepOutcome("done", {}))
    script.status = {"state": "ready"}
    await reconciliation.reconcile_deployment(ctx, dep)
    assert dep.phase == DeploymentPhase.RUNNING.value
    public = dep.observed_status_json["native"]["public_port"]

    env.desired_state = "stopped"
    script.steps.update(unroute=StepOutcome("done", {}), stop=StepOutcome("done", {}))
    script.asked.clear()
    await reconciliation.reconcile_deployment(ctx, dep)
    assert [s for s, _, _ in script.asked] == ["unroute", "stop"]
    assert dep.phase == DeploymentPhase.PENDING.value
    assert "starts again with it" in dep.phase_message
    assert dep.observed_status_json["native"]["public_port"] == public, "it comes back on the same port"
