"""The usage rollups: what the job writes from the request log, how it backfills and purges, and erasure.

The job's queries are Postgres's (``NULLS NOT DISTINCT``, advisory locks, time zones), so most of this runs
on a gateway database migrated to head, named by ``FADEN_TEST_GATEWAY_DSN``
(``postgresql+asyncpg://user:pass@host:5432/db``), and is skipped without one. Each test empties the
request log and the rollup tables first: use a database of its own.
"""

from __future__ import annotations

import itertools
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from faden_gateway.db.models.gateway import LLMGatewayRequestLog, LLMToolCallLog, PriceCatalog
from faden_gateway.db.models.usage import (
    UsageDaily,
    UsageDailyPerson,
    UsageErased,
    UsageHourly,
    UsageRollupState,
)
from faden_gateway.services.gateway import erasure, usage_group, usage_rollup, usage_settings
from faden_gateway.services.gateway.usage_group import PATH_LEVELS, BackendUserGroups, UsageTeam
from faden_gateway.services.gateway.usage_settings import UsageSettings

DSN = os.environ.get("FADEN_TEST_GATEWAY_DSN", "")
needs_postgres = pytest.mark.skipif(
    not DSN, reason="FADEN_TEST_GATEWAY_DSN names no Postgres (postgresql+asyncpg://...)",
)

NOW = datetime(2026, 11, 10, 12, 20, tzinfo=UTC)
H11 = datetime(2026, 11, 10, 11, 0, tzinfo=UTC)
PROVIDER = uuid.uuid4()
UTC_SETTINGS = UsageSettings(time_zone="UTC")


# ── Without a database ───────────────────────────────────────────────


def test_the_migrations_follow_the_last_one_and_leave_one_head() -> None:
    scripts = ScriptDirectory.from_config(Config(str(Path(__file__).parents[1] / "alembic.ini")))
    assert scripts.get_heads() == ["u5ag3ers"]
    assert scripts.get_revision("u5ag3ers").down_revision == "u5ag3rfsd"
    assert scripts.get_revision("u5ag3rfsd").down_revision == "u5ag3l1m"
    assert scripts.get_revision("u5ag3l1m").down_revision == "u5ag3r0ll"
    assert scripts.get_revision("u5ag3r0ll").down_revision == "r3c31pt1d"


def test_settings_out_of_range_take_the_nearest_end_and_a_bad_zone_the_default() -> None:
    found = usage_settings.from_values({
        "request_log_retention_days": 3, "person_retention_days": "999", "hourly_retention_days": None,
        "daily_retention_months": True, "time_zone": "Mars/Olympus", "person_reporting": True,
    })
    assert found == UsageSettings(
        time_zone="UTC", request_log_retention_days=7, person_retention_days=400, hourly_retention_days=90,
        daily_retention_months=25,
    )
    assert usage_settings.from_values({"time_zone": "Europe/Berlin"}).time_zone == "Europe/Berlin"
    assert usage_settings.from_values({}) == usage_settings.defaults()


def test_months_back_land_on_the_shorter_months_last_day() -> None:
    assert usage_rollup._months_before(date(2026, 10, 28), 25) == date(2024, 9, 28)  # noqa: SLF001
    assert usage_rollup._months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)  # noqa: SLF001
    assert usage_rollup._months_before(date(2026, 1, 15), 13) == date(2024, 12, 15)  # noqa: SLF001


# ── On Postgres ──────────────────────────────────────────────────────


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(DSN)
    async with engine.begin() as conn:
        await conn.execute(text(
            "TRUNCATE llm_gateway_request_log, llm_tool_call_log, usage_hourly, usage_daily, usage_daily_person, "
            "usage_erased, price_catalog CASCADE",
        ))
        await conn.execute(text(
            "UPDATE usage_rollup_state SET backfilled_through = NULL, last_run_at = NULL, last_purge_at = NULL, "
            "purge_from = NULL",
        ))
    try:
        yield engine
    finally:
        await engine.dispose()


async def _log(engine: AsyncEngine, at: datetime, **fields: Any) -> uuid.UUID:
    row_id = uuid.uuid4()
    row = {
        "id": row_id, "request_id": f"r-{row_id.hex[:8]}", "tenant_id": "t1", "user_id": "u1",
        "endpoint": "/v1/chat/completions", "status_code": 200, "latency_ms": 5, "model_alias": "m1",
        "provider_instance_id": PROVIDER, "currency": None, "created_at": at, **fields,
    }
    async with engine.begin() as conn:
        await conn.execute(insert(LLMGatewayRequestLog).values(**row))
    return row_id


async def _price(engine: AsyncEngine) -> uuid.UUID:
    price_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(insert(PriceCatalog).values(
            id=price_id, provider="vllm", model="m1", input_price_per_1k=1, output_price_per_1k=2, currency="USD",
        ))
    return price_id


async def _all(engine: AsyncEngine, model: Any, *order: Any) -> list[dict[str, Any]]:
    columns = [c for c in model.__table__.c if c.name != "id"]
    async with engine.connect() as conn:
        rows = (await conn.execute(select(*columns).order_by(*order))).mappings().all()
    return [dict(r) for r in rows]


def _cell(rows: list[dict[str, Any]], **keys: Any) -> dict[str, Any]:
    found = [r for r in rows if all(r[k] == v for k, v in keys.items())]
    assert len(found) == 1, (keys, rows)
    return found[0]


async def _state(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as conn:
        return dict((await conn.execute(select(UsageRollupState.__table__))).mappings().one())


@needs_postgres
@pytest.mark.anyio
async def test_hours_and_days_sum_the_log_cell_by_cell(engine: AsyncEngine) -> None:
    price = await _price(engine)
    team = {"group_id": "g1", "group_path": ["g1"]}
    priced = {"price_catalog_id": price, "currency": "USD"}
    await _log(engine, H11 + timedelta(minutes=10), **team, **priced, user_id="u1", prompt_tokens=100,
               cached_tokens=40, completion_tokens=20, reasoning_tokens=5, estimated_total_cost=Decimal("0.01"))
    await _log(engine, H11 + timedelta(minutes=20), **team, **priced, user_id="u2", prompt_tokens=50,
               completion_tokens=10, estimated_total_cost=Decimal("0.02"))
    # A price applies, though the refused request has no cost: its currency's cell.
    await _log(engine, H11 + timedelta(minutes=25), **team, **priced, user_id="u2", status_code=429,
               error_code="rate_limit_tpm")
    # No price: the cell without a currency (the log's column says USD all the same).
    await _log(engine, H11 + timedelta(minutes=30), **team, user_id="u1", prompt_tokens=7, currency="USD")
    # No team, no agent: NULL keys make one cell.
    await _log(engine, H11 + timedelta(minutes=40), user_id="u3", status_code=429, error_code="rate_limit_rpm")
    await _log(engine, H11 + timedelta(minutes=45), user_id="u3", status_code=502, error_code="upstream_failed")
    await _log(engine, H11 + timedelta(minutes=50), user_id="u4", status_code=429, error_code="budget_exceeded")
    await _log(engine, H11 + timedelta(minutes=55), user_id="u5", status_code=429, error_code="quota_tokens_per_day")
    await _log(engine, NOW - timedelta(minutes=10), **team, user_id="u1", prompt_tokens=3, completion_tokens=1)
    await _log(engine, H11 + timedelta(minutes=5), tenant_id="t2", user_id="v1", prompt_tokens=1)

    run = await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)
    assert run is not None and run.backfilled_through == H11

    hourly = await _all(engine, UsageHourly, UsageHourly.hour)
    usd = _cell(hourly, hour=H11, tenant_id="t1", currency="USD")
    assert usd == {
        "hour": H11, "tenant_id": "t1", "group_id": "g1", "group_path": ["g1"], "agent_id": None,
        "model_alias": "m1", "provider_instance_id": PROVIDER, "currency": "USD", "requests": 3, "failed": 0,
        "refused": 1, "input_tokens": 150, "cached_input_tokens": 40, "output_tokens": 30, "reasoning_tokens": 5,
        "cost": Decimal("0.0300000000"), "people": 2,
    }
    unpriced = _cell(hourly, hour=H11, tenant_id="t1", group_id="g1", currency=None)
    assert (unpriced["requests"], unpriced["input_tokens"], unpriced["cost"], unpriced["people"]) == (1, 7, None, 1)
    nobody = _cell(hourly, hour=H11, tenant_id="t1", group_id=None)
    assert (nobody["group_path"], nobody["agent_id"], nobody["currency"]) == (None, None, None)
    # Every request counts; failed and refused are parts of it.
    assert (nobody["requests"], nobody["failed"], nobody["refused"], nobody["people"]) == (4, 1, 3, 3)
    assert _cell(hourly, hour=H11 + timedelta(hours=1), tenant_id="t1")["input_tokens"] == 3
    assert _cell(hourly, tenant_id="t2")["requests"] == 1

    daily = await _all(engine, UsageDaily, UsageDaily.day)
    day = _cell(daily, day=date(2026, 11, 10), tenant_id="t1", group_id="g1", currency=None)
    assert (day["requests"], day["input_tokens"], day["output_tokens"], day["people"]) == (2, 10, 1, 1)
    assert _cell(daily, day=date(2026, 11, 10), tenant_id="t1", currency="USD")["requests"] == 3

    person = await _all(engine, UsageDailyPerson, UsageDailyPerson.user_id)
    mine = [r for r in person if r["user_id"] == "u1"]
    assert sorted((r["currency"] or "", r["requests"], r["input_tokens"]) for r in mine) == [
        ("", 2, 10), ("USD", 1, 100),
    ]
    assert {r["user_id"] for r in person} == {"u1", "u2", "u3", "u4", "u5", "v1"}
    assert all(r["day"] == date(2026, 11, 10) and r["project_id"] is None for r in person)


@needs_postgres
@pytest.mark.anyio
async def test_a_refusal_counts_by_the_logs_own_flag_whatever_its_code(engine: AsyncEngine) -> None:
    """An extension's check chooses its own code: the log's ``refused`` says it was a refusal.

    A row from before the flag (NULL) is known by its code, as before; an explicit false is not a refusal,
    whatever the code says.
    """
    await _log(engine, H11, user_id="a", status_code=429, error_code="team_spend_cap", refused=True)
    await _log(engine, H11, user_id="e", status_code=402, error_code="tenant_spend_cap", refused=True)
    await _log(engine, H11, user_id="b", status_code=429, error_code="quota_tokens_per_day", refused=None)
    await _log(engine, H11, user_id="c", status_code=429, error_code="budget_from_upstream", refused=False)
    await _log(engine, H11, user_id="d", status_code=200, refused=False)
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)
    [hour] = await _all(engine, UsageHourly)
    assert (hour["requests"], hour["refused"], hour["failed"]) == (5, 3, 1)


@needs_postgres
@pytest.mark.anyio
async def test_a_run_again_writes_the_same_and_picks_up_what_came_since(engine: AsyncEngine) -> None:
    await _log(engine, H11 + timedelta(minutes=10), prompt_tokens=10)
    await _log(engine, NOW - timedelta(minutes=5), prompt_tokens=1)
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)
    first = (await _all(engine, UsageHourly, UsageHourly.hour), await _all(engine, UsageDaily, UsageDaily.day))
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)
    assert (await _all(engine, UsageHourly, UsageHourly.hour), await _all(engine, UsageDaily, UsageDaily.day)) == first

    await _log(engine, NOW - timedelta(minutes=1), prompt_tokens=100, user_id="u2")
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW + timedelta(minutes=5))
    hourly = await _all(engine, UsageHourly, UsageHourly.hour)
    assert [(r["requests"], r["input_tokens"], r["people"]) for r in hourly] == [(1, 10, 1), (2, 101, 2)]
    [day] = await _all(engine, UsageDaily, UsageDaily.day)
    assert (day["requests"], day["input_tokens"]) == (3, 111)


@needs_postgres
@pytest.mark.anyio
async def test_the_first_run_backfills_history_and_purges_only_after_its_grace(engine: AsyncEngine) -> None:
    old = NOW - timedelta(days=120)
    await _log(engine, old, prompt_tokens=10, user_id="gone-by-then")
    await _log(engine, NOW - timedelta(days=10), prompt_tokens=20)
    await _log(engine, H11 + timedelta(minutes=10), prompt_tokens=30)
    settings = UsageSettings(time_zone="UTC")  # 90 days of log, person rows and hours; 25 months of days

    first = await usage_rollup.run_once(engine, settings, now=NOW)
    assert first is not None and first.caught_up_from == old.replace(minute=0)
    assert first.backfilled_through == H11 and first.purge_from == NOW + usage_rollup.PURGE_GRACE
    assert first.purged == {}, "nothing is deleted in the week after the backfill"
    days = {r["day"]: r["input_tokens"] for r in await _all(engine, UsageDaily, UsageDaily.day)}
    assert days == {old.date(): 10, (NOW - timedelta(days=10)).date(): 20, NOW.date(): 30}
    assert len(await _all(engine, UsageHourly, UsageHourly.hour)) == 3
    state = await _state(engine)
    assert (state["backfilled_through"], state["purge_from"], state["last_purge_at"]) == (
        H11, NOW + usage_rollup.PURGE_GRACE, None,
    )

    later = NOW + timedelta(days=8)
    second = await usage_rollup.run_once(engine, settings, now=later)
    assert second is not None and second.purge_from == first.purge_from, "set once, at the first backfill"
    assert second.purged == {"request_log": 1, "usage_daily_person": 1, "usage_hourly": 1, "usage_daily": 0}
    async with engine.connect() as conn:
        left = (await conn.execute(select(LLMGatewayRequestLog.prompt_tokens))).scalars().all()
    assert sorted(left) == [20, 30]
    # The day keeps its total though its log rows and person rows are gone.
    days = {r["day"]: r["input_tokens"] for r in await _all(engine, UsageDaily, UsageDaily.day)}
    assert days[old.date()] == 10
    assert (await _state(engine))["last_purge_at"] == later


@needs_postgres
@pytest.mark.anyio
async def test_without_the_settings_read_nothing_is_purged(engine: AsyncEngine) -> None:
    await _log(engine, NOW - timedelta(days=200), prompt_tokens=10)
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)
    run = await usage_rollup.run_once(engine, None, now=NOW + timedelta(days=30))
    assert run is not None and run.purged == {}
    async with engine.connect() as conn:
        assert (await conn.execute(select(LLMGatewayRequestLog.id))).scalars().all() != []


@needs_postgres
@pytest.mark.anyio
async def test_hours_and_days_missed_while_down_are_caught_up(engine: AsyncEngine) -> None:
    await _log(engine, H11, prompt_tokens=1)
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)
    gap = NOW + timedelta(days=2, hours=3)
    await _log(engine, NOW + timedelta(hours=5), prompt_tokens=7)  # an hour no run saw as current
    await _log(engine, NOW + timedelta(days=1, hours=1), prompt_tokens=9)
    run = await usage_rollup.run_once(engine, UTC_SETTINGS, now=gap)
    assert run is not None and run.caught_up_from == H11
    hours = {r["hour"]: r["input_tokens"] for r in await _all(engine, UsageHourly, UsageHourly.hour)}
    assert hours[(NOW + timedelta(hours=5)).replace(minute=0)] == 7
    assert hours[(NOW + timedelta(days=1, hours=1)).replace(minute=0)] == 9
    days = {r["day"]: r["input_tokens"] for r in await _all(engine, UsageDaily, UsageDaily.day)}
    assert days == {NOW.date(): 8, (NOW + timedelta(days=1)).date(): 9}


@needs_postgres
@pytest.mark.anyio
async def test_days_are_days_of_the_usage_time_zone(engine: AsyncEngine) -> None:
    # 23:30 UTC on 9 November is 00:30 on 10 November in Berlin; 22:30 UTC on 10 November is 23:30 there.
    await _log(engine, datetime(2026, 11, 9, 23, 30, tzinfo=UTC), prompt_tokens=1)
    await _log(engine, datetime(2026, 11, 10, 22, 30, tzinfo=UTC), prompt_tokens=2)
    berlin = UsageSettings(time_zone="Europe/Berlin")
    await usage_rollup.run_once(engine, berlin, now=datetime(2026, 11, 10, 23, 20, tzinfo=UTC))
    days = {r["day"]: (r["requests"], r["input_tokens"]) for r in await _all(engine, UsageDaily, UsageDaily.day)}
    assert days == {date(2026, 11, 10): (2, 3)}
    hours = [r["hour"] for r in await _all(engine, UsageHourly, UsageHourly.hour)]
    assert hours == [datetime(2026, 11, 9, 23, tzinfo=UTC), datetime(2026, 11, 10, 22, tzinfo=UTC)]


@needs_postgres
@pytest.mark.anyio
async def test_usage_stays_booked_on_the_teams_path_at_the_time_of_use(engine: AsyncEngine) -> None:
    await _log(engine, H11, group_id="team", group_path=["company", "team"], prompt_tokens=5)
    await _log(engine, H11, group_id="team", group_path=["company", "team"], prompt_tokens=6, user_id="u2")
    # Booked before a reorganisation moved the team, and a row from before teams had paths.
    await _log(engine, H11, group_id="team", group_path=["old-division", "team"], prompt_tokens=7)
    await _log(engine, H11, group_id="team", group_path=None, prompt_tokens=8)
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)
    for model in (UsageHourly, UsageDaily, UsageDailyPerson):
        rows = await _all(engine, model, model.group_path)
        totals: dict[tuple[str, ...], int] = {}
        for r in rows:
            totals[tuple(r["group_path"])] = totals.get(tuple(r["group_path"]), 0) + r["input_tokens"]
        assert totals == {("company", "team"): 11, ("old-division", "team"): 7, ("team",): 8}, model


@needs_postgres
@pytest.mark.anyio
async def test_one_worker_at_a_time(engine: AsyncEngine) -> None:
    await _log(engine, H11, prompt_tokens=1)
    async with engine.connect() as other:
        await other.execute(text("SELECT pg_advisory_lock(:key)"), {"key": usage_rollup.LOCK_KEY})
        assert await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW) is None
        await other.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": usage_rollup.LOCK_KEY})
        await other.commit()
    assert await _all(engine, UsageHourly) == []
    assert await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW) is not None


@needs_postgres
@pytest.mark.anyio
async def test_erasing_a_person_takes_their_log_and_person_rows_and_leaves_the_sums(engine: AsyncEngine) -> None:
    mine = await _log(engine, H11, user_id="leaver", prompt_tokens=10, group_id="g", group_path=["g"])
    await _log(engine, H11, user_id="stayer", prompt_tokens=5, group_id="g", group_path=["g"])
    async with engine.begin() as conn:
        await conn.execute(insert(LLMToolCallLog).values(
            id=uuid.uuid4(), request_log_id=mine, request_id="r", tool_name="search",
        ))
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW + timedelta(days=1))

    async with AsyncSession(engine) as session:
        erased = await erasure.erase_user(session, None, "leaver")
        await session.commit()
    assert (erased.request_logs, erased.usage_days) == (1, 1)
    async with engine.connect() as conn:
        users = (await conn.execute(select(LLMGatewayRequestLog.user_id))).scalars().all()
        calls = (await conn.execute(select(LLMToolCallLog.id))).scalars().all()
    assert (users, calls) == (["stayer"], [])
    assert [r["user_id"] for r in await _all(engine, UsageDailyPerson)] == ["stayer"]
    [hour] = await _all(engine, UsageHourly)
    assert (hour["requests"], hour["input_tokens"], hour["people"]) == (2, 15, 2), "the sums keep their totals"


@needs_postgres
@pytest.mark.anyio
async def test_what_turns_up_again_of_an_erased_person_is_removed_by_every_run_for_30_days(
    engine: AsyncEngine,
) -> None:
    """A run that read the log just before the erasure wrote a person row back; a request ended after it."""
    await _log(engine, H11 - timedelta(days=3), user_id="leaver", prompt_tokens=10)
    await _log(engine, H11 - timedelta(days=3), user_id="stayer", prompt_tokens=5)
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)
    async with AsyncSession(engine) as session:
        await erasure.erase_user(session, None, "leaver")
        await session.commit()
    stale_day = (H11 - timedelta(days=3)).date()
    async with engine.begin() as conn:
        await conn.execute(insert(UsageDailyPerson).values(
            day=stale_day, tenant_id="t1", user_id="leaver", requests=1, input_tokens=10,
        ))
    await _log(engine, NOW - timedelta(minutes=2), user_id="leaver", prompt_tokens=3)

    await usage_rollup.run_once(engine, UTC_SETTINGS, now=NOW)

    assert [r["user_id"] for r in await _all(engine, UsageDailyPerson)] == ["stayer"]
    async with engine.connect() as conn:
        users = (await conn.execute(select(LLMGatewayRequestLog.user_id))).scalars().all()
    assert users == ["stayer"]
    days = {r["day"]: r["input_tokens"] for r in await _all(engine, UsageDaily, UsageDaily.day)}
    assert days[stale_day] == 15, "the closed day's sum keeps its total"

    # The rollup itself writes no person row for them, even from a log row it still sees.
    await _log(engine, NOW - timedelta(minutes=1), user_id="leaver", prompt_tokens=4)
    async with engine.connect() as conn:
        await usage_rollup._days(conn, NOW.date(), NOW.date(), UTC, "UTC")  # noqa: SLF001
        await conn.commit()
    assert {r["user_id"] for r in await _all(engine, UsageDailyPerson)} == {"stayer"}

    later = NOW + usage_rollup.ERASED_KEPT + timedelta(days=1)
    await usage_rollup.run_once(engine, UTC_SETTINGS, now=later)
    assert await _all(engine, UsageErased) == [], "forgotten after 30 days"


# ── The team's path, from the backend's groups ───────────────────────


@pytest.fixture
async def backend_schema() -> AsyncIterator[tuple[str, asyncpg.Connection]]:
    """``user`` and ``groups`` as the backend has them, in a schema of their own; the DSN reaches it."""
    dsn = DSN.replace("postgresql+asyncpg://", "postgresql://")
    schema = f"usage_paths_{uuid.uuid4().hex[:8]}"
    conn = await asyncpg.connect(dsn)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'CREATE TABLE "{schema}".groups (id uuid PRIMARY KEY, name text)')
    await conn.execute(f'CREATE TABLE "{schema}"."user" (id uuid PRIMARY KEY, usage_group_id uuid)')
    await conn.execute(f'SET search_path TO "{schema}"')
    try:
        yield f"{dsn}?search_path={schema}", conn
    finally:
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


@needs_postgres
@pytest.mark.anyio
async def test_a_teams_path_walks_up_its_parents_once_teams_have_them(
    backend_schema: tuple[str, asyncpg.Connection],
) -> None:
    dsn, conn = backend_schema
    chain = [str(uuid.uuid4()) for _ in range(PATH_LEVELS + 2)]  # root first
    for team in chain:
        await conn.execute("INSERT INTO groups (id, name) VALUES ($1::uuid, 'x')", team)
    people = {name: str(uuid.uuid4()) for name in ("deep", "none", "loop")}
    await conn.execute('INSERT INTO "user" VALUES ($1::uuid, $2::uuid)', people["deep"], chain[-1])
    await conn.execute('INSERT INTO "user" VALUES ($1::uuid, NULL)', people["none"])

    flat = BackendUserGroups(dsn)
    try:
        # No parent_id yet: the team alone.
        assert await flat(people["deep"]) == UsageTeam(chain[-1], (chain[-1],))
        assert await flat(people["none"]) is None
        assert await flat(str(uuid.uuid4())) is None
    finally:
        await flat.close()

    await conn.execute("ALTER TABLE groups ADD COLUMN parent_id uuid")
    for parent, child in itertools.pairwise(chain):
        await conn.execute("UPDATE groups SET parent_id = $1::uuid WHERE id = $2::uuid", parent, child)
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    await conn.execute("INSERT INTO groups (id, name) VALUES ($1::uuid, 'a'), ($2::uuid, 'b')", a, b)
    await conn.execute("UPDATE groups SET parent_id = $1::uuid WHERE id = $2::uuid", b, a)
    await conn.execute("UPDATE groups SET parent_id = $1::uuid WHERE id = $2::uuid", a, b)
    await conn.execute('INSERT INTO "user" VALUES ($1::uuid, $2::uuid)', people["loop"], a)

    tree = BackendUserGroups(dsn)
    try:
        deep = await tree(people["deep"])
        assert deep is not None and deep.group_id == chain[-1]
        assert deep.path == tuple(chain[-PATH_LEVELS:]), "at most six levels, root first, the team last"
        assert await tree(people["loop"]) == UsageTeam(a, (b, a)), "a cycle stops the walk"
    finally:
        await tree.close()


@needs_postgres
@pytest.mark.anyio
async def test_teams_becoming_a_tree_while_the_gateway_runs_are_seen_within_minutes(
    backend_schema: tuple[str, asyncpg.Connection],
) -> None:
    """The backend is upgraded (``groups.parent_id`` added) after the gateway first asked: it asks again."""
    dsn, conn = backend_schema
    root, team, someone = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    await conn.execute("INSERT INTO groups (id, name) VALUES ($1::uuid, 'root'), ($2::uuid, 'team')", root, team)
    await conn.execute('INSERT INTO "user" VALUES ($1::uuid, $2::uuid)', someone, team)
    now = [1000.0]
    resolver = BackendUserGroups(dsn, clock=lambda: now[0])
    try:
        assert await resolver(someone) == UsageTeam(team, (team,))
        await conn.execute("ALTER TABLE groups ADD COLUMN parent_id uuid")
        await conn.execute("UPDATE groups SET parent_id = $1::uuid WHERE id = $2::uuid", root, team)
        now[0] += 60
        assert await resolver(someone) == UsageTeam(team, (team,)), "a no is believed for a few minutes"
        now[0] += usage_group.TREE_RECHECK_SEC
        assert await resolver(someone) == UsageTeam(team, (root, team))
    finally:
        await resolver.close()
