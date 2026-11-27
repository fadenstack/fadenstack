"""Commands the standalone driver sends, carried across reconcile passes.

A native step can take far longer than one pass (staging a 2 GB runtime, a
cold engine start of ~100 s), so a pass never waits it out. The command's id
is kept on the resource's observed status; the next pass reads that command
instead of issuing another, and a changed payload issues a new one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from faden_backend.db.models.node_control import NodeCommandStatus
from faden_backend.services.inference.drivers.ray.commands import NodeCommandGateway

log = logging.getLogger(__name__)

_TERMINAL_FAILURES = {
    NodeCommandStatus.FAILED.value,
    NodeCommandStatus.CANCELED.value,
    NodeCommandStatus.TIMED_OUT.value,
}


@dataclass
class StepOutcome:
    """Where one command stands: ``done`` (with its result), ``running`` or ``failed`` (with why)."""

    state: str
    result: dict[str, Any] | None = None
    error: str | None = None

    @property
    def done(self) -> bool:
        return self.state == "done"

    @property
    def failed(self) -> bool:
        return self.state == "failed"


def payload_hash(payload: dict[str, Any]) -> str:
    """A stable digest of a command payload: the same request is the same command."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


class NativeCommands:
    """Issue-once, read-later commands for one resource.

    *record* is a dict on the resource's observed status (mutated in place);
    the caller writes it back. Keys name steps (``stage``, ``start``, ``stop``...).
    """

    def __init__(self, node_control: Any, record: dict[str, Any], *, owner: str) -> None:
        self._gateway = node_control if isinstance(node_control, NodeCommandGateway) else NodeCommandGateway(node_control)
        self._record = record
        self._owner = owner

    def forget(self, step: str) -> None:
        """Drop a step so the next :meth:`step` issues it afresh."""
        self._record.pop(step, None)

    async def step(
        self,
        step: str,
        *,
        node_id: uuid.UUID | str,
        command_type: str,
        payload: dict[str, Any],
        timeout_sec: int,
        wait_sec: float = 5.0,
    ) -> StepOutcome:
        """Issue *command_type* once for *step*, then report where it stands.

        Waits at most *wait_sec* in this pass: a short command answers within
        it, a long one is read again on the next pass.
        """
        digest = payload_hash(payload)
        entry = self._record.get(step)
        command = None
        if isinstance(entry, dict) and entry.get("payload") == digest and entry.get("id"):
            command = await self._gateway.get_command(entry["id"])
        if command is None:
            command = await self._gateway.issue(
                node_id=node_id,
                command_type=command_type,
                payload=payload,
                idempotency_key=f"standalone:{self._owner}:{step}:{digest}",
                timeout_sec=timeout_sec,
            )
            self._record[step] = {"id": str(command.id), "payload": digest, "type": command_type}
        status = str(getattr(command, "status", "") or "")
        if status not in (NodeCommandStatus.SUCCEEDED.value, *_TERMINAL_FAILURES) and wait_sec > 0:
            waited = await self._gateway.wait(command.id, budget_sec=wait_sec, poll_interval_sec=0.5)
            if waited is not None:
                command = waited
                status = str(command.status)
        if status == NodeCommandStatus.SUCCEEDED.value:
            result = dict(getattr(command, "result_json", None) or {})
            if result.get("ok") is False:
                return StepOutcome("failed", result, str(result.get("error") or f"{command_type} failed"))
            return StepOutcome("done", result)
        if status in _TERMINAL_FAILURES:
            error = getattr(command, "error_message", None) or f"{command_type} ended {status}"
            return StepOutcome("failed", dict(getattr(command, "result_json", None) or {}), str(error))
        return StepOutcome("running")

    async def ask(
        self,
        *,
        node_id: uuid.UUID | str,
        command_type: str,
        payload: dict[str, Any],
        wait_sec: float = 10.0,
    ) -> dict[str, Any] | None:
        """A read-only question with a fresh command each time; ``None`` when not answered in *wait_sec*."""
        command = await self._gateway.issue(
            node_id=node_id,
            command_type=command_type,
            payload=payload,
            idempotency_key=f"standalone:{self._owner}:ask:{command_type}:{uuid.uuid4().hex[:12]}",
            timeout_sec=max(int(wait_sec) * 3, 30),
        )
        await self._gateway.publish()  # the agent cannot see an uncommitted command
        done = await self._gateway.wait(command.id, budget_sec=wait_sec, poll_interval_sec=0.5)
        if done is None:
            return None
        if str(done.status) != NodeCommandStatus.SUCCEEDED.value:
            # Answered, but with a failure: say so, rather than pass it off as silence.
            error = getattr(done, "error_message", None) or f"{command_type} ended {done.status}"
            return {"ok": False, "error": str(error)}
        return dict(done.result_json or {})
