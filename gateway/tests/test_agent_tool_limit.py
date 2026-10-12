"""An agent's "Server tools" limit holds where a tool runs, not only where it is offered.

``_agent_tools`` trimmed the tools the model was offered, but a model -- or text
the app feeds in -- can name a tool it was not offered, and the gateway ran any
MCP tool of the tenant with the admin's credentials. The limit is now checked
when a call runs, and a tool the limit rules out is not findable either.
"""

from __future__ import annotations

import json
import types
from typing import Any

import pytest

from faden_gateway.services.gateway import knowledge, system_tools
from faden_gateway.services.gateway.agents import AgentRequest
from faden_gateway.services.gateway.service import (
    GatewayService,
    _agent_refuses,
    _limit_to_agent,
    _Prepared,
)
from tests.test_agent_routes import _profile

SEARCH = "mcp.github.search_repositories"
CREATE = "mcp.github.create_repository"
JIRA = "mcp.jira.find"


def _req(server_tools: tuple[str, ...] | None | str = "chat", **kw: Any) -> _Prepared:
    """A prepared request; ``"chat"`` is a chat without an agent."""
    req = _Prepared(payload={}, **kw)
    req.system_tools = True
    req.knowledge = True
    req.mcp_findable = [{"qualified_name": n} for n in (SEARCH, CREATE, JIRA)]
    req.mcp_on = {SEARCH, CREATE}
    if server_tools != "chat":
        req.agent = AgentRequest(agent=_profile(server_tools=server_tools))
    return req


def _run(req: _Prepared, name: str) -> tuple[str, bool] | None:
    return object.__new__(GatewayService)._run_system_tool(req, name, {}, routed=False)


@pytest.mark.parametrize("name", [SEARCH, CREATE, JIRA, system_tools.FIND, system_tools.TIME, "knowledge_search"])
def test_the_limit_none_runs_no_gateway_tool_even_when_it_is_named(name: str) -> None:
    req = _req(())
    assert _agent_refuses(req, name)
    answer = _run(req, name)
    assert answer is not None
    content, is_error = answer
    assert is_error and "not available to this agent" in json.loads(content)["error"]


def test_a_partial_limit_runs_what_it_lists_and_refuses_the_rest() -> None:
    req = _req(("mcp.github.search_*", "knowledge_*"))
    assert _run(req, SEARCH) is None, "listed and on: runs (None = the caller runs it)"
    assert _run(req, "knowledge_search") is None
    for name in (CREATE, JIRA, system_tools.FIND, system_tools.TIME):
        answer = _run(req, name)
        assert answer is not None and answer[1] is True
        assert "not available to this agent" in answer[0]


def test_a_tool_the_limit_excludes_is_not_switched_on_by_a_search_either() -> None:
    req = _req(("mcp.github.search_*", system_tools.FIND))
    _limit_to_agent(req)
    assert [t["qualified_name"] for t in req.mcp_findable] == [SEARCH]
    assert req.mcp_on == {SEARCH}

    # A name the limit excludes is "no such tool" to the search: it finds only what is allowed.
    content, _ = GatewayService._find_tools(req, {"query": "create repository"})
    assert "create_repository" not in content
    assert req.mcp_on == {SEARCH}


def test_a_chat_without_an_agent_and_an_agent_without_a_limit_refuse_nothing() -> None:
    for req in (_req("chat"), _req(None)):
        for name in (SEARCH, CREATE, JIRA, system_tools.FIND, system_tools.TIME, "knowledge_search"):
            assert not _agent_refuses(req, name)
    assert _run(_req("chat"), CREATE) is None, "findable and on: runs as before"


def test_a_skills_own_files_stay_readable_under_a_limit() -> None:
    from faden_gateway.services.gateway import skill_files  # noqa: PLC0415

    assert not _agent_refuses(_req(()), skill_files.NAME)


def test_the_hosts_own_tools_are_not_the_limits_to_refuse() -> None:
    """Tools the client defined run on the client; the limit is about the gateway's tools."""
    assert not _agent_refuses(_req(()), "read_page")


@pytest.mark.anyio
async def test_a_forced_call_is_answered_with_an_error_and_never_reaches_mcp() -> None:
    """The whole call path, as the tool loop runs it: the MCP service is not asked."""
    asked: list[str] = []

    class _MCP:
        async def call_tool(self, **kwargs: Any) -> Any:
            asked.append(kwargs["qualified_name"])
            return types.SimpleNamespace(content="[]", is_error=False)

    service = object.__new__(GatewayService)
    service.tool_router = None  # type: ignore[assignment]
    service.mcp_client = _MCP()  # type: ignore[assignment]
    service.pii_client = None  # type: ignore[assignment]
    service.rag_lite_client = None
    req = _req(())
    req.decision = None
    calls = [
        {"id": "c1", "function": {"name": CREATE, "arguments": "{}"}},
        {"id": "c2", "function": {"name": knowledge.NAMES and sorted(knowledge.NAMES)[0], "arguments": "{}"}},
    ]
    answers = await service._run_tool_calls(  # noqa: SLF001
        req, calls, iteration=0, auth=types.SimpleNamespace(tenant_id="t", user_id="u"), request_id="r",
    )
    assert asked == []
    assert answers is not None and [a["tool_call_id"] for a in answers] == ["c1", "c2"]
    assert all("not available to this agent" in a["content"] for a in answers)
    assert [row["is_error"] for row in req.tool_calls] == [True, True]


@pytest.mark.anyio
async def test_a_call_the_limit_allows_still_reaches_mcp() -> None:
    asked: list[str] = []

    class _MCP:
        async def call_tool(self, **kwargs: Any) -> Any:
            asked.append(kwargs["qualified_name"])
            return types.SimpleNamespace(content="[]", is_error=False)

    service = object.__new__(GatewayService)
    service.tool_router = None  # type: ignore[assignment]
    service.mcp_client = _MCP()  # type: ignore[assignment]
    service.pii_client = None  # type: ignore[assignment]
    req = _req(("mcp.github.*",))
    answers = await service._run_tool_calls(  # noqa: SLF001
        req, [{"id": "c1", "function": {"name": SEARCH, "arguments": "{}"}}], iteration=0,
        auth=types.SimpleNamespace(tenant_id="t", user_id="u"), request_id="r",
    )
    assert asked == [SEARCH] and answers is not None and answers[0]["content"] == "[]"
