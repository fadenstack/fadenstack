"""Manages connections to registered MCP servers."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import anyio
import httpx
from mcp.shared.exceptions import McpError

from faden_mcp.db.models.mcp import MCPServerModel
from faden_mcp.services.transport.base import GenericMCPServer, MCPServerConfig

logger = logging.getLogger(__name__)


def _server_config_from_model(server: MCPServerModel) -> MCPServerConfig:
    """Build an MCPServerConfig from a database model."""
    headers = None
    if server.headers_json_encrypted:
        try:
            headers = (
                json.loads(server.headers_json_encrypted)
                if isinstance(server.headers_json_encrypted, str)
                else server.headers_json_encrypted
            )
        except (json.JSONDecodeError, TypeError):
            headers = None

    env = None
    if server.env_json_encrypted:
        try:
            env = (
                json.loads(server.env_json_encrypted)
                if isinstance(server.env_json_encrypted, str)
                else server.env_json_encrypted
            )
        except (json.JSONDecodeError, TypeError):
            env = None

    return MCPServerConfig(
        id=str(server.id),
        name=server.name,
        transport=server.transport,
        url=server.url,
        command=server.command_json,
        args=server.args_json,
        working_dir=server.working_dir,
        headers=headers,
        env=env,
        timeout_sec=server.timeout_sec,
        tool_prefix=server.tool_prefix,
    )


def _create_adapter(config: MCPServerConfig) -> GenericMCPServer:
    """Factory: select transport adapter based on config.transport."""
    if config.transport == "stdio":
        from faden_mcp.services.transport.stdio import StdioMCPServer

        return StdioMCPServer(config)
    if config.transport == "sse":
        from faden_mcp.services.transport.sse import SSEMCPServer

        return SSEMCPServer(config)
    if config.transport == "streamable_http":
        from faden_mcp.services.transport.streamable_http import (
            StreamableHTTPMCPServer,
        )

        return StreamableHTTPMCPServer(config)
    msg = f"Unsupported transport: {config.transport}"
    raise ValueError(msg)


def session_gone(exc: BaseException) -> bool:
    """Whether *exc* says the connection is gone, before a request reached the server.

    A server that restarted -- its container restarted, or was replaced when
    edited from the console -- no longer knows the session: it answers 404,
    which the SDK reports as "Session terminated", and the request was not
    run. A closed stream or a refused connection did not send it either. A
    call that failed so can be sent again, on a new connection.
    """
    if isinstance(exc, McpError):
        return "session terminated" in str(exc).lower()
    gone = (anyio.ClosedResourceError, anyio.BrokenResourceError, httpx.ConnectError)
    return isinstance(exc, gone)


class MCPConnectionManager:
    """Owns active connections to all registered MCP servers."""

    def __init__(self) -> None:
        self._connections: dict[str, GenericMCPServer] = {}
        self._reconnecting: dict[str, asyncio.Lock] = {}

    @property
    def active_count(self) -> int:
        """Return the number of active connections."""
        return len(self._connections)

    def get(self, server_id: str) -> GenericMCPServer | None:
        """Get an active connection by server ID."""
        return self._connections.get(server_id)

    async def start(self, server: MCPServerModel) -> GenericMCPServer:
        """Create and connect a transport adapter for the given server."""
        server_id = str(server.id)
        if server_id in self._connections:
            logger.warning("Server %s already connected — skipping", server_id)
            return self._connections[server_id]

        config = _server_config_from_model(server)
        adapter = _create_adapter(config)
        await adapter.connect()
        self._connections[server_id] = adapter
        return adapter

    async def start_from_config(self, config: MCPServerConfig) -> GenericMCPServer:
        """Create and connect a transport adapter from config (for testing)."""
        adapter = _create_adapter(config)
        await adapter.connect()
        self._connections[config.id] = adapter
        return adapter

    async def stop(self, server_id: str) -> None:
        """Disconnect and remove a server connection."""
        adapter = self._connections.pop(server_id, None)
        if adapter is not None:
            try:
                await adapter.disconnect()
            except Exception:
                logger.warning(
                    "Error disconnecting server %s",
                    server_id,
                    exc_info=True,
                )

    async def reconnect(
        self,
        server_id: str,
        stale: GenericMCPServer | None = None,
    ) -> GenericMCPServer | None:
        """Connect again to a server whose session is gone: the new connection, or None.

        The connection was never made again: after its server restarted, every
        health check and tool call failed until the MCP service itself
        restarted (an MCP server's container edited from the console, VM,
        2026-09-26). The old connection stays registered until a new one is
        up, so a failed attempt is tried again at the next heartbeat. When
        *stale* is given and another caller has replaced it meanwhile, that
        connection is returned.
        """
        lock = self._reconnecting.setdefault(server_id, asyncio.Lock())
        async with lock:
            current = self._connections.get(server_id)
            if current is None:
                return None
            if stale is not None and current is not stale:
                return current
            fresh = _create_adapter(current.config)
            try:
                await fresh.connect()
            except Exception:
                logger.warning(
                    "Could not reconnect to MCP server %s",
                    current.config.name,
                    exc_info=True,
                )
                return None
            self._connections[server_id] = fresh
            try:
                await current.disconnect()
            except BaseException:  # the old session is gone already
                logger.debug(
                    "Closing the old connection to %s failed",
                    current.config.name,
                    exc_info=True,
                )
            logger.info("Reconnected to MCP server %s", current.config.name)
            return fresh

    async def call_tool(
        self,
        server_id: str,
        adapter: GenericMCPServer,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a tool; once more on a new connection if the session was gone.

        Only then: a call that failed so was not run (see :func:`session_gone`).
        """
        try:
            return await adapter.call_tool(tool_name, arguments)
        except Exception as exc:
            if not session_gone(exc):
                raise
            fresh = await self.reconnect(server_id, stale=adapter)
            if fresh is None:
                raise
            return await fresh.call_tool(tool_name, arguments)

    async def restart(self, server: MCPServerModel) -> GenericMCPServer:
        """Disconnect and reconnect a server."""
        await self.stop(str(server.id))
        return await self.start(server)

    async def stop_all(self) -> None:
        """Disconnect all active servers."""
        server_ids = list(self._connections.keys())
        for server_id in server_ids:
            await self.stop(server_id)

    async def health(self, server_id: str) -> str:
        """Check health of a specific server connection."""
        adapter = self._connections.get(server_id)
        if adapter is None:
            return "disconnected"
        try:
            return await adapter.healthcheck()
        except Exception:
            return "error"

    async def heartbeat_all(
        self,
        redis_pool: Any | None = None,
    ) -> None:
        """Run healthcheck on all active connections and update Redis."""
        for server_id, adapter in list(self._connections.items()):
            try:
                status = await adapter.healthcheck()
            except Exception:
                status = "error"
                logger.debug(
                    "Heartbeat failed for %s",
                    server_id,
                    exc_info=True,
                )
            if status != "active":
                fresh = await self.reconnect(server_id, stale=adapter)
                if fresh is not None:
                    try:
                        status = await fresh.healthcheck()
                    except Exception:
                        status = "error"

            if redis_pool is not None:
                try:
                    from redis.asyncio import Redis

                    async with Redis(connection_pool=redis_pool) as redis:
                        await redis.set(
                            f"mcp:server:{server_id}:status",
                            status,
                            ex=120,
                        )
                        await redis.set(
                            f"mcp:server:{server_id}:heartbeat",
                            "1",
                            ex=90,
                        )
                except Exception:
                    logger.debug(
                        "Redis heartbeat update failed for %s",
                        server_id,
                        exc_info=True,
                    )
