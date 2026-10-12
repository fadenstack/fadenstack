"""What a machine was running when its connection went is settled when it connects again.

A command used to run inside the agent's reading of its connection, and died
with it. The backend failed it when it saw the connection close -- but after
a restart of its own (an upgrade on the release VM) no one saw that, and the
model copies both DGX Sparks had been running stayed "running" for an hour.

An agent of 0.2.3 keeps a command running when its connection drops, and
says as it connects what still runs and what finished (X-Faden-Commands).
The backend leaves the running ones alone, asks again for the finished ones
it has no result for, and fails the rest. A connection seen to close fails
nothing for such an agent any more; for an older one it still does.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.inference_dao import ModelAvailabilityDAO
from faden_backend.db.dao.node_control_dao import NodeControlDAO
from faden_backend.db.models.inference import ModelAvailabilityStatus
from faden_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from faden_backend.db.models.node_control import InfraNode, NodeCommandStatus, NodeCommandType
from faden_backend.services.nodes.service import NodeControlService


def _service(session: AsyncSession) -> NodeControlService:
    return NodeControlService(NodeControlDAO(session), pepper="p", enrollment_ttl_minutes=60, default_command_timeout_sec=60)


async def _node(session: AsyncSession, *, continuity: bool = True) -> InfraNode:
    node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex[:8]}", host="192.0.2.71", status="healthy",
                     capabilities_json={"command_continuity": 1} if continuity else {})
    session.add(node)
    await session.flush()
    return node


async def _command(session: AsyncSession, node: InfraNode, status: str, *, command_type: str = "get_ray_status",
                   payload: dict | None = None):
    command = await NodeControlDAO(session).create_command(
        node_id=node.id, command_type=command_type, payload_json=payload or {}, timeout_sec=1800,
        issued_by=None, correlation_id=None, idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
    )
    command.status = status
    await session.flush()
    return command


async def _statuses(session: AsyncSession, *commands) -> list[str]:
    for command in commands:
        await session.refresh(command)
    return [command.status for command in commands]


# -- on connect ---------------------------------------------------------------


async def test_running_is_left_finished_is_asked_again_and_the_rest_is_failed(dbsession: AsyncSession) -> None:
    node = await _node(dbsession)
    running = await _command(dbsession, node, NodeCommandStatus.RUNNING.value)
    finished = await _command(dbsession, node, NodeCommandStatus.RUNNING.value)
    lost_running = await _command(dbsession, node, NodeCommandStatus.RUNNING.value)
    lost_acked = await _command(dbsession, node, NodeCommandStatus.ACKED.value)
    never_accepted = await _command(dbsession, node, NodeCommandStatus.DISPATCHED.value)

    replay = await _service(dbsession).settle_commands_on_connect(
        node_id=node.id, running={str(running.id)}, finished={str(finished.id)},
    )

    assert [c.id for c in replay] == [finished.id]
    assert await _statuses(dbsession, running, finished, lost_running, lost_acked, never_accepted) == [
        "running", "running", "failed", "failed", "dispatched",
    ]
    assert lost_running.error_code == "node_stream_lost"
    assert "no longer running" in (lost_running.error_message or "")


async def test_a_model_copy_that_died_frees_its_node_for_a_retry(dbsession: AsyncSession) -> None:
    """The DGX pair after the upgrade: the copy's row said "syncing" and blocked a retry for an hour."""
    node = await _node(dbsession)
    model = LLMModel(display_name="Qwen3.8-Flash-Next-NVFP4", source=ModelSource.HUGGINGFACE,
                     status=ModelStatus.AVAILABLE, hf_repo_id=f"nvidia/q-{uuid.uuid4().hex[:6]}")
    dbsession.add(model)
    await dbsession.flush()
    availability = ModelAvailabilityDAO(dbsession)
    await availability.mark(model.id, node.id, ModelAvailabilityStatus.SYNCING, status_message="Model sync blob (0%)")
    await _command(dbsession, node, NodeCommandStatus.RUNNING.value, command_type=NodeCommandType.SYNC_MODEL.value,
                   payload={"model_sync": {"model_id": str(model.id), "hf_repo_id": model.hf_repo_id}})

    await _service(dbsession).settle_commands_on_connect(node_id=node.id, running=set(), finished=set())

    row = await availability.get(model.id, node.id)
    assert row is not None and row.status == ModelAvailabilityStatus.FAILED.value


# -- a connection closing ----------------------------------------------------------


async def test_a_closing_connection_fails_nothing_for_an_agent_that_carries_on(dbsession: AsyncSession) -> None:
    node = await _node(dbsession, continuity=True)
    copy = await _command(dbsession, node, NodeCommandStatus.RUNNING.value, command_type="sync_model")
    await _service(dbsession)._node_lost_its_last_stream(node)
    assert await _statuses(dbsession, copy) == ["running"]
    assert node.status == "offline"


async def test_a_closing_connection_still_fails_what_an_older_agent_was_running(dbsession: AsyncSession) -> None:
    node = await _node(dbsession, continuity=False)
    copy = await _command(dbsession, node, NodeCommandStatus.RUNNING.value, command_type="sync_model")
    await _service(dbsession)._node_lost_its_last_stream(node)
    assert await _statuses(dbsession, copy) == ["failed"]


async def test_progress_does_not_bring_a_command_that_is_over_back(dbsession: AsyncSession) -> None:
    """Timed out while its machine was away, the agent carried on: what was retried instead counts."""
    node = await _node(dbsession)
    copy = await _command(dbsession, node, NodeCommandStatus.TIMED_OUT.value, command_type="sync_model")
    await _service(dbsession).record_command_progress(
        node_id=node.id, command_id=copy.id, payload={"message": "Downloading 40%", "progress_pct": 40},
    )
    assert await _statuses(dbsession, copy) == ["timed_out"]


# -- the header ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "report"),
    [
        ("v1; running=; finished=", (set(), set())),
        ("v1; running=a1; finished=b2,c3", ({"a1"}, {"b2", "c3"})),
        ("v1;running=a1 , ;finished=", ({"a1"}, set())),
        ("v2; running=a1", None),
        ("running=a1", None),
    ],
    ids=["nothing", "both", "untidy", "a later format", "no version"],
)
def test_reading_the_header(header: str, report: tuple[set[str], set[str]] | None) -> None:
    assert NodeControlService.commands_reported({"x-faden-commands": header}) == report


def test_an_agent_that_does_not_say_is_left_as_before() -> None:
    assert NodeControlService.commands_reported({"authorization": "Bearer x"}) is None
    assert NodeControlService.keeps_commands_across_connections(None) is False
