"""A command reaches its machine once per connection, and a stale second copy
never overwrites what the first one did.

On the DGX pair a model copy was sent twice on one connection: the stream sent
every unacknowledged command again after each frame from the agent, and one
came in before the ack. The agent held the second copy behind the running one,
refused it as expired once the nine-minute copy finished, and that refusal was
recorded over the copy's success.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommandEvent,
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.nodes.service import NodeControlService


def _service(session: AsyncSession) -> NodeControlService:
    return NodeControlService(
        NodeControlDAO(session), pepper="test-pepper", enrollment_ttl_minutes=60, default_command_timeout_sec=60,
    )


async def _queued(session: AsyncSession) -> tuple[InfraNode, uuid.UUID]:
    node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex[:8]}", host="10.250.10.49", status="healthy",
                     capabilities_json={})
    session.add(node)
    await session.flush()
    command = await NodeControlDAO(session).create_command(
        node_id=node.id, command_type=NodeCommandType.SYNC_MODEL.value, payload_json={}, timeout_sec=3600,
        issued_by=None, correlation_id=None, idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
    )
    await session.flush()
    return node, command.id


@pytest.mark.anyio()
async def test_a_live_connection_sends_a_command_once(dbsession: AsyncSession) -> None:
    node, command_id = await _queued(dbsession)
    service = _service(dbsession)

    first = await service.list_commands_for_dispatch(node_id=node.id)
    # The next frame from the agent comes in before its ack.
    again = await service.list_commands_for_dispatch(node_id=node.id)

    assert [c["id"] for c in first] == [str(command_id)]
    assert again == []


@pytest.mark.anyio()
async def test_a_new_connection_gets_what_the_old_one_never_acknowledged(dbsession: AsyncSession) -> None:
    node, command_id = await _queued(dbsession)
    service = _service(dbsession)
    await service.list_commands_for_dispatch(node_id=node.id)  # sent; the connection then drops

    on_reconnect = await service.list_dispatchable_commands(node_id=node.id)

    assert [c["id"] for c in on_reconnect] == [str(command_id)], "once, not also as an overdue one"


@pytest.mark.anyio()
async def test_a_stale_second_copy_does_not_overwrite_the_result(dbsession: AsyncSession) -> None:
    node, command_id = await _queued(dbsession)
    service = _service(dbsession)
    await service.record_command_result(
        node_id=node.id, command_id=command_id, payload={"success": True, "result": {"synced": True}},
    )
    await service.record_command_result(
        node_id=node.id, command_id=command_id,
        payload={"success": False, "error_code": "command_expired", "error_message": "Command issued_at is too old."},
    )
    await dbsession.flush()

    command = await NodeControlDAO(dbsession).get_command(command_id)
    assert command.status == NodeCommandStatus.SUCCEEDED.value
    assert command.result_json == {"synced": True}
    phases = (await dbsession.execute(
        select(InfraNodeCommandEvent.phase).where(InfraNodeCommandEvent.command_id == command_id),
    )).scalars().all()
    assert "duplicate" in phases
