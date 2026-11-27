"""A native command is issued once and read on later passes; a changed request is a new command."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from faden_backend.services.inference.drivers.ray.commands import NodeCommandGateway
from faden_backend.services.inference.drivers.standalone.commands import NativeCommands

pytestmark = pytest.mark.anyio()


class FakeGateway(NodeCommandGateway):
    def __init__(self) -> None:  # noqa: D107 - no session behind it
        self.commands: dict[str, SimpleNamespace] = {}
        self.issued: list[dict[str, Any]] = []

    async def issue(self, **kw: Any) -> SimpleNamespace:
        self.issued.append(kw)
        cmd = SimpleNamespace(id=uuid.uuid4(), status="queued", result_json=None, error_message=None)
        self.commands[str(cmd.id)] = cmd
        return cmd

    async def get_command(self, command_id: Any) -> SimpleNamespace | None:
        return self.commands.get(str(command_id))

    async def wait(self, command_id: Any, **_: Any) -> SimpleNamespace | None:
        cmd = self.commands[str(command_id)]
        return cmd if cmd.status in ("succeeded", "failed", "timed_out", "canceled") else None


async def test_a_step_is_issued_once_and_read_back() -> None:
    gw, record = FakeGateway(), {}
    cmds = NativeCommands(gw, record, owner="d1")
    kw = dict(node_id=uuid.uuid4(), command_type="start_native_worker", payload={"port": 28100}, timeout_sec=60, wait_sec=0.1)

    first = await cmds.step("start", **kw)
    assert first.state == "running" and len(gw.issued) == 1
    assert gw.issued[0]["idempotency_key"].startswith("standalone:d1:start:")

    again = await cmds.step("start", **kw)
    assert again.state == "running" and len(gw.issued) == 1, "the next pass reads the same command"

    gw.commands[record["start"]["id"]].status = "succeeded"
    gw.commands[record["start"]["id"]].result_json = {"pid": 7}
    done = await cmds.step("start", **kw)
    assert done.done and done.result == {"pid": 7} and len(gw.issued) == 1


async def test_a_changed_request_is_a_new_command() -> None:
    gw, record = FakeGateway(), {}
    cmds = NativeCommands(gw, record, owner="d1")
    base = dict(node_id=uuid.uuid4(), command_type="start_native_worker", timeout_sec=60, wait_sec=0)
    await cmds.step("start", payload={"generation": 1}, **base)
    await cmds.step("start", payload={"generation": 2}, **base)
    assert len(gw.issued) == 2


async def test_a_failed_command_or_an_ok_false_result_is_a_failure() -> None:
    gw, record = FakeGateway(), {}
    cmds = NativeCommands(gw, record, owner="d1")
    kw = dict(node_id=uuid.uuid4(), command_type="stage_native_runtime", payload={}, timeout_sec=60, wait_sec=0)
    await cmds.step("stage", **kw)
    cmd = gw.commands[record["stage"]["id"]]
    cmd.status, cmd.error_message = "failed", "digest mismatch"
    out = await cmds.step("stage", **kw)
    assert out.failed and out.error == "digest mismatch"

    cmds.forget("stage")
    await cmds.step("stage", **kw)
    cmd = gw.commands[record["stage"]["id"]]
    cmd.status, cmd.result_json = "succeeded", {"ok": False, "error": "no space left"}
    out = await cmds.step("stage", **kw)
    assert out.failed and out.error == "no space left"
    assert len(gw.issued) == 2
