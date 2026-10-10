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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from faden_gateway.db.models.gateway import (
    AgentClientEvent,
    AgentClientManifest,
    ChatSession,
    LLMGatewayRequestLog,
    LLMPoolMembership,
    LLMProviderInstance,
    ProviderType,
    TenantLLMPolicy,
)
from faden_gateway.services.extensions import registry
from faden_gateway.services.gateway import agent_manifests
from faden_gateway.services.gateway.agents import (
    AgentDirectory,
    AgentLookupError,
    AgentProfile,
    AgentRequest,
    ApiTokenRecord,
    TtlCache,
    UserFlags,
)
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from faden_gateway.services.gateway.service import GatewayService, _agent_tools, _Prepared
from faden_gateway.services.gateway.usage_group import UsageGroupResolver
from faden_gateway.services.registry import service_registry
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    TEST_JWT_SECRET,
    _chunk,
    _install_pii,
    _Observability,
    _pii,
    _seed,
)

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


def _api_token(sub: str, jti: str) -> str:
    now = int(time.time())
    claims = {"sub": sub, "tenant_id": "tenant-a", "typ": "api", "jti": jti, "iat": now, "exp": now + 600}
    return jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS256")


@pytest.mark.anyio
async def test_a_revoked_or_expired_api_token_is_refused_here_too(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,
) -> None:
    """A maintainer's API token is checked against its row as on the plain routes."""
    rows = {
        "live": ApiTokenRecord(MAINTAINER, revoked=False, tenant_id="tenant-a"),
        "revoked": ApiTokenRecord(MAINTAINER, revoked=True, tenant_id="tenant-a"),
        "expired": ApiTokenRecord(MAINTAINER, revoked=False, expired=True, tenant_id="tenant-a"),
    }
    ids = {name: str(uuid.uuid4()) for name in rows}

    async def fetch_api_token(token_id: str) -> ApiTokenRecord | None:
        return next((rows[name] for name, jti in ids.items() if jti == token_id), None)

    fastapi_app.state.agent_directory = AgentDirectory(
        fetch_agent=backend.agent, fetch_user=backend.user, fetch_api_token=fetch_api_token,
    )
    for path in ("/v1/agents/eplan-assistant/profile", "/v1/agents/eplan-assistant/models"):
        for name, code in (("revoked", "api_token_revoked"), ("expired", "api_token_expired")):
            r = await client.get(path, headers=_headers(_api_token(MAINTAINER, ids[name])))
            assert r.status_code == 401, (path, name, r.text)
            assert r.json()["error"]["code"] == code
        r = await client.get(path, headers=_headers(_api_token(MAINTAINER, str(uuid.uuid4()))))
        assert (r.status_code, r.json()["error"]["code"]) == (401, "api_token_revoked"), "a token with no row"
        r = await client.get(path, headers=_headers(_api_token(MAINTAINER, ids["live"])))
        assert r.status_code == 200, (path, r.text)


@pytest.mark.anyio
async def test_an_api_tokens_tenant_is_its_rows_not_its_claims(
    fastapi_app: FastAPI, client: AsyncClient, backend: _Backend,
) -> None:
    """The claim names the agent's tenant, the row another: the row decides."""
    jti = str(uuid.uuid4())

    async def fetch_api_token(token_id: str) -> ApiTokenRecord | None:
        return ApiTokenRecord(MAINTAINER, revoked=False, tenant_id="default") if token_id == jti else None

    fastapi_app.state.agent_directory = AgentDirectory(
        fetch_agent=backend.agent, fetch_user=backend.user, fetch_api_token=fetch_api_token,
    )
    r = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers(_api_token(MAINTAINER, jti)))
    assert (r.status_code, r.json()["error"]["code"]) == (404, "agent_not_found")


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
async def test_the_profile_says_whether_the_agents_model_sees_pictures(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend,
) -> None:
    first = await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    backend.put(_profile())

    async def vision() -> Any:
        r = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers())
        assert r.status_code == 200
        return r.json()["vision"]

    async def capabilities(instance_id: uuid.UUID, caps: dict[str, Any] | None) -> None:
        instance = await db_session.get(LLMProviderInstance, instance_id)
        assert instance is not None
        instance.capabilities = caps
        await db_session.commit()

    assert await vision() is None, "nobody said"
    await capabilities(first, {"vision": True, "tools": True})
    assert await vision() is True
    instance = await db_session.get(LLMProviderInstance, first)
    assert instance is not None
    instance.capabilities, instance.node_metadata = None, {"task": "chat", "vision": True}  # as the backend publishes it
    await db_session.commit()
    assert await vision() is True

    second = uuid.uuid4()
    db_session.add_all([
        LLMProviderInstance(id=second, type=ProviderType.VLLM, base_url="http://second.local", enabled=True),
        LLMPoolMembership(model_alias=ALIAS, provider_instance_id=second, enabled=True),
    ])
    await db_session.commit()
    assert await vision() is None, "one instance says nothing: a request may go there"
    await capabilities(second, {"vision": False})
    assert await vision() is False


@pytest.mark.anyio
@pytest.mark.parametrize(("task", "images"), [("images", True), ("chat", False)])
async def test_the_profile_says_whether_the_agents_tools_can_make_pictures(
    task: str, images: bool, client: AsyncClient, db_session: AsyncSession, backend: _Backend,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata={"task": task})
    backend.put(_profile())
    r = await client.get("/v1/agents/eplan-assistant/profile", headers=_headers())
    assert r.status_code == 200
    assert r.json()["images"] is images


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


# -- the PII mode a conversation asks for -------------------------------------------------


@pytest.fixture
def pii_service(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The PII service, faked: it records the mode of each scan. Switched off again after the test."""
    yield _install_pii(monkeypatch)
    service_registry.configure("pii", enabled=False, url=None)


async def _tenant_switch(db_session: AsyncSession, on: bool) -> None:
    policy = await db_session.get(TenantLLMPolicy, "tenant-a")
    assert policy is not None
    policy.allow_mode_override = on
    await db_session.commit()


@pytest.mark.anyio
@pytest.mark.parametrize("tenant", [False, True], ids=["tenant-off", "tenant-on"])
@pytest.mark.parametrize("agent", [None, True, False], ids=["agent-follows", "agent-allows", "agent-refuses"])
async def test_the_agents_pii_setting_decides_over_the_tenants(
    tenant: bool, agent: bool | None,
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
    pii_service: Any,
) -> None:
    """The conversation asks for tokenize (``X-Faden-PII-Mode``); the floor redacts."""
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("redact"))
    await _tenant_switch(db_session, tenant)
    backend.put(_profile(pii_allow_mode_override=agent))
    fake = pii_service
    sent: list[dict[str, Any]] = []
    _model(monkeypatch, sent)

    chat = {**_chat(), "messages": [{"role": "user", "content": "I am Alice"}]}
    r = await client.post(URL, headers=_headers(**{"X-Faden-PII-Mode": "tokenize_reversible"}), json=chat)
    assert r.status_code == 200, r.text
    applied = agent if agent is not None else tenant
    assert fake.modes == ["tokenize" if applied else "redact"]

    profile = (await client.get("/v1/agents/eplan-assistant/profile", headers=_headers())).json()
    assert profile["pii"] == {"allow_mode_override": agent, "mode_switch_allowed": applied}, "the SDK can know"


@pytest.mark.anyio
async def test_without_a_mode_a_conversation_gets_the_tenants_and_a_bad_one_is_refused(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
    pii_service: Any,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("redact"))
    backend.put(_profile(pii_allow_mode_override=True))
    fake = pii_service
    _model(monkeypatch, [])

    r = await client.post(URL, headers=_headers(), json=_chat())
    assert r.status_code == 200, r.text
    assert fake.modes == ["redact"]

    r = await client.post(URL, headers=_headers(**{"X-Faden-PII-Mode": "plaintext"}), json=_chat())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_header"


# -- what the agent's version does --------------------------------------------------------


def _tool(name: str, **function: Any) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object"}, **function}}


@pytest.fixture
def manifests(fastapi_app: FastAPI, db_session: AsyncSession) -> Iterator[None]:
    """Manifests written on a session of their own, as the gateway does -- on the test's connection."""
    fastapi_app.state.db_session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    agent_manifests.forget_recent()
    yield
    agent_manifests.forget_recent()


async def _kept(db_session: AsyncSession) -> list[AgentClientManifest]:
    db_session.expire_all()
    return list((await db_session.execute(select(AgentClientManifest))).scalars())


@pytest.mark.anyio
async def test_tools_outside_the_contract_are_held_back_and_kept_for_review(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
    manifests: None,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    sent: list[dict[str, Any]] = []
    _model(monkeypatch, sent)
    tools = [_tool("read_page"), _tool("delete_page", description="Deletes a page.")]

    # The app was updated with a tool the contract does not list yet: it keeps working with the others.
    r = await client.post(URL, headers=_headers(), json=_chat(tools=tools, tool_choice="auto"))
    assert r.status_code == 200, r.text
    assert [t["function"]["name"] for t in sent[-1]["tools"] if t["function"]["name"].endswith("_page")] == ["read_page"]
    assert r.headers["x-faden-held-tools"] == "delete_page"

    [row] = await _kept(db_session)
    assert (row.source, row.agent_id, row.last_user_id, row.app_name, row.reports) == ("request", AGENT_ID, USER, "", 1)
    assert [t["name"] for t in row.manifest["tools"]] == ["delete_page"]
    assert row.manifest["tools"][0]["description"] == "Deletes a page."
    assert len(row.manifest["tools"][0]["schema_sha256"]) == 64

    # Asked again at once: held again, written once in a while.
    r = await client.post(URL, headers=_headers(), json=_chat(tools=tools))
    assert r.headers["x-faden-held-tools"] == "delete_page"
    assert [(m.sha256, m.reports) for m in await _kept(db_session)] == [(row.sha256, 1)]

    # A choice that named the held tool lets the model choose; with nothing left, the request has no tools.
    r = await client.post(URL, headers=_headers(), json=_chat(
        tools=tools, tool_choice={"type": "function", "function": {"name": "delete_page"}},
    ))
    assert r.status_code == 200, r.text
    assert sent[-1]["tool_choice"] == "auto"
    r = await client.post(URL, headers=_headers(), json=_chat(tools=[_tool("delete_page")], tool_choice="required"))
    assert r.status_code == 200, r.text
    assert not any(t["function"]["name"] == "delete_page" for t in sent[-1].get("tools") or [])

    r = await client.post(URL, headers=_headers(), json=_chat(tools=[_tool("read_page")]))
    assert "x-faden-held-tools" not in r.headers


@pytest.mark.anyio
async def test_an_agent_deleted_and_made_again_under_its_address_is_read_again_for_its_tokens(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    _model(monkeypatch, [])
    assert (await client.post(URL, headers=_headers(), json=_chat())).status_code == 200  # cached now
    reads = backend.agent_reads

    # Deleted and made again within the cache's minute: a new id under the same address.
    remade = str(uuid.uuid4())
    backend.put(_profile(id=remade))
    r = await client.post(URL, headers=_headers(_agent_token(remade)), json=_chat())
    assert r.status_code == 200, r.text
    assert backend.agent_reads == reads + 1, "read once more, not on every request"
    assert (await client.post(URL, headers=_headers(_agent_token(remade)), json=_chat())).status_code == 200
    assert backend.agent_reads == reads + 1

    # A token for another agent is still refused.
    r = await client.post(URL, headers=_headers(_agent_token(OTHER_AGENT_ID)), json=_chat())
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "agent_mismatch"


@pytest.mark.anyio
async def test_an_agent_without_a_model_says_so_when_the_sdk_leaves_the_model_to_it(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    backend.put(_profile(model_alias=None))
    sent: list[dict[str, Any]] = []
    _model(monkeypatch, sent)
    r = await client.post(URL, headers=_headers(), json=_chat(model="agent"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "agent_has_no_model"
    assert "pick one in the console" in r.json()["error"]["message"]
    assert sent == []
    # A client that names a model gets it.
    r = await client.post(URL, headers=_headers(), json=_chat(model=ALIAS))
    assert r.status_code == 200, r.text


@pytest.mark.anyio
@pytest.mark.parametrize(("mode", "dev", "allowed"), [
    ("disable", False, False), ("disable", True, True), ("allow", False, True),
])
async def test_local_mcp_tools_pass_the_contract_only_where_local_mcp_is_allowed(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, monkeypatch: pytest.MonkeyPatch,
    manifests: None, mode: str, dev: bool, allowed: bool,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None)
    backend.put(_profile(local_mcp_mode=mode))
    sent: list[dict[str, Any]] = []
    _model(monkeypatch, sent)
    tools = [_tool(n) for n in ("read_page", "count_terminals")]
    r = await client.post(URL, headers=_headers(_agent_token(dev=dev)), json=_chat(tools=tools))
    assert r.status_code == 200, r.text
    names = {t["function"]["name"] for t in sent[-1]["tools"]}
    if allowed:
        assert names >= {"read_page", "count_terminals"}
        assert "x-faden-held-tools" not in r.headers
    else:
        assert "read_page" in names and "count_terminals" not in names
        assert r.headers["x-faden-held-tools"] == "count_terminals"


# -- what the app brings ---------------------------------------------------------------------------

MANIFEST: dict[str, Any] = {
    "app": {"name": "EPLAN add-in", "version": "2.1.0"},
    "sdk": {"language": "dotnet", "version": "0.4.0"},
    "tools": [
        {"name": "read_page", "description": "Reads a page.", "parameters": {"type": "object"}, "class": "read"},
        {"name": "delete_page", "description": "Deletes a page.", "class": "destructive",
         "parameters": {"type": "object", "properties": {"page": {"type": "integer"}}}},
    ],
    "context": [{"id": "project:pages", "title": "The project's pages"}],
    "ui": {"greeting": "Ask about your project.", "suggestions": ["Which pages changed?"]},
}
MANIFEST_URL = "/v1/agents/eplan-assistant/manifest"


@pytest.mark.anyio
async def test_an_app_reports_what_it_brings_once_per_version(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, manifests: None,
) -> None:
    r = await client.put(MANIFEST_URL, headers=_headers(), json=MANIFEST)
    assert r.status_code == 200, r.text
    assert r.json()["new"] is True
    [row] = await _kept(db_session)
    assert (row.source, row.app_name, row.app_version, row.reports) == ("app", "EPLAN add-in", "2.1.0", 1)
    tools = row.manifest["tools"]
    assert [(t["name"], t["class"]) for t in tools] == [("delete_page", "destructive"), ("read_page", "read")]
    assert tools[0]["schema_sha256"] != tools[1]["schema_sha256"]
    assert row.manifest["context"] == [{"id": "project:pages", "title": "The project's pages"}]

    # The same again (another device): counted, not stored twice. A new version: a row of its own.
    r = await client.put(MANIFEST_URL, headers=_headers(_agent_token(did="device-8")), json=MANIFEST)
    assert r.json() == {"sha256": row.sha256, "new": False}
    updated = {**MANIFEST, "app": {"name": "EPLAN add-in", "version": "2.2.0"}}
    assert (await client.put(MANIFEST_URL, headers=_headers(), json=updated)).json()["new"] is True
    rows = {m.app_version: m for m in await _kept(db_session)}
    assert rows["2.1.0"].reports == 2 and rows["2.1.0"].last_device_id == "device-8"
    assert set(rows) == {"2.1.0", "2.2.0"}


@pytest.mark.anyio
async def test_a_manifest_is_checked_and_bounded(
    client: AsyncClient, db_session: AsyncSession, backend: _Backend, manifests: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for bad in (
        {**MANIFEST, "tools": [MANIFEST["tools"][0], MANIFEST["tools"][0]]},  # the same name twice
        {**MANIFEST, "tools": [{"name": "rm -rf"}]},
        {**MANIFEST, "tools": [{"name": "x", "class": "harmless"}]},
        {"tools": []},  # no app
    ):
        r = await client.put(MANIFEST_URL, headers=_headers(), json=bad)
        assert r.status_code == 400, bad
        assert r.json()["error"]["code"] == "invalid_manifest"

    huge = {**MANIFEST, "tools": [{"name": "big", "parameters": {"description": "x" * 40_000}}]}
    assert (await client.put(MANIFEST_URL, headers=_headers(), json=huge)).status_code == 400
    r = await client.put(MANIFEST_URL, headers=_headers(), content=b"{" + b" " * 600_000 + b"}")
    assert r.status_code == 413

    # Fields it does not know are dropped, not kept.
    r = await client.put(MANIFEST_URL, headers=_headers(), json={**MANIFEST, "secret": "s3cr3t"})
    assert r.status_code == 200
    assert all("s3cr3t" not in json.dumps(m.manifest) for m in await _kept(db_session))

    monkeypatch.setattr(agent_manifests, "MAX_MANIFESTS_PER_AGENT", 1)
    r = await client.put(MANIFEST_URL, headers=_headers(), json={**MANIFEST, "app": {"name": "Other"}})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "too_many_manifests"


@pytest.mark.anyio
async def test_only_those_who_may_use_the_agent_report_for_it(client: AsyncClient, backend: _Backend) -> None:
    outsider = _agent_token(sub="44444444-4444-4444-4444-444444444444")
    assert (await client.put(MANIFEST_URL, headers=_headers(outsider), json=MANIFEST)).status_code == 403
    assert (await client.put(MANIFEST_URL, json=MANIFEST)).status_code == 401


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
