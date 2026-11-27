"""The native commands outside the manager: dispatch, ordering, policy, the Apple collector."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from faden_agent.command_order import CHANGE, OBSERVE, TRANSFER, kind_of
from faden_agent.dispatcher import CommandDispatcher
from faden_agent.event_buffer import EventBuffer
from faden_agent.models import NodeCommandType as T
from faden_agent.policy_guard import PolicyGuard


def _dispatcher(tmp_path, native=None, allowlist=None) -> CommandDispatcher:
    from faden_agent.state_store import StateStore

    store = StateStore(tmp_path / "state.json")
    return CommandDispatcher(
        state_store=store, runtime_manager=MagicMock(), policy_guard=PolicyGuard(image_allowlist=allowlist),
        events=EventBuffer(), native_manager=native,
    )


async def _noop(_p):
    return None


@pytest.mark.asyncio
async def test_without_a_native_runtime_the_commands_are_unsupported(tmp_path) -> None:
    result = await _dispatcher(tmp_path).handle(
        {"id": "c1", "command_type": "start_native_worker", "payload": {}}, _noop
    )
    assert result["success"] is False and result["error_code"] == "unsupported"


@pytest.mark.asyncio
async def test_commands_reach_the_manager_and_errors_keep_their_code(tmp_path) -> None:
    from faden_agent.native import NativeError

    native = SimpleNamespace(
        native_worker_status=AsyncMock(return_value={"ok": True, "state": "ready"}),
        start_native_worker=AsyncMock(side_effect=NativeError("busy", code="worker_exists")),
        stage_native_runtime=AsyncMock(return_value={"ok": True, "staged": True}),
    )
    d = _dispatcher(tmp_path, native, allowlist=["ghcr.io/fadenstack/"])
    ok = await d.handle({"id": "c1", "command_type": "native_worker_status", "payload": {"deployment_id": "x"}}, _noop)
    assert ok["success"] and ok["result"]["state"] == "ready"
    bad = await d.handle({"id": "c2", "command_type": "start_native_worker", "payload": {}}, _noop)
    assert bad["error_code"] == "worker_exists"
    refused = await d.handle(
        {"id": "c3", "command_type": "stage_native_runtime", "payload": {"oci_image": "evil.io/x"}}, _noop
    )
    assert refused["error_code"] == "policy_violation"
    native.stage_native_runtime.assert_not_called()


def test_command_kinds() -> None:
    assert kind_of(T.STAGE_NATIVE_RUNTIME) == TRANSFER
    assert kind_of(T.NATIVE_WORKER_STATUS) == OBSERVE
    assert kind_of(T.NATIVE_WORKER_LOGS) == OBSERVE
    assert kind_of(T.START_NATIVE_WORKER) == CHANGE
    assert kind_of(T.STOP_NATIVE_WORKER) == CHANGE
