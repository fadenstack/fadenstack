"""A machine running node agent 0.2.12 is matched exactly as backend 0.7.2 matched it.

The capability documents below have the exact key set 0.2.12 reports
(``git show node-agent-v0.2.12:agent/faden_agent/preflight.py`` plus the static
capabilities its service adds): ``os``, ``machine`` and ``gpu_vendor`` are always
there. For every Ray runtime in the catalogue, the current matcher must give the
answer 0.7.2's did (``fixtures/compat_0_7_2_bundle_match.py``), and every
machine must resolve to the same default runtime.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from faden_backend.services.inference.bundles import default_bundle_registry
from tests.fixtures import compat_0_7_2_bundle_match as v072


def _caps_0_2_12(os_: str, machine: str, vendor: str, gpu_count: int) -> dict[str, Any]:
    return {
        "paths": {"model_store": "/var/lib/faden/models", "ray_session": "/var/lib/faden/ray"},
        "hostname": "node",
        "os": os_,
        "machine": machine,
        "processor": machine,
        "docker_available": True,
        "gpu_count": gpu_count,
        "gpu_vendor": vendor,
        "container_runtime": "docker",
        "model_tls": 1,
        "ray_tls": 2,
        "model_download": 1,
        "command_continuity": 1,
        "ray_drain": 1,
        "serve_scale": 1,
    }


NODES_0_2_12 = {
    "dgx-spark-gb10": _caps_0_2_12("Linux-6.11.0-1016-nvidia-aarch64-with-glibc2.39", "aarch64", "nvidia", 1),
    "x86-nvidia": _caps_0_2_12("Linux-6.8.0-88-generic-x86_64-with-glibc2.39", "x86_64", "nvidia", 2),
    "x86-amd": _caps_0_2_12("Linux-6.8.0-88-generic-x86_64-with-glibc2.39", "x86_64", "amd", 1),
    "x86-cpu-only": _caps_0_2_12("Linux-6.8.0-88-generic-x86_64-with-glibc2.39", "x86_64", "none", 0),
    "arm64-cpu-only": _caps_0_2_12("Linux-6.8.0-1009-raspi-aarch64-with-glibc2.39", "aarch64", "none", 0),
    "windows-nvidia": _caps_0_2_12("Windows-11-10.0.26100-SP0", "AMD64", "nvidia", 1),
    "mac-0.2.12": _caps_0_2_12("macOS-26.1-arm64-arm-64bit", "arm64", "apple", 1),
}


def _node(caps: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), agent_id="n", host="h", capabilities_json=caps)


@pytest.mark.parametrize("name", sorted(NODES_0_2_12))
def test_every_ray_runtime_matches_a_0_2_12_machine_as_on_0_7_2(name: str) -> None:
    node = _node(NODES_0_2_12[name])
    for bundle in default_bundle_registry.bundles_for_driver("ray"):
        now, _ = default_bundle_registry.validate_node_compatibility(bundle, node)
        then, _ = v072.validate_node_compatibility(bundle, node)
        assert now == then, f"{name} x {bundle.bundle_id}: now {now}, 0.7.2 {then}"


@pytest.mark.parametrize("name", sorted(NODES_0_2_12))
def test_a_0_2_12_machine_gets_the_runtime_0_7_2_gave_it(name: str) -> None:
    node = _node(NODES_0_2_12[name])
    ray = default_bundle_registry.bundles_for_driver("ray")
    then = default_bundle_registry.default_among(v072.runtimes_for(ray, node))
    now = default_bundle_registry.resolve_for_node(node, driver="ray")
    assert (now and now.bundle_id) == (then and then.bundle_id)


def test_the_expected_machines_get_a_runtime() -> None:
    """Guard the fixture itself: the NVIDIA Linux machines do resolve."""
    for name in ("dgx-spark-gb10", "x86-nvidia"):
        assert default_bundle_registry.resolve_for_node(_node(NODES_0_2_12[name]), driver="ray") is not None


# ── a running machine whose report is empty keeps its runtime ────────────

from faden_backend.services.inference import bundles as bundles_module  # noqa: E402


def _ray_env(node_id: Any, bundle_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), config_json={},
        observed_status_json={bundles_module.RUNTIME_KEY: {str(node_id): bundle_id}},
    )


@pytest.mark.parametrize(
    "caps",
    [{}, {"paths": {}}, {"machine": "aarch64"}, {"hostname": "spark-1", "docker_available": True}],
    ids=["empty", "paths-only", "machine-only", "no-platform"],
)
def test_a_started_machine_with_an_empty_report_keeps_its_runtime(caps: dict[str, Any]) -> None:
    gb10 = _node(NODES_0_2_12["dgx-spark-gb10"])
    started = default_bundle_registry.resolve_for_node(gb10, driver="ray")
    assert started is not None
    blank = SimpleNamespace(id=gb10.id, agent_id="n", host="h", capabilities_json=caps)
    env = _ray_env(blank.id, started.bundle_id)

    assert default_bundle_registry.resolve_for_node(blank, driver="ray", environment=env) == started
    # Never another driver's runtime, and placement (no environment) gets nothing.
    assert default_bundle_registry.resolve_for_node(blank, driver="standalone", environment=env) is None
    assert default_bundle_registry.resolve_for_node(blank, driver="ray") is None


def test_a_machine_with_no_runtime_yet_is_told_it_has_not_reported() -> None:
    from faden_backend.services.inference.planner import MultiNodeFabricPlanner

    blank = SimpleNamespace(id=uuid.uuid4(), agent_id="n", host="h", capabilities_json={})
    readiness = MultiNodeFabricPlanner._runtime_readiness(None, [blank])  # noqa: SLF001
    assert not readiness.compatible
    assert "has not reported its platform yet" in readiness.node_results[str(blank.id)]


@pytest.mark.anyio()
async def test_a_running_ray_cluster_is_not_failed_when_a_machine_reconnects_blank(dbsession: Any) -> None:
    """Reconciliation never re-resolves a running machine away because its report is momentarily empty."""
    from faden_backend.db.models.inference import InferenceControlPlane, InferenceEnvironment
    from faden_backend.db.models.node_control import InfraNode
    from faden_backend.services.inference.drivers.ray.environment import RayEnvironmentManager

    gb10_caps = NODES_0_2_12["dgx-spark-gb10"]
    started = default_bundle_registry.resolve_for_node(_node(gb10_caps), driver="ray")
    node = InfraNode(agent_id="spark-1", host="10.0.5.1", status="healthy", capabilities_json={})
    dbsession.add(node)
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
        observed_status_json={bundles_module.RUNTIME_KEY: {str(node.id): started.bundle_id}},
    )
    dbsession.add(env)
    await dbsession.flush()

    member = SimpleNamespace(node_id=node.id)
    resolved = await RayEnvironmentManager()._bundles_for(dbsession, env, [member])  # noqa: SLF001
    assert resolved[node.id][0] == started


# ── the model-TLS route a Ray or adopted model sends is 0.7.2's, byte for byte ──


@pytest.mark.anyio()
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"public_port": 8000, "internal_port": 28000}, b'{"action": "ensure", "public_port": 8000, "internal_port": 28000}'),
        ({"public_port": 8000, "remove": True}, b'{"action": "remove", "public_port": 8000}'),
    ],
)
async def test_ray_and_adopted_routes_send_exactly_what_0_7_2_sent(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], expected: bytes,
) -> None:
    """``owner``/``generation`` are for standalone routes only (CONTRACTS C1, B1-1).

    found.py (adopted vLLM) and takeover.py both go through this client.
    """
    import json

    from faden_backend.services.inference.drivers.ray.client import RayClusterClient

    sent: list[dict[str, Any]] = []

    async def capture(self: Any, *, command_type: str, payload: dict[str, Any], **_: Any) -> dict[str, Any]:
        sent.append(payload)
        return {"protected": True}

    monkeypatch.setattr(RayClusterClient, "_dispatch_and_poll", capture)
    client = RayClusterClient.__new__(RayClusterClient)
    await client.model_tls_route(node_id=uuid.uuid4(), **kwargs)
    assert json.dumps(sent[0]).encode() == expected
    assert "owner" not in sent[0] and "generation" not in sent[0]


@pytest.mark.anyio()
async def test_only_the_standalone_route_carries_owner_and_generation(dbsession: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_standalone_driver import (
        MAC,
        Script,
        StepOutcome,
        _Ctx,
        _deployment,
        _environment,
        _member,
        _node as _db_node,
    )
    from faden_backend.db.models.inference import EnvironmentStatus
    from faden_backend.services.inference import reconciliation
    from faden_backend.services.inference.drivers.standalone.commands import NativeCommands
    from tests.test_native_bundles import native_bundle

    bundle = native_bundle()
    monkeypatch.setitem(default_bundle_registry._bundles, bundle.bundle_id, bundle)  # noqa: SLF001
    script = Script()

    async def step(self: Any, step: str, **kw: Any) -> Any:
        return await script.step(self, step, **kw)

    async def ask(self: Any, **kw: Any) -> Any:
        return await script.ask(self, **kw)

    monkeypatch.setattr(NativeCommands, "step", step)
    monkeypatch.setattr(NativeCommands, "ask", ask)
    env = await _environment(dbsession)
    await _member(dbsession, env, await _db_node(dbsession, MAC))
    env.status = EnvironmentStatus.READY
    dep = await _deployment(dbsession, env)
    script.steps["start"] = StepOutcome("done", {})
    script.status = {"state": "ready"}
    await reconciliation.reconcile_deployment(_Ctx(dbsession), dep)
    route = next(p for s, _, p in script.asked if s == "route")
    assert set(route) == {"action", "public_port", "internal_port", "owner", "generation"}
