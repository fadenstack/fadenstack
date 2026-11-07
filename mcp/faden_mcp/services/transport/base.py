"""Abstract base class for MCP server transport adapters."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp.shared.exceptions import MCPError


logger = logging.getLogger(__name__)


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
    if isinstance(cause, httpx2.HTTPStatusError):
        response = cause.response
        said = f"{target} answered {response.status_code} {response.reason_phrase}"
        hint = _STATUS_HINTS.get(response.status_code)
        message = f"{said}: {hint}" if hint else f"{said}."
    elif isinstance(cause, httpx2.TimeoutException):
        message = f"{target} did not answer in time."
    elif isinstance(cause, httpx2.ConnectError):
        message = f"Cannot reach {target}: {cause or 'the connection failed'}."
    elif isinstance(cause, FileNotFoundError):
        missing = cause.filename or cause
        message = f"Cannot start {target}: {missing} is not in the MCP container."
    elif isinstance(cause, MCPError) and "Session terminated" in str(cause):
        # How the SDK reports a 404 to the first request: no endpoint there.
        message = f"{target} has no MCP endpoint (it answered 404). {_ENDPOINT_PATHS}"
    else:
        message = f"{target}: {type(cause).__name__}: {cause}"
    return message


class OwnedConnection:
    """An MCP connection opened, kept and closed by one task of its own.

    The SDK's clients run their transport in an anyio task group, and a task
    group must be left by the task that entered it. The service opened each
    connection in whichever task connected it -- the app's startup, a
    request -- and closed it from another. When the transport failed (the
    server restarted, a stream dropped), the SDK cancelled the task that had
    opened it, which could never leave the task group: anyio retried that
    cancellation on every turn of the event loop, and the MCP service used a
    whole core, doing nothing, until it restarted (VM, 2026-09-26). Here the
    owner task opens the connection and waits; other tasks use the session;
    a failure ends only the owner, which closes the connection itself, and
    the heartbeat connects again.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._task: asyncio.Task[None] | None = None
        self._close = asyncio.Event()
        #: Set once the connection is gone: closed, or failed.
        self.closed = False

    async def open(
        self,
        enter: Callable[[AsyncExitStack], Awaitable[Any]],
        target: str,
    ) -> Any:
        """Open the connection in its own task; what *enter* returned (the session).

        A failure is raised here, as :func:`failed_connect` words it; a
        cancellation of the caller stops the owner too.
        """
        ready: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(
            self._own(enter, target, ready), name=f"mcp-connection:{self.name}",
        )
        try:
            return await ready
        except asyncio.CancelledError:
            self._task.cancel()
            raise

    async def _own(
        self,
        enter: Callable[[AsyncExitStack], Awaitable[Any]],
        target: str,
        ready: asyncio.Future[Any],
    ) -> None:
        stack = AsyncExitStack()
        try:
            try:
                session = await enter(stack)
            except BaseException as error:  # failure, or cancellation: failed_connect
                failure = await failed_connect(stack, error, target)
                if not ready.done():
                    if failure is None:
                        ready.cancel()
                    else:
                        ready.set_exception(failure)
                return
            ready.set_result(session)
            await self._close.wait()
        except BaseException:  # the transport failed and cancelled its owner
            logger.info("MCP connection to %s ended", self.name)
        finally:
            self.closed = True
            try:
                await stack.aclose()  # in the task that opened it
            except BaseException:
                logger.debug(
                    "Closing the connection to %s raised", self.name, exc_info=True,
                )

    async def close(self, timeout: float = 10.0) -> None:
        """Close the connection: its owner leaves it, in its own task."""
        self._close.set()
        task = self._task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            task.cancel()
        except BaseException:
            logger.debug(
                "Closing the connection to %s raised", self.name, exc_info=True,
            )


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
