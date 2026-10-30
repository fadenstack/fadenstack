"""The extensions' admission checks and usage listeners (C3).

When they are called, with what, and what a refusal or a failure of theirs does.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis.asyncio import ConnectionPool
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.services.extensions import AdmissionContext, Refusal, UsageRecord
from faden_gateway.services.extensions import registry as extension_registry
from faden_gateway.services.gateway import usage_hooks
from faden_gateway.services.gateway.service import GatewayService
from faden_gateway.services.gateway.usage_group import UsageGroupResolver, UsageTeam
from tests.test_gateway_api import _FakeObservability, _seed_basic_graph
from tests.test_usage_caps import ASK, RESERVED, USAGE, _answers, _caps, _count, _headers, _rows

BUDGET = Refusal(
    retry_after_sec=3600,
    message="This team's monthly budget is used up. Ask an administrator.",
    headers={"x-faden-budget": "team"},
)


@pytest.fixture
async def gateway(
    fastapi_app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    await _seed_basic_graph(db_session)
    await _caps(db_session, rpm_limit=5, tpm_limit=100_000)
    fastapi_app.state.gateway_observability = _FakeObservability()

    async def team(user_id: str) -> UsageTeam:  # noqa: ARG001
        return UsageTeam("sales-north", ("company", "sales", "sales-north"))

    fastapi_app.state.usage_group_resolver = UsageGroupResolver(team)
    _answers(monkeypatch, USAGE)
    return fastapi_app


def _use(monkeypatch: pytest.MonkeyPatch, *, checks: list[Any] = (), listeners: list[Any] = ()) -> None:  # type: ignore[assignment]
    monkeypatch.setattr(extension_registry, "admission_checks", list(checks))
    monkeypatch.setattr(extension_registry, "usage_listeners", list(listeners))


@pytest.mark.anyio
async def test_a_check_sees_the_request_and_its_refusal_is_answered(
    gateway: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    fake_redis_pool: ConnectionPool,
) -> None:
    seen: list[AdmissionContext] = []

    async def budget(context: AdmissionContext) -> Refusal | None:
        seen.append(context)
        return BUDGET

    heard: list[UsageRecord] = []

    async def listener(record: UsageRecord) -> None:
        heard.append(record)

    _use(monkeypatch, checks=[budget], listeners=[listener])
    r = await client.post("/v1/chat/completions", headers={**_headers(), "x-request-id": "req-budget"}, json=ASK)

    assert r.status_code == 429
    assert r.json() == {"error": {
        "message": BUDGET.message, "type": "insufficient_quota", "code": "budget_exceeded", "param": None,
    }}
    assert r.headers["retry-after"] == "3600" and r.headers["x-should-retry"] == "false"
    assert r.headers["x-faden-budget"] == "team"
    assert r.headers["x-ratelimit-limit-requests"] == "5", "the caps' headers on an extension's refusal too"
    [context] = seen
    assert re.fullmatch(r"[0-9a-f]{32}", context.reservation_id), "the gateway's own id, never the client's"
    assert context == AdmissionContext(
        tenant_id="tenant-a", user_id="user-1", group_id="sales-north", project_id=None, agent_id=None,
        model_alias="qwen3-32b", endpoint="/v1/chat/completions", request_id="req-budget",
        estimated_tokens=RESERVED, group_path=("company", "sales", "sales-north"),
        reservation_id=context.reservation_id,
    )
    # Core's caps count nothing for it: the request and its reservation were given back.
    assert await _count(fake_redis_pool, "ratelimit:rpm:tenant-a:*") == 0
    assert await _count(fake_redis_pool, "ratelimit:tpm:tenant-a:*") == 0
    [row] = await _rows(db_session)
    assert (row.status_code, row.error_code, row.prompt_tokens) == (429, "budget_exceeded", None)
    await usage_hooks.drain()
    assert [(h.status_code, h.estimated_tokens, h.input_tokens) for h in heard] == [(429, 0, 0)]
    assert (heard[0].reservation_id, heard[0].admitted) == (context.reservation_id, False), (
        "the refused request's record names the check's reservation, to be released"
    )


@pytest.mark.anyio
async def test_requests_sharing_a_client_request_id_get_reservations_of_their_own(
    gateway: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client chooses x-request-id: two requests with the same one must not share a budget reservation."""
    seen: list[AdmissionContext] = []
    heard: list[UsageRecord] = []

    async def budget(context: AdmissionContext) -> Refusal | None:
        seen.append(context)
        return None

    async def listener(record: UsageRecord) -> None:
        heard.append(record)

    _use(monkeypatch, checks=[budget], listeners=[listener])
    for _ in range(2):
        r = await client.post("/v1/chat/completions", headers={**_headers(), "x-request-id": "same"}, json=ASK)
        assert r.status_code == 200
    await usage_hooks.drain()

    assert [c.request_id for c in seen] == ["same", "same"]
    assert len({c.reservation_id for c in seen}) == 2
    assert sorted((h.reservation_id, h.admitted) for h in heard) == sorted((c.reservation_id, True) for c in seen)


@pytest.mark.anyio
async def test_a_request_the_caps_refuse_is_heard_as_not_admitted(
    gateway: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    heard: list[UsageRecord] = []

    async def listener(record: UsageRecord) -> None:
        heard.append(record)

    await _caps(db_session, rpm_limit=1)
    _use(monkeypatch, listeners=[listener])
    assert (await client.post("/v1/chat/completions", headers=_headers(), json=ASK)).status_code == 200
    assert (await client.post("/v1/chat/completions", headers=_headers(), json=ASK)).status_code == 429
    await usage_hooks.drain()
    assert [(h.status_code, h.admitted) for h in heard] == [(200, True), (429, False)]
    assert all(re.fullmatch(r"[0-9a-f]{32}", h.reservation_id) for h in heard)


@pytest.mark.anyio
@pytest.mark.parametrize("sent", ["x" * 129, "has spaces", "slash/es", "naïve", ""])
async def test_a_client_request_id_the_log_cannot_hold_is_replaced(
    gateway: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, sent: str,
) -> None:
    """Up to 128 of A-Z a-z 0-9 . _ : - is kept; anything else would fail the log's insert, and is replaced."""
    _use(monkeypatch)
    headers = {**_headers(), "x-request-id": sent.encode("utf-8")}
    r = await client.post("/v1/chat/completions", headers=headers, json=ASK)
    assert r.status_code == 200
    assert r.headers["x-request-id"] != sent and len(r.headers["x-request-id"]) <= 128
    [row] = await _rows(db_session)
    assert row.request_id == r.headers["x-request-id"]


@pytest.mark.anyio
async def test_a_good_client_request_id_is_kept(
    gateway: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch)
    sent = "trace-01.a_b:c-" + "x" * 113
    r = await client.post("/v1/chat/completions", headers={**_headers(), "x-request-id": sent}, json=ASK)
    assert r.headers["x-request-id"] == sent


@pytest.mark.anyio
async def test_the_log_says_which_requests_a_cap_or_a_check_refused(
    gateway: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever code a check chooses, its refusal's row says ``refused``; a cap's too; an answer's does not."""
    asked: list[int] = []

    async def spend_cap(context: AdmissionContext) -> Refusal | None:  # noqa: ARG001
        asked.append(1)
        return Refusal(code="team_spend_cap", message="Over the team's spend.") if len(asked) == 2 else None

    await _caps(db_session, rpm_limit=2)  # the refused second gives its count back
    _use(monkeypatch, checks=[spend_cap])
    codes = [(await client.post("/v1/chat/completions", headers=_headers(), json=ASK)).status_code for _ in range(4)]
    assert codes == [200, 429, 200, 429]
    rows = sorted(await _rows(db_session), key=lambda row: row.created_at)
    assert [(row.error_code, row.refused) for row in rows] == [
        (None, False), ("team_spend_cap", True), (None, False), ("rate_limit_rpm", True),
    ]


def test_a_record_built_without_the_new_fields_counts_as_admitted() -> None:
    fields = {f.name for f in dataclasses.fields(UsageRecord)}
    assert {"reservation_id", "admitted"} <= fields
    assert UsageRecord.__dataclass_fields__["admitted"].default is True


@pytest.mark.anyio
async def test_checks_run_side_by_side_and_the_first_refusal_in_order_is_answered(
    gateway: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    running: list[str] = []
    both = asyncio.Event()

    def refusing(code: str) -> Any:
        async def check(context: AdmissionContext) -> Refusal | None:  # noqa: ARG001
            running.append(code)
            if len(running) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 2)  # each waits for the other: they run at once
            return Refusal(code=code, message=f"Refused by {code}.")

        return check

    _use(monkeypatch, checks=[refusing("first_budget"), refusing("second_budget")])
    r = await client.post("/v1/chat/completions", headers=_headers(), json=ASK)
    assert r.status_code == 429 and r.json()["error"]["code"] == "first_budget"
    assert sorted(running) == ["first_budget", "second_budget"]
    assert "retry-after" not in r.headers, "no Retry-After without a time to retry"


@pytest.mark.anyio
async def test_a_check_that_raises_is_logged_and_does_not_refuse(
    gateway: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(context: AdmissionContext) -> Refusal | None:  # noqa: ARG001
        raise RuntimeError("budget store away")

    async def fine(context: AdmissionContext) -> Refusal | None:  # noqa: ARG001
        return None

    _use(monkeypatch, checks=[broken, fine])
    with caplog.at_level(logging.ERROR):
        r = await client.post("/v1/chat/completions", headers=_headers(), json=ASK)
    assert r.status_code == 200
    assert "An admission check failed" in caplog.text


@pytest.mark.anyio
async def test_without_checks_nothing_is_looked_up_for_them(
    gateway: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def never(*args: object, **kwargs: object) -> AdmissionContext:
        raise AssertionError("an admission context was built with no check to read it")

    monkeypatch.setattr(GatewayService, "_admission_context", never)
    _use(monkeypatch)
    assert (await client.post("/v1/chat/completions", headers=_headers(), json=ASK)).status_code == 200


@pytest.mark.anyio
async def test_listeners_hear_each_request_after_its_row_is_written(
    gateway: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    heard: list[UsageRecord] = []
    insert = GatewayDAO.insert_request_log

    async def written(self: GatewayDAO, **fields: Any) -> Any:
        row = await insert(self, **fields)
        events.append("row")
        return row

    async def broken(record: UsageRecord) -> None:
        raise RuntimeError("counter store away")

    async def listener(record: UsageRecord) -> None:
        events.append("heard")
        heard.append(record)

    monkeypatch.setattr(GatewayDAO, "insert_request_log", written)
    _use(monkeypatch, listeners=[broken, listener])
    with caplog.at_level(logging.ERROR):
        r = await client.post("/v1/chat/completions", headers={**_headers(), "x-request-id": "req-1"}, json=ASK)
        assert r.status_code == 200
        await usage_hooks.drain()
    assert events == ["row", "heard"]
    [record] = heard
    assert (record.tenant_id, record.user_id, record.request_id, record.status_code) == (
        "tenant-a", "user-1", "req-1", 200,
    )
    assert (record.input_tokens, record.cached_input_tokens, record.output_tokens, record.reasoning_tokens) == (
        10, 6, 3, 2,
    )
    assert (record.group_id, record.group_path) == ("sales-north", ("company", "sales", "sales-north"))
    assert record.estimated_tokens == RESERVED and record.model_alias == "qwen3-32b"
    assert record.admitted is True and re.fullmatch(r"[0-9a-f]{32}", record.reservation_id)
    assert record.provider_instance_id is not None
    assert (record.cost, record.currency) == (None, None), "no price applies here"
    assert "A usage listener failed" in caplog.text


@pytest.mark.anyio
async def test_a_refusal_with_its_own_status_and_type() -> None:
    error = usage_hooks.refused(
        Refusal(status=429, error_type="rate_limit_error", code="team_rpm", message="", retry_after_sec=20),
        {"x-ratelimit-limit-requests": "5"},
    )
    assert (error.status_code, error.error_type, error.code) == (429, "rate_limit_error", "team_rpm")
    assert error.headers == {"x-ratelimit-limit-requests": "5", "Retry-After": "20"}
    assert "administrator" in error.message, "a refusal without words still says whom to ask"
    assert error.param is None


@pytest.mark.anyio
async def test_a_refusal_names_its_limit_in_param() -> None:
    error = usage_hooks.refused(Refusal(code="budget_exceeded", message="Over the team's budget.", param="team"))
    assert (error.code, error.param) == ("budget_exceeded", "team"), "a client words it from code and param"


@pytest.mark.anyio
async def test_no_listeners_no_tasks() -> None:
    usage_hooks.notify([], None)  # type: ignore[arg-type]
    await usage_hooks.drain()
