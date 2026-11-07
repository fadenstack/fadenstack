"""The tenant's caps: what they admit, refuse and count, and how a client hears of them (M20, C4).

And what the request log keeps of a request's usage.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import ConnectionPool, Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import LLMGatewayRequestLog, ProviderType, TenantLLMPolicy
from faden_gateway.services.cache.noop import NoOpCache
from faden_gateway.services.cache.redis import RedisCache
from faden_gateway.services.gateway import ratelimit
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from faden_gateway.services.gateway.ratelimit import CapRefusal, Caps, RateLimiter, Reservation
from faden_gateway.services.gateway.usage_group import UsageGroupResolver, UsageTeam
from tests.test_gateway_api import _FakeObservability, _seed_basic_graph, _token
from tests.test_gateway_knowledge_tools import CAN_CALL_TOOLS, _ask, _call, _Model, _say
from tests.test_gateway_pipeline_combined import _Observability, _seed

BERLIN = ZoneInfo("Europe/Berlin")
UTC_ZONE = ZoneInfo("UTC")
#: Ten seconds into a minute.
T0 = datetime(2026, 11, 10, 12, 0, 10, tzinfo=UTC).timestamp()


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def _count(pool: ConnectionPool, pattern: str) -> int | None:
    """The one counter matching *pattern*, or None."""
    redis = Redis(connection_pool=pool)
    keys = await redis.keys(pattern)
    assert len(keys) <= 1, keys
    if not keys:
        return None
    value = await redis.get(keys[0])
    return int(value) if value is not None else None


# ── The limiter ──────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_refused_request_does_not_use_up_the_window(fake_redis_pool: ConnectionPool) -> None:
    limiter = RateLimiter(RedisCache(fake_redis_pool), clock=_Clock(T0))
    caps = Caps(rpm=2, tpm=100)
    first = await limiter.admit(tenant_id="t", caps=caps, tokens=60, zone=UTC_ZONE)
    too_many = await limiter.admit(tenant_id="t", caps=caps, tokens=60, zone=UTC_ZONE)
    fits = await limiter.admit(tenant_id="t", caps=caps, tokens=40, zone=UTC_ZONE)
    over_rpm = await limiter.admit(tenant_id="t", caps=caps, tokens=1, zone=UTC_ZONE)
    assert isinstance(first, Reservation)
    assert isinstance(too_many, CapRefusal) and too_many.kind == "tpm"
    # It would have been refused had the refused request counted (requests or tokens).
    assert isinstance(fits, Reservation)
    assert isinstance(over_rpm, CapRefusal) and over_rpm.kind == "rpm"
    assert await _count(fake_redis_pool, "ratelimit:rpm:t:*") == 2
    assert await _count(fake_redis_pool, "ratelimit:tpm:t:*") == 100
    assert fits.headers == {
        "x-ratelimit-limit-requests": "2", "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "50s",
        "x-ratelimit-limit-tokens": "100", "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "50s",
    }


@pytest.mark.anyio
async def test_settling_replaces_the_reservation_and_never_goes_below_zero(fake_redis_pool: ConnectionPool) -> None:
    cache = RedisCache(fake_redis_pool)
    limiter = RateLimiter(cache, clock=_Clock(T0))
    caps = Caps(rpm=10, tpm=5000, tokens_per_day=10_000)
    reserved = await limiter.admit(tenant_id="t", caps=caps, tokens=1084, zone=UTC_ZONE)
    assert isinstance(reserved, Reservation) and reserved.tokens == 1084
    await limiter.settle(reserved, 13)
    await limiter.settle(reserved, 9999)  # once only
    assert await _count(fake_redis_pool, "ratelimit:tpm:t:*") == 13
    assert await _count(fake_redis_pool, "ratelimit:tpd:t:*") == 13

    # An answer longer than its reservation is counted in full afterwards.
    longer = await limiter.admit(tenant_id="t", caps=caps, tokens=100, zone=UTC_ZONE)
    assert isinstance(longer, Reservation)
    await limiter.settle(longer, 4000)
    assert await _count(fake_redis_pool, "ratelimit:tpm:t:*") == 4013

    # Never below zero.
    small = await limiter.admit(tenant_id="t", caps=caps, tokens=500, zone=UTC_ZONE)
    assert isinstance(small, Reservation)
    await cache.set(small.keys["tpm"], "5")
    await limiter.settle(small, 0)
    assert await _count(fake_redis_pool, "ratelimit:tpm:t:*") == 0

    # A refused request after all gives its request back too.
    given_back = await limiter.admit(tenant_id="t", caps=caps, tokens=50, zone=UTC_ZONE)
    assert isinstance(given_back, Reservation)
    requests = await _count(fake_redis_pool, "ratelimit:rpm:t:*")
    await limiter.release(given_back)
    assert await _count(fake_redis_pool, "ratelimit:rpm:t:*") == (requests or 0) - 1

    # A counter whose window is over is left alone: settling does not make one that never expires.
    late = await limiter.admit(tenant_id="t", caps=caps, tokens=50, zone=UTC_ZONE)
    assert isinstance(late, Reservation)
    redis = Redis(connection_pool=fake_redis_pool)
    await redis.delete(late.keys["tpm"])
    await limiter.settle(late, 900)
    assert not await redis.exists(late.keys["tpm"])
    assert await redis.ttl(late.keys["tokens_per_day"]) > 0


@pytest.mark.anyio
async def test_the_day_turns_at_midnight_of_the_usage_time_zone(fake_redis_pool: ConnectionPool) -> None:
    # 22:59:30 UTC on 10 November is 23:59:30 in Berlin (CET).
    clock = _Clock(datetime(2026, 11, 10, 22, 59, 30, tzinfo=UTC).timestamp())
    limiter = RateLimiter(RedisCache(fake_redis_pool), clock=clock)
    caps = Caps(tokens_per_day=100)
    used_up = await limiter.admit(tenant_id="berlin", caps=caps, tokens=100, zone=BERLIN)
    assert isinstance(used_up, Reservation) and used_up.keys["tokens_per_day"].endswith(":2026-11-10")
    assert used_up.headers["x-ratelimit-reset-tokens"] == "30s"  # the day's cap, with no minute's
    refused = await limiter.admit(tenant_id="berlin", caps=caps, tokens=1, zone=BERLIN)
    assert isinstance(refused, CapRefusal) and refused.kind == "tokens_per_day"
    assert refused.retry_after_sec == 30
    error = refused.error()
    assert (error.status_code, error.error_type, error.code) == (429, "insufficient_quota", "quota_tokens_per_day")
    assert error.headers["Retry-After"] == "30" and error.headers["x-should-retry"] == "false"
    assert "daily token limit" in error.message and "Europe/Berlin" in error.message
    assert "administrator" in error.message

    utc_limiter = RateLimiter(RedisCache(fake_redis_pool), clock=clock)
    assert isinstance(await utc_limiter.admit(tenant_id="utc", caps=caps, tokens=100, zone=UTC_ZONE), Reservation)

    clock.now += 60  # 23:00:30 UTC: still 10 November in UTC, 11 November in Berlin
    again = await limiter.admit(tenant_id="berlin", caps=caps, tokens=1, zone=BERLIN)
    assert isinstance(again, Reservation) and again.keys["tokens_per_day"].endswith(":2026-11-11")
    still = await utc_limiter.admit(tenant_id="utc", caps=caps, tokens=1, zone=UTC_ZONE)
    assert isinstance(still, CapRefusal) and still.retry_after_sec == 3570
    assert still.error().headers["x-ratelimit-reset-tokens"] == "59m30s"


@pytest.mark.anyio
async def test_refusals_name_the_limit_and_say_when_to_retry(fake_redis_pool: ConnectionPool) -> None:
    limiter = RateLimiter(RedisCache(fake_redis_pool), clock=_Clock(T0))
    await limiter.admit(tenant_id="t", caps=Caps(rpm=1), tokens=1, zone=UTC_ZONE)
    rpm = await limiter.admit(tenant_id="t", caps=Caps(rpm=1), tokens=1, zone=UTC_ZONE)
    assert isinstance(rpm, CapRefusal)
    error = rpm.error()
    assert (error.status_code, error.error_type, error.code) == (429, "rate_limit_error", "rate_limit_rpm")
    assert error.headers["Retry-After"] == "50" and "x-should-retry" not in error.headers
    assert error.message == (
        "This instance's limit of requests per minute is reached. Try again in 50s, "
        "or ask an administrator to raise the limit."
    )

    # One request larger than the cap itself: waiting does not help.
    huge = await limiter.admit(tenant_id="u", caps=Caps(tpm=100), tokens=500, zone=UTC_ZONE)
    assert isinstance(huge, CapRefusal) and huge.too_large
    error = huge.error()
    assert error.code == "rate_limit_tpm" and error.headers["x-should-retry"] == "false"
    assert "max_tokens" in error.message


@pytest.mark.anyio
async def test_without_redis_the_caps_let_everything_in() -> None:
    limiter = RateLimiter(NoOpCache(), clock=_Clock(T0))
    reserved = await limiter.admit(tenant_id="t", caps=Caps(rpm=1, tpm=10), tokens=5, zone=UTC_ZONE)
    assert isinstance(reserved, Reservation)
    assert reserved.headers["x-ratelimit-remaining-tokens"] == "5"
    await limiter.settle(reserved, 3)


def test_a_policy_without_caps_has_none() -> None:
    assert not Caps.of(None)
    assert not Caps.of(TenantLLMPolicy(tenant_id="t", rpm_limit=None, tpm_limit=0, tokens_per_day=None))
    assert Caps.of(TenantLLMPolicy(tenant_id="t", rpm_limit=5, tpm_limit=None, tokens_per_day=9)) == Caps(
        rpm=5, tokens_per_day=9,
    )


# ── Through the routes ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _one_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """The caps count in one minute here: a test's requests never straddle two windows."""
    monkeypatch.setattr(ratelimit, "_now", _Clock(T0))


ASK = {"model": "qwen3-32b", "messages": [{"role": "user", "content": "hello"}]}
#: What ASK reserves: "user" and "hello" (one token each) and the default answer.
RESERVED = 2 + 1024


def _answers(monkeypatch: pytest.MonkeyPatch, usage: dict | None) -> None:
    async def complete(self: LLMAdapter, **kwargs: object) -> CompletionResult:  # noqa: ARG001
        payload: dict = {
            "id": "c", "object": "chat.completion", "created": 1, "model": "qwen3-32b",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        }
        if usage is not None:
            payload["usage"] = usage
        return CompletionResult(status_code=200, payload=payload)

    monkeypatch.setattr(LLMAdapter, "completion", complete)


USAGE = {
    "prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
    "prompt_tokens_details": {"cached_tokens": 6}, "completion_tokens_details": {"reasoning_tokens": 2},
}


async def _caps(db: AsyncSession, **caps: int | None) -> None:
    policy = await db.get(TenantLLMPolicy, "tenant-a")
    assert policy is not None
    for name, value in caps.items():
        setattr(policy, name, value)
    await db.commit()


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


async def _rows(db: AsyncSession) -> list[LLMGatewayRequestLog]:
    return list((await db.execute(select(LLMGatewayRequestLog))).scalars())


@pytest.mark.anyio
async def test_a_refusal_reads_as_openai_writes_it_and_every_answer_carries_the_headers(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_basic_graph(db_session)
    await _caps(db_session, rpm_limit=1, tpm_limit=100_000)
    fastapi_app.state.gateway_observability = _FakeObservability()
    _answers(monkeypatch, USAGE)

    ok = await client.post("/v1/chat/completions", headers=_headers(), json=ASK)
    assert ok.status_code == 200
    assert ok.headers["x-ratelimit-limit-requests"] == "1"
    assert ok.headers["x-ratelimit-remaining-requests"] == "0"
    assert re.fullmatch(r"\d+s", ok.headers["x-ratelimit-reset-requests"])
    assert ok.headers["x-ratelimit-limit-tokens"] == "100000"
    assert ok.headers["x-ratelimit-remaining-tokens"] == str(100_000 - RESERVED)

    refused = await client.post("/v1/chat/completions", headers=_headers(), json=ASK)
    assert refused.status_code == 429
    body = refused.json()
    assert set(body) == {"error"} and body["error"]["param"] is None
    assert (body["error"]["type"], body["error"]["code"]) == ("rate_limit_error", "rate_limit_rpm")
    assert "requests per minute" in body["error"]["message"] and "administrator" in body["error"]["message"]
    assert 1 <= int(refused.headers["retry-after"]) <= 60
    assert refused.headers["x-ratelimit-remaining-requests"] == "0"
    assert "x-should-retry" not in refused.headers

    rows = {row.status_code: row for row in await _rows(db_session)}
    answered, turned_away = rows[200], rows[429]
    assert (answered.prompt_tokens, answered.completion_tokens) == (10, 3)
    assert (answered.cached_tokens, answered.reasoning_tokens, answered.usage_source) == (6, 2, "provider")
    assert (turned_away.status_code, turned_away.error_code) == (429, "rate_limit_rpm")
    assert (turned_away.prompt_tokens, turned_away.usage_source) == (None, None), "refused: logged, no tokens"


@pytest.mark.anyio
async def test_the_daily_cap_counts_what_was_used_not_what_was_reserved(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    fake_redis_pool: ConnectionPool,
) -> None:
    await _seed_basic_graph(db_session)
    # Room for one reservation; the first request's real 13 tokens leave room for a second.
    await _caps(db_session, rpm_limit=None, tpm_limit=None, tokens_per_day=RESERVED + 13)
    fastapi_app.state.gateway_observability = _FakeObservability()
    _answers(monkeypatch, USAGE)

    first = await client.post("/v1/chat/completions", headers=_headers(), json=ASK)
    assert first.status_code == 200 and "x-ratelimit-limit-requests" not in first.headers
    assert first.headers["x-ratelimit-limit-tokens"] == str(RESERVED + 13)
    assert await _count(fake_redis_pool, "ratelimit:tpd:tenant-a:*") == 13
    assert (await client.post("/v1/chat/completions", headers=_headers(), json=ASK)).status_code == 200
    assert await _count(fake_redis_pool, "ratelimit:tpd:tenant-a:*") == 26

    refused = await client.post("/v1/chat/completions", headers=_headers(), json=ASK)
    assert refused.status_code == 429
    error = refused.json()["error"]
    assert (error["type"], error["code"]) == ("insufficient_quota", "quota_tokens_per_day")
    assert "daily token limit" in error["message"]
    assert refused.headers["x-should-retry"] == "false" and int(refused.headers["retry-after"]) > 0
    assert await _count(fake_redis_pool, "ratelimit:tpd:tenant-a:*") == 26, "a refusal counts nothing"

    # A request larger than the day's cap is told so.
    huge = await client.post("/v1/chat/completions", headers=_headers(), json={**ASK, "max_tokens": 50_000})
    assert huge.status_code == 429 and "max_tokens" in huge.json()["error"]["message"]


async def _eventually(check: object, *, tries: int = 100) -> None:
    for _ in range(tries):
        if await check():  # type: ignore[operator]
            return
        await asyncio.sleep(0.02)
    raise AssertionError("never came true")


@pytest.mark.anyio
async def test_an_admitted_stream_is_never_cut_and_its_overshoot_counts_afterwards(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    fake_redis_pool: ConnectionPool,
) -> None:
    await _seed_basic_graph(db_session)
    await _caps(db_session, rpm_limit=None, tpm_limit=RESERVED + 10)
    fastapi_app.state.gateway_observability = _FakeObservability()

    async def stream(self: LLMAdapter, **kwargs: object) -> AsyncIterator[bytes]:  # noqa: ARG001
        async def events() -> AsyncIterator[bytes]:
            for _ in range(5):
                yield b'data: {"id":"c","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"word "}}]}\n\n'
            yield (
                b'data: {"id":"c","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":4000,"completion_tokens":2000,"total_tokens":6000,'
                b'"completion_tokens_details":{"reasoning_tokens":1500}}}\n\n'
            )
            yield b"data: [DONE]\n\n"

        return events()

    monkeypatch.setattr(LLMAdapter, "completion", stream)
    response = await client.post("/v1/chat/completions", headers=_headers(), json={**ASK, "stream": True})
    assert response.status_code == 200 and response.text.count("word") == 5 and "[DONE]" in response.text
    assert response.headers["x-ratelimit-limit-tokens"] == str(RESERVED + 10)

    async def counted() -> bool:
        return await _count(fake_redis_pool, "ratelimit:tpm:tenant-a:*") == 6000

    await _eventually(counted)
    refused = await client.post("/v1/chat/completions", headers=_headers(), json=ASK)
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "rate_limit_tpm"

    async def logged() -> bool:
        rows = await _rows(db_session)
        return any(r.stream and r.reasoning_tokens == 1500 and r.usage_source == "provider" for r in rows)

    await _eventually(logged)


@pytest.mark.anyio
async def test_an_answer_without_counts_is_estimated_and_marked_so(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_basic_graph(db_session)
    fastapi_app.state.gateway_observability = _FakeObservability()
    _answers(monkeypatch, None)
    assert (await client.post("/v1/chat/completions", headers=_headers(), json=ASK)).status_code == 200
    [row] = await _rows(db_session)
    assert row.usage_source == "estimated"
    # What went to the model, the gateway's line with the date included; "ok" is one token.
    assert row.prompt_tokens is not None and row.prompt_tokens > 2
    assert (row.completion_tokens, row.total_tokens) == (1, row.prompt_tokens + 1)


@pytest.mark.anyio
async def test_a_whole_answer_after_tool_rounds_counts_every_round(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As a streamed one does: the last round alone under-counted what the request used."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    _Model(monkeypatch, _call("current_time", {}), _say("Noon."))  # 5 in and 3 out, each round
    r = await _ask(client)
    assert r.status_code == 200
    assert r.json()["usage"] == {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}
    [row] = await _rows(db_session)
    assert (row.prompt_tokens, row.completion_tokens, row.usage_source) == (10, 6, "provider")


@pytest.mark.anyio
async def test_a_refused_request_counts_for_its_persons_team(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_basic_graph(db_session)
    await _caps(db_session, rpm_limit=1, tpm_limit=None)
    fastapi_app.state.gateway_observability = _FakeObservability()
    _answers(monkeypatch, USAGE)

    async def fetch(user_id: str) -> object:  # noqa: ARG001
        return UsageTeam("sales-north", ("company", "sales", "sales-north"))

    fastapi_app.state.usage_group_resolver = UsageGroupResolver(fetch)
    assert (await client.post("/v1/chat/completions", headers=_headers(), json=ASK)).status_code == 200
    assert (await client.post("/v1/chat/completions", headers=_headers(), json=ASK)).status_code == 429
    rows = {row.status_code: row for row in await _rows(db_session)}
    refused = rows[429]
    assert (refused.group_id, refused.group_path) == ("sales-north", ["company", "sales", "sales-north"]), (
        "a refusal before the lookups still counts for the person's team"
    )


@pytest.mark.anyio
async def test_the_log_keeps_the_teams_path_at_the_time_of_use(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_basic_graph(db_session)
    fastapi_app.state.gateway_observability = _FakeObservability()
    _answers(monkeypatch, USAGE)
    teams = {"user-1": UsageTeam("sales-north", ("company", "sales", "sales-north")), "user-2": "solo"}

    async def fetch(user_id: str) -> object:
        return teams.get(user_id)

    fastapi_app.state.usage_group_resolver = UsageGroupResolver(fetch)
    for sub in ("user-1", "user-2", "user-3"):
        headers = {"Authorization": f"Bearer {_token(sub=sub)}"}
        assert (await client.post("/v1/chat/completions", headers=headers, json=ASK)).status_code == 200
    paths = {r.user_id: (r.group_id, r.group_path) for r in await _rows(db_session)}
    assert paths == {
        "user-1": ("sales-north", ["company", "sales", "sales-north"]),
        "user-2": ("solo", ["solo"]),  # a resolver that answers with the team's id alone
        "user-3": (None, None),
    }
