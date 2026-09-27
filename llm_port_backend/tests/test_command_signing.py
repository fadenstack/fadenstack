"""The backend signs the commands it sends a machine whose agent checks them.

Before, it sent none, and the agent checked a signature only when there was
one: on plain HTTP anyone on the path could send a machine a command. The key
is derived from the credential the agent opened the stream with, as the agent
derives it -- nothing is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest

from llm_port_backend.services.nodes import signing
from llm_port_backend.web.api.admin.system import views

#: The same vector is checked by the agent's tests (llm_port_node_agent/tests/test_command_signing.py).
CREDENTIAL = "0b8f6c1e-7d2a-4c11-9e3f-5a6b7c8d9e0f.s3cr3t-Example_Secret"
COMMAND = {
    "id": "4f1f2c3d-0000-4000-8000-000000000001",
    "node_id": "n-1",
    "command_type": "deploy_workload",
    "payload": {"image": "vllm/vllm-openai:latest", "gpus": [0, 1], "env": {"Ü": "ß"}},
    "issued_at": "2026-09-27T12:00:00+00:00",
    "redispatch": True,
    "timeout_sec": 900,
}
KEY_HEX = "9aa7248ff072c8efeace2efe39e3142140838c3bde0fc34213cb234e48e58126"
SIGNATURE = "d1d3a621e7a8682d425cd88f4367faf7829b741e4fd7904f760bb11a23dd5e5a"


def _agent_accepts(wire: str, key: bytes) -> bool:
    """What the agent does with a command it received: parse, recompute, compare."""
    command = json.loads(wire)
    body = {k: v for k, v in sorted(command.items()) if k != "signature"}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.compare_digest(command.get("signature", ""), hmac.new(key, canon, hashlib.sha256).hexdigest())


def test_the_backend_and_the_agent_agree_on_the_signature() -> None:
    key = signing.derive_signing_key(CREDENTIAL)
    assert key.hex() == KEY_HEX
    assert signing.sign(COMMAND, key)["signature"] == SIGNATURE


def test_a_signed_command_survives_the_wire_and_nothing_can_be_added() -> None:
    key = signing.derive_signing_key(CREDENTIAL)
    signed = signing.sign(COMMAND, key)
    assert _agent_accepts(json.dumps(signed), key)
    assert not _agent_accepts(json.dumps({**signed, "payload": {"image": "attacker/miner"}}), key)
    assert not _agent_accepts(json.dumps({**signed, "redispatch": False}), key)
    assert not _agent_accepts(json.dumps(signed), signing.derive_signing_key("another.credential"))
    # Signing again replaces the signature rather than covering the old one.
    assert signing.sign(signed, key)["signature"] == SIGNATURE


class _Headers(dict[str, str]):
    def get(self, key: str, default: Any = None) -> Any:  # case-insensitive, like Starlette's
        return next((v for k, v in self.items() if k.lower() == key.lower()), default)


def test_only_an_agent_that_checks_gets_signatures() -> None:
    bearer = f"Bearer {CREDENTIAL}"
    assert signing.stream_key(_Headers({"X-Command-Signing": "hmac-sha256"}), bearer) == bytes.fromhex(KEY_HEX)
    assert signing.stream_key(_Headers({}), bearer) is None, "agents before 0.1.15 change keys mid-stream"
    assert signing.stream_key(_Headers({"X-Command-Signing": "something-else"}), bearer) is None
    assert signing.stream_key(_Headers({"X-Command-Signing": "hmac-sha256"}), None) is None
    assert signing.sign_all([COMMAND], None) == [COMMAND]


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(json.loads(json.dumps(payload)))


class _Service:
    async def list_commands_for_dispatch(self, *, node_id: Any) -> list[dict[str, Any]]:
        return [dict(COMMAND)]


class _Session:
    async def commit(self) -> None:
        return None


@pytest.mark.anyio()
async def test_every_command_on_a_signing_stream_goes_out_signed() -> None:
    key = signing.derive_signing_key(CREDENTIAL)
    ws = _Socket()
    node = type("Node", (), {"id": "n-1"})()
    await views._drain_commands(ws, _Service(), node, _Session(), key)  # type: ignore[arg-type]
    await views._drain_commands(ws, _Service(), node, _Session())  # type: ignore[arg-type]
    signed, unsigned = ws.sent
    assert signed["items"][0]["signature"] == SIGNATURE
    assert "signature" not in unsigned["items"][0], "an agent that does not check gets what it always got"
