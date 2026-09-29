"""What an agent was running when this server restarted is not left "running" for an hour.

A command runs in the agent that received it and dies with its connection.
The backend fails it when it sees the connection close -- but after its own
restart (an upgrade on the release VM) there was no one left to see that, and
the model copies both DGX Sparks had been running stayed "running" until they
were an hour overdue. The deployment waited on them the whole time.

The agent now says, as it connects, which commands it is still executing
(agent 0.2.3); the backend fails the rest of what it had accepted.
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


async def _node(session: AsyncSession) -> InfraNode:
    node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex[:8]}", host="10.250.10.71", status="healthy", capabilities_json={})
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


async def test_what_the_agent_no_longer_runs_is_failed_and_the_rest_kept(dbsession: AsyncSession) -> None:
    node = await _node(dbsession)
    dropped_running = await _command(dbsession, node, NodeCommandStatus.RUNNING.value)
    dropped_acked = await _command(dbsession, node, NodeCommandStatus.ACKED.value)
    still_running = await _command(dbsession, node, NodeCommandStatus.RUNNING.value)
    never_accepted = await _command(dbsession, node, NodeCommandStatus.DISPATCHED.value)
    queued = await _command(dbsession, node, NodeCommandStatus.QUEUED.value)

    failed = await _service(dbsession).fail_commands_the_agent_dropped(
        node_id=node.id, still_running={str(still_running.id)},
    )

    assert failed == 2
    for command in (dropped_running, dropped_acked, still_running, never_accepted, queued):
        await dbsession.refresh(command)
    assert dropped_running.status == dropped_acked.status == NodeCommandStatus.FAILED.value
    assert dropped_running.error_code == "node_stream_lost"
    assert "no longer running" in (dropped_running.error_message or "")
    assert still_running.status == NodeCommandStatus.RUNNING.value
    assert never_accepted.status == NodeCommandStatus.DISPATCHED.value, "sent again on connect, not failed"
    assert queued.status == NodeCommandStatus.QUEUED.value


async def test_a_model_copy_that_died_frees_its_node_for_a_retry(dbsession: AsyncSession) -> None:
    """The DGX pair after the upgrade: the copy row said "syncing" and blocked a retry for an hour."""
    node = await _node(dbsession)
    model = LLMModel(display_name="Qwen3.8-Flash-Next-NVFP4", source=ModelSource.HUGGINGFACE,
                     status=ModelStatus.AVAILABLE, hf_repo_id=f"nvidia/q-{uuid.uuid4().hex[:6]}")
    dbsession.add(model)
    await dbsession.flush()
    availability = ModelAvailabilityDAO(dbsession)
    await availability.mark(model.id, node.id, ModelAvailabilityStatus.SYNCING, status_message="Model sync blob (0%)")
    await _command(dbsession, node, NodeCommandStatus.RUNNING.value, command_type=NodeCommandType.SYNC_MODEL.value,
                   payload={"model_sync": {"model_id": str(model.id), "hf_repo_id": model.hf_repo_id}})

    await _service(dbsession).fail_commands_the_agent_dropped(node_id=node.id, still_running=set())

    row = await availability.get(model.id, node.id)
    assert row is not None and row.status == ModelAvailabilityStatus.FAILED.value


async def test_an_agent_that_does_not_say_changes_nothing(dbsession: AsyncSession) -> None:
    """An agent older than 0.2.3 connects without the header: its commands are left as they were."""
    assert NodeControlService.running_commands_reported({"authorization": "Bearer x"}) is None


@pytest.mark.parametrize(
    ("header", "ids"),
    [("v1:", set()), ("v1:a1", {"a1"}), ("v1:a1, b2,,", {"a1", "b2"}), ("v2:a1", None), ("a1,b2", None)],
    ids=["none running", "one", "several, untidy", "a later format", "no version"],
)
def test_reading_the_header(header: str, ids: set[str] | None) -> None:
    assert NodeControlService.running_commands_reported({"x-faden-running-commands": header}) == ids
