"""Agent 0.3.0 is a standard upgrade: what 0.2.12 and a 0.7.2 backend must keep doing.

The released agent (tag ``node-agent-v0.2.12``) is extracted with ``git archive`` and run
in a child process by ``compat/runner.py``, beside the current tree, so these tests parse
frames with the code operators really run, not with a copy that could drift. Without the
tag (a shallow checkout) they skip; set ``FADEN_REQUIRE_0_2_12=1`` to fail instead.

The frames are frozen: ``NEW_*`` is what the current backend sends (CONTRACTS C1),
``V072_*`` is what backend v0.7.2 sent (``admin/system/views.py`` ``hello_ack``).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest
import websockets

from faden_agent.models import NodeCommandType
from faden_agent.stream_client import HelloAckTimeout

TAG = "node-agent-v0.2.12"
REPO = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).parent / "compat" / "runner.py"
NATIVE = {
    "stage_native_runtime", "native_runtime_status", "start_native_worker",
    "native_worker_status", "stop_native_worker", "native_worker_logs",
}

NEW_NATIVE_COMMANDS = [
    {"id": "n-stage", "command_type": "stage_native_runtime", "payload": {
        "bundle_id": "vllm-metal-0.34.0-darwin-arm64", "oci_image": "ghcr.io/x/rt", "tag": "1",
        "config_digest": "sha256:a", "rootfs_digest": "sha256:b", "layer_digest": "sha256:c",
        "size_bytes": 1, "pyvenv_home_rewrite": True, "interpreter_path": "bin/python"}},
    {"id": "n-status", "command_type": "native_runtime_status", "payload": {}},
    {"id": "n-start", "command_type": "start_native_worker", "payload": {"deployment_id": "d", "generation": 3}},
    {"id": "n-wstatus", "command_type": "native_worker_status", "payload": {"deployment_id": "d"}},
    {"id": "n-stop", "command_type": "stop_native_worker", "payload": {"deployment_id": "d"}},
    {"id": "n-logs", "command_type": "native_worker_logs", "payload": {"deployment_id": "d", "tail": 50}},
    {"id": "n-future", "command_type": "some_future_command", "payload": {"x": 1}},
]
#: The standalone route, with ownership (B1/C1), and the Ray/adopted one exactly as 0.7.2 sent it.
NEW_STANDALONE_ROUTES = [
    {"id": "r-new-ensure", "command_type": "model_tls_route", "payload": {
        "action": "ensure", "public_port": 8000, "internal_port": 28000, "owner": "dep-1", "generation": 4}},
    {"id": "r-new-remove", "command_type": "model_tls_route", "payload": {
        "action": "remove", "public_port": 8000, "owner": "dep-1", "generation": 4}},
]
V072_ROUTES = [
    {"id": "r-old-ensure", "command_type": "model_tls_route",
     "payload": {"action": "ensure", "public_port": 8000, "internal_port": 28000}},
    {"id": "r-old-remove", "command_type": "model_tls_route", "payload": {"action": "remove", "public_port": 8000}},
]
V072_HELLO_ACK = {
    "type": "hello_ack", "session_id": "s-1", "node_id": "node-1", "profile": {"name": "n"},
    "signs_commands": True,
    "commands": [{"id": "c-1", "command_type": "refresh_inventory", "payload": {}, "signature": "sig"},
                 {"id": "c-2", "command_type": "model_tls_route", "payload": V072_ROUTES[0]["payload"],
                  "redispatch": True}],
}
NEW_HELLO_ACK = V072_HELLO_ACK | {"capabilities_hint": {"future": 1}, "server_version": "0.7.3"}
COMMANDS_FRAME = {"type": "commands", "items": [{"id": "c-3", "command_type": "get_runtime_status", "payload": {}}]}
SINGLE_FRAME = {"type": "command", "command": {"id": "c-4", "command_type": "get_runtime_status", "payload": {}}}


@pytest.fixture(scope="session")
def old_agent(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The agent directory of the released 0.2.12 (``agent/`` of the tag)."""
    proc = subprocess.run(["git", "-C", str(REPO), "archive", TAG, "agent/faden_agent"], capture_output=True, check=False)
    if proc.returncode != 0:
        if os.environ.get("FADEN_REQUIRE_0_2_12"):
            pytest.fail(f"tag {TAG} is not available: {proc.stderr.decode()[:200]}")
        pytest.skip(f"tag {TAG} is not available (shallow clone)")
    root = tmp_path_factory.mktemp("agent_0_2_12")
    tmp_tar = root / "a.tar"
    tmp_tar.write_bytes(proc.stdout)
    with tarfile.open(tmp_tar) as tar:
        tar.extractall(root, filter="data")  # noqa: S202 - our own git archive
    return root / "agent"


def run_tree(tree: Path, scenario: dict[str, Any]) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(RUNNER), str(tree)], input=json.dumps(scenario), capture_output=True, text=True,
        check=False, timeout=120, cwd=str(tree.parent),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


CURRENT = REPO / "agent"


# ---- new backend -> 0.2.12 -------------------------------------------------------------

def test_0_2_12_answers_every_native_command_with_a_failed_result_not_a_crash(old_agent: Path) -> None:
    out = run_tree(old_agent, {"op": "dispatch", "commands": NEW_NATIVE_COMMANDS})
    assert set(out["results"]) == {c["id"] for c in NEW_NATIVE_COMMANDS}
    for result in out["results"].values():
        assert result["success"] is False
        assert result["error_code"] == "runtime_error"
        assert "Unsupported command type" in result["error_message"]


def test_0_2_12_ignores_owner_and_generation_on_a_route(old_agent: Path) -> None:
    out = run_tree(old_agent, {"op": "dispatch", "commands": NEW_STANDALONE_ROUTES})
    assert all(r["success"] for r in out["results"].values()), out
    assert out["tls_calls"] == [["ensure", 8000, 28000], ["remove", 8000]]


@pytest.mark.parametrize("frame", [NEW_HELLO_ACK, COMMANDS_FRAME, SINGLE_FRAME], ids=["hello_ack", "commands", "command"])
def test_0_2_12_reads_the_frames_the_new_backend_sends(old_agent: Path, frame: dict[str, Any]) -> None:
    out = run_tree(old_agent, {"op": "receive", "frames": [frame, {"type": "a_future_frame", "x": 1}, "not json"]})
    expected = {"hello_ack": ["c-1", "c-2"], "commands": ["c-3"], "command": ["c-4"]}[frame["type"]]
    assert out["started"] == expected


# ---- 0.7.2 backend -> new agent --------------------------------------------------------

@pytest.mark.parametrize("frame", [V072_HELLO_ACK, COMMANDS_FRAME, SINGLE_FRAME], ids=["hello_ack", "commands", "command"])
def test_the_new_agent_accepts_the_frames_of_backend_0_7_2(frame: dict[str, Any]) -> None:
    out = run_tree(CURRENT, {"op": "receive", "frames": [frame, {"type": "a_future_frame"}, "not json"]})
    expected = {"hello_ack": ["c-1", "c-2"], "commands": ["c-3"], "command": ["c-4"]}[frame["type"]]
    assert out["started"] == expected
    if frame["type"] == "hello_ack":
        assert out["node_id"] == "node-1" and out["commands_signed"] is True and out["profile"] == {"name": "n"}


def test_the_new_agent_and_0_2_12_do_the_same_with_the_routes_0_7_2_sent(old_agent: Path) -> None:
    scenario = {"op": "dispatch", "commands": V072_ROUTES}
    old, new = run_tree(old_agent, scenario), run_tree(CURRENT, scenario)
    assert new["tls_calls"] == old["tls_calls"] == [["ensure", 8000, 28000], ["remove", 8000]]
    assert new["results"] == old["results"]


def test_the_new_agent_keeps_every_command_type_of_0_2_12(old_agent: Path) -> None:
    old_types = set(_enum_values(old_agent / "faden_agent" / "models.py"))
    new_types = {c.value for c in NodeCommandType}
    assert old_types <= new_types, f"removed: {sorted(old_types - new_types)}"
    # remove_model (track M): capability-gated (`model_remove`, Mac agents only; COMPAT M-1).
    assert new_types - old_types == NATIVE | {"remove_model"}, "a new command type needs a compat decision (COMPAT DESIGN row 1)"


def _enum_values(path: Path) -> list[str]:
    import ast

    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "NodeCommandType":
            return [s.value.value for s in node.body if isinstance(s, ast.Assign) and isinstance(s.value, ast.Constant)]
    raise AssertionError("NodeCommandType not found")


# ---- state: 0.2.12 -> 0.3.0 -> 0.2.12 ----------------------------------------------------

STATE = {
    "node_id": "11111111-2222-3333-4444-555555555555", "credential": "cred.secret", "commands_signed": True,
    "profile": {"name": "gpu-1"}, "maintenance_mode": True, "draining": False, "seq": 7,
    "workload": {"container_name": "faden-rt-1", "image": "x"}, "result": {"success": True, "result": {"ok": 1}},
}


def test_a_state_file_round_trips_0_2_12_to_0_3_0_and_back(old_agent: Path, tmp_path: Path) -> None:
    path = str(tmp_path / "state.json")
    written = run_tree(old_agent, {"op": "state", "path": path, "write": STATE})
    on_disk_old = json.loads(Path(path).read_text())

    # 0.3.0 reads what 0.2.12 wrote (same identity, same credential), then saves it again.
    read_new = run_tree(CURRENT, {"op": "state", "path": path})
    assert read_new == written
    again = run_tree(CURRENT, {"op": "state", "path": path, "write": STATE | {"seq": 0}})
    assert again["node_id"] == STATE["node_id"] and again["credential"] == STATE["credential"]
    assert set(json.loads(Path(path).read_text())) == set(on_disk_old), "0.3.0 adds no key 0.2.12 does not write"

    # Rollback: 0.2.12 reads the file 0.3.0 saved; nothing is lost.
    back = run_tree(old_agent, {"op": "state", "path": path})
    assert back["node_id"] == STATE["node_id"] and back["credential"] == STATE["credential"]
    assert back["tx_seq"] == written["tx_seq"] == STATE["seq"]
    assert back["commands_signed"] is True and back["maintenance_mode"] is True
    assert back["workload"] == STATE["workload"] and back["result"] == STATE["result"]


def test_0_3_0_state_with_an_unknown_future_key_is_still_read_by_0_2_12(old_agent: Path, tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    run_tree(old_agent, {"op": "state", "path": str(path), "write": STATE})
    data = json.loads(path.read_text()) | {"native": {"workers": {}}}
    path.write_text(json.dumps(data))
    assert run_tree(old_agent, {"op": "state", "path": str(path)})["node_id"] == STATE["node_id"]
    assert run_tree(CURRENT, {"op": "state", "path": str(path)})["credential"] == STATE["credential"]


# ---- 0.3.0 against an old backend: the hello_ack timeout ------------------------------------

class _OldBackend:
    """Backend v0.7.2's stream: ``hello_ack`` after the replay and settle database work, never earlier."""

    def __init__(self, *, ack_delay: float | None) -> None:
        self.ack_delay = ack_delay
        self.connections = 0

    async def handler(self, connection: Any) -> None:
        self.connections += 1
        if self.ack_delay is not None:
            await asyncio.sleep(self.ack_delay)
            await connection.send(json.dumps(V072_HELLO_ACK | {"commands": []}))
        async for _ in connection:
            pass


@pytest.mark.asyncio()
async def test_a_slow_but_within_timeout_hello_ack_from_0_7_2_keeps_the_stream(tmp_path: Path) -> None:
    from test_stream_lifecycle import _client

    server = _OldBackend(ack_delay=0.3)
    async with websockets.serve(server.handler, "127.0.0.1", 0) as listening:
        client = _client(tmp_path, listening.sockets[0].getsockname()[1], hello_ack_timeout=1.0)
        running = asyncio.create_task(client.run(credential="c.s"))
        await asyncio.sleep(1.5)
        assert not running.done() and server.connections == 1
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running


@pytest.mark.asyncio()
async def test_a_backend_that_never_acknowledges_costs_one_reconnect_with_backoff_not_a_tight_loop(tmp_path: Path) -> None:
    """The service sleeps the backoff after ``HelloAckTimeout``; here the same loop shape, counted."""
    from test_stream_lifecycle import _client

    server = _OldBackend(ack_delay=None)
    async with websockets.serve(server.handler, "127.0.0.1", 0) as listening:
        client = _client(tmp_path, listening.sockets[0].getsockname()[1], hello_ack_timeout=0.2)
        started = time.monotonic()
        with pytest.raises(HelloAckTimeout):
            await asyncio.wait_for(client.run(credential="c.s"), timeout=5)
        assert time.monotonic() - started >= 0.2
    import inspect

    from faden_agent import service

    src = inspect.getsource(service.NodeAgentService)
    after_timeout = src.split("except HelloAckTimeout:", 1)[1]
    assert "await asyncio.sleep(backoff)" in after_timeout, "a missed ack must fall through to the backoff sleep"
    assert "continue" not in after_timeout.split("await asyncio.sleep(backoff)", 1)[0].split("except RuntimeError", 1)[0]


def test_the_default_hello_ack_timeout_is_well_beyond_what_0_7_2_needs() -> None:
    from faden_agent.config import AgentConfig

    assert AgentConfig.__dataclass_fields__["hello_ack_timeout_sec"].default >= 30.0
