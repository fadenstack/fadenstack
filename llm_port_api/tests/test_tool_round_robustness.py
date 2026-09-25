"""A malformed tool call, and a refused stream, do not break a chat.

Under load on the dev stack (2026-09-26) the 0.5B model sometimes wrote a
tool call's arguments malformed (``{"a": 17, "b": }``). The gateway ran the
tool with ``{}`` and sent the model's text back as it was; vLLM parses it for
the chat template and refused the next round with a 400, and the chat got an
error event for a reply -- logged as a 200.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import LLMGatewayRequestLog, ProviderType
from llm_port_api.services.gateway.context_assembler import _replayed_exchange
from llm_port_api.services.gateway.service import _assistant_turn
from tests.test_gateway_knowledge_tools import _ask, _Model, _say
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _session, _token
from tests.test_llm_adapter_direct import _engine
from tests.test_session_tool_switches import _MCP, _ready

MALFORMED = '{"a": 17, "b": }'


def _malformed_call() -> tuple[str, Any]:
    return ("call", {"id": "call_1", "type": "function",
                     "function": {"name": "mcp.everything.get-sum", "arguments": MALFORMED}})


def test_the_calls_go_back_with_the_arguments_they_ran_with() -> None:
    """Malformed: as {} (what the tool got). Well formed: unchanged, as JSON."""
    turn = _assistant_turn(None, [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": MALFORMED}},
        {"id": "c2", "type": "function", "function": {"name": "g", "arguments": '{"a": 1}'}},
    ])
    assert [json.loads(c["function"]["arguments"]) for c in turn["tool_calls"]] == [{}, {"a": 1}]
    assert [c["id"] for c in turn["tool_calls"]] == ["c1", "c2"]


def test_a_malformed_call_kept_before_the_fix_is_replayed_parseable() -> None:
    """One such call in the history would refuse every later turn of the chat."""
    saved = SimpleNamespace(role="assistant", tool_call_json={"exchange": [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "f", "arguments": MALFORMED}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "error"},
    ]})
    replayed = _replayed_exchange(saved)
    assert replayed[0]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert replayed[1] == {"role": "tool", "tool_call_id": "c1", "content": "error"}


@pytest.mark.anyio
async def test_the_round_after_a_malformed_call_is_sent_parseable(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The next round carries JSON vLLM can parse."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, "mcp.everything.get-sum")
    model = _Model(monkeypatch, _malformed_call(), _say("I could not add them."))
    sid = await _session(db_session)

    assert (await _ask(client, session_id=str(sid))).status_code == 200
    call = next(m for m in model.sent[1]["messages"] if m.get("tool_calls"))
    assert json.loads(call["tool_calls"][0]["function"]["arguments"]) == {}


@pytest.mark.anyio
async def test_a_refused_stream_is_logged_as_a_failure(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    """The client gets the error event; the request log says 502, not 200."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sid = await _session(db_session)
    seen: list[httpx.Request] = []
    engine = _engine([b'{"error": {"message": "Expecting value: line 1 column 17"}}'], seen, status=400)
    fastapi_app.state.http_client = engine

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": True, "session_id": str(sid),
        "messages": [{"role": "user", "content": "hi"}],
    })
    await engine.aclose()

    assert r.status_code == 200  # the stream had begun: the error arrives in it
    assert '"error"' in r.text
    row = (await db_session.execute(
        select(LLMGatewayRequestLog).where(LLMGatewayRequestLog.session_id == str(sid)),
    )).scalar_one()
    assert (row.status_code, row.error_code) == (502, "upstream_refused")
