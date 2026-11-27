"""COMPAT C2-1: a Linux agent advertises exactly what 0.2.12 did; the native commands answer "unsupported"."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from faden_agent.dispatcher import CommandDispatcher
from faden_agent.event_buffer import EventBuffer
from faden_agent.models import NodeCommandType as T
from faden_agent.policy_guard import PolicyGuard
from faden_agent.preflight import build_static_capabilities
from faden_agent.service import NodeAgentService, add_agent_capabilities

FIXTURE = Path(__file__).parent / "fixtures" / "capabilities_0.2.12_linux.json"


@pytest.mark.asyncio
async def test_linux_capability_keys_equal_0_2_12(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    caps = await build_static_capabilities(None, None, paths={"model_store": "/srv/faden/models"})
    add_agent_capabilities(caps)
    host = SimpleNamespace(_native=None)
    assert await NodeAgentService._start_native(host, caps) is None  # type: ignore[arg-type]
    expected = json.loads(FIXTURE.read_text())["linux_capability_keys"]
    assert sorted(caps) == expected
    values = json.loads(FIXTURE.read_text())["linux_static_values"]
    assert {k: caps[k] for k in values} == values
    assert "native_runtime" not in caps and "metal" not in caps


NATIVE = [T.STAGE_NATIVE_RUNTIME, T.NATIVE_RUNTIME_STATUS, T.START_NATIVE_WORKER,
          T.NATIVE_WORKER_STATUS, T.STOP_NATIVE_WORKER, T.NATIVE_WORKER_LOGS]


@pytest.mark.parametrize("command", NATIVE)
@pytest.mark.asyncio
async def test_native_commands_on_linux_are_cleanly_unsupported(tmp_path: Path, command: T) -> None:
    from faden_agent.state_store import StateStore

    dispatcher = CommandDispatcher(
        state_store=StateStore(tmp_path / "state.json"), runtime_manager=MagicMock(),
        policy_guard=PolicyGuard(), events=EventBuffer(), native_manager=None,
    )

    async def _noop(_p):
        return None

    result = await dispatcher.handle({"id": f"c-{command.value}", "command_type": command.value, "payload": {}}, _noop)
    assert result == {
        "success": False, "error_code": "unsupported", "result": {},
        "error_message": f"{command.value}: this machine has no native runtime support (macOS on Apple silicon only).",
    }
