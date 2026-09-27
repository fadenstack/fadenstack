"""The start of a chat request stays the same from one turn to the next.

vLLM reuses its KV cache only for an identical token prefix. Three things
moved the prefix every turn (chat plan, Phase 1): a date stamp to the minute
as the first message, memory facts newest first, and the turn's skills before
the history. A changed first line cost 2 to 3 times the time to the first
token at 4k and 10k tokens of history on qwen-chat (2026-09-25).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import litellm
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import ProviderType
from llm_port_api.services.gateway import llm_adapter
from llm_port_api.services.gateway.context_assembler import ContextAssembler
from llm_port_api.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from llm_port_api.services.gateway.service import GatewayService
from llm_port_api.services.gateway.skills_client import ResolvedSkill, SkillResolveResult, SkillsClient
from llm_port_api.services.registry import service_registry
from llm_port_api.settings import settings
from tests.test_gateway_pipeline_combined import ALIAS, _model_answers, _Observability, _seed, _session, _token
from tests.test_llm_adapter_direct import _event, _stream

REFUSED = "Conversation roles must alternate user/assistant/user/assistant/..."
SERVICE_TOKEN = "service-token"

# ── The date ─────────────────────────────────────────────────────


def test_the_first_line_is_the_date_without_the_time() -> None:
    """The minute changed the first line of every request a minute apart."""
    payload = GatewayService._inject_datetime_context(  # noqa: SLF001
        {"messages": [{"role": "user", "content": "hi"}]},
    )
    first = payload["messages"][0]
    assert first["role"] == "system"
    assert re.fullmatch(r"Current date: \w+day, \w+ \d{2}, \d{4}\.", first["content"])


# ── Memory facts ─────────────────────────────────────────────────


class _Facts:
    """The session DAO's facts, newest first per scope, as SQL returns them."""

    def __init__(self, by_scope: dict[str, list[str]]) -> None:
        self.by_scope = by_scope

    async def list_active_facts(self, *, scope: Any, **_: Any) -> list[Any]:
        return [SimpleNamespace(id=uuid.uuid4(), key=key) for key in self.by_scope.get(scope.value, [])]


@pytest.mark.anyio
async def test_facts_keep_their_order_when_one_is_added() -> None:
    """User, project, session; each scope by key, not newest first."""
    before = _Facts({"user": ["name", "city"], "project": ["repo"], "session": ["topic", "goal"]})
    after = _Facts({"user": ["age", "name", "city"], "project": ["repo"], "session": ["topic", "goal"]})

    async def keys(dao: _Facts) -> list[str]:
        assembler = ContextAssembler(dao=dao)  # type: ignore[arg-type]
        facts = await assembler._collect_facts(  # noqa: SLF001
            tenant_id="t", user_id="u", session_id=uuid.uuid4(), project_id=uuid.uuid4(),
        )
        return [fact.key for fact in facts]

    assert await keys(before) == ["city", "name", "repo", "goal", "topic"]
    assert await keys(after) == ["age", "city", "name", "repo", "goal", "topic"]


# ── Skills ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _skills_off_after() -> Any:
    """The registry is global: a test that switches skills on switches them off."""
    yield
    service_registry.configure("skills", enabled=False, url=None)


def _skilled(monkeypatch: pytest.MonkeyPatch) -> None:
    service_registry.configure("skills", enabled=True, url="http://skills.local")
    monkeypatch.setattr(settings, "skills_service_token", SERVICE_TOKEN)

    async def resolve(self: SkillsClient, **_: Any) -> SkillResolveResult:
        return SkillResolveResult(skills=[ResolvedSkill(
            skill_id="s1", name="Terse", slug="terse", version=1, body_markdown="Answer in five words.",
            priority=1, score=1,
        )])

    async def record(self: SkillsClient, **_: Any) -> None: ...

    monkeypatch.setattr(SkillsClient, "resolve_skills", resolve)
    monkeypatch.setattr(SkillsClient, "record_usage", record)


CONVERSATION = [
    {"role": "system", "content": "You are helpful."},
    {"role": "user", "content": "first question"},
    {"role": "assistant", "content": "first answer"},
    {"role": "user", "content": "second question"},
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("position", "expected"),
    [
        # date, system, history, the skill, the question
        ("tail", ["system", "system", "user", "assistant", "SKILL", "user"]),
        # date, system, the skill, history, the question
        ("prefix", ["system", "system", "SKILL", "user", "assistant", "user"]),
    ],
)
async def test_a_skill_goes_after_the_history_unless_set_to_the_prefix(
    position: str, expected: list[str],
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In the prefix, a skill resolved for one turn moved everything after it."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    _skilled(monkeypatch)
    monkeypatch.setattr(settings, "session_skills_position", position)
    sent: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "messages": CONVERSATION,
    })
    assert r.status_code == 200
    roles = [
        "SKILL" if "ACTIVE SKILL" in (m.get("content") or "") else m["role"]
        for m in sent[0]["messages"]
    ]
    assert roles == expected


# ── A template that refuses a system message after the start ─────


LATE_SYSTEM = [
    {"role": "system", "content": "date"},
    {"role": "user", "content": "q1"},
    {"role": "assistant", "content": "a1"},
    {"role": "system", "content": "skill"},
    {"role": "user", "content": "q2"},
]
UP_FRONT = ["date", "skill", "q1", "a1", "q2"]


def _contents(request: httpx.Request) -> list[str]:
    return [m["content"] for m in json.loads(request.content)["messages"]]


def _refusing_engine(seen: list[httpx.Request], *, refusal: str = REFUSED) -> httpx.AsyncClient:
    """A vLLM whose template takes system messages only at the start."""

    async def body() -> AsyncIterator[bytes]:
        yield _event({"content": "ok"})
        yield b"data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        roles = [m["role"] for m in json.loads(request.content)["messages"]]
        first_other = next(i for i, role in enumerate(roles) if role != "system")
        if "system" in roles[first_other:]:
            return httpx.Response(400, json={"object": "error", "message": refusal})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def fresh_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """No model is remembered as refusing, before or after the test."""
    monkeypatch.setattr(llm_adapter, "_SYSTEM_FIRST", set())


@pytest.mark.anyio
@pytest.mark.usefixtures("fresh_memory")
async def test_a_refused_stream_goes_again_with_system_messages_up_front() -> None:
    """Refused before a byte went out: the client sees the answer, not the 400."""
    seen: list[httpx.Request] = []
    payload = {"model": "qwen", "messages": LATE_SYSTEM}
    async with _refusing_engine(seen) as http:
        out = await _stream(LLMAdapter(http_client=http), payload=payload)
        assert b"ok" in b"".join(out)
        assert len(seen) == 2
        assert _contents(seen[1]) == UP_FRONT

        # Remembered: the next request is sent up front at once.
        await _stream(LLMAdapter(http_client=http), payload=payload)
    assert len(seen) == 3
    assert _contents(seen[2]) == UP_FRONT


@pytest.mark.anyio
@pytest.mark.usefixtures("fresh_memory")
async def test_any_other_400_is_not_sent_again() -> None:
    """Only a template's refusal is worth a second request."""
    seen: list[httpx.Request] = []
    async with _refusing_engine(seen, refusal="max_tokens is too large") as http:
        out = await _stream(LLMAdapter(http_client=http), payload={"model": "qwen", "messages": LATE_SYSTEM})
    assert len(seen) == 1
    assert b"upstream 400" in b"".join(out)


@pytest.mark.anyio
@pytest.mark.usefixtures("fresh_memory")
async def test_a_refused_whole_answer_goes_again_up_front(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same through LiteLLM, for an answer that is not streamed."""
    sent: list[list[str]] = []

    async def acompletion(**kwargs: Any) -> Any:
        contents = [m["content"] for m in kwargs["messages"]]
        sent.append(contents)
        if contents != UP_FRONT:
            raise litellm.exceptions.BadRequestError(message=REFUSED, model="qwen", llm_provider="openai")
        return SimpleNamespace(model_dump=lambda: {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    result = await LLMAdapter().completion(
        provider_type=ProviderType.REMOTE_OPENAI, base_url="http://api.local/v1", api_key_encrypted=None,
        litellm_provider=None, litellm_model=None, extra_params=None,
        payload={"model": "qwen", "messages": LATE_SYSTEM}, stream=False,
    )
    assert isinstance(result, CompletionResult)
    assert result.status_code == 200
    assert sent == [[m["content"] for m in LATE_SYSTEM], UP_FRONT]


# ── A chat's reply is capped when the request sets no cap ────────


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("with_session", "asked", "sent"),
    [
        (True, None, 4096),  # the chat page: capped
        (True, 200, 200),  # a chat that set its own: kept
        (False, None, None),  # the API without a session: OpenAI's meaning
    ],
)
async def test_a_chat_reply_is_capped_unless_it_sets_its_own(
    with_session: bool, asked: int | None, sent: int | None,
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A looping model ran one chat reply to 31,136 tokens in 212 s."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    sent_payloads: list[dict[str, Any]] = []
    _model_answers(monkeypatch, "ok", sent_payloads)
    body: dict[str, Any] = {"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]}
    if with_session:
        body["session_id"] = str(await _session(db_session))
    if asked is not None:
        body["max_tokens"] = asked

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json=body)
    assert r.status_code == 200
    assert sent_payloads[0].get("max_tokens") == sent
