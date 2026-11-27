"""``NativeCommands.ask`` on a request-scoped session, answered by another connection.

The deployment-logs route hands the driver a ``NodeControlService`` bound to the
request's own session (``expire_on_commit=False``). The node's agent answers
from a different connection. These tests use real, separately committing
sessions: nothing in the chain is mocked, because the failure lived in what
one session sees of another's writes (the stale identity map).
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from faden_backend.db.models.node_control import InfraNode, InfraNodeCommand, NodeCommandStatus
from faden_backend.services.inference.drivers.ray.commands import NodeCommandGateway
from faden_backend.services.inference.drivers.standalone.commands import NativeCommands


@pytest.fixture
async def maker(_engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(_engine, expire_on_commit=False)


@pytest.fixture
async def node_id(maker: async_sessionmaker) -> AsyncGenerator[uuid.UUID]:
    async with maker() as session:
        node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex[:8]}", host="127.0.0.1", status="healthy")
        session.add(node)
        await session.commit()
        nid = node.id
    yield nid
    async with maker() as session:
        await session.execute(delete(InfraNode).where(InfraNode.id == nid))
        await session.commit()


async def _agent(maker: async_sessionmaker, nid: uuid.UUID, result: dict, *, status: str, seen: list) -> None:
    """The node's agent: another connection that only sees committed commands."""
    while True:
        async with maker() as session:
            row = (
                await session.execute(
                    select(InfraNodeCommand).where(
                        InfraNodeCommand.node_id == nid, InfraNodeCommand.status == NodeCommandStatus.QUEUED.value
                    )
                )
            ).scalars().first()
            if row is not None:
                seen.append(row.command_type)
                row.status = status
                row.result_json = result
                if status != NodeCommandStatus.SUCCEEDED.value:
                    row.error_message = "boom"
                await session.commit()
                return
        await asyncio.sleep(0.05)


async def _ask(maker: async_sessionmaker, nid: uuid.UUID, **kwargs: object) -> dict | None:
    async with maker() as request_session:  # the route's session
        service = NodeCommandGateway(request_session)._get_service(request_session)
        return await NativeCommands(service, {}, owner="test").ask(
            node_id=nid, command_type="native_worker_logs", payload={"tail": 5}, wait_sec=3.0, **kwargs
        )


async def test_ask_sees_the_agents_answer(maker: async_sessionmaker, node_id: uuid.UUID) -> None:
    seen: list[str] = []
    agent = asyncio.create_task(_agent(maker, node_id, {"ok": True, "lines": ["a", "b"]}, status="succeeded", seen=seen))
    try:
        answer = await _ask(maker, node_id)
    finally:
        agent.cancel()
    assert seen == ["native_worker_logs"], "the command was never committed where the agent can see it"
    assert answer == {"ok": True, "lines": ["a", "b"]}


async def test_ask_reports_a_failed_command(maker: async_sessionmaker, node_id: uuid.UUID) -> None:
    seen: list[str] = []
    agent = asyncio.create_task(_agent(maker, node_id, {"ok": False}, status="failed", seen=seen))
    try:
        answer = await _ask(maker, node_id)
    finally:
        agent.cancel()
    assert answer == {"ok": False, "error": "boom"}
