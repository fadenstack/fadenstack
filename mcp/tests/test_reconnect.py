"""A server that restarted is connected to again.

The MCP service connected to each server once. After a server restarted --
its container edited from the console, and so replaced -- it no longer knew
the session: every health check and tool call failed until the MCP service
itself restarted (VM, 2026-09-26).
"""

from __future__ import annotations

from typing import Any

import anyio
import httpx
import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from faden_mcp.services import connection_manager
from faden_mcp.services.connection_manager import MCPConnectionManager, session_gone
from faden_mcp.services.transport.base import MCPServerConfig

TERMINATED = McpError(ErrorData(code=32600, message="Session terminated"))


@pytest.mark.parametrize(
    ("exc", "gone"),
    [
        (TERMINATED, True),
        (anyio.ClosedResourceError(), True),
        (httpx.ConnectError("refused"), True),
        (McpError(ErrorData(code=-32602, message="Invalid arguments")), False),
        (TimeoutError(), False),  # it may have run: not sent again
        (ValueError("bad"), False),
    ],
)
def test_only_a_request_that_did_not_reach_the_server_is_sent_again(
    exc: BaseException, gone: bool
) -> None:
    assert session_gone(exc) is gone


class _Server:
    """A connection to a server: alive, or holding a session the server forgot."""

    def __init__(
        self, config: MCPServerConfig, *, alive: bool = True, connects: bool = True
    ) -> None:
        self.config = config
        self.alive = alive
        self.connects = connects
        self.closed = False
        self.calls: list[str] = []

    async def connect(self) -> None:
        if not self.connects:
            raise httpx.ConnectError("refused")

    async def disconnect(self) -> None:
        self.closed = True

    async def healthcheck(self) -> str:
        return "active" if self.alive else "error"

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        if not self.alive:
            raise TERMINATED
        return {"content": [{"type": "text", "text": "42"}]}


CONFIG = MCPServerConfig(
    id="s1",
    name="everything",
    transport="streamable_http",
    url="http://mcp-everything:3001/mcp",
    tool_prefix="everything",
)


def _manager(
    monkeypatch: pytest.MonkeyPatch, *, new_connects: bool = True
) -> tuple[MCPConnectionManager, _Server]:
    manager = MCPConnectionManager()
    stale = _Server(CONFIG, alive=False)
    manager._connections["s1"] = stale  # type: ignore[assignment]  # noqa: SLF001
    monkeypatch.setattr(
        connection_manager,
        "_create_adapter",
        lambda config: _Server(config, connects=new_connects),
    )
    return manager, stale


@pytest.mark.anyio
async def test_a_call_on_a_forgotten_session_runs_on_a_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, stale = _manager(monkeypatch)
    result = await manager.call_tool("s1", stale, "get-sum", {"a": 1, "b": 2})  # type: ignore[arg-type]
    assert result["content"][0]["text"] == "42"
    fresh = manager.get("s1")
    assert fresh is not stale and fresh.calls == ["get-sum"]  # type: ignore[union-attr]
    assert stale.closed


@pytest.mark.anyio
async def test_a_tool_that_failed_is_not_run_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _ = _manager(monkeypatch)
    alive = _Server(CONFIG)

    async def fails(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        alive.calls.append(name)
        raise TimeoutError

    alive.call_tool = fails  # type: ignore[method-assign]
    manager._connections["s1"] = alive  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(TimeoutError):
        await manager.call_tool("s1", alive, "create_issue", {})  # type: ignore[arg-type]
    assert alive.calls == ["create_issue"]
    assert manager.get("s1") is alive


@pytest.mark.anyio
async def test_a_server_still_down_keeps_its_connection_for_the_next_try(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, stale = _manager(monkeypatch, new_connects=False)
    assert await manager.reconnect("s1", stale=stale) is None  # type: ignore[arg-type]
    assert manager.get("s1") is stale


@pytest.mark.anyio
async def test_the_heartbeat_connects_again(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, stale = _manager(monkeypatch)
    await manager.heartbeat_all()
    assert manager.get("s1") is not stale
    assert await manager.health("s1") == "active"


@pytest.mark.anyio
async def test_two_callers_reconnect_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second finds the first's new connection instead of making another."""
    manager, stale = _manager(monkeypatch)
    first = await manager.reconnect("s1", stale=stale)  # type: ignore[arg-type]
    second = await manager.reconnect("s1", stale=stale)  # type: ignore[arg-type]
    assert first is second
