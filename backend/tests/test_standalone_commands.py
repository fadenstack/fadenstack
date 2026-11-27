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


class _Session:
    def __init__(self) -> None:
        self.flushed = self.committed = False

    async def flush(self) -> None:
        self.flushed = True

    async def commit(self) -> None:
        self.committed = True


class _RequestScopedService:
    """What the API route passes: a NodeControlService on the request's own, uncommitted session."""

    def __init__(self) -> None:
        self._dao = SimpleNamespace(session=_Session())
        self.cmd = SimpleNamespace(id=uuid.uuid4(), status="queued", result_json=None, error_message=None)

    async def issue_command(self, **_: Any) -> SimpleNamespace:
        return self.cmd

    async def get_command(self, *, command_id: Any) -> SimpleNamespace | None:
        # The agent answers only what it could see: a committed command.
        if self._dao.session.committed:
            self.cmd.status, self.cmd.result_json = "succeeded", {"ok": True, "lines": ["boom"], "cursor": "4"}
        return self.cmd


async def test_ask_on_a_request_scoped_service_commits_the_command_so_the_agent_can_see_it() -> None:
    service = _RequestScopedService()
    answer = await NativeCommands(service, {}, owner="d1").ask(
        node_id=uuid.uuid4(), command_type="native_worker_logs", payload={}, wait_sec=2,
    )
    assert service._dao.session.committed
    assert answer == {"ok": True, "lines": ["boom"], "cursor": "4"}


async def test_ask_reports_a_failed_answer_instead_of_silence() -> None:
    gw = FakeGateway()
    cmds = NativeCommands(gw, {}, owner="d1")

    async def wait(command_id: Any, **_: Any) -> SimpleNamespace:
        cmd = gw.commands[str(command_id)]
        cmd.status, cmd.error_message = "failed", "no such deployment"
        return cmd

    gw.wait = wait  # type: ignore[method-assign]
    answer = await cmds.ask(node_id=uuid.uuid4(), command_type="native_worker_logs", payload={})
    assert answer == {"ok": False, "error": "no such deployment"}
