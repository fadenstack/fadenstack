"""Commands run beside the reading, outlive their connection, and are accounted for on the next.

gpu-01, 2026-09-28: a model copy of half an hour ran inside the loop that
reads the connection. The server's status requests queued unread; with the
websocket's buffer full the socket was not read at all -- the replies to the
agent's own keepalive pings neither -- and the connection was dropped as
dead after 120 s, taking the copy with it. After a restart of the server the
copies both machines had been running stayed "running" for an hour.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from faden_agent.dispatcher import CommandDispatcher
from faden_agent.event_buffer import EventBuffer
from faden_agent.policy_guard import PolicyGuard
from faden_agent.state_store import StateStore
from faden_agent.stream_client import COMMANDS_HEADER, StreamClient


class _Socket:
    """A connection: what the agent sent on it, what it receives, and whether it is gone."""

    def __init__(self, incoming: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        for item in incoming or []:
            self.incoming.put_nowait(item)
        self.closed = False

    async def send(self, raw: str) -> None:
        if self.closed:
            raise ConnectionClosedError(None, None)
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        item = await self.incoming.get()
        if item is None:
            raise ConnectionClosedOK(None, None)
        return json.dumps(item)

    def results(self) -> list[str]:
        return [m["command_id"] for m in self.sent if m["type"] == "command_result"]


def _command(command_id: str, command_type: str = "start_ray_head") -> dict[str, Any]:
    return {"id": command_id, "command_type": command_type, "payload": {}, "issued_at": datetime.now(UTC).isoformat()}


def _agent(tmp_path: Path) -> tuple[StreamClient, CommandDispatcher, dict[str, asyncio.Event], list[str]]:
    """A stream client over the real dispatcher; each command waits for its own event to finish."""
    store = StateStore(tmp_path / "state.json")
    dispatcher = CommandDispatcher(state_store=store, runtime_manager=MagicMock(), policy_guard=PolicyGuard(),
                                   events=EventBuffer())
    gates: dict[str, asyncio.Event] = {}
    ran: list[str] = []

    async def execute(*, command_type: str, payload: dict[str, Any], emit_progress: Any) -> dict[str, Any]:
        name = payload.get("name", command_type)
        ran.append(name)
        gate = gates.get(name)
        if gate is not None:
            await gate.wait()
        return {"done": name}

    dispatcher._execute = execute  # type: ignore[method-assign]
    client = StreamClient.__new__(StreamClient)
    client._config = SimpleNamespace(require_signed_commands="auto")  # type: ignore[attr-defined]
    client._state = store  # type: ignore[attr-defined]
    client._signing_key = None  # type: ignore[attr-defined]
    client._send_lock = asyncio.Lock()  # type: ignore[attr-defined]
    client._dispatcher = dispatcher  # type: ignore[attr-defined]
    client._ws = None  # type: ignore[attr-defined]
    client._command_tasks = {}  # type: ignore[attr-defined]
    return client, dispatcher, gates, ran


async def _settle() -> None:
    """Let the commands' tasks run until nothing moves (state is saved to disk on the way)."""
    await asyncio.sleep(0.05)


@pytest.mark.asyncio()
async def test_a_long_copy_does_not_stop_the_status_requests_being_answered(tmp_path: Path) -> None:
    client, _dispatcher, gates, _ran = _agent(tmp_path)
    gates["copy"] = asyncio.Event()
    copy = _command("c-copy", "sync_model") | {"payload": {"name": "copy"}}
    status = _command("c-status", "get_ray_status") | {"payload": {"name": "status"}}
    ws = _Socket([{"type": "command", "command": copy}, {"type": "command", "command": status}, None])
    client._ws = ws

    await client._receive_loop(ws)  # reads both, and returns once the connection says it is closed
    await _settle()

    assert ws.results() == ["c-status"], "answered while the copy still runs"
    gates["copy"].set()
    await _settle()
    assert ws.results() == ["c-status", "c-copy"]


@pytest.mark.asyncio()
async def test_a_command_outlives_its_connection_and_the_next_connection_hears_of_it(tmp_path: Path) -> None:
    client, _dispatcher, gates, _ran = _agent(tmp_path)
    gates["copy"] = asyncio.Event()
    first = _Socket()
    client._ws = first
    client._start_command(first, _command("c-copy", "sync_model") | {"payload": {"name": "copy"}})
    await _settle()

    # The connection goes; the copy does not.
    first.closed = True
    client._ws = None
    assert client._commands_header() == "v1; running=c-copy; finished="

    gates["copy"].set()
    await _settle()
    assert first.results() == [], "nothing to send it on"
    # Finished with no connection open: the next one names it, and the server asks for it again.
    assert client._commands_header() == "v1; running=; finished=c-copy"


@pytest.mark.asyncio()
async def test_progress_and_result_go_out_on_the_connection_open_when_they_come(tmp_path: Path) -> None:
    client, _dispatcher, gates, _ran = _agent(tmp_path)
    gates["copy"] = asyncio.Event()
    first, second = _Socket(), _Socket()
    client._ws = first
    client._start_command(first, _command("c-copy", "sync_model") | {"payload": {"name": "copy"}})
    await _settle()
    first.closed = True
    client._ws = second  # reconnected

    gates["copy"].set()
    await _settle()
    assert second.results() == ["c-copy"]


@pytest.mark.asyncio()
async def test_a_command_delivered_again_while_it_runs_is_not_run_twice(tmp_path: Path) -> None:
    client, _dispatcher, gates, ran = _agent(tmp_path)
    gates["start"] = asyncio.Event()
    ws = _Socket()
    client._ws = ws
    command = _command("c-start") | {"payload": {"name": "start"}}
    client._start_command(ws, command)
    await _settle()
    client._start_command(ws, command | {"redispatch": True})  # the server sends overdue ones again
    await _settle()
    gates["start"].set()
    await _settle()
    assert ran == ["start"]
    assert ws.results() == ["c-start"]


@pytest.mark.asyncio()
async def test_a_finished_command_delivered_again_is_answered_from_the_store(tmp_path: Path) -> None:
    """What the server does with a command the header named as finished."""
    client, _dispatcher, _gates, ran = _agent(tmp_path)
    ws = _Socket()
    client._ws = ws
    command = _command("c-start") | {"payload": {"name": "start"}}
    client._start_command(ws, command)
    await _settle()
    client._start_command(ws, command | {"redispatch": True})
    await _settle()
    assert ran == ["start"]
    replayed = [m for m in ws.sent if m["type"] == "command_result"]
    assert len(replayed) == 2 and replayed[1]["result"]["replayed"] is True


def test_the_header_is_sent_by_name() -> None:
    assert COMMANDS_HEADER == "X-Faden-Commands"


# -- over a real websocket ---------------------------------------------------------


@pytest.mark.asyncio()
async def test_a_real_connection_stays_up_while_a_long_copy_runs(tmp_path: Path) -> None:
    """The failure itself: the server's requests pile up while a copy runs, and its pings must still be answered.

    With the command run inside the reading, the client stopped reading the
    socket once its buffer (16 messages) was full -- pongs included -- and the
    server's ping timed out. Here the server pings every 0.1 s and gives up
    after 0.5 s, and sends 50 status requests while a copy holds for 1.5 s.
    """
    import websockets

    client, _dispatcher, gates, _ran = _agent(tmp_path)
    gates["copy"] = asyncio.Event()
    answered: list[str] = []
    closed_early: list[str] = []

    async def server(connection: Any) -> None:
        await connection.send(json.dumps({"type": "command", "command": _command("c-copy", "sync_model") | {"payload": {"name": "copy"}}}))
        for i in range(50):
            await connection.send(json.dumps({"type": "command", "command": _command(f"c-status-{i}", "get_ray_status") | {"payload": {"name": f"s{i}"}}}))
        try:
            async for raw in connection:
                message = json.loads(raw)
                if message.get("type") == "command_result":
                    answered.append(message["command_id"])
                    if message["command_id"] == "c-copy":
                        return
        except websockets.ConnectionClosed as exc:
            closed_early.append(str(exc))

    async with websockets.serve(server, "127.0.0.1", 0, ping_interval=0.1, ping_timeout=0.5) as listening:
        port = listening.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            client._ws = ws
            reading = asyncio.create_task(client._receive_loop(ws))
            await asyncio.sleep(1.5)  # the copy runs; pings keep coming
            gates["copy"].set()
            await asyncio.sleep(0.5)
            reading.cancel()

    assert closed_early == [], "the connection was dropped while the copy ran"
    assert len([a for a in answered if a.startswith("c-status-")]) == 50
    assert answered[-1] == "c-copy"
