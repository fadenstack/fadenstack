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
from faden_gateway.services.extensions import registry
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
@pytest.mark.parametrize(("mode", "marks"), [
    ("ask", [("read", False, True)]), ("read_only", [("read", False, True)]), ("auto", []),
])
async def test_a_client_tool_an_admin_marks_open_world_asks_below_auto_and_the_mark_says_so(
    mode: str, marks: list[tuple[str, bool, bool]],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What it is given leaves the organisation (sdk/51 F08 §4): the client asks, and offers "for this chat" at most."""
    await _setup(fastapi_app, db_session, gateway_tools=False)
    await _classify(db_session, "get_weather", "read", open_world=True)
    _Model(monkeypatch, _call("get_weather", {"city": "Munich"}))
    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS, **{"X-Faden-Tool-Mode": mode}), json={
        "model": ALIAS, "stream": True, "tools": [WEATHER], "messages": [{"role": "user", "content": "Weather?"}],
    })
    assert r.status_code == 200, r.text
    found = _marks(_faden(r.text))
    assert [(m["toolClass"], m["rule"], m.get("openWorld", False)) for m in found] == marks
    assert all("leaves your organisation" in m["message"] for m in found)
    assert await _decisions(db_session) == [], "the user's mode is not an admin's decision"


def test_the_approval_events_say_open_world_only_when_it_is() -> None:
    call = {"id": "c1"}
    assert gov.approval_event("a1", call, "mcp.search.web", "m", 300, tool_class="read", rule=False, open_world=True)[
        "value"]["openWorld"] is True
    assert "openWorld" not in gov.approval_event("a1", call, "mcp.search.web", "m", 300, tool_class="read", rule=False)["value"]
    assert gov.client_approval_event(call, "get_weather", "m", None, tool_class="read", rule=False, open_world=True)[
        "value"]["openWorld"] is True
    assert "openWorld" not in gov.client_approval_event(call, "get_weather", "m", None, tool_class="read", rule=False)["value"]


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
async def test_the_sdks_tool_search_runs_unasked_and_the_tool_it_found_is_asked_about(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """With no contract (the .NET SDK's local MCP tools are never in one), find_tool is read: no mark, it runs in Ask.
    The tool it switched on is still unclassified, and asked about."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    backend.put(_profile(host_tool_contract=()))
    fastapi_app.state.agent_directory.invalidate()
    _Model(monkeypatch, _call("find_tool", {"query": "convert time"}), _call("convert_time", {"time": "14:30"}))
    tools = [{"type": "function", "function": {"name": n, "parameters": {"type": "object"}}} for n in ("find_tool", "convert_time")]
    asked = []
    for _ in range(2):
        r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
            "model": "x", "stream": True, "tools": tools, "messages": [{"role": "user", "content": "14:30 in New York?"}],
        }, headers={"Authorization": f"Bearer {_agent_token()}", **EVENTS})
        assert r.status_code == 200, r.text
        asked.append([(m["toolCallName"], m["toolClass"]) for m in _marks(_faden(r.text))])
    assert asked == [[], [("convert_time", "unknown")]]


class _AskingPolicy:
    """A policy, as EE would register one, that asks about every tool call."""

    name = "asking_policy"
    stages = frozenset({gov.GovernanceStage.TOOL})
    output_window_chars = 0

    async def check(self, ctx: gov.GovernanceContext) -> gov.GovernanceDecision | None:
        return gov.GovernanceDecision(
            outcome="approval_required", policy_id="p-ask", category="review", message="This needs a second look.",
        )


@pytest.mark.anyio
@pytest.mark.parametrize(("item", "rule"), [
    ("mode", False), ("admin-rule", True), ("policy", True), ("approval-always", True),
])
async def test_every_floor_item_reaches_the_client_as_a_rule(
    item: str, rule: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch, backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """38 §4.3: the SDK asks every time, whatever stands, only for ``rule: true``; the chat's mode is no floor."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    contract = {"name": "update_page", "risk": "write"}
    if item == "approval-always":
        contract["approval"] = "always"
    backend.put(_profile(host_tool_contract=(contract,)))
    fastapi_app.state.agent_directory.invalidate()
    if item == "admin-rule":
        await _rule(db_session, "update_page", "approval_required", "Page changes need your OK.")
    if item == "policy":
        monkeypatch.setattr(registry, "governance_checks", [*registry.governance_checks, _AskingPolicy()])
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    update_page = {"type": "function", "function": {"name": "update_page", "parameters": {"type": "object"}}}

    r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
        "model": "x", "stream": True, "tools": [update_page], "messages": [{"role": "user", "content": "Fix page 3."}],
    }, headers={"Authorization": f"Bearer {_agent_token()}", **EVENTS})
    assert r.status_code == 200, r.text
    [mark] = _marks(_faden(r.text))
    assert (mark["toolClass"], mark["rule"]) == ("write", rule)


@pytest.mark.anyio
@pytest.mark.parametrize(("risk", "approval", "mode", "expected"), [
    ("write", "never", "ask", []),  # pre-approved by the author: runs without asking
    ("destructive", "never", "auto", [("destructive", False, "This tool needs your approval in this chat's tool mode.")]),
    ("read", "ask", "auto", [("read", False, "This agent asks for your approval before it uses this tool.")]),
    ("write", "always", "auto", [("write", True, "This agent asks for your approval every time it uses this tool.")]),
], ids=["never-write", "never-destructive-ignored", "ask-read-in-auto", "always-write-in-auto"])
async def test_the_contracts_approval_decides_what_the_client_is_told(
    risk: str, approval: str, mode: str, expected: list[tuple[str, bool, str]],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    """38 §4.3: ``always`` is a floor item (``rule``), ``ask`` lets a standing approval answer, ``never`` runs."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    backend.put(_profile(host_tool_contract=({"name": "update_page", "risk": risk, "approval": approval},)))
    fastapi_app.state.agent_directory.invalidate()
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    update_page = {"type": "function", "function": {"name": "update_page", "parameters": {"type": "object"}}}
    r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
        "model": "x", "stream": True, "tools": [update_page], "messages": [{"role": "user", "content": "Fix page 3."}],
    }, headers={"Authorization": f"Bearer {_agent_token()}", "X-Faden-Tool-Mode": mode, **EVENTS})
    assert r.status_code == 200, r.text
    assert [(m["toolClass"], m["rule"], m["message"]) for m in _marks(_faden(r.text))] == expected


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
