"""The node stream waits for the backend's hello_ack, and a stop closes it with 1001.

Live on a Mac after a reboot (.22, 27 Nov), a stream stayed open for seven
minutes, its pings answered, while the backend recorded none of its heartbeats:
the machine read offline until the user logged in. And a SIGTERM killed the
agent without a close frame, leaving the backend a half-open session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import websockets

from faden_agent import __main__ as cli
from faden_agent.state_store import StateStore
from faden_agent.stream_client import HelloAckTimeout, StreamClient


async def _forever(*_args: Any) -> None:
    await asyncio.Event().wait()


def _client(tmp_path: Path, port: int, *, hello_ack_timeout: float = 0.3) -> StreamClient:
    """A real StreamClient whose periodic loops are idle, so only the protocol under test runs."""
    client = StreamClient.__new__(StreamClient)
    client._config = SimpleNamespace(  # type: ignore[attr-defined]
        backend_url=f"http://127.0.0.1:{port}",
        request_timeout_sec=5.0,
        hello_ack_timeout_sec=hello_ack_timeout,
        require_signed_commands="auto",
    )
    client._state = StateStore(tmp_path / "state.json")  # type: ignore[attr-defined]
    client._dispatcher = MagicMock(running_command_ids=lambda: [])  # type: ignore[attr-defined]
    client._command_tasks = {}  # type: ignore[attr-defined]
    client._send_lock = asyncio.Lock()  # type: ignore[attr-defined]
    client._ws = None  # type: ignore[attr-defined]
    for loop_name in ("_heartbeat_loop", "_inventory_loop", "_event_flush_loop"):
        setattr(client, loop_name, _forever)
    client._seq_flush_loop = _forever  # type: ignore[method-assign]
    client._credential_rotation_loop = _forever  # type: ignore[method-assign]
    client._health_supervisor = SimpleNamespace(run_forever=_forever)  # type: ignore[attr-defined]
    return client


class _Server:
    """A local node-stream endpoint: whether it acknowledges, and how each connection ended."""

    def __init__(self, *, ack: bool) -> None:
        self.ack = ack
        self.connections = 0
        self.close_codes: list[int | None] = []

    async def handler(self, connection: Any) -> None:
        self.connections += 1
        if self.ack:
            await connection.send(json.dumps({"type": "hello_ack", "session_id": "s-123", "node_id": "n-1",
                                              "commands": [], "profile": None, "signs_commands": False}))
        try:
            async for _ in connection:
                pass
        finally:
            self.close_codes.append(connection.close_code)


@pytest.mark.asyncio()
async def test_a_stream_the_backend_never_acknowledges_is_closed_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server = _Server(ack=False)
    async with websockets.serve(server.handler, "127.0.0.1", 0) as listening:
        port = listening.sockets[0].getsockname()[1]
        client = _client(tmp_path, port, hello_ack_timeout=0.3)
        started = time.monotonic()
        with caplog.at_level(logging.INFO, logger="faden_agent.stream_client"):
            with pytest.raises(HelloAckTimeout):
                await asyncio.wait_for(client.run(credential="c.s"), timeout=5)
        assert time.monotonic() - started < 3
        await asyncio.sleep(0.1)
    assert server.close_codes, "the agent closed the stream it gave up on"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].getMessage().startswith("The backend did not acknowledge the node stream within ")
    assert warnings[0].getMessage().endswith(" s; reconnecting.")
    assert not any("hello_ack, session" in r.getMessage() for r in caplog.records)


def test_a_timeout_is_a_reconnect_not_a_fatal_error() -> None:
    # The service treats RuntimeError as fatal (it re-raises); a missing ack must not be one.
    assert not issubclass(HelloAckTimeout, RuntimeError)


@pytest.mark.asyncio()
async def test_an_acknowledged_stream_logs_its_session_and_stays_open(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server = _Server(ack=True)
    async with websockets.serve(server.handler, "127.0.0.1", 0) as listening:
        port = listening.sockets[0].getsockname()[1]
        client = _client(tmp_path, port, hello_ack_timeout=0.3)
        with caplog.at_level(logging.INFO, logger="faden_agent.stream_client"):
            running = asyncio.create_task(client.run(credential="c.s"))
            await asyncio.sleep(0.8)  # well past the ack timeout
            assert not running.done(), "an acknowledged stream is not reconnected"
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
    assert server.connections == 1
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert "Node stream acknowledged by the backend (hello_ack, session s-123)." in infos
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class _Service:
    """What ``_run_until_stopped`` drives: a run that never ends, and a stop over a real stream."""

    def __init__(self, client: StreamClient) -> None:
        self.client = client
        self.cancelled = False

    async def run_forever(self) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def stop(self, *, timeout: float) -> str | None:
        return await self.client.close_for_shutdown(timeout=timeout)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.asyncio()
async def test_sigterm_closes_the_stream_with_1001_and_returns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    server = _Server(ack=True)
    async with websockets.serve(server.handler, "127.0.0.1", 0) as listening:
        port = listening.sockets[0].getsockname()[1]
        client = _client(tmp_path, port)
        ws = await websockets.connect(f"ws://127.0.0.1:{port}")
        client._ws = ws  # type: ignore[attr-defined]
        service = _Service(client)
        asyncio.get_running_loop().call_later(0.2, os.kill, os.getpid(), signal.SIGTERM)
        with caplog.at_level(logging.INFO, logger="faden_agent"):
            await asyncio.wait_for(cli._run_until_stopped(service), timeout=5)
        await asyncio.sleep(0.1)
    assert server.close_codes == [1001]
    assert service.cancelled
    assert "Stopping: closed the node stream." in [r.getMessage() for r in caplog.records]


class _DeadSocket:
    """A connection whose other end is gone: the close never completes."""

    close_timeout = 10.0

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.code = code
        await asyncio.Event().wait()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.asyncio()
async def test_sigterm_still_returns_promptly_when_the_close_cannot_complete(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_STOP_CLOSE_TIMEOUT_SEC", 0.2)
    client = _client(tmp_path, 1)
    dead = _DeadSocket()
    client._ws = dead  # type: ignore[attr-defined]
    service = _Service(client)
    asyncio.get_running_loop().call_later(0.1, os.kill, os.getpid(), signal.SIGTERM)
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="faden_agent"):
        await asyncio.wait_for(cli._run_until_stopped(service), timeout=5)
    assert time.monotonic() - started < 2
    assert dead.code == 1001
    assert dead.close_timeout == 0.2
    assert service.cancelled
    assert any(r.getMessage().startswith("Stopping: could not close the node stream: no answer within")
               for r in caplog.records)

