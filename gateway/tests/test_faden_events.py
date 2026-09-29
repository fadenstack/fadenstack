"""What the gateway did on its own, told to a client that asks (``X-Faden-Events``).

A UI can show "searching the knowledge base"; an agent's SDK, which keeps the
conversation on the device, keeps the gateway's rounds and sends them back.
Clients that do not ask get the plain OpenAI stream, untouched.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import openai
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway.pii_client import PIIClient, SanitizeResult
from faden_gateway.services.registry import service_registry
from tests.test_gateway_knowledge_tools import (
    CAN_CALL_TOOLS,
    WEATHER,
    _ask,
    _call,
    _Knowledge,
    _Model,
    _say,
)
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _pii, _seed, _token

EVENTS = {"X-Faden-Events": "ag-ui"}


def _faden(raw: str) -> list[dict[str, Any]]:
    """The named ``faden`` events of an SSE body, in order."""
    out = []
    for block in raw.split("\n\n"):
        if block.startswith("event: faden\n"):
            out.append(json.loads(block.split("data: ", 1)[1]))
    return out


def _text(raw: str) -> str:
    text = []
    for block in raw.split("\n\n"):
        if block.startswith("data: ") and block != "data: [DONE]":
            for choice in json.loads(block[6:]).get("choices", []):
                text.append((choice.get("delta") or {}).get("content") or "")
    return "".join(text)


async def _setup(fastapi_app: FastAPI, db_session: AsyncSession, pii: dict[str, Any] | None = None) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=pii, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()


@pytest.mark.anyio
async def test_a_streamed_answer_tells_what_the_gateway_ran(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    _Knowledge(monkeypatch)
    _Model(monkeypatch, _call("knowledge_search", {"query": "launch date"}), _say("On 5 May [handbook.pdf]."))

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}", **EVENTS}, json={
        "model": ALIAS, "stream": True, "messages": [{"role": "user", "content": "When is the launch?"}],
    })
    events = _faden(r.text)
    assert [e["type"] for e in events] == ["TOOL_CALL_START", "TOOL_CALL_END", "CUSTOM", "CUSTOM"]
    start, end, cited, round_ = events
    assert (start["toolCallName"], start["kind"], start["toolCallId"]) == ("knowledge_search", "knowledge", "call_1")
    assert end["toolCallId"] == "call_1" and end["error"] is False and end["durationMs"] >= 0
    assert cited["name"] == "faden.citations"
    assert cited["value"]["sources"] == [{"source": "handbook.pdf", "document_id": "d1", "chunk": 3, "score": 0.91}]
    assert "Alice leads" not in json.dumps(cited), "sources, not their text"
    assert round_["name"] == "faden.gateway_round"
    turn, answer = round_["value"]["messages"]
    assert turn["role"] == "assistant" and turn["tool_calls"][0]["function"]["name"] == "knowledge_search"
    assert answer["role"] == "tool" and answer["tool_call_id"] == "call_1" and "handbook.pdf" in answer["content"]

    assert _text(r.text) == "On 5 May [handbook.pdf]."
    assert r.text.index("event: faden") < r.text.index("On 5 "), "told before the answer that follows"
    assert r.text.rstrip().endswith("data: [DONE]")


@pytest.mark.anyio
async def test_a_whole_answer_carries_them_beside_the_openai_fields(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    _Knowledge(monkeypatch)
    _Model(monkeypatch, _call("knowledge_search", {"query": "launch date"}), _say("On 5 May."))

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}", **EVENTS}, json={
        "model": ALIAS, "messages": [{"role": "user", "content": "When is the launch?"}],
    })
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "On 5 May."
    types = [(e["type"], e.get("name")) for e in body["faden"]["events"]]
    assert types == [
        ("TOOL_CALL_START", None), ("TOOL_CALL_END", None),
        ("CUSTOM", "faden.citations"), ("CUSTOM", "faden.gateway_round"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_without_asking_nothing_changes_and_the_openai_client_reads_it(
    stream: bool, fastapi_app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    _Knowledge(monkeypatch)
    _Model(monkeypatch, _call("knowledge_search", {"query": "launch date"}), _say("On 5 May."))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fastapi_app), base_url="http://test")
    client = openai.AsyncOpenAI(base_url="http://test/v1", api_key=_token(), http_client=http)
    messages = [{"role": "user", "content": "When is the launch?"}]
    if stream:
        text = []
        async for chunk in await client.chat.completions.create(model=ALIAS, messages=messages, stream=True):
            text.extend(c.delta.content or "" for c in chunk.choices)
        assert "".join(text) == "On 5 May."
    else:
        whole = await client.chat.completions.with_raw_response.create(model=ALIAS, messages=messages)
        assert "faden" not in json.loads(whole.http_response.text)
        assert whole.parse().choices[0].message.content == "On 5 May."
    await http.aclose()


@pytest.mark.anyio
async def test_the_pii_notice_names_types_and_the_round_comes_back_with_the_values(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session, pii=_pii("tokenize_reversible"))
    service_registry.configure("pii", enabled=True, url="http://pii.local")

    async def sanitize(self: PIIClient, *, payload: dict[str, Any], policy: Any, mode: str | None = None,
                       token_mapping: dict[str, str] | None = None) -> SanitizeResult:
        text = json.dumps(payload)
        found = "Alice" in text
        return SanitizeResult(
            sanitized_payload=json.loads(text.replace("Alice", "[PERSON_1]")),
            pii_detected=found,
            token_mapping={"[PERSON_1]": "Alice"},
            entity_types={"PERSON": 1} if found else {},
        )

    monkeypatch.setattr(PIIClient, "sanitize", sanitize)
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "[PERSON_1] launch"}), _say("[PERSON_1] leads it."))
    try:
        r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}", **EVENTS},
                              json={"model": ALIAS, "stream": True,
                                    "messages": [{"role": "user", "content": "What does Alice lead?"}]})
    finally:
        service_registry.configure("pii", enabled=False, url=None)
    events = _faden(r.text)
    assert events[0] == {"type": "CUSTOM", "name": "faden.pii",
                         "value": {"mode": "tokenize_reversible", "entities": {"PERSON": 1}}}
    round_ = next(e for e in events if e.get("name") == "faden.gateway_round")["value"]["messages"]
    assert json.loads(round_[0]["tool_calls"][0]["function"]["arguments"]) == {"query": "Alice launch"}
    assert "Alice leads the launch" in round_[1]["content"], "the client keeps the values, not the tokens"
    assert "[PERSON_1]" not in json.dumps(round_)
    assert "Alice" not in json.dumps(model.sent[1]), "the model still saw only tokens"
    assert knowledge.searches[0]["query"] == "Alice launch", "the search ran with the value"
    assert _text(r.text) == "Alice leads it."


@pytest.mark.anyio
async def test_a_gateway_round_sent_back_reaches_the_model_on_the_next_request(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A search, then a call to the client's tool, answered without a session (02 §6)."""
    await _setup(fastapi_app, db_session)
    _Knowledge(monkeypatch)
    model = _Model(
        monkeypatch,
        _call("knowledge_search", {"query": "site"}),
        _call("get_weather", {"city": "Munich"}, call_id="call_2"),
        _say("Sunny at the Munich site."),
    )
    question = {"role": "user", "content": "What is the weather at the launch site?"}
    first = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}", **EVENTS},
                              json={"model": ALIAS, "stream": True, "tools": [WEATHER], "messages": [question]})
    round_ = next(e for e in _faden(first.text) if e.get("name") == "faden.gateway_round")["value"]["messages"]
    client_call = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_2", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Munich"}'}},
    ]}
    history = [question, *round_, client_call, {"role": "tool", "tool_call_id": "call_2", "content": "Sunny, 24 C"}]

    second = await _ask(client, tools=[WEATHER], messages=history)
    assert second.json()["choices"][0]["message"]["content"] == "Sunny at the Munich site."
    seen = model.sent[2]["messages"]
    roles = [(m["role"], m.get("tool_call_id")) for m in seen if m["role"] in ("assistant", "tool")]
    assert roles == [("assistant", None), ("tool", "call_1"), ("assistant", None), ("tool", "call_2")]
    assert any("handbook.pdf" in str(m.get("content")) for m in seen), "the search round is in the model's history"
