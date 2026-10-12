"""Several runtimes per flavor, and which one a cluster runs.

A flavor can ship several runtimes: a certified stable one, a newer one as a
preview, an older one to go back to. A cluster keeps what it was started with
until an operator switches it -- a recovery after an upgrade that ships a newer
runtime does not quietly move a production cluster. The exception is a runtime
the catalogue no longer holds: that cluster gets the default. The catalogues
here are built by the tests, not read from ``runtime-image``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.node_control import InfraNode
from faden_backend.services.inference import runtime_choice
from faden_backend.services.inference.bundles import (
    AcceleratorSpec,
    RuntimeBundleManifest,
    RuntimeBundleRegistry,
    TargetArchitecture,
)

STABLE = "gb10-vllm0.29.0"
PREVIEW = "gb10-vllm0.30.0"
OLDER = "gb10-vllm0.28.0"
WITHDRAWN = "gb10-withdrawn"
X86 = "x86_64-2.58"


def _runtime(runtime_id: str, *, cpu: str = "aarch64", status: str = "passed", channel: str = "stable",
             certified: str | None = "2026-09-29T20:14:15Z") -> RuntimeBundleManifest:
    return RuntimeBundleManifest.from_runtime_manifest(
        {
            "release_tag": f"faden/ray-vllm:{runtime_id}",
            "image_id": "sha256:" + uuid.uuid4().hex * 2,
            "stack_components": {"ray": "2.58.0", "vllm": "0.29.0", "cuda": "13.4"},
            "certification": {"overall_status": status, "channel": channel, "timestamp": certified},
        },
        bundle_id=runtime_id,
        display_name=runtime_id,
        target_architecture=TargetArchitecture(
            cpu=cpu, accelerator=AcceleratorSpec(compute_capabilities=["12.1"] if cpu == "aarch64" else ["8.6"]),
        ),
    )


def _registry(*runtimes: RuntimeBundleManifest) -> RuntimeBundleRegistry:
    registry = RuntimeBundleRegistry()
    registry._bundles = {r.bundle_id: r for r in runtimes}
    return registry


def _spark(host: str = "10.250.10.49") -> InfraNode:
    return InfraNode(id=uuid.uuid4(), agent_id=f"agent-{uuid.uuid4().hex[:8]}", host=host, status="healthy",
                     capabilities_json={"machine": "aarch64", "gpu_vendor": "nvidia"})


# -- a machine's default -----------------------------------------------------------


def test_the_default_is_the_certified_stable_runtime_never_a_preview() -> None:
    registry = _registry(
        _runtime(STABLE),
        _runtime(PREVIEW, channel="preview", certified="2026-09-30T08:00:00Z"),
        _runtime("gb10-broken", status="failed", certified="2026-10-01T08:00:00Z"),
        _runtime("gb10-fresh-build", status="uncertified", certified=None),
    )
    assert registry.resolve_for_node(_spark()).bundle_id == STABLE


def test_of_two_certified_stable_runtimes_the_later_certified_is_the_default() -> None:
    registry = _registry(_runtime(OLDER, certified="2026-08-01T10:00:00Z"), _runtime(STABLE))
    assert registry.resolve_for_node(_spark()).bundle_id == STABLE


def test_a_flavor_with_nothing_certified_still_gets_its_build() -> None:
    """The x86_64 runtime has run clusters uncertified; that does not stop."""
    registry = _registry(_runtime(X86, cpu="x86_64", status="uncertified", certified=None))
    box = InfraNode(agent_id="box", host="10.0.0.2", capabilities_json={"machine": "x86_64", "gpu_vendor": "nvidia"})
    assert registry.resolve_for_node(box).bundle_id == X86


# -- what the cluster says ------------------------------------------------------------


def test_the_cluster_s_choice_then_what_it_was_started_with_then_the_default() -> None:
    registry = _registry(_runtime(OLDER, certified="2026-08-01T10:00:00Z"), _runtime(STABLE),
                         _runtime(PREVIEW, channel="preview"))
    node = _spark()
    env = InferenceEnvironment(config_json={}, observed_status_json={"runtime": {str(node.id): OLDER}})
    # Started on the older one: it stays there, though a newer stable one is out.
    assert registry.resolve_for_node(node, environment=env).bundle_id == OLDER
    env.config_json = {"runtime": PREVIEW}
    assert registry.resolve_for_node(node, environment=env).bundle_id == PREVIEW
    # A choice this machine cannot run (another flavor's) is passed over.
    env.config_json = {"runtime": X86}
    assert registry.resolve_for_node(node, environment=env).bundle_id == OLDER
    assert registry.resolve_for_node(node).bundle_id == STABLE


# -- the switch --------------------------------------------------------------------------


@pytest.fixture()
def registry(monkeypatch: pytest.MonkeyPatch) -> RuntimeBundleRegistry:
    registry = _registry(
        _runtime(OLDER, certified="2026-08-01T10:00:00Z"),
        _runtime(STABLE),
        _runtime(PREVIEW, channel="preview"),
        _runtime("gb10-broken", status="failed"),
        _runtime(X86, cpu="x86_64", status="uncertified", certified=None),
    )
    monkeypatch.setattr(runtime_choice, "default_bundle_registry", registry)
    return registry


async def _pair(session: AsyncSession, *, started: str | None = None) -> tuple[InferenceEnvironment, list[InfraNode]]:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    session.add(cp)
    await session.flush()
    nodes = [_spark("10.250.10.49"), _spark("10.250.10.71")]
    session.add_all(nodes)
    await session.flush()
    observed: dict[str, Any] = {"runtime": {str(n.id): started for n in nodes}} if started else {}
    env = InferenceEnvironment(control_plane_id=cp.id, name=f"dgx-{uuid.uuid4().hex[:6]}", head_node_id=nodes[0].id,
                               config_json={"ray_tls": True}, observed_status_json=observed)
    session.add(env)
    await session.flush()
    for i, node in enumerate(nodes):
        session.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head" if i == 0 else "worker"))
    await session.flush()
    return env, nodes


async def test_a_cluster_on_an_older_runtime_is_told_an_update_is_out(
    dbsession: AsyncSession, registry: RuntimeBundleRegistry,
) -> None:
    env, _ = await _pair(dbsession, started=OLDER)
    status = await runtime_choice.environment_status(dbsession, env.id)
    assert status["runs"] == [OLDER]
    assert status["applied"] is True
    assert status["update"] == STABLE
    offered = {r["bundle_id"]: r for r in status["runtimes"]}
    assert set(offered) == {OLDER, STABLE, PREVIEW, "gb10-broken"}  # not the x86_64 one
    assert offered[STABLE]["default"] is True
    assert offered[PREVIEW]["channel"] == "preview"


async def test_a_cluster_recorded_on_a_withdrawn_runtime_gets_the_default(
    dbsession: AsyncSession, registry: RuntimeBundleRegistry,
) -> None:
    """The runtime is gone from the catalogue: no error, no dangling id, nothing pretending to restart."""
    env, nodes = await _pair(dbsession, started=WITHDRAWN)
    env.config_json = {**env.config_json, "runtime": WITHDRAWN}  # it was also the operator's choice

    for node in nodes:
        assert registry.resolve_for_node(node, environment=env).bundle_id == STABLE

    status = await runtime_choice.environment_status(dbsession, env.id)
    assert status["runs"] == [STABLE]
    assert WITHDRAWN not in {r["bundle_id"] for r in status["runtimes"]}
    # Nothing is going to restart it: the record is replaced when it next starts.
    assert status["applied"] is True and status["restarting"] is False
    assert status["update"] is None


async def test_a_withdrawn_runtime_is_passed_over_for_the_one_the_machine_was_started_with(
    registry: RuntimeBundleRegistry,
) -> None:
    node = _spark()
    env = InferenceEnvironment(config_json={"runtime": WITHDRAWN}, observed_status_json={"runtime": {str(node.id): OLDER}})
    assert registry.resolve_for_node(node, environment=env).bundle_id == OLDER


async def test_switching_restarts_the_cluster_on_the_chosen_runtime(
    dbsession: AsyncSession, registry: RuntimeBundleRegistry,
) -> None:
    env, _ = await _pair(dbsession, started=STABLE)
    generation = env.generation

    status = await runtime_choice.switch(dbsession, env.id, PREVIEW)

    assert env.generation == generation + 1
    assert env.config_json == {"ray_tls": True, "runtime": PREVIEW}
    assert status["runs"] == [PREVIEW]
    assert status["applied"] is False  # until the machines are started on it
    assert status["restarting"] is True
    # A preview is the operator's choice: no "update" nags them back.
    assert status["update"] is None


async def test_a_cluster_formed_before_the_record_restarts_when_switched(
    dbsession: AsyncSession, registry: RuntimeBundleRegistry,
) -> None:
    """The DGX pair after this upgrade: no record yet, running its default. Switched, it must restart."""
    env, _ = await _pair(dbsession)
    assert (await runtime_choice.environment_status(dbsession, env.id))["applied"] is True
    generation = env.generation
    status = await runtime_choice.switch(dbsession, env.id, PREVIEW)
    assert env.generation == generation + 1 and status["applied"] is False


async def test_a_stopped_cluster_takes_the_switch_when_it_starts(
    dbsession: AsyncSession, registry: RuntimeBundleRegistry,
) -> None:
    env, _ = await _pair(dbsession, started=STABLE)
    env.desired_state = "stopped"
    status = await runtime_choice.switch(dbsession, env.id, PREVIEW)
    assert status["applied"] is False and status["restarting"] is False


async def test_choosing_what_the_machines_already_run_restarts_nothing(
    dbsession: AsyncSession, registry: RuntimeBundleRegistry,
) -> None:
    env, _ = await _pair(dbsession, started=STABLE)
    generation = env.generation
    status = await runtime_choice.switch(dbsession, env.id, STABLE)
    assert env.generation == generation
    assert env.config_json["runtime"] == STABLE and status["applied"] is True


@pytest.mark.parametrize(
    ("runtime_id", "says"),
    [("gb10-vllm9.99.9", "ships no runtime"), ("gb10-broken", "failed its certification"), (X86, "other hardware")],
    ids=["unknown", "failed", "other-flavor"],
)
async def test_what_cannot_be_chosen(
    dbsession: AsyncSession, registry: RuntimeBundleRegistry, runtime_id: str, says: str,
) -> None:
    env, _ = await _pair(dbsession, started=STABLE)
    with pytest.raises(runtime_choice.RuntimeChoiceError, match=says):
        await runtime_choice.switch(dbsession, env.id, runtime_id)
    assert "runtime" not in env.config_json


async def test_a_taken_over_cluster_keeps_what_its_machines_run(
    monkeypatch: pytest.MonkeyPatch, registry: RuntimeBundleRegistry,
) -> None:
    """Taken over running the preview, its first recovery must not move it to the default."""
    from types import SimpleNamespace  # noqa: PLC0415

    from faden_backend.db.dao.node_control_dao import NodeControlDAO  # noqa: PLC0415
    from faden_backend.services.inference import takeover  # noqa: PLC0415

    monkeypatch.setattr(takeover, "default_bundle_registry", registry)
    node, unknown = _spark(), _spark("10.250.10.71")
    runs = {node.id: registry.get_bundle(PREVIEW).container.image, unknown.id: "someone/else:1"}

    async def latest(self: Any, *, node_ids: list[Any]) -> dict[Any, Any]:
        return {i: SimpleNamespace(inventory_json={"ray_runtime": {"running": True, "image": runs[i]}}) for i in node_ids}

    monkeypatch.setattr(NodeControlDAO, "latest_inventory_snapshots", latest)
    assert await takeover._runtimes_running(None, [node, unknown]) == {str(node.id): PREVIEW}
