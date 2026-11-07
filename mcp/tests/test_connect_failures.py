"""A server that cannot be connected says why; a real cancellation stays one.

Registering GitHub's hosted MCP server without its token (release VM,
2026-09-25): GitHub answered 401, the MCP SDK cancelled the task waiting in
``initialize()``, and the cancellation reached the request as a bare 500 with
the server left "registering". These drive the streamable HTTP adapter
against a fake upstream; the SDK in between is the real one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest

from faden_mcp.services.transport import streamable_http
from faden_mcp.services.transport.base import (
    MCPConnectError,
    MCPServerConfig,
    describe_failure,
)
from faden_mcp.services.transport.streamable_http import StreamableHTTPMCPServer
from faden_mcp.web.api.admin import views

URL = "https://mcp.example.test/mcp"


def _adapter(headers: dict[str, str] | None = None) -> StreamableHTTPMCPServer:
    return StreamableHTTPMCPServer(
        MCPServerConfig(
            id="s1",
            name="github",
            transport="streamable_http",
            url=URL,
            headers=headers,
            timeout_sec=5,
            tool_prefix="gh",
        ),
    )


def _upstream(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], Any],
) -> list[httpx2.Request]:
    """Answer the adapter's HTTP requests with *handler*; keep what it sent."""
    seen: list[httpx2.Request] = []
    real = httpx2.AsyncClient

    async def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        result = handler(request)
        return await result if asyncio.iscoroutine(result) else result

    def client(**kwargs: Any) -> httpx2.AsyncClient:
        return real(transport=httpx2.MockTransport(answer), **kwargs)

    monkeypatch.setattr(streamable_http.httpx2, "AsyncClient", client)
    return seen


@pytest.mark.asyncio
async def test_an_upstream_401_says_to_add_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitHub without a token: the 401 and what to do about it."""
    _upstream(monkeypatch, lambda _request: httpx2.Response(401))
    with pytest.raises(MCPConnectError, match=r"401 Unauthorized.*Bearer"):
        await _adapter().connect()


@pytest.mark.asyncio
async def test_the_configured_headers_reach_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A header set on the server goes out with the request."""
    seen = _upstream(monkeypatch, lambda _request: httpx2.Response(401))
    with pytest.raises(MCPConnectError):
        await _adapter({"Authorization": "Bearer t0ken"}).connect()
    assert seen[0].headers["authorization"] == "Bearer t0ken"


@pytest.mark.asyncio
async def test_a_wrong_path_points_at_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 names the paths MCP endpoints usually have."""
    _upstream(monkeypatch, lambda _request: httpx2.Response(404))
    with pytest.raises(MCPConnectError, match=r"/mcp"):
        await _adapter().connect()


@pytest.mark.asyncio
async def test_a_server_that_cannot_be_reached_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused connection is reported as one."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("Connection refused", request=request)

    _upstream(monkeypatch, refuse)
    with pytest.raises(MCPConnectError, match=r"Cannot reach .*Connection refused"):
        await _adapter().connect()


@pytest.mark.asyncio
async def test_a_cancellation_from_outside_stays_a_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutting down mid-connect must not turn into an error to report."""
    reached = asyncio.Event()

    async def hang(_request: httpx2.Request) -> httpx2.Response:
        reached.set()
        await asyncio.Event().wait()
        return httpx2.Response(200)

    _upstream(monkeypatch, hang)
    task = asyncio.create_task(_adapter().connect())
    await asyncio.wait_for(reached.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    ("status", "says"),
    [
        (403, "403 Forbidden"),
        (500, "500 Internal Server Error"),
        (401, "Authorization: Bearer"),
    ],
)
def test_each_status_says_something_useful(status: int, says: str) -> None:
    """Every status names itself; the common ones say what to change."""
    request = httpx2.Request("POST", URL)
    response = httpx2.Response(status, request=request)
    error = httpx2.HTTPStatusError("x", request=request, response=response)
    assert says in describe_failure(error, URL)


@pytest.mark.asyncio
async def test_the_connection_test_says_why_as_refresh_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Test** gives the operator's reason, not the exception's text."""
    server = SimpleNamespace(
        id=uuid.uuid4(), name="github", url=URL, command_json=None,
    )

    class _Dao:
        def __init__(self, _session: object) -> None:
            pass

        async def get_server(self, _server_id: object) -> SimpleNamespace:
            return server

    class _Manager:
        def __init__(self, failure: Exception) -> None:
            self.failure = failure

        def get(self, _server_id: str) -> None:
            return None

        async def start(self, _server: object) -> None:
            raise self.failure

    monkeypatch.setattr(views, "MCPDao", _Dao)
    request = httpx2.Request("POST", URL)
    unauthorized = httpx2.Response(401, request=request)
    refused = "x is a cloud metadata address"
    for failure, says in (
        (
            httpx2.HTTPStatusError("x", request=request, response=unauthorized),
            f"{URL} answered 401 Unauthorized: it needs credentials.",
        ),
        (MCPConnectError(refused), refused),
    ):
        manager = _Manager(failure)
        app = SimpleNamespace(state=SimpleNamespace(connection_manager=manager))
        result = await views.test_server(
            server.id,
            SimpleNamespace(app=app),  # type: ignore[arg-type]
            auth=None,  # type: ignore[arg-type]
            session=None,  # type: ignore[arg-type]
        )
        assert result["status"] == "error"
        assert result["message"].startswith(says)
        assert "Connection failed" not in result["message"]


def test_a_command_that_is_not_installed_says_where() -> None:
    """A stdio command missing from the MCP container is named."""
    missing = FileNotFoundError(2, "No such file", "uvx")
    message = describe_failure(missing, "uvx mcp-server-time")
    assert "uvx" in message
    assert "MCP container" in message
