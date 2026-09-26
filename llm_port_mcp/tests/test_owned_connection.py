"""A connection is opened and closed by a task of its own.

The service opened each connection in whichever task connected it and kept
it open. When the transport failed later -- a server restarted, a stream
dropped -- the SDK cancelled that task, which could never leave the
transport's task group: anyio retried the cancellation on every turn of the
event loop, and the MCP service used a whole core, idle, until it restarted
(VM, 2026-09-26).
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack

import anyio
import pytest

from llm_port_mcp.services.transport.base import MCPConnectError, OwnedConnection


def _transport(fail_after: float | None, events: list[str]):
    """An ``enter`` like the SDK's: a task group running a transport that may fail."""

    async def enter(stack: AsyncExitStack) -> str:
        group = await stack.enter_async_context(anyio.create_task_group())
        stack.callback(events.append, "closed")
        stack.callback(group.cancel_scope.cancel)  # as the SDK's clients do on exit

        async def transport() -> None:
            if fail_after is None:
                await anyio.sleep_forever()
            await anyio.sleep(fail_after)
            raise RuntimeError("stream dropped")

        group.start_soon(transport)
        return "session"

    return enter


@pytest.mark.anyio
async def test_a_transport_that_fails_later_ends_only_its_own_task() -> None:
    events: list[str] = []
    connection = OwnedConnection("everything")
    assert (
        await connection.open(
            _transport(0.05, events), "http://mcp-everything:3001/mcp"
        )
        == "session"
    )

    started = time.process_time()
    await asyncio.sleep(0.5)  # this task, which opened it, is not cancelled
    assert time.process_time() - started < 0.2, "the event loop spun"
    assert connection.closed
    assert events == ["closed"]


@pytest.mark.anyio
async def test_closing_leaves_it_in_the_task_that_opened_it() -> None:
    events: list[str] = []
    connection = OwnedConnection("everything")
    await connection.open(_transport(None, events), "x")

    async def elsewhere() -> None:
        await connection.close()

    await asyncio.create_task(elsewhere())
    assert connection.closed
    assert events == ["closed"]


@pytest.mark.anyio
async def test_a_failure_to_connect_is_raised_to_the_caller() -> None:
    async def refused(stack: AsyncExitStack) -> str:
        raise ConnectionRefusedError("refused")

    with pytest.raises(MCPConnectError, match="refused"):
        await OwnedConnection("x").open(refused, "http://nowhere/mcp")
