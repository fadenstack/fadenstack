"""A long chat's left-out messages stay readable: the model searches them (0.7.2 K, layer 4).

The oldest messages of a long chat no longer go to the model (test_context_budget), and the model used to answer as
if they had never been said. They are kept with the chat: a model that calls tools gets ``search_chat`` over the ones
left out, and the line in their place says so; one that cannot is told to ask the person instead.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ChatMessage, ProviderType
from faden_gateway.services.gateway.chat_search import ChatSearch, EarlierMessage
from faden_gateway.services.gateway.context_assembler import DROPPED, DROPPED_PREFIX
from faden_gateway.settings import settings
from tests.test_gateway_knowledge_tools import _ask, _call, _Model, _say, _tool_names
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _session, _token

EARLY = "The Lisbon office closes on 30 June 2027 and its staff move to Porto."
FILLER = "We went through the quarterly figures region by region and agreed on the next steps. " * 5
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def _earlier(*texts: str) -> list[EarlierMessage]:
    return [
        EarlierMessage(id=str(i), number=i + 1, role="user" if i % 2 == 0 else "assistant",
                       written=(T0 + timedelta(minutes=i)).isoformat(), text=text)
        for i, text in enumerate(texts)
    ]


# ── The search ────────────────────────────────────────────────────────────────────────────────────────


def test_the_message_with_the_rare_words_comes_first_with_its_place_and_writer() -> None:
    found = ChatSearch(_earlier(FILLER, FILLER, EARLY, FILLER)).search({"query": "When does the Lisbon office close?"})
    first = found["results"][0]
    assert EARLY in first["text"]
    assert (first["message"], first["role"]) == (3, "user")
    assert first["written"] == (T0 + timedelta(minutes=2)).isoformat()


def test_no_earlier_messages_find_nothing_and_no_query_is_an_error() -> None:
    assert ChatSearch([]).search({"query": "Lisbon"}) == {"query": "Lisbon", "results": []}
    content, is_error = ChatSearch(_earlier(EARLY)).run({"query": " "})
    assert is_error and "needs a query" in json.loads(content)["error"]


def test_at_most_eight_passages() -> None:
    found = ChatSearch(_earlier(*[f"Lisbon note {i}" for i in range(20)])).search({"query": "Lisbon", "top_k": 50})
    assert len(found["results"]) == 8


# ── Through the gateway ───────────────────────────────────────────────────────────────────────────────


async def _long_chat(db: AsyncSession, *, early_at: int = 2, count: int = 60) -> uuid.UUID:
    sid = await _session(db)
    for i in range(count):
        db.add(ChatMessage(
            session_id=sid, role="user" if i % 2 == 0 else "assistant",
            content=EARLY if i == early_at else f"{i:03d} {FILLER}", status="completed",
            created_at=T0 + timedelta(minutes=i),
        ))
    await db.commit()
    return sid


def _texts(payload: dict[str, Any]) -> list[str]:
    return [str(m.get("content")) for m in payload["messages"]]


@pytest.mark.anyio
async def test_a_model_that_calls_tools_searches_the_left_out_messages(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None,
                node_metadata={"task": "chat", "tools": True, "context_length": 8_192})
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    sid = await _long_chat(db_session)
    model = _Model(monkeypatch, _call("search_chat", {"query": "Lisbon office close"}), _say("30 June 2027."))

    r = await _ask(client, session_id=str(sid),
                   messages=[{"role": "user", "content": "When does the Lisbon office close?"}])

    assert r.status_code == 200, r.text
    assert r.json()["choices"][0]["message"]["content"] == "30 June 2027."
    first = model.sent[0]
    assert "search_chat" in _tool_names(first)
    lines = [t for t in _texts(first) if t.startswith(DROPPED_PREFIX)]
    assert len(lines) == 1 and "search them with search_chat" in lines[0]
    assert EARLY not in "\n".join(_texts(first)), "the early message itself was left out"
    answer = next(m for m in model.sent[1]["messages"] if m["role"] == "tool")
    found = json.loads(answer["content"])["results"][0]
    assert EARLY in found["text"] and found["message"] == 3 and found["role"] == "user"


@pytest.mark.anyio
async def test_the_search_reads_only_the_left_out_messages(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message still in the context is not searched: the model has it already."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None,
                node_metadata={"task": "chat", "tools": True, "context_length": 8_192})
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    sid = await _long_chat(db_session, early_at=58)  # the newest messages are kept
    model = _Model(monkeypatch, _call("search_chat", {"query": "Lisbon office close"}), _say("ok"))

    await _ask(client, session_id=str(sid), messages=[{"role": "user", "content": "Lisbon?"}])

    assert EARLY in "\n".join(_texts(model.sent[0]))
    answer = next(m for m in model.sent[1]["messages"] if m["role"] == "tool")
    assert all(EARLY not in hit["text"] for hit in json.loads(answer["content"])["results"])


@pytest.mark.anyio
async def test_a_model_that_cannot_call_tools_is_told_to_ask_the_person(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None,
                node_metadata={"task": "chat", "tools": False, "context_length": 8_192})
    fastapi_app.state.gateway_observability = _Observability()
    sid = await _long_chat(db_session)
    model = _Model(monkeypatch, _say("I do not have that."))

    assert (await _ask(client, session_id=str(sid))).status_code == 200
    first = model.sent[0]
    assert "search_chat" not in _tool_names(first)
    [line] = [t for t in _texts(first) if t.startswith(DROPPED_PREFIX)]
    assert line.endswith("ask the person to repeat the detail. Do not guess.]")
    assert line == DROPPED.format(n=int(line.split()[2]))


@pytest.mark.anyio
async def test_a_short_chat_is_not_offered_the_search(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None,
                node_metadata={"task": "chat", "tools": True, "context_length": 32_768})
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "rag_lite_enabled", False)
    sid = await _long_chat(db_session, count=6)
    model = _Model(monkeypatch, _say("ok"))

    await _ask(client, session_id=str(sid))
    assert "search_chat" not in _tool_names(model.sent[0])
    assert not any(t.startswith(DROPPED_PREFIX) for t in _texts(model.sent[0]))


@pytest.mark.anyio
async def test_the_model_list_says_each_models_context_length(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None,
                node_metadata={"task": "chat", "context_length": 32_768})
    r = await client.get("/v1/models", headers={"Authorization": f"Bearer {_token()}"})
    [model] = [m for m in r.json()["data"] if m["id"] == ALIAS]
    assert model["context_length"] == 32_768


@pytest.mark.anyio
async def test_the_smallest_window_of_a_models_instances_counts_and_none_when_none_says(
    db_session: AsyncSession,
) -> None:
    from faden_gateway.db.dao.gateway_dao import GatewayDAO  # noqa: PLC0415
    from faden_gateway.db.models.gateway import (  # noqa: PLC0415
        LLMModelAlias,
        LLMPoolMembership,
        LLMProviderInstance,
        ProviderHealthStatus,
    )

    now = datetime.now(timezone.utc)
    db_session.add_all([LLMModelAlias(alias=a, description="", enabled=True, created_at=now, updated_at=now)
                        for a in ("two", "none")])
    for alias, meta, caps in (("two", {"context_length": 32_768}, None), ("two", None, {"context_length": 16_384}),
                              ("two", {"context_length": "big"}, None), ("none", {"task": "chat"}, None)):
        instance = LLMProviderInstance(
            id=uuid.uuid4(), type=ProviderType.VLLM, base_url=f"http://{uuid.uuid4().hex}.local", enabled=True,
            weight=1.0, max_concurrency=1, health_status=ProviderHealthStatus.HEALTHY, created_at=now,
            updated_at=now, node_metadata=meta, capabilities=caps,
        )
        db_session.add(instance)
        await db_session.flush()
        db_session.add(LLMPoolMembership(model_alias=alias, provider_instance_id=instance.id, enabled=True))
    await db_session.flush()

    assert await GatewayDAO(db_session).alias_context_lengths(["two", "none", "unknown"]) == {
        "two": 16_384, "none": None, "unknown": None,
    }


@pytest.mark.anyio
async def test_the_turn_says_how_much_of_the_context_it_used(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0.7.2 K, layer 7: ``faden.context`` -- the context's tokens, its budget and window, what was left out."""
    from faden_gateway.services.gateway import faden_events  # noqa: PLC0415

    await _seed(db_session, provider=ProviderType.VLLM, pii=None,
                node_metadata={"task": "chat", "tools": False, "context_length": 8_192})
    fastapi_app.state.gateway_observability = _Observability()
    sid = await _long_chat(db_session)
    _Model(monkeypatch, _say("ok"))

    r = await client.post("/v1/chat/completions", headers={
        "Authorization": f"Bearer {_token()}", faden_events.HEADER: "ag-ui",
    }, json={"model": ALIAS, "session_id": str(sid), "messages": [{"role": "user", "content": "And?"}]})

    assert r.status_code == 200, r.text
    [used] = [e["value"] for e in r.json()["faden"]["events"] if e.get("name") == "faden.context"]
    assert used["window"] == 8_192 and used["budget"] == 3_686  # (8,192 - 4,096) x 0.9
    assert used["dropped"] > 0 and 0 < used["tokens"] <= used["budget"]


@pytest.mark.anyio
async def test_the_model_list_says_which_thinking_levels_each_model_tells_apart(db_session: AsyncSession) -> None:
    """0.7.2 I.2: a Qwen3 on vLLM honours only its off switch; a model that says its levels, those; else unknown."""
    from faden_gateway.db.dao.gateway_dao import GatewayDAO  # noqa: PLC0415
    from faden_gateway.db.models.gateway import (  # noqa: PLC0415
        LLMModelAlias,
        LLMPoolMembership,
        LLMProviderInstance,
        ProviderHealthStatus,
    )

    now = datetime.now(timezone.utc)
    rows = [
        ("qwen", ProviderType.VLLM, "Qwen/Qwen3.8-27B-FP8", None),
        ("gpt-oss", ProviderType.VLLM, "openai/gpt-oss-20b", {"reasoning_levels": ["low", "high", "medium", "loud"]}),
        ("mixed", ProviderType.VLLM, "Qwen/Qwen3-8B", None),
        ("mixed", ProviderType.VLLM, "other", {"reasoning_levels": ["off", "low"]}),
        ("unknown", ProviderType.VLLM, "meta-llama/Llama-3.3-70B", None),
    ]
    db_session.add_all([LLMModelAlias(alias=a, description="", enabled=True, created_at=now, updated_at=now)
                        for a in ("qwen", "gpt-oss", "mixed", "unknown")])
    for alias, kind, model, meta in rows:
        instance = LLMProviderInstance(
            id=uuid.uuid4(), type=kind, base_url=f"http://{uuid.uuid4().hex}.local", enabled=True, weight=1.0,
            max_concurrency=1, health_status=ProviderHealthStatus.HEALTHY, created_at=now, updated_at=now,
            litellm_model=model, node_metadata=meta,
        )
        db_session.add(instance)
        await db_session.flush()
        db_session.add(LLMPoolMembership(model_alias=alias, provider_instance_id=instance.id, enabled=True))
    await db_session.flush()

    assert await GatewayDAO(db_session).alias_reasoning_levels(["qwen", "gpt-oss", "mixed", "unknown"]) == {
        "qwen": ["off"], "gpt-oss": ["low", "medium", "high"], "mixed": ["off"], "unknown": None,
    }
