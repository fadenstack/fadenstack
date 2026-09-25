"""A chat's tool switches decide what the model gets and what runs.

The chat's tool panel showed one GitHub tool of 48 switched on, and the model
was given all 48 -- ~14.7k prompt tokens a turn -- because the gateway merged
the tenant's whole MCP catalog into every request (release VM, 2026-09-25).
A tool switched off also ran when the model named it: the tool router checked
the execution mode and the policy, not the switch.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.db.models.gateway import (
    ChatSession,
    ExecutionMode,
    ProviderType,
    SessionToolOverride,
)
from llm_port_api.services.gateway.mcp_client import MCPClient, ToolCallResult, ToolCatalog
from llm_port_api.services.registry import service_registry
from llm_port_api.settings import settings
from tests.test_gateway_knowledge_tools import (
    CAN_CALL_TOOLS,
    _ask,
    _call,
    _Model,
    _say,
    _tool_names,
)
from tests.test_gateway_pipeline_combined import _Observability, _seed, _session, _token

SEARCH = "mcp.github.search_repositories"
CREATE = "mcp.github.create_repository"
SERVICE_TOKEN = "service-token"


class _MCP:
    """The MCP service, as the gateway reaches it: a catalog and tool calls."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
        self.calls: list[dict[str, Any]] = []
        mcp = self
        catalog = [
            {
                "qualified_name": name,
                "openai_tool": {
                    "type": "function",
                    "function": {"name": name, "description": name, "parameters": {"type": "object"}},
                },
                "realm": "mcp_remote",
            }
            for name in names
        ]
        registry_url = service_registry.get_url
        monkeypatch.setattr(
            service_registry, "get_url",
            lambda name: "http://mcp.local" if name == "mcp" else registry_url(name),
        )
        monkeypatch.setattr(settings, "mcp_service_token", SERVICE_TOKEN)
        monkeypatch.setattr(settings, "rag_lite_enabled", False)

        async def version(self: MCPClient, tenant_id: str) -> int:
            return 1

        async def tool_catalog(self: MCPClient, tenant_id: str) -> ToolCatalog:
            return ToolCatalog(version=1, tools=catalog)

        async def call_tool(self: MCPClient, **kwargs: Any) -> ToolCallResult:
            mcp.calls.append(kwargs)
            return ToolCallResult(content="[]")

        monkeypatch.setattr(MCPClient, "get_catalog_version", version)
        monkeypatch.setattr(MCPClient, "get_tool_catalog", tool_catalog)
        monkeypatch.setattr(MCPClient, "call_tool", call_tool)


async def _switch_off(db: AsyncSession, sid: uuid.UUID, *tool_ids: str) -> None:
    db.add_all(
        SessionToolOverride(session_id=sid, tool_id=tool_id, enabled=False, updated_by="user-1")
        for tool_id in tool_ids
    )
    await db.flush()


async def _ready(fastapi_app: FastAPI, db: AsyncSession) -> None:
    await _seed(db, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()


@pytest.mark.anyio
async def test_the_model_gets_only_the_tools_the_chat_has_on(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One switched off: the other goes to the model; a chat with none off gets both."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, SEARCH, CREATE)
    model = _Model(monkeypatch, _say("a"), _say("b"))
    narrowed, untouched = await _session(db_session), await _session(db_session)
    await _switch_off(db_session, narrowed, CREATE)

    assert (await _ask(client, session_id=str(narrowed))).status_code == 200
    assert (await _ask(client, session_id=str(untouched))).status_code == 200
    assert _tool_names(model.sent[0]) == [SEARCH]
    assert sorted(_tool_names(model.sent[1])) == [CREATE, SEARCH]


@pytest.mark.anyio
async def test_a_chat_with_every_tool_off_sends_none(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No tools, and no tool_choice forced on the model either."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, SEARCH, CREATE)
    model = _Model(monkeypatch, _say("a"))
    sid = await _session(db_session)
    await _switch_off(db_session, sid, SEARCH, CREATE)

    await _ask(client, session_id=str(sid))
    assert _tool_names(model.sent[0]) == []
    assert "tool_choice" not in model.sent[0]


@pytest.mark.anyio
async def test_a_local_only_chat_gets_no_server_tools(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The execution mode filters as the panel shows it."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, SEARCH)
    model = _Model(monkeypatch, _say("a"))
    sid = await _session(db_session)
    chat = await db_session.get(ChatSession, sid)
    assert chat is not None
    chat.execution_mode = ExecutionMode.LOCAL_ONLY
    await db_session.flush()

    await _ask(client, session_id=str(sid))
    assert _tool_names(model.sent[0]) == []


@pytest.mark.anyio
async def test_a_switched_off_tool_the_model_names_anyway_does_not_run(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named from an earlier turn, or guessed: refused, and the model is told."""
    await _ready(fastapi_app, db_session)
    mcp = _MCP(monkeypatch, SEARCH, CREATE)
    model = _Model(monkeypatch, _call(CREATE, {"name": "x"}), _say("I can't create it here."))
    sid = await _session(db_session)
    await _switch_off(db_session, sid, CREATE)

    assert (await _ask(client, session_id=str(sid))).status_code == 200
    assert mcp.calls == []
    told = model.sent[1]["messages"][-1]
    assert told["role"] == "tool"
    assert "switched off in this chat" in told["content"]


@pytest.mark.anyio
async def test_a_made_up_tool_is_named_as_one_to_the_model() -> None:
    """MCP's 404 "Tool not found." reaches the model as what it is."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Tool not found."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        mcp = MCPClient(base_url="http://mcp.local", http_client=http, service_token=SERVICE_TOKEN)
        result = await mcp.call_tool(
            qualified_name="mcp.github.check_repo_lifecycle", arguments={},
            tenant_id="tenant-a", request_id="r1",
        )
    assert result.is_error
    assert result.content == (
        "There is no tool named 'mcp.github.check_repo_lifecycle'. "
        "Call only the tools you were given."
    )


@pytest.mark.anyio
async def test_any_other_failure_carries_the_service_reason() -> None:
    """A disabled tool, an upstream error: the reason, not a bare status code."""

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Tool is disabled."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        mcp = MCPClient(base_url="http://mcp.local", http_client=http, service_token=SERVICE_TOKEN)
        result = await mcp.call_tool(
            qualified_name=CREATE, arguments={}, tenant_id="tenant-a", request_id="r1",
        )
    assert result.content == f"Tool '{CREATE}' failed: Tool is disabled."


@pytest.mark.anyio
async def test_a_tool_named_twice_in_one_update_takes_the_last_value(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
) -> None:
    """It was inserted twice: the request answered 500 and switched nothing."""
    sid = await _session(db_session)
    r = await client.patch(
        f"/v1/sessions/{sid}/tool-policy",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"tool_overrides": [
            {"tool_id": SEARCH, "enabled": True},
            {"tool_id": CREATE, "enabled": False},
            {"tool_id": SEARCH, "enabled": False},
        ]},
    )
    assert r.status_code == 200
    rows = await GatewayDAO(db_session).get_session_tool_overrides(sid)
    assert sorted((o.tool_id, o.enabled) for o in rows) == [(CREATE, False), (SEARCH, False)]


@pytest.mark.parametrize(
    ("called", "runs"),
    [
        ("mcp.everything.get-sum", "mcp.everything.get-sum"),  # was cut to "mcp.everything.get"
        ("knowledge_search<|channel|>commentary", "knowledge_search"),  # what some models append
        ("mcp.github.search_repositories", "mcp.github.search_repositories"),
    ],
)
def test_a_tool_is_run_by_its_whole_name(called: str, runs: str) -> None:
    """Hyphens are part of a name; a model's appended markup is not."""
    from llm_port_api.services.gateway.service import _tool_name  # noqa: PLC0415

    assert _tool_name({"function": {"name": called}}) == runs


@pytest.mark.anyio
async def test_a_hyphenated_mcp_tool_is_called_by_its_name(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP service is asked for the tool the model named, whole."""
    await _ready(fastapi_app, db_session)
    mcp = _MCP(monkeypatch, "mcp.everything.get-sum")
    _Model(monkeypatch, _call("mcp.everything.get-sum", {"a": 17, "b": 25}), _say("42"))
    sid = await _session(db_session)

    assert (await _ask(client, session_id=str(sid))).status_code == 200
    assert [call["qualified_name"] for call in mcp.calls] == ["mcp.everything.get-sum"]


# ── The tool exchange stays with the reply ────────────────────────


def _roles(messages: list[dict[str, Any]]) -> list[str]:
    return [
        f"assistant+calls({m['tool_calls'][0]['function']['name']})" if m.get("tool_calls") else m["role"]
        for m in messages if m["role"] != "system"
    ]


@pytest.mark.anyio
async def test_the_next_turn_sees_the_tool_call_and_its_result(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the reply's text alone, chats stopped calling tools from their third turn."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, "mcp.everything.get-sum")
    model = _Model(monkeypatch, _call("mcp.everything.get-sum", {"a": 17, "b": 25}), _say("It is 42."), _say("ok"))
    sid = await _session(db_session)

    await _ask(client, session_id=str(sid), messages=[{"role": "user", "content": "Add 17 and 25."}])
    await _ask(client, session_id=str(sid), messages=[{"role": "user", "content": "And now?"}])

    second_turn = model.sent[2]["messages"]
    assert _roles(second_turn) == [
        "user", "assistant+calls(mcp.everything.get-sum)", "tool", "assistant", "user",
    ]
    tool = next(m for m in second_turn if m["role"] == "tool")
    call = next(m for m in second_turn if m.get("tool_calls"))
    assert tool["tool_call_id"] == call["tool_calls"][0]["id"]


@pytest.mark.anyio
async def test_a_turn_without_tools_gets_the_history_without_them(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A template may refuse tool messages in a request that offers no tools."""
    await _ready(fastapi_app, db_session)
    _MCP(monkeypatch, "mcp.everything.get-sum")
    model = _Model(monkeypatch, _call("mcp.everything.get-sum", {"a": 1, "b": 2}), _say("3"), _say("ok"))
    sid = await _session(db_session)

    await _ask(client, session_id=str(sid), messages=[{"role": "user", "content": "Add 1 and 2."}])
    await _switch_off(db_session, sid, "mcp.everything.get-sum")
    await _ask(client, session_id=str(sid), messages=[{"role": "user", "content": "Thanks."}])

    assert _roles(model.sent[2]["messages"]) == ["user", "assistant", "user"]


def test_a_long_tool_result_is_kept_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """One GitHub response can be tens of kilobytes; the history keeps the start."""
    from llm_port_api.services.gateway.service import _exchange_record  # noqa: PLC0415

    monkeypatch.setattr(settings, "session_tool_result_max_chars", 10)
    record = _exchange_record([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "0123456789abcdef"},
    ])
    assert record is not None
    assert record["exchange"][1]["content"] == "0123456789 [truncated]"
    assert _exchange_record([]) is None
