"""Commands the agent runs are signed by its backend, once the backend has said it signs.

Before 0.1.15 the agent checked a signature only when a command carried one,
and the backend sent none: on plain HTTP anyone on the path could send a
machine a command. The agent now announces it checks (X-Command-Signing), the
backend signs every command on that stream and says so in hello_ack, and from
then on the agent refuses a command without a valid signature.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from faden_agent.command_verifier import _canonical_payload, derive_signing_key
from faden_agent.state_store import StateStore
from faden_agent.stream_client import StreamClient

#: The same vector is checked by the backend's tests (test_command_signing.py there):
#: both sides have to arrive at these bytes, or no signed command is ever accepted.
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


def test_the_agent_and_the_backend_agree_on_the_signature() -> None:
    import hashlib
    import hmac

    key = derive_signing_key(CREDENTIAL)
    assert key.hex() == KEY_HEX
    assert hmac.new(key, _canonical_payload(COMMAND), hashlib.sha256).hexdigest() == SIGNATURE


class _Socket:
    def __init__(self, incoming: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self._incoming = list(incoming or [])

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        from websockets.exceptions import ConnectionClosedOK

        if not self._incoming:
            raise ConnectionClosedOK(None, None)
        return json.dumps(self._incoming.pop(0))


class _Dispatcher:
    def __init__(self) -> None:
        self.ran: list[str] = []

    async def handle(self, command: dict[str, Any], _emit: Any) -> dict[str, Any]:
        self.ran.append(command["id"])
        return {"success": True}


def _client(tmp_path: Path, *, mode: str = "auto") -> tuple[StreamClient, _Dispatcher]:
    client = StreamClient.__new__(StreamClient)
    client._config = SimpleNamespace(require_signed_commands=mode)  # type: ignore[attr-defined]
    client._state = StateStore(tmp_path / "state.json")  # type: ignore[attr-defined]
    client._signing_key = derive_signing_key(CREDENTIAL)  # type: ignore[attr-defined]
    client._send_lock = asyncio.Lock()  # type: ignore[attr-defined]
    dispatcher = _Dispatcher()
    client._dispatcher = dispatcher  # type: ignore[attr-defined]
    return client, dispatcher


def _fresh(**extra: Any) -> dict[str, Any]:
    from datetime import UTC, datetime

    return {**COMMAND, "issued_at": datetime.now(UTC).isoformat(), **extra}


def _signed(command: dict[str, Any]) -> dict[str, Any]:
    import hashlib
    import hmac

    key = derive_signing_key(CREDENTIAL)
    return {**command, "signature": hmac.new(key, _canonical_payload(command), hashlib.sha256).hexdigest()}


def _results(ws: _Socket) -> list[tuple[str, Any]]:
    return [(m["type"], m.get("error_code")) for m in ws.sent]


@pytest.mark.asyncio
async def test_before_the_backend_signs_an_unsigned_command_still_runs(tmp_path: Path) -> None:
    """An older backend sends none; the agent must keep working with it."""
    client, dispatcher = _client(tmp_path)
    ws = _Socket()
    await client._handle_command(ws, _fresh())  # type: ignore[arg-type]
    assert dispatcher.ran == [COMMAND["id"]]


@pytest.mark.asyncio
async def test_once_the_backend_said_it_signs_an_unsigned_command_is_refused(tmp_path: Path) -> None:
    client, dispatcher = _client(tmp_path)
    await client._receive_loop(_Socket([{"type": "hello_ack", "signs_commands": True, "commands": []}]))  # type: ignore[arg-type]
    assert client._state.state.commands_signed
    assert StateStore(tmp_path / "state.json").state.commands_signed, "kept for every later connection"

    ws = _Socket()
    await client._handle_command(ws, _fresh())  # type: ignore[arg-type]
    assert dispatcher.ran == []
    assert _results(ws) == [("command_result", "signature_missing")]

    await client._handle_command(ws, _signed(_fresh(id="c-2")))  # type: ignore[arg-type]
    assert dispatcher.ran == ["c-2"]


@pytest.mark.asyncio
async def test_a_tampered_or_foreign_signature_is_refused(tmp_path: Path) -> None:
    client, dispatcher = _client(tmp_path)
    ws = _Socket()
    tampered = _signed(_fresh())
    tampered["payload"] = {"image": "attacker/miner:latest"}
    await client._handle_command(ws, tampered)  # type: ignore[arg-type]
    other = _signed(_fresh(id="c-3"))
    client._signing_key = derive_signing_key("another.credential")  # type: ignore[attr-defined]
    await client._handle_command(ws, other)  # type: ignore[arg-type]
    assert dispatcher.ran == []
    assert _results(ws) == [("command_result", "signature_invalid"), ("command_result", "signature_invalid")]


@pytest.mark.asyncio
async def test_a_hello_that_no_longer_says_so_does_not_undo_it(tmp_path: Path) -> None:
    """Stripping the flag off a later hello_ack is the downgrade this refuses."""
    client, dispatcher = _client(tmp_path)
    await client._receive_loop(_Socket([{"type": "hello_ack", "signs_commands": True, "commands": []}]))  # type: ignore[arg-type]
    unsigned = _fresh(id="c-4")
    await client._receive_loop(_Socket([{"type": "hello_ack", "signs_commands": False, "commands": [unsigned]}]))  # type: ignore[arg-type]
    assert dispatcher.ran == []
    assert client._state.state.commands_signed


@pytest.mark.asyncio
async def test_the_operator_can_require_it_or_switch_it_off(tmp_path: Path) -> None:
    strict, strict_ran = _client(tmp_path / "a", mode="true")
    await strict._handle_command(_Socket(), _fresh())  # type: ignore[arg-type]
    assert strict_ran.ran == []

    relaxed, relaxed_ran = _client(tmp_path / "b", mode="false")
    relaxed._state.state.commands_signed = True  # type: ignore[attr-defined]
    await relaxed._handle_command(_Socket(), _fresh())  # type: ignore[arg-type]
    assert relaxed_ran.ran == [COMMAND["id"]], "a backend rolled back to one that does not sign"


@pytest.mark.asyncio
async def test_the_stream_is_opened_announcing_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from faden_agent import stream_client as module

    seen: dict[str, Any] = {}

    class _Refused(Exception):
        pass

    def connect(url: str, **kwargs: Any) -> Any:
        seen.update(kwargs.get("additional_headers") or {})
        raise _Refused

    monkeypatch.setattr(module.websockets, "connect", connect)
    client, _ = _client(tmp_path)
    client._config = SimpleNamespace(  # type: ignore[attr-defined]
        require_signed_commands="auto", backend_url="http://10.0.0.1", request_timeout_sec=5,
        verify_tls=True, ssl_context=None, tls_ca_bundle=None, tls_client_cert=None, tls_client_key=None,
    )
    with pytest.raises(_Refused):
        await client.run(credential=CREDENTIAL)
    assert seen["X-Command-Signing"] == "hmac-sha256"
    assert seen["Authorization"] == f"Bearer {CREDENTIAL}"
