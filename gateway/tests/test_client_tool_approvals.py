"""The client's own tools: the gateway cannot hold them, so it tells the client.

An admin's "Ask first" rule, a deny rule and the chat's tool mode apply to the
tools a client (an agent's SDK) defines and runs itself: as the gateway hands
a call over, it marks it (``faden.approval_required`` without an
``approvalId``: the client asks) or refuses it (``faden.tool_denied``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import AgentClientEvent, ProviderType
from faden_gateway.services.gateway import faden_events
from faden_gateway.services.gateway import governance as gov
from faden_gateway.services.gateway import tool_classes as tc
from faden_gateway.services.gateway.service import GatewayService
from tests.test_agent_routes import _agent_token, _Backend, _profile, backend  # noqa: F401
from tests.test_gateway_knowledge_tools import (
    CAN_CALL_TOOLS,
    WEATHER,
    _call,
    _Knowledge,
    _Model,
    _say,
    _tool_names,
)
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed
from tests.test_governance import EVENTS, _decisions, _faden, _headers, _rule
from tests.test_tool_modes import _classify


@pytest.fixture(autouse=True)
def _fresh_rules() -> Iterator[None]:
    gov.tool_rules.invalidate()
    tc.class_rules.invalidate()
    yield
    gov.tool_rules.invalidate()
    tc.class_rules.invalidate()


async def _setup(fastapi_app: FastAPI, db: AsyncSession, *, gateway_tools: bool) -> None:
    """With the gateway's own tools the answer streams in rounds; without, as the model sends it."""
    await _seed(db, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS if gateway_tools else None)
    fastapi_app.state.gateway_observability = _Observability()


def _marks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e["value"] for e in events if e.get("name") == "faden.approval_required"]


@pytest.mark.anyio
@pytest.mark.parametrize(("stream", "gateway_tools"), [(True, True), (True, False), (False, True)],
                         ids=["streamed-in-rounds", "streamed-as-sent", "whole"])
async def test_a_call_of_a_client_tool_a_rule_asks_about_is_marked_and_recorded(
    stream: bool, gateway_tools: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session, gateway_tools=gateway_tools)
    await _rule(db_session, "get_*", "approval_required", "Weather lookups need your OK.")
    model = _Model(monkeypatch, _call("get_weather", {"city": "Munich"}))
    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": stream, "tools": [WEATHER], "messages": [{"role": "user", "content": "Weather?"}],
    })
    assert r.status_code == 200, r.text
    assert "get_weather" in _tool_names(model.sent[0]), "offered: the client asks before it runs it"
    assert len(model.sent) == 1, "the client's to run"
    events = _faden(r.text) if stream else r.json()["faden"]["events"]
    [mark] = _marks(events)
    assert mark == {
        "toolCallId": "call_1", "toolCallName": "get_weather", "message": "Weather lookups need your OK.",
        "decisionId": mark["decisionId"], "toolClass": "unknown", "rule": True,
    }
    [decision] = await _decisions(db_session)
    assert (decision.stage, decision.tool_id, decision.outcome) == ("tool", "get_weather", "approval_required")
    assert mark["decisionId"] == str(decision.id)
    if stream:
        # Before the round ends: the client has it before it runs the call.
        assert r.text.index("faden.approval_required") < r.text.index('"finish_reason": "tool_calls"')


@pytest.mark.anyio
async def test_a_client_that_cannot_be_told_is_not_offered_a_tool_a_rule_asks_about(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session, gateway_tools=False)
    await _rule(db_session, "get_weather", "approval_required", "Weather lookups need your OK.")
    model = _Model(monkeypatch, _say("I cannot look that up."))
    r = await client.post("/v1/chat/completions", headers=_headers(), json={
        "model": ALIAS, "tools": [WEATHER], "messages": [{"role": "user", "content": "Weather?"}],
    })
    assert r.status_code == 200
    assert "get_weather" not in _tool_names(model.sent[0])
    [decision] = await _decisions(db_session)
    assert (decision.tool_id, decision.outcome) == ("get_weather", "approval_required")


@pytest.mark.anyio
@pytest.mark.parametrize("gateway_tools", [True, False], ids=["in-rounds", "as-sent"])
async def test_a_call_of_a_client_tool_a_rule_refuses_is_refused_by_its_id(
    gateway_tools: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session, gateway_tools=gateway_tools)
    await _rule(db_session, "get_weather", "deny", "No weather lookups.")
    # Not offered, and the model calls it anyway (it saw it earlier in the chat).
    model = _Model(monkeypatch, _call("get_weather", {"city": "Munich"}))
    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": True, "tools": [WEATHER], "messages": [{"role": "user", "content": "Weather?"}],
    })
    assert "get_weather" not in _tool_names(model.sent[0])
    denied = [e["value"] for e in _faden(r.text) if e.get("name") == "faden.tool_denied"]
    assert [d["toolCallId"] for d in denied] == ["", "call_1"], "at offer, then the call itself"
    assert {d.outcome for d in await _decisions(db_session)} == {"block"}


@pytest.mark.anyio
async def test_the_tool_mode_asks_about_an_unclassified_client_tool_and_an_admin_class_settles_it(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session, gateway_tools=False)
    _Model(monkeypatch, _call("get_weather", {"city": "Munich"}), _call("get_weather", {"city": "Bonn"}))
    ask = {"model": ALIAS, "stream": True, "tools": [WEATHER], "messages": [{"role": "user", "content": "Weather?"}]}
    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS, **{"X-Faden-Tool-Mode": "auto"}), json=ask)
    [mark] = _marks(_faden(r.text))
    assert (mark["toolClass"], mark["rule"], mark["decisionId"]) == ("unknown", False, None)
    assert await _decisions(db_session) == [], "the user's mode is not an admin's decision"

    await _classify(db_session, "get_weather", "read")
    tc.class_rules.invalidate()
    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS), json=ask)
    assert _marks(_faden(r.text)) == [], "a read tool runs"


@pytest.mark.anyio
@pytest.mark.parametrize(("contract", "expected"), [
    ((), [("unknown", False)]),
    (({"name": "read_page"},), [("unknown", False)]),
    (({"name": "read_page", "risk": "read"},), []),
], ids=["no-contract", "no-risk", "read"])
async def test_an_agent_without_a_contract_has_its_tools_asked_about(
    contract: tuple[dict[str, str], ...], expected: list[tuple[str, bool]],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    backend.put(_profile(host_tool_contract=contract))
    fastapi_app.state.agent_directory.invalidate()
    _Model(monkeypatch, _call("read_page", {"page": 3}))
    read_page = {"type": "function", "function": {"name": "read_page", "parameters": {"type": "object"}}}
    r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
        "model": "x", "stream": True, "tools": [read_page], "messages": [{"role": "user", "content": "Page 3?"}],
    }, headers={"Authorization": f"Bearer {_agent_token()}", **EVENTS})
    assert r.status_code == 200, r.text
    assert [(m["toolClass"], m["rule"]) for m in _marks(_faden(r.text))] == expected


@pytest.mark.anyio
@pytest.mark.parametrize(("by", "rule", "tool_class"), [("rule", True, "read"), ("mode", False, "write")])
async def test_a_gateway_tool_that_waits_says_its_class_and_whether_a_rule_asks(
    by: str, rule: bool, tool_class: str, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session, gateway_tools=True)
    if by == "rule":
        await _rule(db_session, "knowledge_search", "approval_required", "Searching needs your OK.")
    else:
        await _classify(db_session, "knowledge_search", "write")
    _Knowledge(monkeypatch)
    _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("Done."))

    async def answer() -> None:
        for _ in range(200):
            if gov.approvals.waiting():
                await client.post(f"/v1/approvals/{gov.approvals.waiting()[0]}", headers=_headers(), json={"approved": False})
                return
            await asyncio.sleep(0.01)
        raise AssertionError("nobody asked")

    ask = client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": True, "messages": [{"role": "user", "content": "When?"}],
    })
    r, _ = await asyncio.gather(ask, answer())
    [asked] = _marks(_faden(r.text))
    assert asked["approvalId"] and (asked["toolClass"], asked["rule"]) == (tool_class, rule)


@pytest.mark.anyio
async def test_an_approval_is_reported_with_what_let_it_through(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    body = {"events": [
        {"type": "approval.decided", "tool": "delete_page", "decision": "approved", "scope": "bypass"},
        {"type": "approval.decided", "tool": "write_page", "decision": "approved", "wait_ms": 900, "scope": "once"},
    ]}
    auth = {"Authorization": f"Bearer {_agent_token()}"}
    r = await client.post("/v1/agents/eplan-assistant/events", headers=auth, json=body)
    assert r.status_code == 202, r.text
    rows = (await db_session.execute(select(AgentClientEvent).order_by(AgentClientEvent.created_at))).scalars().all()
    assert sorted(e.data["scope"] for e in rows) == ["bypass", "once"]
    bad = {"events": [{"type": "approval.decided", "tool": "x", "decision": "approved", "scope": "forever"}]}
    assert (await client.post("/v1/agents/eplan-assistant/events", headers=auth, json=bad)).status_code == 400


@pytest.mark.anyio
async def test_the_stream_goes_on_unchanged_with_the_mark_before_the_round_ends() -> None:
    def chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
        body = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n".encode()

    sent = (
        chunk({"content": "Grüße aus München"})
        + chunk({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "send_mail", "arguments": "{}"}}]})
        + chunk({}, "tool_calls") + b"data: [DONE]\n\n"
    )
    cut = sent.index("ü".encode()) + 1  # a chunk that ends inside a character

    async def upstream() -> AsyncIterator[bytes]:
        for piece in (sent[:cut], sent[cut:cut + 7], sent[cut + 7:]):
            yield piece

    mark = {"type": "CUSTOM", "name": "faden.approval_required", "value": {"toolCallId": "c1"}}

    async def screen(req: Any, calls: list[dict[str, Any]], *, auth: Any, request_id: str) -> list[dict[str, Any]]:
        assert [c["function"]["name"] for c in calls] == ["send_mail"]
        return [mark]

    service = SimpleNamespace(_client_call_events=screen)
    out = b"".join([
        piece async for piece in GatewayService._screen_client_stream(  # type: ignore[arg-type]  # noqa: SLF001
            service, None, upstream(), auth=None, request_id="r",  # type: ignore[arg-type]
        )
    ])
    told = faden_events.sse_event(mark)
    assert out.replace(told, b"") == sent
    assert out.index(told) < out.index(b'"finish_reason": "tool_calls"')


def test_the_event_without_an_approval_id_is_the_clients_to_ask() -> None:
    event = gov.client_approval_event({"id": "c1"}, "send_mail", "Mail needs your OK.", uuid.UUID(int=7),
                                      tool_class="destructive", rule=True)
    assert event["name"] == "faden.approval_required" and "approvalId" not in event["value"]
    assert event["value"]["decisionId"] == str(uuid.UUID(int=7))
