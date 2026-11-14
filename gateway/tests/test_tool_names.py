"""MCP tools reach the model under names every provider takes; the gateway keeps its own.

An MCP tool is ``mcp.<prefix>.<tool>`` in the gateway. OpenAI, Azure OpenAI and Bedrock's Converse API
refuse a tool name outside ``[a-zA-Z0-9_-]{1,64}``, so every MCP tool would have been refused there.
The model now gets ``mcp_<prefix>_<tool>`` (tool_names.py), on every route; rules, grants, the audit,
the call log, the events and the history keep the dotted name.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ChatMessage, ProviderType, ToolRule
from faden_gateway.services.gateway import governance as gov
from faden_gateway.services.gateway import tool_classes
from faden_gateway.services.gateway.system_tools import FIND
from faden_gateway.services.gateway.tool_names import ToolNames
from tests.test_faden_events import EVENTS, _faden
from tests.test_gateway_knowledge_tools import CAN_CALL_TOOLS, _call, _Model, _say, _streamed, _tool_names
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _session, _token
from tests.test_governance import _decisions
from tests.test_session_tool_switches import (
    _MCP,
    CREATE,
    CREATE_SEEN,
    SEARCH,
    SEARCH_SEEN,
    SYSTEM,
    _ready,
    _switch_on,
)

#: What OpenAI, Azure OpenAI and Bedrock Converse take as a tool's name.
TAKEN_BY_EVERY_PROVIDER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _hash(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()[:6]


# ── The table ─────────────────────────────────────────────────────


def test_an_mcp_tool_gets_a_name_every_provider_takes() -> None:
    names = ToolNames(taken={"get_weather"})
    names.add(["mcp.github.search_repositories", "mcp.everything.get-sum", "mcp.my server.do/it", "get_weather"])
    assert names.wire("mcp.github.search_repositories") == "mcp_github_search_repositories"
    assert names.wire("mcp.everything.get-sum") == "mcp_everything_get-sum"
    assert names.wire("mcp.my server.do/it") == "mcp_my_server_do_it"
    assert names.wire("get_weather") == "get_weather"


def test_a_long_name_is_cut_to_64_and_told_apart_by_a_hash() -> None:
    long_a, long_b = "mcp.server." + "a" * 80, "mcp.server." + "a" * 79 + "b"
    names = ToolNames()
    names.add([long_a, long_b])
    a, b = names.wire(long_a), names.wire(long_b)
    assert a == "mcp_server_" + "a" * 46 + "_" + _hash(long_a)
    assert len(a) == len(b) == 64 and a != b
    assert (names.back(a), names.back(b)) == (long_a, long_b)


def test_two_names_that_would_meet_both_get_a_hash() -> None:
    """Neither keeps the plain name: which one would have depended on the order."""
    names = ToolNames()
    names.add(["mcp.a.b_c", "mcp.a_b.c"])
    assert names.wire("mcp.a.b_c") == f"mcp_a_b_c_{_hash('mcp.a.b_c')}"
    assert names.wire("mcp.a_b.c") == f"mcp_a_b_c_{_hash('mcp.a_b.c')}"
    assert names.back(names.wire("mcp.a_b.c")) == "mcp.a_b.c"
    assert names.back("mcp_a_b_c") is None


def test_no_mcp_tool_gets_the_name_of_a_tool_of_the_clients() -> None:
    names = ToolNames(taken={"mcp_jira_find"})
    names.add(["mcp.jira.find"])
    assert names.wire("mcp.jira.find") == f"mcp_jira_find_{_hash('mcp.jira.find')}"
    assert names.back("mcp_jira_find") is None, "the client's own"
    assert names.back(names.wire("mcp.jira.find")) == "mcp.jira.find"


def test_a_client_tool_named_like_an_mcp_tool_keeps_its_name() -> None:
    names = ToolNames(taken={"mcp.local.thing"})
    names.add(["mcp.local.thing"])
    assert names.wire("mcp.local.thing") == "mcp.local.thing"


def test_a_name_keeps_its_form_for_the_whole_request() -> None:
    """One round's history matches the next round's tools, whatever came in between."""
    names = ToolNames()
    names.add(["mcp.a.b_c"])
    names.add(["mcp.a_b.c"])
    assert names.wire("mcp.a.b_c") == "mcp_a_b_c"
    assert names.wire("mcp.a_b.c") == f"mcp_a_b_c_{_hash('mcp.a_b.c')}"


def test_the_same_names_make_the_same_table_in_any_order() -> None:
    batch = ["mcp.a.b_c", "mcp.a_b.c", "mcp.x." + "y" * 70, "mcp.github.search", "mcp.x." + "y" * 69 + "z"]
    one, two = ToolNames(), ToolNames()
    one.add(batch)
    two.add(reversed(batch))
    assert [one.wire(name) for name in batch] == [two.wire(name) for name in batch]


def test_a_tool_named_after_another_ones_hash_does_not_take_its_calls() -> None:
    """A server may name a tool after another tool's name with its hash: each name still means one tool."""
    victim, other = "mcp.a.b_c", "mcp.a_b.c"
    trap = f"mcp.a.b_c_{_hash(victim)}"  # its plain name is the victim's first choice
    names = ToolNames()
    names.add([victim, other, trap])
    seen = {name: names.wire(name) for name in (victim, other, trap)}
    assert seen[trap] == f"mcp_a_b_c_{_hash(victim)}"
    assert len(set(seen.values())) == 3
    assert all(names.back(wire) == name for name, wire in seen.items())


@pytest.mark.parametrize("seed", range(4))
def test_any_names_map_one_to_one_onto_names_every_provider_takes(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - names to test with, not secrets
    batch = {"mcp." + "".join(rng.choice("ab_-.: /Ä") for _ in range(rng.randint(1, 90))) for _ in range(400)}
    taken = {"mcp_a", "mcp_b_", "mcp_a_b"}
    names = ToolNames(taken=taken)
    for start in range(0, len(batch), 100):  # added in four rounds, as a request may
        names.add(sorted(batch)[start:start + 100])
    seen = {name: names.wire(name) for name in batch}
    assert all(TAKEN_BY_EVERY_PROVIDER.match(wire) for wire in seen.values())
    assert len(set(seen.values())) == len(batch)
    assert not set(seen.values()) & taken
    assert all(names.back(wire) == name for name, wire in seen.items())


def test_a_call_comes_back_under_the_gateways_name() -> None:
    names = ToolNames(taken={"get_weather"})
    names.add(["mcp.everything.get-sum", "mcp.github.search"])
    assert names.back("mcp_everything_get-sum") == "mcp.everything.get-sum"
    assert names.back("mcp_everything_get-sum<|channel|>commentary") == "mcp.everything.get-sum"
    assert names.back("MCP_Everything_Get_Sum") == "mcp.everything.get-sum", "varied, as a small model does"
    assert names.back("mcp.everything.get-sum") is None, "the gateway's own name: taken as it is"
    assert names.back("get_weather") is None
    assert names.back("mcp_github_delete_everything") is None
    calls = [
        {"id": "1", "type": "function", "function": {"name": "mcp_github_search", "arguments": "{}"}},
        {"id": "2", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}},
    ]
    names.calls_back(calls)
    assert [c["function"]["name"] for c in calls] == ["mcp.github.search", "get_weather"]


def test_a_varied_name_that_could_be_the_clients_is_not_guessed() -> None:
    names = ToolNames(taken={"mcp-github-search"})
    names.add(["mcp.github.search"])
    assert names.back("MCP_GITHUB_SEARCH") is None
    assert names.back("mcp_github_search") == "mcp.github.search"


def test_a_varied_name_two_tools_could_have_is_not_guessed() -> None:
    names = ToolNames()
    names.add(["mcp.gh.get-sum", "mcp.gh.get_sum"])
    assert names.back("mcp_gh_get-sum") == "mcp.gh.get-sum"
    assert names.back("mcp_gh_get_sum") == "mcp.gh.get_sum"
    assert names.back("MCP_GH_GETSUM") is None


def _tool(name: str) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}


def _called(call_id: str, name: str) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_a_payload_leaves_with_the_models_names_and_stays_as_it_was() -> None:
    payload = {
        "model": "m",
        "tools": [_tool("mcp.github.search"), _tool("get_weather"), _tool("get_available_tools")],
        "tool_choice": {"type": "function", "function": {"name": "mcp.github.search"}},
        "messages": [
            {"role": "user", "content": "Find faden with mcp.github.search."},
            {"role": "assistant", "content": None,
             "tool_calls": [_called("c1", "mcp.github.search"), _called("c2", "mcp.jira.find")]},
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"switched_on": [{"name": "mcp.github.search"}]})},
            {"role": "tool", "tool_call_id": "c2", "name": "mcp.jira.find",
             "content": [{"type": "text", "text": "There is no tool named 'mcp.jira.find'."}]},
        ],
    }
    kept = copy.deepcopy(payload)
    sent = ToolNames(taken={"get_weather", "get_available_tools"}).out(payload)
    assert payload == kept
    assert _tool_names(sent) == ["mcp_github_search", "get_weather", "get_available_tools"]
    assert sent["tool_choice"]["function"]["name"] == "mcp_github_search"
    assert sent["messages"][0] == payload["messages"][0], "what a person wrote stays theirs"
    assert [c["function"]["name"] for c in sent["messages"][1]["tool_calls"]] == ["mcp_github_search", "mcp_jira_find"]
    assert json.loads(sent["messages"][2]["content"]) == {"switched_on": [{"name": "mcp_github_search"}]}
    assert sent["messages"][3]["name"] == "mcp_jira_find"
    assert sent["messages"][3]["content"] == [{"type": "text", "text": "There is no tool named 'mcp_jira_find'."}]


def test_a_payload_without_mcp_tools_leaves_as_it_is() -> None:
    payload = {"model": "m", "tools": [_tool("get_weather")], "messages": [{"role": "user", "content": "Hi"}]}
    assert ToolNames(taken={"get_weather"}).out(payload) is payload


def test_a_text_names_only_whole_tool_names() -> None:
    names = ToolNames()
    names.add(["mcp.a.b", "mcp.a.b.c", "mcp.a.b x"])
    assert names.text("Use mcp.a.b. Or mcp.a.b.c, not xmcp.a.b, mcp.a.bd or mcp.a.b.x.") == (
        "Use mcp_a_b. Or mcp_a_b_c, not xmcp.a.b, mcp.a.bd or mcp.a.b.x."
    )
    assert names.text("Then mcp.a.b x.") == "Then mcp_a_b_x.", "the longest name that fits"


# ── Through the gateway ───────────────────────────────────────────


@pytest.fixture(autouse=True)
def _tools_are_read(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Not about tool modes (test_tool_modes.py): every tool is read, and the rules are read afresh."""

    async def rules(self: object, session: object, tenant_id: str) -> tuple[tool_classes.ClassRule, ...]:
        return (tool_classes.ClassRule("*", "read"),)

    monkeypatch.setattr(tool_classes.ClassRules, "for_tenant", rules)
    gov.tool_rules.invalidate()
    yield
    gov.tool_rules.invalidate()


def _names_sent(payload: dict[str, Any]) -> list[str]:
    """Every tool name a payload gives the model: its tools, its choice, its history's calls."""
    names = _tool_names(payload)
    choice = payload.get("tool_choice")
    if isinstance(choice, dict):
        names.append(choice["function"]["name"])
    for message in payload.get("messages") or []:
        names += [c["function"]["name"] for c in message.get("tool_calls") or []]
    return names


async def _post(client: AsyncClient, **body: Any) -> Any:
    body.setdefault("model", ALIAS)
    body.setdefault("messages", [{"role": "user", "content": "Find faden."}])
    headers = {"Authorization": f"Bearer {_token()}", **(EVENTS if body.get("stream") else {})}
    return await client.post("/v1/chat/completions", headers=headers, json=body)


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_the_model_gets_names_a_strict_provider_takes_and_the_gateway_keeps_its_own(
    stream: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready(fastapi_app, db_session)
    mcp = _MCP(monkeypatch, SEARCH, CREATE)
    model = _Model(monkeypatch, _call(SEARCH_SEEN, {"query": "faden"}), _say("Found it."), _say("ok"))
    sid = await _session(db_session)
    await _switch_on(db_session, sid, SEARCH)

    r = await _post(client, session_id=str(sid), stream=stream)
    reply = _streamed(r.text)[0] if stream else r.json()["choices"][0]["message"]["content"]
    assert reply == "Found it."
    assert [c["qualified_name"] for c in mcp.calls] == [SEARCH], "the MCP service hears the gateway's name"
    assert _tool_names(model.sent[1]) == [*SYSTEM, SEARCH_SEEN]
    assert [c["function"]["name"] for c in model.sent[1]["messages"][-2]["tool_calls"]] == [SEARCH_SEEN]

    # The chat's history keeps the gateway's name, and the next turn gives the model its own.
    [kept] = (await db_session.execute(
        select(ChatMessage.tool_call_json).where(ChatMessage.tool_call_json.is_not(None)),
    )).scalars().all()
    assert [c["function"]["name"] for c in kept["exchange"][0]["tool_calls"]] == [SEARCH]
    await _post(client, session_id=str(sid), messages=[{"role": "user", "content": "And now?"}])
    assert [n for n in _names_sent(model.sent[2]) if n.startswith("mcp")] == [SEARCH_SEEN, SEARCH_SEEN]
    assert all(TAKEN_BY_EVERY_PROVIDER.match(n) for sent in model.sent for n in _names_sent(sent))

    if stream:  # the client hears the gateway's names
        events = _faden(r.text)
        assert [e["toolCallName"] for e in events if e.get("type") in ("TOOL_CALL_START", "TOOL_CALL_END")] == [
            SEARCH, SEARCH,
        ]
        [turn] = [e for e in events if e.get("name") == "faden.gateway_round"]
        assert turn["value"]["messages"][0]["tool_calls"][0]["function"]["name"] == SEARCH


@pytest.mark.anyio
async def test_a_rule_on_the_gateways_name_holds_for_a_call_by_the_models_name(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready(fastapi_app, db_session)
    db_session.add(ToolRule(tenant_id="tenant-a", name="no github", tool_pattern="mcp.github.*", action="deny",
                            message="No GitHub today.", category="tool_rule", priority=10, enabled=True))
    await db_session.flush()
    mcp = _MCP(monkeypatch, SEARCH)
    model = _Model(monkeypatch, _call(SEARCH_SEEN, {"query": "faden"}), _say("I may not."))
    sid = await _session(db_session)
    await _switch_on(db_session, sid, SEARCH)

    await _post(client, session_id=str(sid))
    assert mcp.calls == []
    assert "No GitHub today." in model.sent[1]["messages"][-1]["content"]
    [decision] = await _decisions(db_session)
    assert (decision.tool_id, decision.outcome) == (SEARCH, "block")


@pytest.mark.anyio
async def test_a_history_the_client_keeps_reaches_the_model_under_the_models_names(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``faden.gateway_round`` the client sends back holds the gateway's names, as it heard them."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, SEARCH)
    model = _Model(monkeypatch, _say("ok"))
    found = json.dumps({"switched_on": [{"name": SEARCH}], "note": "call them by name"})

    await _post(client, messages=[
        {"role": "user", "content": "Find faden."},
        {"role": "assistant", "content": None, "tool_calls": [_called("c1", SEARCH)]},
        {"role": "tool", "tool_call_id": "c1", "content": found},
        {"role": "assistant", "content": "Found it."},
        {"role": "user", "content": "And now?"},
    ])
    history = model.sent[0]["messages"]
    assert [c["function"]["name"] for m in history for c in m.get("tool_calls") or []] == [SEARCH_SEEN]
    [answer] = [m for m in history if m["role"] == "tool"]
    assert json.loads(answer["content"])["switched_on"] == [{"name": SEARCH_SEEN}]


@pytest.mark.anyio
async def test_a_long_name_and_two_that_would_meet_each_reach_their_own_tool(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready(fastapi_app, db_session)
    long_name, one, other = "mcp.docs." + "find_the_page_" * 6, "mcp.a.b_c", "mcp.a_b.c"
    seen = ToolNames()
    seen.add([long_name, one, other])
    mcp = _MCP(monkeypatch, long_name, one, other)
    model = _Model(
        monkeypatch,
        _call(seen.wire(other), {}),
        _call(seen.wire(long_name), {}, call_id="call_2"),
        _say("Done."),
    )
    sid = await _session(db_session)
    await _switch_on(db_session, sid, long_name, one, other)

    await _post(client, session_id=str(sid))
    assert sorted(_tool_names(model.sent[0])[len(SYSTEM):]) == sorted(seen.wire(n) for n in (long_name, one, other))
    assert all(TAKEN_BY_EVERY_PROVIDER.match(n) for n in _tool_names(model.sent[0]))
    assert [c["qualified_name"] for c in mcp.calls] == [other, long_name]


@pytest.mark.anyio
async def test_a_search_that_finds_nothing_lists_every_tool_by_the_models_name(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None of them is offered yet: the table holds the tenant's whole catalog."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, SEARCH, CREATE)
    model = _Model(monkeypatch, _call(FIND, {"query": "weather forecast"}), _say("None."))
    sid = await _session(db_session)

    await _post(client, session_id=str(sid))
    told = json.loads(model.sent[1]["messages"][-1]["content"])
    assert told["switched_on"] == []
    assert sorted(told["tools"]) == sorted([SEARCH_SEEN, CREATE_SEEN])


@pytest.mark.anyio
async def test_a_client_tool_with_the_name_an_mcp_tool_would_get_keeps_it_and_its_calls(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready(fastapi_app, db_session)
    mcp = _MCP(monkeypatch, SEARCH)
    model = _Model(monkeypatch, _call(SEARCH_SEEN, {"query": "faden"}))
    sid = await _session(db_session)
    await _switch_on(db_session, sid, SEARCH)

    r = await _post(client, session_id=str(sid), tools=[_tool(SEARCH_SEEN)], tool_choice="auto")
    names = _tool_names(model.sent[0])
    assert SEARCH_SEEN in names and f"{SEARCH_SEEN}_{_hash(SEARCH)}" in names
    assert mcp.calls == []
    [call] = r.json()["choices"][0]["message"]["tool_calls"]
    assert call["function"]["name"] == SEARCH_SEEN, "the client's own, to run"


def _calls_sent(payload: dict[str, Any]) -> list[str]:
    return [c["function"]["name"] for m in payload["messages"] for c in m.get("tool_calls") or []]


#: The history of a turn that called an MCP tool, as a client keeps it.
HISTORY = [
    {"role": "user", "content": "Find faden."},
    {"role": "assistant", "content": None, "tool_calls": [_called("c1", SEARCH)]},
    {"role": "tool", "tool_call_id": "c1", "content": "[]"},
    {"role": "assistant", "content": "Nothing found."},
    {"role": "user", "content": "And the weather?"},
]


@pytest.mark.anyio
async def test_a_turn_without_the_gateways_tools_gets_the_history_under_the_models_names(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tool_choice: none`` with the client's own tools: the stream goes as the model sends it."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, SEARCH)
    model = _Model(monkeypatch, _say("ok"))

    await _post(client, stream=True, tools=[_tool("get_weather")], tool_choice="none", messages=HISTORY)
    assert _tool_names(model.sent[0]) == ["get_weather"]
    assert _calls_sent(model.sent[0]) == [SEARCH_SEEN]


@pytest.mark.anyio
async def test_n_answers_asked_for_one_by_one_each_get_the_models_names(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A watermarking route gets ``n`` as n requests (answer_variety.py)."""
    watermark = {"watermark": {"algorithm": "gumbel", "key_version": 2, "context_width": 4}}
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata={**CAN_CALL_TOOLS, **watermark})
    fastapi_app.state.gateway_observability = _Observability()
    _MCP(monkeypatch, SEARCH)
    model = _Model(monkeypatch, _say("One."), _say("Two."))

    r = await _post(client, n=2, messages=HISTORY)
    assert r.status_code == 200, r.text
    assert [p["n"] for p in model.sent] == [1, 1]
    assert [_calls_sent(p) for p in model.sent] == [[SEARCH_SEEN], [SEARCH_SEEN]]
