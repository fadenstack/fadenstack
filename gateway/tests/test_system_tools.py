"""The tools every chat has: get_available_tools and current_time.

An MCP tool is off in a chat until it is switched on. The model finds the
ones it needs with get_available_tools, which switches them on: offered from
the next round of the answer, and kept on in the chat, where the tool panel
shows them. A chat used to be offered every tool of every server -- GitHub's
48 alone cost ~14.7k prompt tokens a turn (release VM, 2026-09-25).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.services.gateway import system_tools
from llm_port_api.services.gateway.skills_client import ResolvedSkill
from llm_port_api.services.gateway.system_tools import FIND, TIME
from tests.test_gateway_knowledge_tools import _ask, _call, _Model, _say, _streamed, _tool_names
from tests.test_gateway_pipeline_combined import _session, _token
from tests.test_session_tool_switches import CREATE, SEARCH, SYSTEM, _MCP, _ready, _switch_on


def _entry(name: str, description: str) -> dict[str, Any]:
    return {
        "qualified_name": name,
        "openai_tool": {"type": "function", "function": {"name": name, "description": description}},
    }


GITHUB = [
    _entry("mcp.github.create_repository", "Create a new GitHub repository in your account."),
    _entry("mcp.github.search_repositories", "Search for GitHub repositories by name or topic."),
    _entry("mcp.github.get_file_contents", "Get the contents of a file or directory from a GitHub repository."),
    _entry("mcp.github.list_issues", "List issues in a GitHub repository."),
    _entry("mcp.everything.get-sum", "Returns the sum of two numbers."),
]


def _names(found: list[dict[str, Any]]) -> list[str]:
    return [t["qualified_name"] for t in found]


# ── The search ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("search repositories", "mcp.github.search_repositories"),
        ("read a file from the repo", "mcp.github.get_file_contents"),
        ("add two numbers", "mcp.everything.get-sum"),
        ("get_file_contents", "mcp.github.get_file_contents"),  # a tool's own name
        ("listIssues", "mcp.github.list_issues"),  # camelCase splits like snake_case
    ],
)
def test_a_search_finds_a_tool_by_what_it_does_or_its_name(query: str, first: str) -> None:
    assert _names(system_tools.find(GITHUB, {"query": query}))[0] == first


def test_a_tool_that_matches_only_a_common_word_is_not_switched_on() -> None:
    """Nearly every GitHub tool mentions repositories: the ones that match little else stay off."""
    assert _names(system_tools.find(GITHUB, {"query": "search repositories"})) == ["mcp.github.search_repositories"]


def test_a_search_switches_on_at_most_its_limit() -> None:
    catalog = [_entry(f"mcp.s.tool_{i}", "Reads a ticket.") for i in range(20)]
    assert len(system_tools.find(catalog, {"query": "ticket"})) == 5
    assert len(system_tools.find(catalog, {"query": "ticket", "limit": 50})) == 10
    assert len(system_tools.find(catalog, {"query": "ticket", "limit": "two"})) == 5


def test_a_search_without_a_query_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a query"):
        system_tools.find(GITHUB, {"query": "  "})


def test_a_search_that_finds_nothing_lists_the_tools_to_search_again() -> None:
    answer = json.loads(system_tools.found_answer("weather", [], GITHUB))
    assert answer["switched_on"] == []
    assert answer["tools"] == _names(GITHUB)
    assert answer["note"].startswith("No tool matched.")


# ── The clock ─────────────────────────────────────────────────────

NOW = datetime(2026, 9, 26, 12, 30, tzinfo=timezone.utc)


def test_the_time_is_utc_unless_a_zone_is_asked_for() -> None:
    content, is_error = system_tools.current_time({}, now=NOW)
    assert not is_error
    assert json.loads(content) == {"timezone": "UTC", "datetime": "2026-09-26T12:30:00+00:00", "weekday": "Saturday"}


def test_the_time_in_a_zone_keeps_its_summer_time() -> None:
    content, _ = system_tools.current_time({"timezone": "Europe/Berlin"}, now=NOW)
    assert json.loads(content)["datetime"] == "2026-09-26T14:30:00+02:00"


def test_an_unknown_zone_is_an_error_that_still_gives_utc() -> None:
    content, is_error = system_tools.current_time({"timezone": "Mars/Olympus"}, now=NOW)
    assert is_error
    assert json.loads(content)["utc"] == "2026-09-26T12:30:00+00:00"


# ── In a chat ─────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_a_tool_the_model_finds_is_offered_next_round_and_stays_on(
    stream: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ready(fastapi_app, db_session)
    mcp = _MCP(monkeypatch, SEARCH, CREATE)
    model = _Model(
        monkeypatch,
        _call(FIND, {"query": "search repositories"}),
        _call(SEARCH, {"query": "llm"}, call_id="call_2"),
        _say("Found none."),
        _say("ok"),
    )
    sid = await _session(db_session)

    r = await _ask(client, session_id=str(sid), stream=stream)
    reply = _streamed(r.text)[0] if stream else r.json()["choices"][0]["message"]["content"]
    assert reply == "Found none."
    assert [_tool_names(p) for p in model.sent] == [SYSTEM, [*SYSTEM, SEARCH], [*SYSTEM, SEARCH]]
    found = json.loads(model.sent[1]["messages"][-1]["content"])
    assert [t["name"] for t in found["switched_on"]] == [SEARCH]
    assert [c["qualified_name"] for c in mcp.calls] == [SEARCH]

    rows = await GatewayDAO(db_session).get_session_tool_overrides(sid)
    assert [(o.tool_id, o.enabled, o.updated_by) for o in rows] == [(SEARCH, True, "model")]
    await _ask(client, session_id=str(sid))
    assert _tool_names(model.sent[3]) == [*SYSTEM, SEARCH]


@pytest.mark.anyio
async def test_a_request_without_a_chat_finds_tools_for_itself(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No MCP tool up front, and what it finds is on for that request only."""
    await _ready(fastapi_app, db_session)
    mcp = _MCP(monkeypatch, SEARCH, CREATE)
    model = _Model(
        monkeypatch,
        _call(FIND, {"query": "create a repository"}),
        _call(CREATE, {"name": "x"}, call_id="call_2"),
        _say("Created."),
        _say("ok"),
    )

    await _ask(client)
    await _ask(client)
    assert [_tool_names(p) for p in model.sent] == [SYSTEM, [*SYSTEM, CREATE], [*SYSTEM, CREATE], SYSTEM]
    assert [c["qualified_name"] for c in mcp.calls] == [CREATE]


@pytest.mark.anyio
async def test_the_model_reads_the_clock(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offered with no MCP server at all: nothing to find, so no finder."""
    await _ready(fastapi_app, db_session)
    model = _Model(monkeypatch, _call(TIME, {"timezone": "Europe/Berlin"}), _say("It is afternoon."))

    r = await _ask(client)
    assert r.json()["choices"][0]["message"]["content"] == "It is afternoon."
    assert _tool_names(model.sent[0]) == [TIME]
    told = json.loads(model.sent[1]["messages"][-1]["content"])
    assert told["timezone"] == "Europe/Berlin"
    assert told["datetime"].endswith(("+01:00", "+02:00"))


@pytest.mark.anyio
async def test_the_tool_panel_shows_mcp_tools_off_until_switched_on(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off is not a problem to report: no reason under the switch."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, SEARCH, CREATE)
    sid = await _session(db_session)
    await _switch_on(db_session, sid, SEARCH)
    headers = {"Authorization": f"Bearer {_token()}"}

    chat = (await client.get("/v1/tools/available", params={"session_id": str(sid)}, headers=headers)).json()
    new_chat = (await client.get("/v1/tools/catalog", headers=headers)).json()
    assert {t["tool_id"]: (t["user_enabled"], t["availability_reason"]) for t in chat["tools"]} == {
        SEARCH: (True, None), CREATE: (False, None),
    }
    assert {t["tool_id"]: t["user_enabled"] for t in new_chat["tools"]} == {SEARCH: False, CREATE: False}


def _skill(**tools: list[str]) -> ResolvedSkill:
    return ResolvedSkill(
        skill_id="s1", name="s", slug="s", version=1, body_markdown="", priority=0, score=1, **tools,
    )


@pytest.mark.parametrize(
    ("skills", "allowed"),
    [
        ([], {SEARCH, CREATE, FIND}),
        ([_skill(forbidden_tools=[CREATE])], {SEARCH, FIND}),
        ([_skill(allowed_tools=[SEARCH])], {SEARCH, FIND}),  # the system tools stay
        ([_skill(forbidden_tools=[FIND])], {SEARCH, CREATE}),
    ],
)
def test_a_skill_decides_what_can_be_found(skills: list[ResolvedSkill], allowed: set[str]) -> None:
    from llm_port_api.services.gateway.service import _skills_allow  # noqa: PLC0415

    assert {name for name in (SEARCH, CREATE, FIND) if _skills_allow(skills, name)} == allowed


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_a_tool_the_model_invents_is_answered_not_sent_to_the_page(
    stream: bool,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 0.5B model called "add_numbers"; the chat page, which runs no tools, showed an empty reply."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, SEARCH)
    model = _Model(monkeypatch, _call("add_numbers", {"a": 17, "b": 25}), _say("Let me find one."))

    r = await _ask(client, stream=stream)
    reply = _streamed(r.text)[0] if stream else r.json()["choices"][0]["message"]["content"]
    assert reply == "Let me find one."
    assert model.sent[1]["messages"][-1]["content"] == (
        f"There is no tool named 'add_numbers'. Call {FIND} with what the tool should do to find one."
    )


@pytest.mark.anyio
async def test_a_tool_the_model_invents_finds_the_one_it_meant(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"add_numbers" is searched as "add numbers": get-sum is switched on, and called next round."""
    await _ready(fastapi_app, db_session)
    get_sum = "mcp.everything.get-sum"
    mcp = _MCP(monkeypatch, get_sum, "mcp.everything.echo", descriptions={
        get_sum: "Returns the sum of two numbers.", "mcp.everything.echo": "Echoes back the input.",
    })
    model = _Model(
        monkeypatch,
        _call("add_numbers", {"num1": 17, "num2": 25}),
        _call(get_sum, {"a": 17, "b": 25}, call_id="call_2"),
        _say("42"),
    )
    sid = await _session(db_session)

    await _ask(client, session_id=str(sid))
    told = json.loads(model.sent[1]["messages"][-1]["content"])
    assert told["error"] == "There is no tool named 'add_numbers'."
    assert [t["name"] for t in told["switched_on"]] == [get_sum]
    assert _tool_names(model.sent[1]) == [*SYSTEM, get_sum]
    assert [c["qualified_name"] for c in mcp.calls] == [get_sum]


@pytest.mark.anyio
async def test_a_tool_called_by_its_name_without_the_prefix_runs(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model told "use the get-sum tool" calls "get-sum", or "get_sum"."""
    await _ready(fastapi_app, db_session)
    mcp = _MCP(monkeypatch, "mcp.everything.get-sum", "mcp.other.echo", "mcp.everything.echo")
    model = _Model(
        monkeypatch,
        _call("get_sum", {"a": 17, "b": 25}),  # its name is "get-sum"
        _call("echo", {"message": "hi"}, call_id="call_2"),  # two servers have one: not guessed
        _say("42"),
    )
    sid = await _session(db_session)

    await _ask(client, session_id=str(sid))
    assert [c["qualified_name"] for c in mcp.calls] == ["mcp.everything.get-sum"]
    assert _tool_names(model.sent[1]) == [*SYSTEM, "mcp.everything.get-sum"]
    assert "There is no tool named 'echo'" in model.sent[2]["messages"][-1]["content"]


# ── Tools the question names ──────────────────────────────────────

EVERYTHING = [
    _entry(f"mcp.everything.{name}", "")
    for name in ("echo", "get-env", "get-tiny-image", "toggle-simulated-logging")  # get-sum is in GITHUB
]


@pytest.mark.parametrize(
    ("question", "picked"),
    [
        ("Use the get-sum tool to add 17 and 25.", ["mcp.everything.get-sum"]),
        ("Use the echo tool to echo: spindle check done.", ["mcp.everything.echo"]),
        ("Search my repositories for llm-port.", ["mcp.github.search_repositories"]),
        ("In three sentences, list the risks of skipping the spindle check.", []),  # "list" of list_issues
        ("Get me a summary of the plan.", []),  # "get" of every get-* tool
        ("", []),
    ],
)
def test_the_tools_a_question_names_are_picked(question: str, picked: list[str]) -> None:
    assert _names(system_tools.picked_for([*GITHUB, *EVERYTHING], question)) == picked


@pytest.mark.anyio
async def test_a_tool_the_question_names_is_offered_and_kept_when_used(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model gets it with its schema from the first round; unused, it is not kept."""
    await _ready(fastapi_app, db_session)
    get_sum, echo = "mcp.everything.get-sum", "mcp.everything.echo"
    mcp = _MCP(monkeypatch, echo, get_sum)
    model = _Model(monkeypatch, _call(get_sum, {"a": 17, "b": 25}), _say("42"), _say("Done."), _say("ok"))
    sid = await _session(db_session)

    def asked(text: str) -> list[dict[str, str]]:
        return [{"role": "user", "content": text}]

    await _ask(client, session_id=str(sid), messages=asked("Use the get-sum tool to add 17 and 25."))
    await _ask(client, session_id=str(sid), messages=asked("Use the echo tool to echo: done."))
    await _ask(client, session_id=str(sid), messages=asked("Thanks."))
    assert [_tool_names(p) for p in model.sent] == [
        [*SYSTEM, get_sum], [*SYSTEM, get_sum], [*SYSTEM, echo, get_sum], [*SYSTEM, get_sum],
    ]
    assert [c["qualified_name"] for c in mcp.calls] == [get_sum]
    rows = await GatewayDAO(db_session).get_session_tool_overrides(sid)
    assert [(o.tool_id, o.enabled) for o in rows] == [(get_sum, True)]
