"""Machines joining and leaving a running cluster, and draining one (``services/inference/membership.py``).

Draining used to be a flag on the machine's row: nothing reached Ray, its
copies kept serving where they were, and the tooltip promised they would move.
Adding a machine to a running cluster wrote a row that a health check later
"rejoined" on the wrong address without the model's files; removing one
deleted the row and left the machine in Ray, serving.

The plan (:func:`plan_drain`) is a function of what the head reports; the
flows run against a stand-in for the agents' commands.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.node_control_dao import NodeControlDAO
from faden_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.node_control import InfraNode
from faden_backend.services.inference import membership
from faden_backend.services.inference.membership import MembershipError, copies_on, plan_drain

HEAD, WORKER, THIRD = "h" * 56, "w" * 56, "t" * 56


def _node(node_id: str, gpu_free: float, *, status: str = "RUNNING", ip: str = "") -> dict[str, Any]:
    return {"node_id": node_id, "ip": ip, "status": status, "available": {"GPU": gpu_free, "CPU": 10.0},
            "total": {"GPU": 1.0, "CPU": 20.0}}


def _copy(app: str, bundles: list[tuple[str, float]], *, strategy: str = "PACK", name: str | None = None) -> dict[str, Any]:
    return {
        "app": app, "deployment": "LLMServer:m", "replica_id": name or app, "actor_name": name or f"SERVE_REPLICA::{app}",
        "state": "RUNNING", "node_id": bundles[0][0], "strategy": strategy,
        "bundles": [{"node_id": node, "resources": {"GPU": gpu, "CPU": 1.0} if gpu else {"CPU": 1.0}}
                    for node, gpu in bundles],
    }


# --------------------------------------------------------------------------
# Which copies can move
# --------------------------------------------------------------------------


def test_a_copy_moves_where_there_is_room_and_says_where() -> None:
    plan = plan_drain(
        {"nodes": [_node(HEAD, 0.6), _node(WORKER, 0.5)], "replicas": [_copy("faden-a", [(WORKER, 0.5)])]},
        WORKER, names={HEAD: "gpu-01", WORKER: "gpu-02"},
        deployments={"faden-a": {"deployment_id": "a", "name": "qwen-chat"}},
    )
    assert plan["moving"] == 1 and plan["stopping"] == 0
    (copy,) = plan["copies"]
    assert copy["name"] == "qwen-chat" and copy["moves"] is True and copy["to"] == ["gpu-01"]
    assert copy["spans"] == ["gpu-02"] and copy["gpus"] == 0.5


def test_a_copy_with_no_room_would_stop_and_the_plan_says_why() -> None:
    plan = plan_drain({"nodes": [_node(HEAD, 0.2), _node(WORKER, 0.5)],
                       "replicas": [_copy("faden-a", [(WORKER, 0.5)])]}, WORKER)
    (copy,) = plan["copies"]
    assert copy["moves"] is False and plan["stopping"] == 1
    assert copy["reason"] == "It needs 0.5 GPU; the most another machine has free is 0.2 GPU."


def test_copies_compete_for_the_same_room() -> None:
    plan = plan_drain({"nodes": [_node(HEAD, 0.6), _node(WORKER, 0.0)], "replicas": [
        _copy("faden-a", [(WORKER, 0.5)], name="a1"), _copy("faden-b", [(WORKER, 0.4)], name="b1")]}, WORKER)
    assert [(c["replica_id"], c["moves"]) for c in plan["copies"]] == [("a1", True), ("b1", False)]


def test_a_copy_spread_over_two_machines_needs_room_for_both_halves() -> None:
    # TP=2 over the DGX pair: one GPU on each machine. Draining one of them
    # needs two whole GPUs elsewhere -- the half on the other machine is
    # still held while the replacement starts.
    spread = _copy("faden-27b", [(HEAD, 1.0), (WORKER, 1.0)])
    plan = plan_drain({"nodes": [_node(HEAD, 0.0), _node(WORKER, 0.0)], "replicas": [spread]}, WORKER,
                      names={HEAD: "gpu-01", WORKER: "gpu-02"})
    (copy,) = plan["copies"]
    assert copy["moves"] is False and copy["spans"] == ["gpu-01", "gpu-02"]
    assert copy["reason"] == "It needs 2 GPU; the most another machine has free is 0 GPU."


def test_pack_spills_over_and_strict_pack_does_not() -> None:
    nodes = [_node(HEAD, 1.0), _node(THIRD, 1.0), _node(WORKER, 0.0)]
    two_gpus = [(WORKER, 1.0), (WORKER, 1.0)]
    assert plan_drain({"nodes": nodes, "replicas": [_copy("faden-a", two_gpus)]}, WORKER)["moving"] == 1
    strict = plan_drain({"nodes": nodes, "replicas": [_copy("faden-a", two_gpus, strategy="STRICT_PACK")]}, WORKER)
    assert strict["stopping"] == 1


def test_a_machine_without_the_models_files_is_no_destination() -> None:
    plan = plan_drain(
        {"nodes": [_node(HEAD, 1.0), _node(WORKER, 0.0)], "replicas": [_copy("faden-a", [(WORKER, 0.5)])]},
        WORKER, deployments={"faden-a": {"ready_nodes": {WORKER}}},
    )
    assert plan["copies"][0]["reason"] == "No other machine holds this model's files yet."


def test_draining_and_dead_machines_take_nothing_and_small_parts_are_only_counted() -> None:
    plan = plan_drain({"nodes": [_node(HEAD, 1.0, status="DRAINING"), _node(THIRD, 1.0, status="DEAD"),
                                 _node(WORKER, 0.0)],
                       "replicas": [_copy("faden-a", [(WORKER, 0.5)]), _copy("faden-a", [(WORKER, 0.0)], name="router")]},
                      WORKER)
    assert plan["copies"][0]["reason"] == "The cluster has no other machine that can take it."
    assert plan["other_parts"] == 1 and len(plan["copies"]) == 1


def test_copies_on_lists_the_gpu_copies_still_holding_the_machine() -> None:
    placement = {"replicas": [_copy("faden-a", [(HEAD, 0.5)]), _copy("faden-b", [(HEAD, 1.0), (WORKER, 1.0)]),
                              _copy("faden-c", [(WORKER, 0.0)])]}
    assert [r["app"] for r in copies_on(placement, WORKER)] == ["faden-b"]


# --------------------------------------------------------------------------
# The flows
# --------------------------------------------------------------------------


async def _machine(session: AsyncSession, host: str, *, caps: dict[str, Any] | None = None, status: str = "healthy") -> InfraNode:
    node = InfraNode(agent_id=f"spark-{uuid.uuid4().hex[:6]}", host=host, status=status,
                     capabilities_json={"ray_drain": 1, **(caps or {})})
    session.add(node)
    await session.flush()
    return node


async def _cluster(session: AsyncSession, *, status: str = "ready") -> tuple[InferenceEnvironment, InfraNode, InfraNode]:
    head = await _machine(session, "10.250.10.71")
    worker = await _machine(session, "10.250.10.49")
    plane = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    session.add(plane)
    await session.flush()
    env = InferenceEnvironment(
        control_plane_id=plane.id, name=f"pair-{uuid.uuid4().hex[:6]}", desired_state="running",
        status=status, head_node_id=head.id, generation=3, observed_generation=3,
        observed_status_json={
            "converged_generation": 3,
            "resolved_fabric": {"cidr": "10.100.0.0/24", "fabric_type": "roce", "node_bindings": {
                str(head.id): {"node_id": str(head.id), "interface": "enp1", "ip": "10.100.0.2"},
                str(worker.id): {"node_id": str(worker.id), "interface": "enp1", "ip": "10.100.0.1"},
            }},
            "network": {"node_bindings": {str(head.id): {"ip": "10.100.0.2"}, str(worker.id): {"ip": "10.100.0.1"}}},
        },
    )
    session.add(env)
    await session.flush()
    for node, role in ((head, "head"), (worker, "worker")):
        session.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role=role, observed_json={}))
    await session.flush()
    return env, head, worker


def _placement(*replicas: dict[str, Any], head_free: float = 0.6) -> dict[str, Any]:
    return {"nodes": [_node(HEAD, head_free, ip="10.100.0.2"), _node(WORKER, 0.0, ip="10.100.0.1")],
            "replicas": list(replicas), "serve_running": True, "drain_patch_active": True}


class _Client:
    """The agents' answers: where the copies run, and what was asked."""

    def __init__(self, *placements: dict[str, Any]) -> None:
        self.placements = list(placements)
        self.calls: list[tuple[str, Any]] = []

    async def describe_placement(self, *, head_node_id: Any, budget_sec: float | None = None) -> dict[str, Any]:
        self.calls.append(("placement", head_node_id))
        return self.placements[0] if len(self.placements) == 1 else self.placements.pop(0)

    async def enable_serve_drain(self, *, head_node_id: Any, key: str) -> dict[str, Any]:
        self.calls.append(("enable", head_node_id))
        return {"active": True}

    async def drain_node(self, *, node_id: Any, key: str) -> dict[str, Any]:
        self.calls.append(("drain", node_id))
        return {"accepted": True}

    async def stop_replicas(self, *, head_node_id: Any, actor_names: list[str], key: str) -> dict[str, Any]:
        self.calls.append(("stop", tuple(actor_names)))
        return {"stopped": actor_names}


class _Manager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def leave_member(self, session: Any, environment: Any, node_id: Any, *, gateway: Any, tag: str) -> None:
        self.calls.append(("leave", node_id))

    async def rejoin_member(self, session: Any, environment: Any, member: Any, *, gateway: Any, tag: str, **_: Any) -> None:
        self.calls.append(("rejoin", member.node_id))


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    fake = _Client(_placement(_copy("faden-a", [(WORKER, 0.5)], name="a1")))
    monkeypatch.setattr(membership, "_client", lambda gateway: fake)
    return fake


@pytest.mark.anyio()
async def test_the_head_cannot_be_drained(dbsession: AsyncSession, client: _Client) -> None:
    env, head, _worker = await _cluster(dbsession)
    plan = await membership.drain_plan(dbsession, None, head.id)
    assert plan["can_drain"] is False and "head" in plan["reason"]
    assert client.calls == [], "nothing is asked of the cluster for a drain that cannot happen"


@pytest.mark.anyio()
async def test_an_old_agent_cannot_drain(dbsession: AsyncSession, client: _Client) -> None:
    _env, _head, worker = await _cluster(dbsession)
    worker.capabilities_json = {}
    plan = await membership.drain_plan(dbsession, None, worker.id)
    assert plan["can_drain"] is False and "0.2.6" in plan["reason"]


@pytest.mark.anyio()
async def test_the_plan_finds_the_machine_in_ray_by_its_network_address(dbsession: AsyncSession, client: _Client) -> None:
    env, head, worker = await _cluster(dbsession)
    plan = await membership.drain_plan(dbsession, None, worker.id)
    assert plan["can_drain"] is True and plan["ray_node_id"] == WORKER
    assert plan["environment_id"] == str(env.id)
    (copy,) = plan["copies"]
    assert copy["moves"] is True and copy["to"] == [head.agent_id]


@pytest.mark.anyio()
async def test_a_drain_that_would_stop_copies_waits_for_the_operator(dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    env, _head, worker = await _cluster(dbsession)
    fake = _Client(_placement(_copy("faden-a", [(WORKER, 0.5)], name="a1"), head_free=0.1))
    monkeypatch.setattr(membership, "_client", lambda gateway: fake)
    with pytest.raises(MembershipError, match="cannot move and would stop") as refused:
        await membership.start_drain(dbsession, None, worker.id)
    assert refused.value.plan["stopping"] == 1
    assert worker.draining is False, "nothing changed"

    await membership.start_drain(dbsession, None, worker.id, stop_unmovable=True)
    entry = env.observed_status_json["drains"][str(worker.id)]
    assert entry["state"] == "starting" and entry["stop_unmovable"] is True and entry["ray_node_id"] == WORKER
    assert worker.draining is True and worker.status == "draining"
    assert env.observed_generation < env.generation, "queued for the reconciler"


@pytest.mark.anyio()
async def test_a_drain_moves_the_copies_then_the_machine_leaves(dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    env, head, worker = await _cluster(dbsession)
    on_worker = _copy("faden-a", [(WORKER, 0.5)], name="a1")
    fake = _Client(_placement(on_worker), _placement(on_worker), _placement(on_worker), _placement())
    monkeypatch.setattr(membership, "_client", lambda gateway: fake)
    await membership.remove_member(dbsession, None, env.id, worker.id)
    manager = _Manager()

    # Pass 1: Serve is told to follow drains, Ray drains the machine; one copy is still there.
    await membership.advance(dbsession, None, env, manager=manager, ctx={})
    assert ("enable", head.id) in fake.calls and ("drain", worker.id) in fake.calls
    entry = env.observed_status_json["drains"][str(worker.id)]
    assert entry["state"] == "moving" and entry["left"] == 1 and entry["message"] == "Still moving 1 copy."

    # Pass 2: Serve has moved it; the machine leaves Ray and the cluster.
    await membership.advance(dbsession, None, env, manager=manager, ctx={})
    assert manager.calls == [("leave", worker.id)]
    assert "drains" not in env.observed_status_json
    assert str(worker.id) not in env.observed_status_json["resolved_fabric"]["node_bindings"]
    rows = (await dbsession.execute(select(InferenceEnvironmentNode).where(
        InferenceEnvironmentNode.environment_id == env.id))).scalars().all()
    assert [r.node_id for r in rows] == [head.id]
    assert worker.draining is False


@pytest.mark.anyio()
async def test_copies_that_cannot_move_are_stopped_once_agreed(dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    env, _head, worker = await _cluster(dbsession)
    stuck = _copy("faden-27b", [(WORKER, 1.0)], name="big")
    fake = _Client(_placement(stuck, head_free=0.0))
    monkeypatch.setattr(membership, "_client", lambda gateway: fake)
    await membership.start_drain(dbsession, None, worker.id, stop_unmovable=True)
    await membership.advance(dbsession, None, env, manager=_Manager(), ctx={})
    assert ("stop", ("big",)) in fake.calls
    assert env.observed_status_json["drains"][str(worker.id)]["state"] == "drained"


@pytest.mark.anyio()
async def test_stopping_a_drain_rejoins_the_machine(dbsession: AsyncSession, client: _Client) -> None:
    env, _head, worker = await _cluster(dbsession)
    await membership.start_drain(dbsession, None, worker.id)
    await membership.advance(dbsession, None, env, manager=_Manager(), ctx={})
    assert env.observed_status_json["drains"][str(worker.id)]["state"] == "moving"

    result = await membership.stop_drain(dbsession, worker.id)
    assert result["resuming"] is True and worker.draining is False
    manager = _Manager()
    await membership.advance(dbsession, None, env, manager=manager, ctx={})
    assert manager.calls == [("rejoin", worker.id)]
    assert "drains" not in env.observed_status_json


@pytest.mark.anyio()
async def test_stopping_a_drain_that_never_reached_ray_just_forgets_it(dbsession: AsyncSession, client: _Client) -> None:
    env, _head, worker = await _cluster(dbsession)
    await membership.start_drain(dbsession, None, worker.id)
    result = await membership.stop_drain(dbsession, worker.id)
    assert result["resuming"] is False and "drains" not in env.observed_status_json


@pytest.mark.anyio()
async def test_removing_a_machine_from_a_stopped_cluster_is_just_the_record(dbsession: AsyncSession, client: _Client) -> None:
    env, head, worker = await _cluster(dbsession, status="stopped")
    env.desired_state = "stopped"
    result = await membership.remove_member(dbsession, None, env.id, worker.id)
    assert result == {"removed": True, "pending": False}
    assert client.calls == []
    rows = (await dbsession.execute(select(InferenceEnvironmentNode.node_id).where(
        InferenceEnvironmentNode.environment_id == env.id))).scalars().all()
    assert rows == [head.id]


@pytest.mark.anyio()
async def test_candidates_say_why_a_machine_cannot_join(dbsession: AsyncSession) -> None:
    env, _head, _worker = await _cluster(dbsession)
    fabric = {"network": {"fabrics": [{"cidr": "10.100.0.0/24", "ip": "10.100.0.3", "interface": "enp1", "is_up": True}]}}
    ready = await _machine(dbsession, "10.250.10.80", caps=fabric)
    no_link = await _machine(dbsession, "10.250.10.81")
    offline = await _machine(dbsession, "10.250.10.82", caps=fabric, status="offline")
    listed = {m["node_id"]: m for m in (await membership.candidates(dbsession, env.id))["machines"]}
    assert listed[str(ready.id)]["can_add"] is True
    assert listed[str(no_link.id)]["reasons"] == ["It has no address on the cluster's network (10.100.0.0/24)."]
    assert listed[str(offline.id)]["reasons"] == ["It is offline."]
    assert str(_head.id) not in listed, "members are not candidates"


@pytest.mark.anyio()
async def test_adding_a_machine_binds_it_checks_it_reaches_the_head_and_bumps_the_generation(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from faden_backend.services.inference.planner import FabricValidation, MultiNodeFabricPlanner

    env, head, _worker = await _cluster(dbsession)
    fabric = {"network": {"fabrics": [{"cidr": "10.100.0.0/24", "ip": "10.100.0.3", "interface": "enp9",
                                       "is_up": True, "link_type": "roce", "speed_gbps": 200}]}}
    newcomer = await _machine(dbsession, "10.250.10.80", caps=fabric)
    probed: list[set[str]] = []
    reachable: list[bool | None] = [False, True]

    async def probe(self: Any, candidate: Any) -> FabricValidation:
        probed.append(set(candidate.node_bindings))
        return FabricValidation(performed=True, reachable=reachable.pop(0), detail="10.100.0.3 -> 10.100.0.2: refused")

    monkeypatch.setattr(MultiNodeFabricPlanner, "_probe_candidate", probe)
    with pytest.raises(MembershipError, match="could not reach the cluster's head"):
        await membership.add_members(dbsession, object(), env.id, [newcomer.id])
    assert str(newcomer.id) not in env.observed_status_json["resolved_fabric"]["node_bindings"]

    generation = env.generation
    await membership.add_members(dbsession, object(), env.id, [newcomer.id])
    assert probed[-1] == {str(head.id), str(newcomer.id)}, "the newcomer is checked against the head"
    binding = env.observed_status_json["resolved_fabric"]["node_bindings"][str(newcomer.id)]
    assert binding["ip"] == "10.100.0.3" and binding["interface"] == "enp9"
    assert env.generation == generation + 1
    roles = {r.node_id: r.role for r in (await dbsession.execute(select(InferenceEnvironmentNode).where(
        InferenceEnvironmentNode.environment_id == env.id))).scalars()}
    assert roles[newcomer.id] == "worker"


@pytest.mark.anyio()
async def test_a_machine_in_another_cluster_is_refused(dbsession: AsyncSession) -> None:
    env, _head, _worker = await _cluster(dbsession)
    other, _other_head, other_worker = await _cluster(dbsession)
    with pytest.raises(MembershipError, match=f"belongs to the cluster {other.name}"):
        await membership.add_members(dbsession, None, env.id, [other_worker.id])


@pytest.mark.anyio()
async def test_a_heartbeat_does_not_undo_drain_or_maintenance(dbsession: AsyncSession) -> None:
    node = await _machine(dbsession, "10.250.10.49")
    dao = NodeControlDAO(dbsession)
    await dao.set_node_draining(node=node, enabled=True)
    await dao.update_node_heartbeat(node, status="healthy")
    assert node.status == "draining"
    node.maintenance_mode = True
    await dao.update_node_heartbeat(node, status="healthy")
    assert node.status == "maintenance"
    await dao.update_node_heartbeat(node, status="offline")
    assert node.status == "offline", "a machine that went away still reads offline"


@pytest.mark.anyio()
async def test_the_plain_member_removal_refuses_a_machine_the_cluster_runs_on(dbsession: AsyncSession) -> None:
    from faden_backend.services.inference.service import ConflictError, EnvironmentService

    env, _head, worker = await _cluster(dbsession)
    with pytest.raises(ConflictError, match="moves its model copies"):
        await EnvironmentService(dbsession).remove_node(env.id, worker.id)
