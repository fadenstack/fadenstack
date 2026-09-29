"""On every connect the agent says which commands it is still executing.

The backend fails the rest of what it had sent here: it died with the last
connection. Without this, after a restart of the backend -- which never saw
that connection close -- the model copies both DGX Sparks had been running
read "running" for the hour it takes to call a command overdue.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from faden_agent.dispatcher import CommandDispatcher
from faden_agent.event_buffer import EventBuffer
from faden_agent.policy_guard import PolicyGuard
from faden_agent.state_store import StateStore
from faden_agent.stream_client import RUNNING_COMMANDS_HEADER, StreamClient


def _dispatcher(tmp_path: Path) -> CommandDispatcher:
    return CommandDispatcher(
        state_store=StateStore(tmp_path / "state.json"), runtime_manager=MagicMock(),
        policy_guard=PolicyGuard(), events=EventBuffer(),
    )


@pytest.mark.asyncio()
async def test_the_dispatcher_knows_what_it_is_still_running(tmp_path: Path) -> None:
    dispatcher = _dispatcher(tmp_path)
    release = asyncio.Event()

    async def long(**_: Any) -> dict[str, Any]:
        await release.wait()
        return {"ok": True}

    dispatcher._execute = long  # type: ignore[method-assign]
    running = asyncio.create_task(dispatcher.handle({"id": "c-sync", "command_type": "sync_model"}, AsyncMock()))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert dispatcher.running_command_ids() == ["c-sync"]
    release.set()
    await running
    assert dispatcher.running_command_ids() == []


def test_the_header_names_them_and_says_so_when_there_are_none(tmp_path: Path) -> None:
    client = StreamClient.__new__(StreamClient)
    client._dispatcher = MagicMock(running_command_ids=MagicMock(return_value=["a1", "b2"]))
    assert client._running_commands_header() == "v1:a1,b2"
    client._dispatcher = MagicMock(running_command_ids=MagicMock(return_value=[]))
    assert client._running_commands_header() == "v1:", "none running is said too: an older agent says nothing"
    assert RUNNING_COMMANDS_HEADER.lower() == "x-faden-running-commands"
