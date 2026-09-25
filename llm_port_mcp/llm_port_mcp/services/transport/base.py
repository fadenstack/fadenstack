"""Abstract base class for MCP server transport adapters."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp.shared.exceptions import McpError


class MCPConnectError(RuntimeError):
    """A server could not be connected; the message says why, and what to change."""


_ENDPOINT_PATHS = (
    "Streamable HTTP endpoints usually end in /mcp, SSE endpoints in /sse."
)
_STATUS_HINTS = {
    401: (
        "it needs credentials. Add them as a header, for example "
        "Authorization: Bearer <token>."
    ),
    403: "the credentials were refused or lack a permission.",
    404: f"no MCP endpoint at that URL. {_ENDPOINT_PATHS}",
}


def describe_failure(cause: BaseException, target: str) -> str:
    """Say why connecting to *target* failed, in terms an operator can act on."""
    if isinstance(cause, httpx.HTTPStatusError):
        response = cause.response
        said = f"{target} answered {response.status_code} {response.reason_phrase}"
        hint = _STATUS_HINTS.get(response.status_code)
        message = f"{said}: {hint}" if hint else f"{said}."
    elif isinstance(cause, httpx.TimeoutException):
        message = f"{target} did not answer in time."
    elif isinstance(cause, httpx.ConnectError):
        message = f"Cannot reach {target}: {cause or 'the connection failed'}."
    elif isinstance(cause, FileNotFoundError):
        missing = cause.filename or cause
        message = f"Cannot start {target}: {missing} is not in the MCP container."
    elif isinstance(cause, McpError) and "Session terminated" in str(cause):
        # How the SDK reports a 404 to the first request: no endpoint there.
        message = f"{target} has no MCP endpoint (it answered 404). {_ENDPOINT_PATHS}"
    else:
        message = f"{target}: {type(cause).__name__}: {cause}"
    return message


def _first_cause(error: BaseException) -> BaseException | None:
    """The first exception in *error* that is not a cancellation."""
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            found = _first_cause(inner)
            if found is not None:
                return found
        return None
    if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
        return None
    return error


async def failed_connect(
    stack: AsyncExitStack,
    error: BaseException,
    target: str,
) -> MCPConnectError | None:
    """Close what a failed connect opened; what to raise, or None to re-raise.

    The MCP SDK runs its transport in a task group. When the transport fails
    -- an upstream 401, a refused connection -- the SDK cancels the task
    waiting in ``initialize()``: the caller saw ``CancelledError``, the real
    error surfaced only when that task group was closed, and nothing closed
    it. Registering a server that answered 401 therefore failed with a bare
    500 and left it "registering". Closing the stack here, in the task that
    opened it, brings the cause out. A cancellation from outside, with no
    failure behind it, stays what it is.
    """
    cause = _first_cause(error)
    try:
        await stack.aclose()
    except BaseException as closing:
        # The SDK's task group reports its failure here.
        cause = cause or _first_cause(closing)
    if cause is None:
        return None
    failure = MCPConnectError(describe_failure(cause, target))
    failure.__cause__ = cause
    return failure


@dataclass(frozen=True)
class MCPToolDescriptor:
    """Normalized tool metadata discovered from an MCP server."""

    upstream_name: str
    qualified_name: str
    description: str
    input_schema: dict[str, Any]
    title: str | None = None
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuration needed to connect to an MCP server."""

    id: str
    name: str
    transport: str
    url: str | None = None
    command: list[str] | None = None
    args: list[str] | None = None
    working_dir: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    timeout_sec: int = 60
    tool_prefix: str = ""


class GenericMCPServer(ABC):
    """Abstract base for transport-specific MCP server connections."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config

    @abstractmethod
    async def connect(self) -> None:
        """Establish session/process/transport connectivity."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close all active resources cleanly."""

    @abstractmethod
    async def healthcheck(self) -> str:
        """Return current health state as a status string."""

    @abstractmethod
    async def list_tools(self) -> list[MCPToolDescriptor]:
        """Discover tools from the upstream MCP server."""

    @abstractmethod
    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Invoke the upstream MCP tool and return normalized result."""

    @property
    def server_id(self) -> str:
        """Return the server config ID."""
        return self.config.id
