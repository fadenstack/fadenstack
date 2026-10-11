"""Tool classes and tool modes (``faden_gateway/services/gateway/tool_classes.py``).

A tool is read, write, destructive -- or unknown when nobody vouched for it.
Every chat starts in Ask: read tools run, everything else waits for the user.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ChatSession, ProviderType, ToolClassification
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
    assert profile["tool_modes"] == {"default": "ask", "ceiling": None, "open_world_enforced": False}
