"""Governance outcomes (02 §11, 04 §8): one contract, whatever check objects.

Core runs no content checks: a check stands in for an EE content policy here.
Core's own check, the tenant's tool rules, is tested as it ships.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import openai
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import (
    ChatMessage,
    ChatSession,
    LLMGatewayRequestLog,
    PolicyDecisionRecord,
    ProviderType,
    TenantLLMPolicy,
    ToolRule,
)
from faden_gateway.services.extensions import load_plugin, registry
from faden_gateway.services.gateway import governance as gov
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from faden_gateway.settings import settings
from tests.test_gateway_knowledge_tools import (
    CAN_CALL_TOOLS,
    WEATHER,
    _call,
    _Knowledge,
    _Model,
    _say,
    _tool_names,
)
from tests.test_gateway_pipeline_combined import ALIAS, TEST_JWT_SECRET, _Observability, _seed, _token

EVENTS = {"X-Faden-Events": "ag-ui"}
SECRET = "the merger code is BLUEBIRD"


class _Policy:
    """A content policy, as EE would register one."""

    name = "test_policy"

    def __init__(self, *, stages: set[gov.GovernanceStage], word: str, window: int = 8) -> None:
        self.stages = frozenset(stages)
        self.word = word
        self.output_window_chars = window if gov.GovernanceStage.OUTPUT in stages else 0
        self.seen: list[gov.GovernanceContext] = []

    async def check(self, ctx: gov.GovernanceContext) -> gov.GovernanceDecision | None:
        self.seen.append(ctx)
        text = ctx.text or json.dumps(list(ctx.messages))
        if self.word.lower() not in text.lower():
            return None
        return gov.GovernanceDecision(
            outcome="block", policy_id="p-7", category="confidential",
            message="Project names stay out of the assistant.", policy_name="Codename list v3",
        )


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    saved = list(registry.governance_checks)
    gov.tool_rules.invalidate()
    yield
    registry.governance_checks[:] = saved
    gov.tool_rules.invalidate()


def _headers(token: str | None = None, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or _token()}", **extra}


def _other_tenant_token() -> str:
    import jwt  # noqa: PLC0415

    return jwt.encode({"sub": "user-1", "tenant_id": "tenant-b"}, TEST_JWT_SECRET, algorithm="HS256")


async def _setup(fastapi_app: FastAPI, db: AsyncSession) -> uuid.UUID:
    await _seed(db, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    sess = ChatSession(tenant_id="tenant-a", user_id="user-1", title="t")
    db.add(sess)
    await db.flush()
    return sess.id


async def _messages(db: AsyncSession, sid: uuid.UUID) -> list[ChatMessage]:
    return list((await db.execute(
        select(ChatMessage).where(ChatMessage.session_id == sid).order_by(ChatMessage.created_at),
    )).scalars().all())


async def _decisions(db: AsyncSession) -> list[PolicyDecisionRecord]:
    return list((await db.execute(
        select(PolicyDecisionRecord).order_by(PolicyDecisionRecord.created_at),
    )).scalars().all())


def _faden(raw: str) -> list[dict[str, Any]]:
    return [json.loads(b.split("data: ", 1)[1]) for b in raw.split("\n\n") if b.startswith("event: faden\n")]


def _data(raw: str) -> list[Any]:
    out: list[Any] = []
    for block in raw.split("\n\n"):
        if block.startswith("data: "):
            out.append("[DONE]" if block == "data: [DONE]" else json.loads(block[6:]))
    return out


def _text(raw: str) -> str:
    return "".join(
        (c.get("delta") or {}).get("content") or ""
        for e in _data(raw) if isinstance(e, dict) for c in e.get("choices", [])
    )


# -- no checks: nothing changes -----------------------------------------------------------


@pytest.mark.anyio
async def test_without_an_output_check_the_stream_is_passed_through_untouched() -> None:
    from faden_gateway.services.gateway.service import GatewayService, _Prepared  # noqa: PLC0415

    async def stream() -> AsyncIterator[bytes]:
        yield b"data: {}\n\n"

    original = stream()
    service = object.__new__(GatewayService)
    req = _Prepared(payload={})
    req.checks = [_Policy(stages={gov.GovernanceStage.INPUT}, word="x")]
    kept = service._govern_output(  # noqa: SLF001
        req, original, auth=None, request_id="r", model_alias="m",  # type: ignore[arg-type]
    )
    assert kept is original, "no buffering unless a check reads answers"


# -- a refused prompt ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_refused_prompt_is_a_422_with_the_notice_and_leaves_no_text(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _setup(fastapi_app, db_session)
    registry.governance_checks.append(_Policy(stages={gov.GovernanceStage.INPUT}, word="bluebird"))
    model = _Model(monkeypatch, _say("Hello."))

    r = await client.post("/v1/chat/completions", headers=_headers(), json={
        "model": ALIAS, "session_id": str(sid), "messages": [{"role": "user", "content": SECRET}],
    })
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == {"type": "governance_error", "code": "policy_blocked", "param": None,
                             "message": "Project names stay out of the assistant."}
    notice = body["faden"]["governance"]
    assert (notice["outcome"], notice["stage"], notice["category"]) == ("prompt_blocked", "input", "confidential")
    assert notice["session_id"] == str(sid) and notice["message_id"] and notice["decision_id"]
    assert "Codename" not in r.text, "the policy's name stays with auditors"
    assert model.sent == [], "the model never saw it"

    [stored] = await _messages(db_session, sid)
    assert (stored.role, stored.status, stored.content) == ("user", "blocked_input", "")
    assert str(stored.id) == notice["message_id"] and str(stored.decision_id) == notice["decision_id"]
    [decision] = await _decisions(db_session)
    assert (decision.stage, decision.outcome, decision.policy_id, decision.policy_name) == (
        "input", "block", "p-7", "Codename list v3")
    assert decision.content_hmac == stored.content_hmac and len(decision.content_hmac or "") == 64
    for row in (stored, decision):
        assert "BLUEBIRD" not in json.dumps({k: str(v) for k, v in vars(row).items()})

    log = (await db_session.execute(select(LLMGatewayRequestLog))).scalars().all()
    assert [(row.status_code, row.error_code) for row in log] == [(422, "policy_blocked")]


@pytest.mark.anyio
async def test_the_next_request_goes_on_and_the_model_never_sees_the_refused_turn(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _setup(fastapi_app, db_session)
    registry.governance_checks.append(_Policy(stages={gov.GovernanceStage.INPUT}, word="bluebird"))
    model = _Model(monkeypatch, _say("It is sunny."))
    ask = {"model": ALIAS, "session_id": str(sid)}
    refused = await client.post("/v1/chat/completions", headers=_headers(),
                                json={**ask, "messages": [{"role": "user", "content": SECRET}]})
    assert refused.status_code == 422

    r = await client.post("/v1/chat/completions", headers=_headers(),
                          json={**ask, "messages": [{"role": "user", "content": "How is the weather?"}]})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "It is sunny."
    said = json.dumps(model.sent[0]["messages"])
    assert "BLUEBIRD" not in said and "How is the weather?" in said
    listed = (await client.get(f"/v1/sessions/{sid}/messages", headers=_headers())).json()["data"]
    assert (listed[0]["role"], listed[0]["status"], listed[0]["content"]) == ("user", "blocked_input", "")
    assert listed[0]["notice"] == "Project names stay out of the assistant."


@pytest.mark.anyio
async def test_the_same_prompt_has_the_same_hmac_in_a_tenant_and_another_elsewhere(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    other = await db_session.get(TenantLLMPolicy, "tenant-a")
    assert other is not None
    db_session.add(TenantLLMPolicy(
        tenant_id="tenant-b", privacy_mode=other.privacy_mode, allowed_model_aliases=[ALIAS],
        allowed_provider_types=other.allowed_provider_types, rpm_limit=100, tpm_limit=1_000_000,
        created_at=other.created_at, updated_at=other.updated_at,
    ))
    await db_session.flush()
    registry.governance_checks.append(_Policy(stages={gov.GovernanceStage.INPUT}, word="bluebird"))
    _Model(monkeypatch)
    body = {"model": ALIAS, "messages": [{"role": "user", "content": SECRET}]}
    for token in (_token(), _token(), _other_tenant_token()):
        assert (await client.post("/v1/chat/completions", headers=_headers(token), json=body)).status_code == 422
    a1, a2, b = [d.content_hmac for d in await _decisions(db_session)]
    assert a1 == a2 != b


@pytest.mark.anyio
async def test_the_openai_client_raises_on_a_refused_prompt(
    fastapi_app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    registry.governance_checks.append(_Policy(stages={gov.GovernanceStage.INPUT}, word="bluebird"))
    _Model(monkeypatch)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fastapi_app), base_url="http://test")
    client = openai.AsyncOpenAI(base_url="http://test/v1", api_key=_token(), http_client=http, max_retries=0)
    with pytest.raises(openai.UnprocessableEntityError) as refused:
        await client.chat.completions.create(model=ALIAS, messages=[{"role": "user", "content": SECRET}])
    assert refused.value.code == "policy_blocked"
    assert refused.value.body["code"] == "policy_blocked"  # type: ignore[index]
    await http.aclose()


# -- a refused answer ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_refused_streamed_answer_ends_in_content_filter_and_is_not_stored(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _setup(fastapi_app, db_session)
    registry.governance_checks.append(_Policy(stages={gov.GovernanceStage.OUTPUT}, word="bluebird", window=8))
    answer = "Sure. The code name is BLUEBIRD, as agreed in May."
    _Model(monkeypatch, _say(answer))

    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": True, "session_id": str(sid),
        "messages": [{"role": "user", "content": "What is the code name?"}],
    })
    assert r.status_code == 200
    assert "BLUEBIRD" not in r.text, "nothing of the refused window was forwarded"
    shown = _text(r.text)
    assert answer.startswith(shown) and len(shown) < answer.index("BLUEBIRD")
    event = next(e for e in _faden(r.text) if e.get("name") == "faden.governance")
    assert event["value"]["outcome"] == "response_blocked" and event["value"]["stage"] == "output"
    data = _data(r.text)
    assert data[-1] == "[DONE]" and data[-2]["choices"][0]["finish_reason"] == "content_filter"

    user, reply = await _messages(db_session, sid)
    assert (user.status, user.content) == ("complete", "What is the code name?")
    assert (reply.role, reply.status, reply.content) == ("assistant", "blocked_output", ""), "not even the part shown"
    assert reply.content_hmac and str(reply.decision_id) == event["value"]["decision_id"]


@pytest.mark.anyio
async def test_a_refused_answer_keeps_the_question_in_context(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _setup(fastapi_app, db_session)
    registry.governance_checks.append(_Policy(stages={gov.GovernanceStage.OUTPUT}, word="bluebird"))
    model = _Model(monkeypatch, _say("It is BLUEBIRD."), _say("I cannot say."))
    ask = {"model": ALIAS, "session_id": str(sid)}
    await client.post("/v1/chat/completions", headers=_headers(),
                      json={**ask, "messages": [{"role": "user", "content": "What is the code name?"}]})
    await client.post("/v1/chat/completions", headers=_headers(),
                      json={**ask, "messages": [{"role": "user", "content": "Why not?"}]})
    users = [m["content"] for m in model.sent[1]["messages"] if m["role"] == "user"]
    assert users == ["What is the code name?\n\nWhy not?"], "the question stays, merged with the next"
    assert not any(m["role"] == "assistant" for m in model.sent[1]["messages"]), "the answer goes"


@pytest.mark.anyio
async def test_a_refused_whole_answer_is_a_200_with_content_filter_the_openai_client_reads(
    fastapi_app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    registry.governance_checks.append(_Policy(stages={gov.GovernanceStage.OUTPUT}, word="bluebird"))
    _Model(monkeypatch, _say("It is BLUEBIRD."))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fastapi_app), base_url="http://test")
    client = openai.AsyncOpenAI(base_url="http://test/v1", api_key=_token(), http_client=http)
    raw = await client.chat.completions.with_raw_response.create(
        model=ALIAS, messages=[{"role": "user", "content": "What is the code name?"}],
    )
    answer = raw.parse()
    assert answer.choices[0].finish_reason == "content_filter" and answer.choices[0].message.content == ""
    notice = json.loads(raw.http_response.text)["faden"]["governance"]
    assert notice["outcome"] == "response_blocked" and "BLUEBIRD" not in raw.http_response.text
    await http.aclose()


# -- tools ------------------------------------------------------------------------------------


async def _rule(db: AsyncSession, pattern: str, action: str, message: str, *, tenant: str = "tenant-a") -> None:
    db.add(ToolRule(tenant_id=tenant, name=f"rule for {pattern}", tool_pattern=pattern, action=action,
                    message=message, category="tool_rule", priority=10, enabled=True))
    await db.flush()


@pytest.mark.anyio
async def test_a_refused_tool_call_is_told_to_the_model_and_to_the_client(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _rule(db_session, "knowledge_*", "deny", "The knowledge base is closed this week.")
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("I could not search."))

    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": True, "messages": [{"role": "user", "content": "When is the launch?"}],
    })
    assert knowledge.searches == [], "not run"
    told = model.sent[1]["messages"][-1]
    assert told["role"] == "tool" and "The knowledge base is closed this week." in told["content"]
    [denied] = [e for e in _faden(r.text) if e.get("name") == "faden.tool_denied"]
    assert denied["value"]["toolCallName"] == "knowledge_search" and denied["value"]["toolCallId"] == "call_1"
    assert denied["value"]["message"] == "The knowledge base is closed this week."
    assert _text(r.text) == "I could not search."
    [decision] = await _decisions(db_session)
    assert (decision.stage, decision.tool_id, decision.outcome) == ("tool", "knowledge_search", "block")


@pytest.mark.anyio
async def test_a_client_tool_a_rule_refuses_is_not_offered(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _rule(db_session, "get_weather", "deny", "No weather lookups.")
    model = _Model(monkeypatch, _say("I cannot look that up."))
    r = await client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": True, "tools": [WEATHER], "tool_choice": "auto",
        "messages": [{"role": "user", "content": "Weather?"}],
    })
    assert "get_weather" not in _tool_names(model.sent[0])
    assert _faden(r.text)[0]["name"] == "faden.tool_denied"


@pytest.mark.anyio
async def test_a_rule_in_another_tenant_does_not_apply(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _rule(db_session, "*", "deny", "Nothing.", tenant="tenant-b")
    knowledge = _Knowledge(monkeypatch)
    _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("On 5 May."))
    r = await client.post("/v1/chat/completions", headers=_headers(), json={
        "model": ALIAS, "messages": [{"role": "user", "content": "When?"}],
    })
    assert r.json()["choices"][0]["message"]["content"] == "On 5 May." and len(knowledge.searches) == 1


async def _approve_when_asked(client: AsyncClient, approved: bool, token: str | None = None) -> int:
    for _ in range(200):
        if gov.approvals.waiting():
            approval_id = gov.approvals.waiting()[0]
            r = await client.post(f"/v1/approvals/{approval_id}", headers=_headers(token), json={"approved": approved})
            return r.status_code
        await asyncio.sleep(0.01)
    raise AssertionError("nobody asked")


@pytest.mark.anyio
@pytest.mark.parametrize("approved", [True, False], ids=["approved", "declined"])
async def test_a_tool_that_needs_approval_waits_for_the_user(
    approved: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _rule(db_session, "knowledge_search", "approval_required", "Searching needs your OK.")
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("Done."))

    ask = client.post("/v1/chat/completions", headers=_headers(**EVENTS), json={
        "model": ALIAS, "stream": True, "messages": [{"role": "user", "content": "When is the launch?"}],
    })
    r, answered = await asyncio.gather(ask, _approve_when_asked(client, approved))
    assert answered == 200
    [asked] = [e for e in _faden(r.text) if e.get("name") == "faden.approval_required"]
    assert asked["value"]["toolCallName"] == "knowledge_search" and asked["value"]["message"] == "Searching needs your OK."
    told = model.sent[1]["messages"][-1]["content"]
    if approved:
        assert len(knowledge.searches) == 1 and "handbook.pdf" in told
    else:
        assert knowledge.searches == [] and "The user declined." in told
    outcomes = [(d.outcome, d.tool_id) for d in await _decisions(db_session)]
    assert outcomes[0] == ("approved" if approved else "rejected", "knowledge_search")


@pytest.mark.anyio
async def test_only_the_user_who_waits_can_answer_and_a_client_that_cannot_ask_is_refused(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    await _rule(db_session, "knowledge_search", "approval_required", "Searching needs your OK.")
    knowledge = _Knowledge(monkeypatch)
    model = _Model(monkeypatch, _call("knowledge_search", {"query": "launch"}), _say("Done."))
    r = await client.post("/v1/chat/completions", headers=_headers(), json={
        "model": ALIAS, "messages": [{"role": "user", "content": "When?"}],
    })
    assert r.status_code == 200 and knowledge.searches == []
    assert "could not be asked for here" in model.sent[1]["messages"][-1]["content"]

    waiting = asyncio.create_task(gov.approvals.wait(tenant_id="tenant-a", user_id="user-1", approval_id="a1", timeout=2))
    await asyncio.sleep(0)
    stranger = await client.post("/v1/approvals/a1", headers=_headers(_other_tenant_token()), json={"approved": True})
    assert stranger.status_code == 404
    assert (await client.post("/v1/approvals/a1", headers=_headers(), json={"approved": False})).status_code == 200
    assert await waiting is False


# -- errors, limits, the plugin -------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_failed_turn_is_left_out_of_the_next_request(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = await _setup(fastapi_app, db_session)
    sent: list[dict[str, Any]] = []

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        sent.append(kwargs["payload"])
        if len(sent) == 1:
            raise ConnectionError("upstream down")
        return await original(self, **kwargs)

    model = _Model(monkeypatch, _say("Fine."))
    original = LLMAdapter.completion
    monkeypatch.setattr(LLMAdapter, "completion", completion)
    monkeypatch.setattr(settings, "retry_pre_first_token", 0)
    ask = {"model": ALIAS, "session_id": str(sid)}
    failed = await client.post("/v1/chat/completions", headers=_headers(),
                               json={**ask, "messages": [{"role": "user", "content": "First try"}]})
    assert failed.status_code == 502
    [first] = await _messages(db_session, sid)
    assert first.status == "failed"
    await client.post("/v1/chat/completions", headers=_headers(),
                      json={**ask, "messages": [{"role": "user", "content": "Second try"}]})
    users = [m["content"] for m in model.sent[0]["messages"] if m["role"] == "user"]
    assert users == ["Second try"]


@pytest.mark.anyio
async def test_a_rate_limit_says_when_to_retry_and_is_logged(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    policy = await db_session.get(TenantLLMPolicy, "tenant-a")
    assert policy is not None
    policy.rpm_limit = 1
    await db_session.flush()
    _Model(monkeypatch, _say("One."), _say("Two."))

    class _Counter:
        def __init__(self) -> None:
            self.n = 0

        async def incr(self, key: str, amount: int, *, ttl_sec: int) -> int:
            self.n += amount if key.startswith("ratelimit:rpm") else 0
            return self.n

    from faden_gateway.services.gateway.ratelimit import RateLimiter  # noqa: PLC0415

    counter = _Counter()
    monkeypatch.setattr(RateLimiter, "__init__", lambda self, cache: setattr(self, "cache", counter))
    body = {"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]}
    assert (await client.post("/v1/chat/completions", headers=_headers(), json=body)).status_code == 200
    r = await client.post("/v1/chat/completions", headers=_headers(), json=body)
    assert r.status_code == 429
    assert r.json()["error"]["type"] == "rate_limit_error" and r.json()["error"]["code"] == "rate_limit_rpm"
    assert 1 <= int(r.headers["retry-after"]) <= 60
    codes = [row.error_code for row in (await db_session.execute(select(LLMGatewayRequestLog))).scalars()]
    assert "rate_limit_rpm" in codes, "a request refused early is logged too"


def test_the_enterprise_plugin_hands_over_its_checks() -> None:
    check = _Policy(stages={gov.GovernanceStage.INPUT}, word="x")

    class _Plugin:
        def register(self, registry: Any) -> None: ...

        def governance_checks(self) -> list[Any]:
            return [check]

    load_plugin(_Plugin())
    assert check in registry.governance_checks


@pytest.mark.anyio
async def test_the_most_restrictive_objection_wins_and_all_are_kept() -> None:
    class _Asks:
        name = "asks"
        stages = frozenset({gov.GovernanceStage.TOOL})
        output_window_chars = 0

        async def check(self, ctx: gov.GovernanceContext) -> gov.GovernanceDecision:
            return gov.GovernanceDecision(outcome="approval_required", policy_id="a", category="c", message="ask")

    class _Broken:
        name = "broken"
        stages = frozenset({gov.GovernanceStage.TOOL})
        output_window_chars = 0

        async def check(self, ctx: gov.GovernanceContext) -> None:
            raise RuntimeError("down")

    ctx = gov.GovernanceContext(stage=gov.GovernanceStage.TOOL, tenant_id="t", user_id="u", agent_id=None,
                                session_id=None, request_id="r", tool_id="x")
    rules = gov.ToolRuleCheck([gov.Rule(id="r1", name="n", pattern="x", action="deny", message="no", category="c")])
    verdict = await gov.decide([_Asks(), _Broken(), rules], ctx)
    assert verdict is not None and verdict.blocked and verdict.check == "tool_rules"
    assert [r["check"] for r in verdict.reasons] == ["tool_rules", "asks"]
    _Broken.fail_closed = True  # type: ignore[attr-defined]
    only_broken = await gov.decide([_Broken()], ctx)
    assert only_broken is not None and only_broken.blocked


@pytest.mark.anyio
async def test_a_request_refused_before_the_pipeline_is_logged_once(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(fastapi_app, db_session)
    _Model(monkeypatch, _say("ok"))
    bad = await client.post("/v1/chat/completions", headers=_headers(), json={"model": ALIAS, "messages": "nope"})
    unknown = await client.post("/v1/chat/completions", headers=_headers(), json={
        "model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]})
    assert (bad.status_code, unknown.status_code) == (400, 404)
    rows = (await db_session.execute(select(LLMGatewayRequestLog))).scalars().all()
    assert sorted((r.status_code, r.error_code) for r in rows) == [
        (400, "validation_error"), (404, unknown.json()["error"]["code"])]
