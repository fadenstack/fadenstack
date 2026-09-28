"""The agent routes: ``/v1/agents/{agent}/...``.

An agent token works on its own agent only, and only there; access follows
the agent's grants; the agent's version shapes the request (instructions
first, its model, its host tool contract); the request runs through the same
pipeline as ``/v1/chat/completions`` with no server session, and answers in
the OpenAI shapes, streamed or whole. The request log names the agent, device
and conversation -- nothing of the conversation itself is kept.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import AgentClientEvent, ChatSession, LLMGatewayRequestLog, ProviderType
from faden_gateway.services.extensions import registry
from faden_gateway.services.gateway.agents import (
    AgentDirectory,
    AgentLookupError,
    AgentProfile,
    AgentRequest,
    TtlCache,
    UserFlags,
)
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from faden_gateway.services.gateway.service import GatewayService, _agent_tools, _Prepared
from faden_gateway.services.gateway.usage_group import UsageGroupResolver
from tests.test_gateway_pipeline_combined import ALIAS, TEST_JWT_SECRET, _chunk, _Observability, _seed

AGENT_ID = str(uuid.uuid4())
OTHER_AGENT_ID = str(uuid.uuid4())
USER = "11111111-1111-1111-1111-111111111111"
MAINTAINER = "22222222-2222-2222-2222-222222222222"
TEAM = "33333333-3333-3333-3333-333333333333"
INSTRUCTIONS = "You help with EPLAN pages. Answer briefly."
ANSWER = "Page 12 holds the motor circuit."
USAGE = {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28}


def _profile(**overrides: Any) -> AgentProfile:
    fields: dict[str, Any] = {
        "id": AGENT_ID,
        "tenant_id": "tenant-a",
        "slug": "eplan-assistant",
        "name": "EPLAN assistant",
        "status": "published",
        "published": True,
        "version": 3,
        "instructions": INSTRUCTIONS,
        "model_alias": ALIAS,
        "knowledge": (),
        "server_tools": None,
        "host_tool_contract": ({"name": "read_page", "risk": "read"},),
        "local_mcp_mode": "disable",
        "ui": {"greeting": "Hi"},
        "extensions": {},
        "maintainers": frozenset({MAINTAINER}),
        "grant_users": frozenset({USER}),
        "grant_groups": frozenset(),
        "instructions_sha256": "abc",
    }
    fields.update(overrides)
    return AgentProfile(**fields)


class _Backend:
    """The backend database, as the directory reads it."""

    def __init__(self) -> None:
        self.agents: dict[str, AgentProfile] = {}
        self.users: dict[str, UserFlags] = {
            USER: UserFlags(is_active=True, is_superuser=False),
            MAINTAINER: UserFlags(is_active=True, is_superuser=False),
        }
        self.groups: dict[str, tuple[str, ...]] = {}
        self.agent_reads = 0
        self.fail = False

    def put(self, profile: AgentProfile) -> None:
        self.agents[profile.slug] = self.agents[profile.id] = profile

    async def agent(self, key: tuple[str, str]) -> AgentProfile | None:
        self.agent_reads += 1
        if self.fail:
            raise AgentLookupError("backend database down")
        profile = self.agents.get(key[1])
        return profile if profile is not None and profile.tenant_id == key[0] else None

    async def user(self, user_id: str) -> UserFlags | None:
        return self.users.get(user_id)

    async def memberships(self, user_id: str) -> tuple[str, ...]:
        return self.groups.get(user_id, ())


@pytest.fixture
def backend(fastapi_app: FastAPI) -> _Backend:
    fake = _Backend()
    fake.put(_profile())
    fastapi_app.state.agent_directory = AgentDirectory(fetch_agent=fake.agent, fetch_user=fake.user)
    fastapi_app.state.group_membership_resolver = UsageGroupResolver(fake.memberships, ttl_sec=60.0)
    fastapi_app.state.gateway_observability = _Observability()
    return fake


@pytest.fixture
def clean_registry() -> Iterator[None]:
    saved = (list(registry.agent_access_rules), set(registry.agent_extensions), list(registry.required_client_modules))
    yield
    registry.agent_access_rules[:] = saved[0]
    registry.agent_extensions.clear()
    registry.agent_extensions.update(saved[1])
    registry.required_client_modules[:] = saved[2]


def _agent_token(agent_id: str = AGENT_ID, *, sub: str = USER, **extra: Any) -> str:
    now = int(time.time())
    claims = {
        "sub": sub, "tenant_id": "tenant-a", "aud": f"agent:{agent_id}", "typ": "agent",
        "did": "device-7", "dev": False, "iat": now, "exp": now + 600, **extra,
    }
    return jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS256")


def _login_token(sub: str) -> str:
    return jwt.encode({"sub": sub, "tenant_id": "tenant-a"}, TEST_JWT_SECRET, algorithm="HS256")


def _headers(token: str | None = None, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or _agent_token()}", **extra}


def _model(monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, Any]], *, fail_after: int | None = None) -> None:
    """The model answers ANSWER in pieces, with usage last, and keeps what it was sent."""

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        sent.append(kwargs["payload"])
        if kwargs.get("stream"):
            async def pieces() -> AsyncIterator[bytes]:
                for n, i in enumerate(range(0, len(ANSWER), 4)):
                    if fail_after is not None and n == fail_after:
                        raise ConnectionError("upstream reset")
                    yield _chunk(ANSWER[i:i + 4])
                done = {"id": "c", "object": "chat.completion.chunk", "model": ALIAS,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                yield f"data: {json.dumps(done)}\n\n".encode()
                usage = {"id": "c", "object": "chat.completion.chunk", "model": ALIAS, "choices": [], "usage": USAGE}
                yield f"data: {json.dumps(usage)}\n\n".encode()
                yield b"data: [DONE]\n\n"
            return pieces()
        return CompletionResult(status_code=200, payload={
            "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": ALIAS,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER}, "finish_reason": "stop"}],
            "usage": USAGE,
        })

    monkeypatch.setattr(LLMAdapter, "completion", completion)


def _events(sse: str) -> list[Any]:
    """The data lines of an SSE body, decoded; ``[DONE]`` as the string."""
    out: list[Any] = []
    for line in sse.splitlines():
        if line.startswith("data: "):
            data = line[6:]
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


def _text(events: list[Any]) -> str:
    return "".join(
        (choice.get("delta") or {}).get("content") or ""
        for event in events if isinstance(event, dict)
        for choice in event.get("choices", [])
    )


def _logged_stream(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    async def log(self: GatewayService, **fields: Any) -> None:
        rows.append(fields)

    monkeypatch.setattr(GatewayService, "_audit_on_own_session", log)
    return rows


async def _until(condition: Any, seconds: float = 2.0) -> None:
    for _ in range(int(seconds / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)


def _chat(**body: Any) -> dict[str, Any]:
    return {"model": "whatever-the-client-says", "messages": [{"role": "user", "content": "What is on page 12?"}], **body}


URL = "/v1/agents/eplan-assistant/chat/completions"


# -- the answer ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_whole_answer_is_an_openai_completion_with_usage(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    sent: list[dict[str, Any]] = []
    _model(monkeypatch, sent)

    r = await client.post(URL, headers=_headers(**{"X-Faden-Conversation-Id": "conv-1", "X-Faden-Turn-Id": "t-4"}),
                          json=_chat())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": ANSWER}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == USAGE
    assert r.headers["x-request-id"]

    upstream = sent[0]
    assert upstream["model"] == ALIAS, "the agent's model, not the client's"
    contents = [m["content"] for m in upstream["messages"]]
    assert INSTRUCTIONS in contents, "the agent's instructions reach the model"
    assert contents.index(INSTRUCTIONS) < contents.index("What is on page 12?"), "before the conversation"
    assert "session_id" not in upstream

    row = (await db_session.execute(select(LLMGatewayRequestLog))).scalar_one()
    assert (row.agent_id, row.device_id, row.conversation_id, row.turn_id) == (AGENT_ID, "device-7", "conv-1", "t-4")
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (21, 7, 28)
    assert row.session_id is None
    assert (await db_session.execute(select(ChatSession))).first() is None, "no server session"


@pytest.mark.anyio
async def test_a_streamed_answer_arrives_whole_with_usage_and_done(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    _model(monkeypatch, [])
    logged = _logged_stream(monkeypatch)

    r = await client.post(URL, headers=_headers(**{"X-Faden-Conversation-Id": "conv-2"}), json=_chat(stream=True))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)
    assert _text(events) == ANSWER, "not a character lost"
    assert events[-1] == "[DONE]" and events.count("[DONE]") == 1
    chunks = [e for e in events if isinstance(e, dict)]
    assert all(e["object"] == "chat.completion.chunk" for e in chunks)
    assert chunks[-1]["usage"] == USAGE, "usage in the last chunk, as OpenAI sends it"
    assert not any("error" in e for e in chunks)

    await _until(lambda: logged)
    assert logged[0]["agent_id"] == AGENT_ID and logged[0]["conversation_id"] == "conv-2"
    assert logged[0]["total_tokens"] == 28
    assert logged[0]["status_code"] == 200


@pytest.mark.anyio
async def test_a_stream_that_breaks_says_so_before_done(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    _model(monkeypatch, [], fail_after=2)
    logged = _logged_stream(monkeypatch)

    for url, token in ((URL, _agent_token()), ("/v1/chat/completions", _login_token(USER))):
        r = await client.post(url, headers=_headers(token), json=_chat(model=ALIAS, stream=True))
        events = _events(r.text)
        assert _text(events) == ANSWER[:8], "what came before the break still arrives"
        assert events[-2]["error"]["code"] == "upstream_stream_failed", "the break is said, not hidden"
        assert events[-1] == "[DONE]"
    await _until(lambda: len(logged) == 2)
    assert {row["status_code"] for row in logged} == {502}


@pytest.mark.anyio
async def test_the_agent_endpoint_keeps_no_session(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    _model(monkeypatch, [])
    r = await client.post(URL, headers=_headers(), json=_chat(session_id=str(uuid.uuid4())))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "session_not_supported"


# -- tokens and access ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_token_for_another_agent_is_refused(client: AsyncClient, backend: _Backend) -> None:
    backend.put(_profile(id=OTHER_AGENT_ID, slug="tia-assistant"))
    r = await client.post(URL, headers=_headers(_agent_token(OTHER_AGENT_ID)), json=_chat())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "agent_mismatch"


@pytest.mark.anyio
async def test_an_agent_token_is_refused_on_the_plain_routes(client: AsyncClient, backend: _Backend) -> None:
    for method, url in (("post", "/v1/chat/completions"), ("get", "/v1/models")):
        r = await client.request(method, url, headers=_headers(), json=_chat() if method == "post" else None)
        assert r.status_code == 403, url
        assert r.json()["error"]["code"] == "agent_token_not_allowed"


@pytest.mark.anyio
async def test_other_tokens_work_for_maintainers_only(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    _model(monkeypatch, [])
    r = await client.post(URL, headers=_headers(_login_token(USER)), json=_chat())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "agent_token_required"
    r = await client.post(URL, headers=_headers(_login_token(MAINTAINER)), json=_chat())
    assert r.status_code == 200, r.text


@pytest.mark.anyio
async def test_access_follows_grants_and_the_agents_state(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    _model(monkeypatch, [])
    cases = [
        (_profile(grant_users=frozenset()), 403, "agent_not_granted"),
        (_profile(grant_users=frozenset(), grant_groups=frozenset({TEAM})), 200, None),
        (_profile(status="retired"), 403, "agent_retired"),
        (_profile(status="draft", published=False), 403, "agent_not_published"),
        (_profile(extensions={"pii": {"mode": "device_only"}}), 403, "agent_needs_enterprise"),
    ]
    backend.groups[USER] = (TEAM,)
    for profile, code, error in cases:
        backend.put(profile)
        client_app = client._transport.app  # type: ignore[attr-defined]  # noqa: SLF001
        client_app.state.agent_directory.invalidate()
        r = await client.post(URL, headers=_headers(), json=_chat())
        assert r.status_code == code, (profile, r.text)
        if error:
            assert r.json()["error"]["code"] == error


@pytest.mark.anyio
async def test_a_deactivated_user_is_refused(client: AsyncClient, backend: _Backend) -> None:
    backend.users[USER] = UserFlags(is_active=False, is_superuser=False)
    r = await client.post(URL, headers=_headers(), json=_chat())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "user_inactive"


@pytest.mark.anyio
async def test_enterprise_rules_and_extensions_plug_in(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
    clean_registry: None,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    _model(monkeypatch, [])
    backend.put(_profile(extensions={"pii": {"mode": "device_only"}}))
    registry.agent_extensions.add("pii")
    registry.required_client_modules.append(
        lambda ext: ["pii.local"] if ext.get("pii", {}).get("mode") == "device_only" else [],
    )
    r = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers())
    assert r.status_code == 200
    assert r.json()["required_client_modules"] == ["pii.local"]

    async def exclude(user_id: str, agent_id: str) -> str | None:
        return "Excluded from this agent." if user_id == USER else None

    registry.agent_access_rules.append(exclude)
    r = await client.post(URL, headers=_headers(), json=_chat())
    assert r.status_code == 403
    assert r.json()["error"] == {
        "message": "Excluded from this agent.", "type": "permission_error",
        "param": None, "code": "agent_access_denied",
    }


@pytest.mark.anyio
async def test_unknown_agents_and_a_lost_backend_are_told_apart(client: AsyncClient, backend: _Backend) -> None:
    r = await client.post("/v1/agents/nope/chat/completions", headers=_headers(), json=_chat())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "agent_not_found"
    backend.fail = True
    r = await client.post("/v1/agents/other/chat/completions", headers=_headers(), json=_chat())
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "agent_lookup_failed"


@pytest.mark.anyio
async def test_a_bad_conversation_id_is_refused(client: AsyncClient, backend: _Backend) -> None:
    r = await client.post(URL, headers=_headers(**{"X-Faden-Conversation-Id": "bad id!"}), json=_chat())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_header"


# -- what the agent's version does --------------------------------------------------------


@pytest.mark.anyio
async def test_the_callers_tools_must_be_in_the_host_tool_contract(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    sent: list[dict[str, Any]] = []
    _model(monkeypatch, sent)

    def tool(name: str) -> dict[str, Any]:
        return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}

    r = await client.post(URL, headers=_headers(), json=_chat(tools=[tool("read_page"), tool("delete_page")]))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "tool_not_in_contract"
    assert "delete_page" in r.json()["error"]["message"]

    r = await client.post(URL, headers=_headers(), json=_chat(tools=[tool("read_page")]))
    assert r.status_code == 200, r.text
    names = [t["function"]["name"] for t in sent[-1]["tools"]]
    assert "read_page" in names and "delete_page" not in names


def test_only_the_gateway_tools_the_agent_allows_are_offered() -> None:
    def tool(name: str) -> dict[str, Any]:
        return {"type": "function", "function": {"name": name}}

    payload = {"tools": [tool("read_page"), tool("current_time"), tool("knowledge_search"), tool("mcp.jira.find")],
               "tool_choice": "auto"}
    req = _Prepared(payload=payload, client_tools={"read_page"})

    req.agent = AgentRequest(agent=_profile(server_tools=("knowledge_*", "mcp.jira.*")))
    names = [t["function"]["name"] for t in _agent_tools(payload, req)["tools"]]
    assert names == ["read_page", "knowledge_search", "mcp.jira.find"]

    req.agent = AgentRequest(agent=_profile(server_tools=None))
    assert _agent_tools(payload, req) is payload, "no list: what a chat gets"

    req = _Prepared(payload=payload, client_tools=set())
    req.agent = AgentRequest(agent=_profile(server_tools=()))
    assert "tools" not in _agent_tools(payload, req) and "tool_choice" not in _agent_tools(payload, req)


@pytest.mark.anyio
async def test_the_profile_carries_what_the_sdk_caches_not_the_instructions(
    client: AsyncClient, backend: _Backend,
) -> None:
    r = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers(_agent_token(dev=True)))
    assert r.status_code == 200
    profile = r.json()
    assert profile["id"] == AGENT_ID and profile["version"] == 3 and profile["model"] == ALIAS
    assert INSTRUCTIONS not in json.dumps(profile)
    assert profile["instructions_sha256"] == "abc"
    assert profile["host_tool_contract"] == [{"name": "read_page", "risk": "read"}]
    assert profile["local_mcp"] == {"mode": "disable", "developer_mode": True}
    assert profile["required_client_modules"] == []
    assert profile["edition"] == "community" and "agents" in profile["features"]


@pytest.mark.anyio
async def test_the_models_are_the_agents_model(client: AsyncClient, backend: _Backend) -> None:
    r = await client.get("/v1/agents/eplan-assistant/models", headers=_headers())
    assert r.status_code == 200
    assert r.json() == {"object": "list", "data": [{"id": ALIAS, "object": "model", "created": 0, "owned_by": "faden"}]}


# -- events and features ------------------------------------------------------------------------


@pytest.mark.anyio
async def test_events_are_stored_without_content(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend,
) -> None:
    body = {"events": [
        {"type": "tool.executed", "tool": "read_page", "source": "host", "duration_ms": 42, "outcome": "ok"},
        {"type": "approval.decided", "tool": "write_page", "decision": "denied", "wait_ms": 3000},
        {"type": "session.stats", "turns": 7, "conversation_id": "conv-9"},
    ]}
    r = await client.post("/v1/agents/eplan-assistant/events", headers=_headers(), json=body)
    assert r.status_code == 202, r.text
    assert r.json() == {"accepted": 3}
    rows = (await db_session.execute(select(AgentClientEvent).order_by(AgentClientEvent.type))).scalars().all()
    assert [(e.type, e.agent_id, e.user_id, e.device_id) for e in rows] == [
        ("approval.decided", AGENT_ID, USER, "device-7"),
        ("session.stats", AGENT_ID, USER, "device-7"),
        ("tool.executed", AGENT_ID, USER, "device-7"),
    ]
    assert rows[2].data == {"tool": "read_page", "source": "host", "server": None, "duration_ms": 42, "outcome": "ok"}
    assert rows[1].conversation_id == "conv-9"

    for bad in (
        {"type": "tool.executed", "tool": "x", "duration_ms": 1, "outcome": "ok", "arguments": {"page": 12}},
        {"type": "chat.message", "content": "hello"},
    ):
        r = await client.post("/v1/agents/eplan-assistant/events", headers=_headers(), json={"events": [bad]})
        assert r.status_code == 400, bad
        assert r.json()["error"]["code"] == "invalid_events"


@pytest.mark.anyio
async def test_features_answer_any_signed_in_caller(client: AsyncClient, backend: _Backend) -> None:
    for token in (_agent_token(), _login_token(USER)):
        r = await client.get("/v1/features", headers=_headers(token))
        assert r.status_code == 200
        assert r.json()["edition"] == "community"
        assert {"agents", "streaming"} <= set(r.json()["features"])
    assert (await client.get("/v1/features")).status_code == 401


# -- cost -------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_agent_is_read_once_not_per_request(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    _model(monkeypatch, [])
    for _ in range(20):
        r = await client.post(URL, headers=_headers(), json=_chat())
        assert r.status_code == 200
    assert backend.agent_reads == 1


@pytest.mark.anyio
async def test_concurrent_misses_share_one_read_and_failures_are_not_kept() -> None:
    reads = 0
    fail = True

    async def fetch(key: str) -> str:
        nonlocal reads
        reads += 1
        await asyncio.sleep(0.01)
        if fail:
            raise AgentLookupError("down")
        return key.upper()

    cache: TtlCache[str, str] = TtlCache(fetch)
    results = await asyncio.gather(*(cache.get("a") for _ in range(10)), return_exceptions=True)
    assert reads == 1 and all(isinstance(r, AgentLookupError) for r in results)
    fail = False
    assert await cache.get("a") == "A", "the failure was not cached"
    assert await cache.get("a") == "A"
    assert reads == 2
