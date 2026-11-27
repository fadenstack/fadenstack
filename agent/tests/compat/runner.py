"""Runs one scenario against an agent tree: ``python runner.py <agent dir> < scenario.json``.

The same script drives node-agent-v0.2.12 (extracted from its tag) and the
current tree, in a child process, so the two ``faden_agent`` packages never
meet. It prints one JSON object on its last stdout line.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class _ModelTls:
    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    async def ensure_route(self, public: int, internal: int) -> dict[str, Any]:
        self.calls.append(["ensure", public, internal])
        return {"public_port": public, "internal_port": internal}

    async def remove_route(self, public: int) -> None:
        self.calls.append(["remove", public])


def _kwargs(cls: Any, **wanted: Any) -> dict[str, Any]:
    accepted = inspect.signature(cls.__init__).parameters
    return {k: v for k, v in wanted.items() if k in accepted}


async def dispatch(scenario: dict[str, Any], work: Path) -> dict[str, Any]:
    from faden_agent.dispatcher import CommandDispatcher
    from faden_agent.event_buffer import EventBuffer
    from faden_agent.policy_guard import PolicyGuard
    from faden_agent.runtime_manager import RuntimeManager
    from faden_agent.state_store import StateStore

    tls = _ModelTls()
    runtime = RuntimeManager.__new__(RuntimeManager)
    runtime._model_tls = tls  # type: ignore[attr-defined]
    dispatcher = CommandDispatcher(**_kwargs(
        CommandDispatcher, state_store=StateStore(work / "state.json"), runtime_manager=runtime,
        policy_guard=PolicyGuard(), events=EventBuffer(),
    ))

    async def progress(_p: Any) -> None:
        return None

    results = {}
    for command in scenario["commands"]:
        results[command["id"]] = await dispatcher.handle(command, progress)
    return {"results": results, "tls_calls": tls.calls}


class _Ws:
    def __init__(self, frames: list[Any]) -> None:
        self._frames = list(frames)

    async def recv(self) -> Any:
        if self._frames:
            return self._frames.pop(0)
        from websockets.exceptions import ConnectionClosed

        raise ConnectionClosed(None, None)


async def receive(scenario: dict[str, Any], work: Path) -> dict[str, Any]:
    from faden_agent.state_store import StateStore
    from faden_agent.stream_client import StreamClient

    client = StreamClient.__new__(StreamClient)
    client._state = StateStore(work / "state.json")  # type: ignore[attr-defined]
    started: list[dict[str, Any]] = []
    client._start_command = lambda _ws, command: started.append(command)  # type: ignore[attr-defined]
    frames = [json.dumps(f) if isinstance(f, dict) else f for f in scenario["frames"]]
    await client._receive_loop(_Ws(frames))  # type: ignore[attr-defined]
    st = client._state.state  # type: ignore[attr-defined]
    return {"started": [c["id"] for c in started], "node_id": st.node_id, "profile": st.profile,
            "commands_signed": st.commands_signed}


def state(scenario: dict[str, Any], _work: Path) -> dict[str, Any]:
    from faden_agent.state_store import StateStore

    path = Path(scenario["path"])
    store = StateStore(path)
    if scenario.get("write"):
        w = scenario["write"]
        s = store.state
        s.node_id, s.credential = w["node_id"], w["credential"]
        s.commands_signed, s.profile = w["commands_signed"], w["profile"]
        s.maintenance_mode, s.draining = w["maintenance_mode"], w["draining"]
        store.set_workload("rt-1", w["workload"])
        store.remember_command_result("cmd-1", w["result"])
        for _ in range(w["seq"]):
            store.next_seq()
        store.save()
    s = store.state
    return {"node_id": s.node_id, "credential": s.credential, "tx_seq": s.tx_seq,
            "commands_signed": s.commands_signed, "profile": s.profile,
            "maintenance_mode": s.maintenance_mode, "draining": s.draining,
            "workload": store.workload("rt-1"), "result": store.get_command_result("cmd-1"),
            "file_keys": sorted(json.loads(path.read_text()))}


def main() -> None:
    sys.path.insert(0, sys.argv[1])
    logging.disable(logging.CRITICAL)
    scenario = json.loads(sys.stdin.read())
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        op = scenario["op"]
        out = state(scenario, work) if op == "state" else asyncio.run({"dispatch": dispatch, "receive": receive}[op](scenario, work))
    sys.stdout.write("\n" + json.dumps(out, default=str) + "\n")


if __name__ == "__main__":
    main()
