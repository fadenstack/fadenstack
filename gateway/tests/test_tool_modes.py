"""Tool classes and tool modes (``faden_gateway/services/gateway/tool_classes.py``).

A tool is read, write, destructive -- or unknown when nobody vouched for it.
Every chat starts in Ask: read tools run, everything else waits for the user.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ChatSession, ProviderType, ToolClassification, ToolRule
from faden_gateway.services.extensions import registry
from faden_gateway.services.gateway import governance as gov
from faden_gateway.services.gateway import tool_classes as tc
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
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _token

EVENTS = {"X-Faden-Events": "ag-ui"}
M, C, D = tc.ToolMode, tc.ToolClass, tc.Decision


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    saved = list(registry.tool_mode_ceilings)
    tc.class_rules.invalidate()
    yield
    registry.tool_mode_ceilings[:] = saved
    tc.class_rules.invalidate()


def _headers(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", **extra}


def _faden(raw: str) -> list[dict[str, Any]]:
    return [json.loads(b.split("data: ", 1)[1]) for b in raw.split("\n\n") if b.startswith("event: faden\n")]


async def _setup(fastapi_app: FastAPI, db: AsyncSession) -> uuid.UUID:
    await _seed(db, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    sess = ChatSession(tenant_id="tenant-a", user_id="user-1", title="t")
    db.add(sess)
    await db.flush()
    return sess.id


async def _classify(db: AsyncSession, pattern: str, tool_class: str, priority: int = 10) -> None:
    db.add(ToolClassification(tenant_id="tenant-a", tool_pattern=pattern, tool_class=tool_class, priority=priority))
    await db.flush()


async def _answer_when_asked(
    client: AsyncClient, approved: bool, *, first: Callable[[], Awaitable[None]] | None = None,
) -> int:
    """Answer the call that waits for the user, after *first* (a change made while they were asked)."""
    for _ in range(500):
        if gov.approvals.waiting():
            if first is not None:
                await first()
            approval = gov.approvals.waiting()[0]
            return (await client.post(f"/v1/approvals/{approval}", headers=_headers(),
                                      json={"approved": approved})).status_code
        await asyncio.sleep(0.01)
    raise AssertionError("nobody asked")


# -- the table --------------------------------------------------------------------------


@pytest.mark.parametrize(("mode", "expected"), [
    (M.OFF, {C.READ: D.HIDDEN, C.WRITE: D.HIDDEN, C.DESTRUCTIVE: D.HIDDEN, C.UNKNOWN: D.HIDDEN}),
    (M.READ_ONLY, {C.READ: D.RUN, C.WRITE: D.HIDDEN, C.DESTRUCTIVE: D.HIDDEN, C.UNKNOWN: D.HIDDEN}),
    (M.ASK, {C.READ: D.RUN, C.WRITE: D.ASK, C.DESTRUCTIVE: D.ASK, C.UNKNOWN: D.ASK}),
    (M.AUTO, {C.READ: D.RUN, C.WRITE: D.RUN, C.DESTRUCTIVE: D.ASK, C.UNKNOWN: D.ASK}),
])
def test_what_each_mode_does_with_each_class(mode: tc.ToolMode, expected: dict[tc.ToolClass, tc.Decision]) -> None:
    for tool_class, decision in expected.items():
        assert tc.decide(mode, tc.Classified("t", tool_class, "rule")) is decision


@pytest.mark.parametrize(("mode", "tool_class", "approval", "expected"), [
    (M.ASK, C.WRITE, "never", D.RUN),  # the author pre-approves what the mode would ask about
    (M.ASK, C.READ, "never", D.RUN),
    (M.ASK, C.DESTRUCTIVE, "never", D.ASK),  # never for a destructive or unclassified tool
    (M.AUTO, C.UNKNOWN, "never", D.ASK),
    (M.AUTO, C.WRITE, "ask", D.ASK),  # asks where the mode would run it
    (M.ASK, C.READ, "ask", D.ASK),
    (M.AUTO, C.READ, "always", D.ASK),
    (M.AUTO, C.WRITE, None, D.RUN),  # unset: the table
    (M.READ_ONLY, C.WRITE, "never", D.HIDDEN),  # none of them shows a hidden tool
    (M.READ_ONLY, C.WRITE, "always", D.HIDDEN),
    (M.OFF, C.READ, "ask", D.HIDDEN),
])
def test_the_authors_approval_goes_on_top_of_the_mode(
    mode: tc.ToolMode, tool_class: tc.ToolClass, approval: str | None, expected: tc.Decision,
) -> None:
    """38 §4.3, D7: ``always`` and ``ask`` ask, ``never`` runs read and write tools from Ask upward."""
    assert tc.decide(mode, tc.Classified("t", tool_class, "agent", approval=approval)) is expected


def test_the_contract_says_each_tools_approval() -> None:
    classifier = tc.Classifier(contract={"save": "write"}, approvals={"save": "never", "open_url": "always"})
    assert (classifier.classify("save").approval, classifier.classify("open_url").approval) == ("never", "always")
    assert classifier.classify("other").approval is None


def test_an_unknown_tool_never_runs_unasked_and_open_world_is_not_acted_on() -> None:
    for mode in M:
        assert tc.decide(mode, tc.Classified("t", C.UNKNOWN, "none", open_world=True)) is not D.RUN
    assert tc.OPEN_WORLD_ENFORCED is False
    assert tc.decide(M.AUTO, tc.Classified("t", C.WRITE, "rule", open_world=True)) is D.RUN


def test_where_a_class_comes_from() -> None:
    annotations = {"mcp.gh.get": {"readOnlyHint": True}, "mcp.gh.del": {"destructiveHint": True},
                   "mcp.gh.edit": {"readOnlyHint": False, "destructiveHint": False}, "mcp.x.y": {"readOnlyHint": True}}
    classifier = tc.Classifier(
        rules=(tc.ClassRule("mcp.gh.*", "annotations"), tc.ClassRule("read_page", "destructive")),
        gateway_tools=frozenset({"knowledge_search", "read_page"}),
        contract={"write_page": "write", "odd": "maybe"},
        annotations=annotations,
    )
    got = {n: (t.tool_class.value, t.source) for n in
           ("mcp.gh.get", "mcp.gh.del", "mcp.gh.edit", "mcp.x.y", "read_page", "knowledge_search", "write_page", "odd")
           for t in [classifier.classify(n)]}
    assert got == {
        "mcp.gh.get": ("read", "annotations"),
        "mcp.gh.del": ("destructive", "annotations"),
        "mcp.gh.edit": ("write", "annotations"),
        "mcp.x.y": ("unknown", "none"),  # a server nobody trusts: its hints do not count
        "read_page": ("destructive", "rule"),  # the admin's word first
        "knowledge_search": ("read", "gateway"),
        "write_page": ("write", "agent"),
        "odd": ("unknown", "none"),
    }


def test_the_sdks_tool_search_is_read_unless_a_rule_or_the_contract_says_otherwise() -> None:
    """find_tool only switches the client's own tools on; what it finds is classified on its own call."""
    assert (tc.Classifier().classify("find_tool").tool_class, tc.Classifier().classify("find_tool").source) == (
        C.READ, "sdk",
    )
    assert tc.Classifier(contract={"find_tool": ""}).classify("find_tool").tool_class is C.READ, "listed without a risk"
    assert tc.Classifier(contract={"find_tool": "write"}).classify("find_tool").tool_class is C.WRITE, "the contract's word"
    rule = tc.Classifier(rules=(tc.ClassRule("find_*", "destructive"),)).classify("find_tool")
    assert (rule.tool_class, rule.source) == (C.DESTRUCTIVE, "rule"), "the admin's word first"
    assert tc.Classifier().classify("find_tools").tool_class is C.UNKNOWN, "only that name"
    assert tc.decide(M.ASK, tc.Classifier().classify("find_tool")) is D.RUN


@pytest.mark.anyio
async def test_ceilings_narrow_and_a_failing_one_counts_as_read_only() -> None:
    ctx = tc.CeilingContext(tenant_id="t", user_id="u")
    assert (await tc.effective(M.AUTO, [], ctx)).mode is M.AUTO, "no ceiling: the user's pick"
    assert (await tc.effective(None, [], ctx)).mode is M.ASK, "no pick: Ask"

    async def to_ask(_: tc.CeilingContext) -> str:
        return "ask"

    async def none(_: tc.CeilingContext) -> None:
        return None

    async def broken(_: tc.CeilingContext) -> str:
        raise RuntimeError("EE down")

    narrowed = await tc.effective(M.AUTO, [to_ask, none], ctx)
    assert (narrowed.mode, narrowed.chosen, narrowed.ceiling) == (M.ASK, M.AUTO, M.ASK)
    assert (await tc.effective(M.READ_ONLY, [to_ask], ctx)).mode is M.READ_ONLY, "never widens"
    assert (await tc.effective(M.AUTO, [broken], ctx)).mode is M.READ_ONLY


# -- in a chat -------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_read_only_offers_only_read_tools_and_refuses_a_hidden_one_called_anyway(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("get_weather", {"city": "Munich"}), _say("I cannot."))
    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS, **{"X-Faden-Tool-Mode": "read_only"}),
                          json={"model": ALIAS, "stream": True, "tools": [WEATHER],
                                "messages": [{"role": "user", "content": "Weather?"}]})
    offered = _tool_names(model.sent[0])
    assert "get_weather" not in offered and "knowledge_search" in offered
    notice = next(e for e in _faden(r.text) if e.get("name") == "faden.tool_mode")["value"]
    assert (notice["effective_tool_mode"], notice["hidden"]) == ("read_only", ["get_weather"])
    told = model.sent[1]["messages"][-1]
    assert told["role"] == "tool" and "Only read-only tools are allowed" in told["content"]


@pytest.mark.anyio
async def test_off_offers_nothing(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _say("Hi."))
    await client.post("/v1/chat/completions", headers=_headers(**{"X-Faden-Tool-Mode": "off"}),
                      json={"model": ALIAS, "tools": [WEATHER], "messages": [{"role": "user", "content": "Hi"}]})
    assert "tools" not in model.sent[0] and "tool_choice" not in model.sent[0]


@pytest.mark.anyio
@pytest.mark.parametrize("approved", [True, False], ids=["approved", "declined"])
async def test_ask_pauses_a_write_tool_until_the_user_answers(
    approved: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _classify(db_session, "knowledge_search", "write")
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("Done."))

    async def answer() -> int:
        for _ in range(200):
            if gov.approvals.waiting():
                approval = gov.approvals.waiting()[0]
                return (await client.post(f"/v1/approvals/{approval}", headers=_headers(),
                                          json={"approved": approved})).status_code
            await asyncio.sleep(0.01)
        raise AssertionError("nobody asked")

    ask = client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": True, "messages": [{"role": "user", "content": "When?"}]})
    r, status = await asyncio.gather(ask, answer())
    assert status == 200
    asked = next(e for e in _faden(r.text) if e.get("name") == "faden.approval_required")
    assert asked["value"]["message"] == "This tool needs your approval in this chat's tool mode."
    assert len(knowledge.searches) == (1 if approved else 0)
    assert ("handbook.pdf" in model.sent[1]["messages"][-1]["content"]) is approved


@pytest.mark.anyio
async def test_a_write_tool_runs_in_auto_and_a_client_that_cannot_ask_is_told(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _setup(fastapi_app, db_session)
    await _classify(db_session, "knowledge_search", "write")
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "a"}), _say("x"),
                   _call("knowledge_search", {"query": "b"}), _say("y"))
    await client.post("/v1/chat/completions", headers=_headers(), json={
        "model": ALIAS, "messages": [{"role": "user", "content": "When?"}]})
    assert knowledge.searches == [] and "could not be asked for here" in model.sent[1]["messages"][-1]["content"]

    r = await client.patch(f"/v1/sessions/{sid}/tool-policy", headers=_headers(), json={"tool_mode": "auto"})
    assert r.status_code == 200 and r.json()["effective_tool_mode"] == "auto"
    await client.post("/v1/chat/completions", headers=_headers(), json={
        "model": ALIAS, "session_id": str(sid), "messages": [{"role": "user", "content": "When?"}]})
    assert [s["query"] for s in knowledge.searches] == ["b"], "auto runs a write tool unasked"


@pytest.mark.anyio
async def test_the_session_setting_and_a_ceiling_decide_the_mode(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _setup(fastapi_app, db_session)
    got = (await client.get(f"/v1/sessions/{sid}/tool-policy", headers=_headers())).json()
    assert (got["tool_mode"], got["effective_tool_mode"], got["tool_mode_ceiling"]) == ("ask", "ask", None)

    seen: list[tc.CeilingContext] = []

    async def team_ceiling(ctx: tc.CeilingContext) -> tc.ToolMode:
        seen.append(ctx)
        return tc.ToolMode.READ_ONLY

    registry.tool_mode_ceilings.append(team_ceiling)
    got = (await client.patch(f"/v1/sessions/{sid}/tool-policy", headers=_headers(), json={"tool_mode": "auto"})).json()
    assert (got["tool_mode"], got["effective_tool_mode"], got["tool_mode_ceiling"]) == ("auto", "read_only", "read_only")

    model = _Model(monkeypatch, _say("Hi."))
    await client.post("/v1/chat/completions", headers=_headers(), json={
        "model": ALIAS, "session_id": str(sid), "tools": [WEATHER], "messages": [{"role": "user", "content": "Hi"}]})
    assert "get_weather" not in _tool_names(model.sent[0]), "the ceiling applies in the chat too"
    assert seen[-1].session_id == str(sid) and seen[-1].user_id == "user-1"

    bad = await client.patch(f"/v1/sessions/{sid}/tool-policy", headers=_headers(), json={"tool_mode": "yolo"})
    assert bad.status_code in (400, 422)


# -- read again before a call runs (39 F2) ----------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_a_ceiling_lowered_between_rounds_holds_for_the_next_call(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _classify(db_session, "knowledge_search", "write")
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "a"}), _call("knowledge_search", {"query": "b"}),
                   _say("Done."))

    async def lowered_once_a_search_ran(_: tc.CeilingContext) -> str | None:
        return "read_only" if knowledge.searches else None

    registry.tool_mode_ceilings.append(lowered_once_a_search_ran)
    r = await client.post("/v1/chat/completions", headers=_headers(**{"X-Faden-Tool-Mode": "auto"}), json={
        "model": ALIAS, "stream": stream, "messages": [{"role": "user", "content": "When?"}]})
    assert r.status_code == 200, r.text
    assert [s["query"] for s in knowledge.searches] == ["a"], "the second round runs under the lowered ceiling"
    assert "Only read-only tools are allowed in this chat." in model.sent[2]["messages"][-1]["content"]


@pytest.mark.anyio
async def test_an_ask_first_rule_added_while_the_mode_asked_refuses_the_approved_call(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _classify(db_session, "knowledge_search", "write")
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("Done."))

    async def add_rule() -> None:
        db_session.add(ToolRule(tenant_id="tenant-a", name="look twice", tool_pattern="knowledge_*",
                                action="approval_required", message="Searching needs a second look.",
                                category="tool_rule", priority=10, enabled=True))
        await db_session.flush()

    ask = client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": True, "messages": [{"role": "user", "content": "When?"}]})
    r, status = await asyncio.wait_for(asyncio.gather(ask, _answer_when_asked(client, True, first=add_rule)), 10)
    assert status == 200 and knowledge.searches == [], "approved for the mode, not for the rule"
    told = "The tool policy changed before this call ran: Searching needs a second look."
    assert told in model.sent[1]["messages"][-1]["content"]
    assert any(e.get("name") == "faden.tool_denied" and e["value"]["message"] == told for e in _faden(r.text))


@pytest.mark.anyio
async def test_an_approved_call_runs_when_only_the_mode_that_asks_moved(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _classify(db_session, "knowledge_search", "destructive")
    knowledge = _Knowledge(monkeypatch)
    _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("Done."))
    ceiling: list[str | None] = ["ask"]

    async def held(_: tc.CeilingContext) -> str | None:
        return ceiling[0]

    async def lift() -> None:
        ceiling[0] = None  # Auto now, where a destructive tool still asks: the user's answer stands

    registry.tool_mode_ceilings.append(held)
    ask = client.post("/v1/chat/completions", headers=_headers(**EVENTS, **{"X-Faden-Tool-Mode": "auto"}), json={
        "model": ALIAS, "stream": True, "messages": [{"role": "user", "content": "When?"}]})
    r, status = await asyncio.wait_for(asyncio.gather(ask, _answer_when_asked(client, True, first=lift)), 10)
    assert status == 200 and len(knowledge.searches) == 1, r.text


@pytest.mark.anyio
async def test_an_agents_host_tools_are_classed_by_its_contract(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    backend.put(_profile(host_tool_contract=(
        {"name": "read_page", "risk": "read"}, {"name": "delete_page", "risk": "destructive"},
    )))
    fastapi_app.state.agent_directory.invalidate()
    model = _Model(monkeypatch, _say("ok"))
    read_page, delete_page = (
        {"type": "function", "function": {"name": n, "parameters": {"type": "object"}}}
        for n in ("read_page", "delete_page")
    )
    r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
        "model": "x", "tools": [read_page, delete_page], "messages": [{"role": "user", "content": "Page 3?"}],
    }, headers={"Authorization": f"Bearer {_agent_token()}", "X-Faden-Tool-Mode": "read_only"})
    assert r.status_code == 200, r.text
    offered = _tool_names(model.sent[0])
    assert "read_page" in offered and "delete_page" not in offered, "its contract says which is which"
    profile = (await client.get("/v1/agents/eplan-assistant/profile",
                                headers={"Authorization": f"Bearer {_agent_token()}"})).json()
    assert profile["tool_modes"] == {"default": "ask", "ceiling": None, "max": "auto", "open_world_enforced": False}


# -- each agent's default and highest level (sdk/38 §5, D1, D2) -------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(("levels", "ee_ceiling", "expected"), [
    ({}, None, {"default": "ask", "ceiling": None, "max": "auto"}),  # as before: no new ceiling
    ({"default_level": "auto", "max_level": "auto"}, None, {"default": "auto", "ceiling": None, "max": "auto"}),
    ({"default_level": "read_only", "max_level": "ask"}, None, {"default": "read_only", "ceiling": "ask", "max": "ask"}),
    ({"default_level": "auto", "max_level": "auto"}, "read_only",
     {"default": "read_only", "ceiling": "read_only", "max": "auto"}),  # the narrowest applies
    ({"default_level": "ask", "max_level": "bypass"}, None,
     {"default": "ask", "ceiling": None, "max": "auto"}),  # a client without Bypass sees Auto (sdk/40 A3)
    ({"default_level": "ask", "max_level": "planet"}, None,
     {"default": "read_only", "ceiling": "read_only", "max": "read_only"}),  # unknown: never wider
], ids=["unset", "auto-agent", "research-agent", "ee-narrower", "bypass-max-old-client", "unknown-max"])
async def test_the_profile_says_the_agents_default_and_highest_level(
    levels: dict[str, str], ee_ceiling: str | None, expected: dict[str, str | None],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    backend.put(_profile(**levels))
    fastapi_app.state.agent_directory.invalidate()
    if ee_ceiling is not None:
        async def ceiling(_: tc.CeilingContext) -> str:
            return ee_ceiling

        registry.tool_mode_ceilings.append(ceiling)
    profile = (await client.get("/v1/agents/eplan-assistant/profile",
                                headers={"Authorization": f"Bearer {_agent_token()}"})).json()
    assert {k: profile["tool_modes"][k] for k in expected} == expected


@pytest.mark.anyio
@pytest.mark.parametrize(("levels", "header", "asked"), [
    ({"default_level": "auto", "max_level": "auto"}, None, False),  # starts in the agent's Auto
    ({}, None, True),  # unset: Ask, as before
    ({"default_level": "ask", "max_level": "ask"}, "auto", True),  # Auto asked for, Ask is the most
], ids=["agent-default-auto", "unset-ask", "header-above-max"])
async def test_an_agents_levels_decide_its_conversations_mode(
    levels: dict[str, str], header: str | None, asked: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    backend: _Backend,  # noqa: F811 - the fixture
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    backend.put(_profile(host_tool_contract=({"name": "update_page", "risk": "write"},), **levels))
    fastapi_app.state.agent_directory.invalidate()
    _Model(monkeypatch, _call("update_page", {"page": 3}))
    update_page = {"type": "function", "function": {"name": "update_page", "parameters": {"type": "object"}}}
    headers = {"Authorization": f"Bearer {_agent_token()}", **EVENTS, **({"X-Faden-Tool-Mode": header} if header else {})}
    r = await client.post("/v1/agents/eplan-assistant/chat/completions", json={
        "model": "x", "stream": True, "tools": [update_page], "messages": [{"role": "user", "content": "Fix page 3."}],
    }, headers=headers)
    assert r.status_code == 200, r.text
    marks = [e for e in _faden(r.text) if e.get("name") == "faden.approval_required"]
    assert bool(marks) is asked
