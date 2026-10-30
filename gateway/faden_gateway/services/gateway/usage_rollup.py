"""The usage rollup job: hourly and daily sums from the request log, and the purge (M09, M10).

Every gateway worker runs :func:`rollup_loop` from its lifespan; one at a time does the work, under a
Postgres advisory lock (``pg_try_advisory_lock``), every five minutes (``usage_rollup_interval_sec``):

1. It reads the usage settings from the backend's database (``usage_settings.read``): a change applies at
   the next run, without a restart.
2. **Catch-up**: hours and days that ended since the last run's watermark
   (``usage_rollup_state.backfilled_through``) and before the previous hour are rolled up from the log.
   The first run starts at the oldest row of the log, so all history is backfilled before anything is
   purged; a run that stops half way goes on from its watermark (it moves a week at a time). In steady
   state this is the one hour that just closed, and nothing older: a window is not recomputed once the
   watermark is past it.
3. **Recompute**: the current and the previous hour (UTC) and day (the usage time zone) are deleted and
   written again from the log, in one transaction each: idempotent, so a run may repeat, and a reader sees
   the old sums or the new.
Before all that, every run removes the usage of the people erased in the last 30 days (``usage_erased``,
written by ``erasure.erase_user``) that turned up again -- request-log rows of a request that ended after
the erasure, person rows a run wrote from a log read just before it -- and writes no person row for them.

4. **Purge**, once the watermark has caught up, only with the settings read, and not before
   ``usage_rollup_state.purge_from`` -- a week after the first backfill was done, so that after an upgrade
   administrators set the retention before anything is deleted (plan decision 9): request-log rows older
   than ``usage.request_log_retention_days`` (never at or after the watermark), person-level days older
   than ``usage.person_retention_days``, hours older than ``usage.hourly_retention_days``, days older than
   ``usage.daily_retention_months``. The sums without a person are not recomputed for closed windows, so
   they keep their totals when the log is purged or a person is erased.

What is counted (``db/models/usage.py``): ``requests`` is every request that reached the gateway (every row
of the log), failed and refused ones included; ``refused`` those a cap or an admission check refused -- the
log's ``refused``, which the gateway sets on every row whatever the refusal's code (an extension's check
chooses its own), and for a row from before it (NULL) its ``error_code`` (``rate_limit_*``, ``quota_*``,
``budget_*``); ``failed`` the others with a status of 400 or more. ``currency`` is the price's currency for
a request a price applied to, else NULL, and ``cost`` the sum of the requests' estimates. A row logged before
teams had paths books on ``[group_id]``.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from sqlalchemy import (
    Date,
    String,
    and_,
    case,
    cast,
    delete,
    distinct,
    false,
    func,
    insert,
    literal,
    not_,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.selectable import Subquery

from faden_gateway.db.models.gateway import LLMGatewayRequestLog
from faden_gateway.db.models.usage import (
    UsageDaily,
    UsageDailyPerson,
    UsageErased,
    UsageHourly,
    UsageRollupState,
)
from faden_gateway.services.gateway import usage_settings
from faden_gateway.services.gateway.usage_settings import UsageSettings

log = logging.getLogger(__name__)

#: Advisory lock key: one gateway worker rolls up at a time ("faden-usage" in ASCII, cut to 63 bits).
LOCK_KEY = 0x6661_6465_6E75_7361 & 0x7FFF_FFFF_FFFF_FFFF
#: How much history one catch-up transaction rolls up.
CATCH_UP_STEP = timedelta(days=7)
#: Request-log rows deleted per purge transaction.
PURGE_BATCH = 5000
#: The first purge waits this long after the first backfill: after an upgrade, administrators set the
#: retention before anything is deleted (the backend shows a notice until ``purge_from``).
PURGE_GRACE = timedelta(days=7)
#: How long an erased person's usage is removed again by every run (``usage_erased``).
ERASED_KEPT = timedelta(days=30)
#: An ``error_code`` with one of these marks a request a cap or an admission check refused, on a row logged
#: before the log said so itself (``refused`` NULL).
REFUSED_PREFIXES = ("rate_limit_", "quota_", "budget_")

_HOUR = timedelta(hours=1)
_LOG = LLMGatewayRequestLog

_MEASURES = (
    "requests", "failed", "refused", "input_tokens", "cached_input_tokens", "output_tokens",
    "reasoning_tokens", "cost",
)
_HOURLY_KEYS = (
    "hour", "tenant_id", "group_id", "group_path", "agent_id", "model_alias", "provider_instance_id",
    "currency",
)
_DAILY_KEYS = ("day", *_HOURLY_KEYS[1:])
_PERSON_KEYS = (
    "day", "tenant_id", "user_id", "group_id", "group_path", "project_id", "agent_id", "model_alias",
    "currency",
)


@dataclass
class RollupRun:
    """What one run did."""

    now: datetime
    #: Where the catch-up began (the old watermark, or the log's first hour), and the new watermark.
    caught_up_from: datetime
    backfilled_through: datetime
    #: Nothing is purged before this.
    purge_from: datetime
    #: Rows the purge deleted, by table; empty when it did not run.
    purged: dict[str, int] = field(default_factory=dict)


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _midnight(day: date, zone: tzinfo) -> datetime:
    """Where *day* of *zone* begins."""
    return datetime.combine(day, datetime.min.time(), tzinfo=zone)


def _months_before(day: date, months: int) -> date:
    """*day*, *months* calendar months earlier (that month's last day when it is shorter)."""
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def _log_rows(start: datetime, end: datetime, zone_name: str) -> Subquery:
    """The request log's rows in ``[start, end)``, with what the rollups group and count them by."""
    refused = func.coalesce(
        _LOG.refused,
        or_(*(func.starts_with(_LOG.error_code, prefix) for prefix in REFUSED_PREFIXES)),
        false(),
    )
    priced = or_(_LOG.estimated_total_cost.is_not(None), _LOG.price_catalog_id.is_not(None))
    group_path = func.coalesce(
        _LOG.group_path,
        case((_LOG.group_id.is_not(None), cast(array([_LOG.group_id]), ARRAY(String(128)))), else_=None),
    )
    return (
        select(
            func.date_trunc("hour", _LOG.created_at, "UTC").label("hour"),
            cast(func.timezone(cast(literal(zone_name), String), _LOG.created_at), Date).label("day"),
            _LOG.tenant_id,
            _LOG.user_id,
            _LOG.group_id,
            group_path.label("group_path"),
            _LOG.project_id,
            _LOG.agent_id,
            _LOG.model_alias,
            _LOG.provider_instance_id,
            case((priced, _LOG.currency), else_=None).label("currency"),
            _LOG.status_code,
            refused.label("refused"),
            _LOG.prompt_tokens,
            _LOG.cached_tokens,
            _LOG.completion_tokens,
            _LOG.reasoning_tokens,
            _LOG.estimated_total_cost.label("cost"),
        )
        .where(_LOG.created_at >= start, _LOG.created_at < end)
        .subquery("log")
    )


def _measures(rows: Subquery) -> list[Any]:
    """The measures of a group of *rows*, in ``_MEASURES``' order."""
    return [
        func.count(),
        func.count().filter(and_(rows.c.status_code >= 400, not_(rows.c.refused))),
        func.count().filter(rows.c.refused),
        func.coalesce(func.sum(rows.c.prompt_tokens), 0),
        func.coalesce(func.sum(rows.c.cached_tokens), 0),
        func.coalesce(func.sum(rows.c.completion_tokens), 0),
        func.coalesce(func.sum(rows.c.reasoning_tokens), 0),
        func.sum(rows.c.cost),
    ]


async def _hours(conn: AsyncConnection, start: datetime, end: datetime) -> None:
    """Write the hours in ``[start, end)`` (whole UTC hours) again from the log."""
    await conn.execute(delete(UsageHourly).where(UsageHourly.hour >= start, UsageHourly.hour < end))
    rows = _log_rows(start, end, "UTC")
    keys = [rows.c[name] for name in _HOURLY_KEYS]
    grouped = select(*keys, *_measures(rows), func.count(distinct(rows.c.user_id))).group_by(*keys)
    await conn.execute(insert(UsageHourly).from_select([*_HOURLY_KEYS, *_MEASURES, "people"], grouped))


async def _days(conn: AsyncConnection, first: date, last: date, zone: tzinfo, zone_name: str) -> None:
    """Write the days *first* to *last* of *zone*, and their person-level rows, again from the log."""
    for table in (UsageDaily, UsageDailyPerson):
        await conn.execute(delete(table).where(table.day >= first, table.day <= last))
    rows = _log_rows(_midnight(first, zone), _midnight(last + timedelta(days=1), zone), zone_name)
    # Only the days asked for: a row the database's time zone rules put on another day (other tzdata)
    # is left out rather than written over a day this run did not clear.
    within = rows.c.day.between(first, last)
    keys = [rows.c[name] for name in _DAILY_KEYS]
    people = func.count(distinct(rows.c.user_id))
    daily = select(*keys, *_measures(rows), people).where(within).group_by(*keys)
    await conn.execute(insert(UsageDaily).from_select([*_DAILY_KEYS, *_MEASURES, "people"], daily))
    keys = [rows.c[name] for name in _PERSON_KEYS]
    erased = select(UsageErased.user_id).scalar_subquery()
    person = select(*keys, *_measures(rows)).where(within, rows.c.user_id.not_in(erased)).group_by(*keys)
    await conn.execute(insert(UsageDailyPerson).from_select([*_PERSON_KEYS, *_MEASURES], person))


async def forget_erased(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    """Remove what turned up again of the people erased within ``ERASED_KEPT``; forget older erasures.

    Their request-log rows (with their tool calls, ``ON DELETE CASCADE``) and person-level rows. The caller
    commits.
    """
    recent = select(UsageErased.user_id).where(UsageErased.erased_at >= now - ERASED_KEPT).scalar_subquery()
    removed = {
        "request_log": (await conn.execute(delete(_LOG).where(_LOG.user_id.in_(recent)))).rowcount or 0,
        "usage_daily_person": (await conn.execute(
            delete(UsageDailyPerson).where(UsageDailyPerson.user_id.in_(recent)),
        )).rowcount or 0,
    }
    await conn.execute(delete(UsageErased).where(UsageErased.erased_at < now - ERASED_KEPT))
    return removed


async def _state(conn: AsyncConnection, **values: Any) -> None:
    await conn.execute(update(UsageRollupState).where(UsageRollupState.id == 1).values(**values))


async def _roll_up(conn: AsyncConnection, config: UsageSettings, now: datetime | None) -> RollupRun:
    """Catch up to the previous hour, then write the current and the previous hour and day again."""
    zone = config.zone
    if now is None:
        now = (await conn.execute(text("SELECT now()"))).scalar_one()
    current_hour = _floor_hour(now)
    previous_hour = current_hour - _HOUR
    today = now.astimezone(zone).date()
    await forget_erased(conn, now)
    await conn.commit()
    await conn.execute(pg_insert(UsageRollupState).values(id=1).on_conflict_do_nothing())
    through = (await conn.execute(
        select(UsageRollupState.backfilled_through).where(UsageRollupState.id == 1),
    )).scalar_one_or_none()
    if through is None:  # the first run: all history, from the log's first hour
        first = (await conn.execute(select(func.min(_LOG.created_at)))).scalar_one_or_none()
        through = _floor_hour(first) if first is not None else previous_hour
    started = through
    while through < previous_hour:
        end = min(through + CATCH_UP_STEP, previous_hour)
        await _hours(conn, through, end)
        # The days it touches, but for the previous day and today: the recompute below writes those.
        first_day = through.astimezone(zone).date()
        last_day = min((end - timedelta(microseconds=1)).astimezone(zone).date(), today - timedelta(days=2))
        if first_day <= last_day:
            await _days(conn, first_day, last_day, zone, config.time_zone)
        await _state(conn, backfilled_through=end)
        await conn.commit()
        through = end
    await _hours(conn, previous_hour, current_hour + _HOUR)
    await _days(conn, today - timedelta(days=1), today, zone, config.time_zone)
    through = max(through, previous_hour)
    # The backfill is done: the purge may start a week from the first time it was.
    await _state(
        conn, backfilled_through=through, last_run_at=now,
        purge_from=func.coalesce(UsageRollupState.purge_from, now + PURGE_GRACE),
    )
    purge_from = (await conn.execute(
        select(UsageRollupState.purge_from).where(UsageRollupState.id == 1),
    )).scalar_one() or now + PURGE_GRACE  # set just above: never None
    await conn.commit()
    return RollupRun(now=now, caught_up_from=started, backfilled_through=through, purge_from=purge_from)


async def _purge(conn: AsyncConnection, config: UsageSettings, run: RollupRun) -> dict[str, int]:
    """Delete what is older than its retention; request-log rows only before the watermark."""
    now = run.now
    today = now.astimezone(config.zone).date()
    cutoff = min(now - timedelta(days=config.request_log_retention_days), run.backfilled_through)
    purged = {"request_log": 0}
    while True:  # in batches: the first purge after an upgrade may meet a long history
        batch = select(_LOG.id).where(_LOG.created_at < cutoff).limit(PURGE_BATCH).scalar_subquery()
        # Their tool calls go with them (ON DELETE CASCADE).
        done = (await conn.execute(delete(_LOG).where(_LOG.id.in_(batch)))).rowcount or 0
        await conn.commit()
        purged["request_log"] += done
        if done < PURGE_BATCH:
            break
    for name, statement in (
        ("usage_daily_person", delete(UsageDailyPerson).where(
            UsageDailyPerson.day < today - timedelta(days=config.person_retention_days),
        )),
        ("usage_hourly", delete(UsageHourly).where(
            UsageHourly.hour < now - timedelta(days=config.hourly_retention_days),
        )),
        ("usage_daily", delete(UsageDaily).where(
            UsageDaily.day < _months_before(today, config.daily_retention_months),
        )),
    ):
        purged[name] = (await conn.execute(statement)).rowcount or 0
    await _state(conn, last_purge_at=now)
    await conn.commit()
    return purged


async def run_once(
    engine: AsyncEngine, found: UsageSettings | None, *, now: datetime | None = None,
) -> RollupRun | None:
    """One run of the job; ``None`` when another worker holds the lock (or the database is not Postgres).

    *found*: the usage settings as just read, or ``None`` when they could not be read -- then the last read
    (or the defaults) sets the time zone, and nothing is purged. *now* is for tests: the database's clock
    otherwise, which also stamps the log.
    """
    if engine.dialect.name != "postgresql":
        return None
    config = found if found is not None else usage_settings.current()
    async with engine.connect() as conn:
        locked = (await conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY})).scalar()
        await conn.commit()
        if not locked:
            return None
        try:
            run = await _roll_up(conn, config, now)
            if found is not None and run.now >= run.purge_from:
                run.purged = await _purge(conn, found, run)
            return run
        finally:
            await conn.rollback()
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
            await conn.commit()


async def rollup_loop(engine: AsyncEngine, *, interval_sec: float, first_delay_sec: float = 60.0) -> None:
    """Run the job every *interval_sec*, from the gateway's lifespan; each worker reads the settings."""
    await asyncio.sleep(first_delay_sec)  # not during start-up
    while True:
        try:
            found = await usage_settings.read()
            usage_settings.remember(found)
            run = await run_once(engine, found)
            if run is not None and run.caught_up_from < run.backfilled_through - _HOUR:
                log.info("Usage rolled up from %s to %s", run.caught_up_from, run.backfilled_through)
            if run is not None and any(run.purged.values()):
                log.info("Usage past its retention purged: %s", run.purged)
        except Exception:
            log.warning("The usage rollup failed; it runs again in %s s", interval_sec, exc_info=True)
        await asyncio.sleep(interval_sec)
