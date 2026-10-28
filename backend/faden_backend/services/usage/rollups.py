"""Queries against the gateway's usage rollups (and, for a person's own export, its request log).

The tables are the gateway's (migration ``u5ag3r0ll``): ``usage_hourly`` (UTC hours) and ``usage_daily``
(days in the usage time zone) per tenant, team, agent, model, provider instance and currency, each with
``people``, the distinct people in the cell; ``usage_daily_person`` per day, person, team, project, agent,
model and currency. Raw SQL with bound parameters, like the other readers of the gateway database: the
backend does not import the gateway's models.

Aggregates for administrators read ``usage_daily``/``usage_hourly``; distinct people over several cells come
from ``usage_daily_person`` (the per-cell ``people`` cannot be added up). A person's own figures and the usage
auditor's view read ``usage_daily_person``; the request log is read only for a person's own export.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.api_tokens import ACCOUNT_TENANT
from faden_backend.services.usage.measures import COUNTS, SUMS_SQL, Measures
from faden_backend.services.usage.suppression import Cell

GroupBy = Literal["team", "agent", "model", "provider"]
Bucket = Literal["hour", "day", "month"]

#: The column each grouping reads in ``usage_daily``/``usage_hourly`` (fixed strings, never user input).
AGGREGATE_KEYS: dict[str, str] = {
    "team": "group_id",
    "agent": "agent_id",
    "model": "model_alias",
    "provider": "CAST(provider_instance_id AS TEXT)",
}
#: The same in ``usage_daily_person``, which has no provider instance but a project.
PERSON_KEYS: dict[str, str] = {
    "team": "group_id",
    "agent": "agent_id",
    "model": "model_alias",
    "project": "project_id",
}
#: The period of a day-table row, per bucket.
PERIODS: dict[str, str] = {
    "day": "day",
    "month": "CAST(date_trunc('month', day) AS date)",
}


@dataclass(frozen=True)
class DayRange:
    """Whole days in the usage time zone, both ends included."""

    first: date
    last: date

    def params(self) -> dict[str, Any]:
        """The range as bind parameters."""
        return {"first": self.first, "last": self.last}


def _cells(rows: Iterable[Any], *, floor: bool = True) -> dict[Hashable, Cell]:
    """Rows of ``key, currency, <sums>, cost[, floor]`` folded into one cell per key."""
    cells: dict[Hashable, Cell] = {}
    for row in rows:
        cell = cells.setdefault(row["key"], Cell(key=row["key"]))
        cell.measures.add(row)
        if floor:
            cell.floor = max(cell.floor, int(row["floor"] or 0))
    return cells


# ── Aggregates (no person) ──────────────────────────────────────────


async def group_cells(gw: AsyncSession, days: DayRange, group_by: GroupBy) -> dict[Hashable, Cell]:
    """One cell per team, agent, model or provider over *days*, its floor the largest cell's ``people``."""
    key = AGGREGATE_KEYS[group_by]
    rows = await gw.execute(
        text(f"""
            SELECT {key} AS key, currency, {SUMS_SQL}, MAX(COALESCE(people, 0)) AS floor
            FROM usage_daily
            WHERE day BETWEEN :first AND :last
            GROUP BY 1, currency
        """),  # noqa: S608 - the key is one of AGGREGATE_KEYS
        days.params(),
    )
    return _cells(rows.mappings())


async def period_cells(
    gw: AsyncSession, days: DayRange, bucket: Literal["day", "month"],
) -> dict[Hashable, Cell]:
    """One cell per day or month of *days*."""
    rows = await gw.execute(
        text(f"""
            SELECT {PERIODS[bucket]} AS key, currency, {SUMS_SQL}, MAX(COALESCE(people, 0)) AS floor
            FROM usage_daily
            WHERE day BETWEEN :first AND :last
            GROUP BY 1, currency
        """),  # noqa: S608 - the period is one of PERIODS
        days.params(),
    )
    return _cells(rows.mappings())


async def hour_cells(gw: AsyncSession, start: datetime, end: datetime) -> dict[Hashable, Cell]:
    """One cell per UTC hour in ``[start, end)``; the floor is all a cell has (there are no person hours)."""
    rows = await gw.execute(
        text(f"""
            SELECT hour AS key, currency, {SUMS_SQL}, MAX(COALESCE(people, 0)) AS floor
            FROM usage_hourly
            WHERE hour >= :start AND hour < :end
            GROUP BY 1, currency
        """),  # noqa: S608 - only the fixed sums are formatted in
        {"start": start, "end": end},
    )
    return _cells(rows.mappings())


async def total_cell(gw: AsyncSession, days: DayRange) -> Cell:
    """The whole range as one cell."""
    rows = await gw.execute(
        text(f"""
            SELECT 'total' AS key, currency, {SUMS_SQL}, MAX(COALESCE(people, 0)) AS floor
            FROM usage_daily
            WHERE day BETWEEN :first AND :last
            GROUP BY currency
        """),  # noqa: S608 - only the fixed sums are formatted in
        days.params(),
    )
    return _cells(rows.mappings()).get("total") or Cell(key="total")


async def team_cells(gw: AsyncSession, days: DayRange) -> list[dict[str, Any]]:
    """Per home team and recorded path (``group_path``, root first): figures and the largest ``people``."""
    rows = await gw.execute(
        text(f"""
            SELECT group_id, group_path, currency, {SUMS_SQL}, MAX(COALESCE(people, 0)) AS floor
            FROM usage_daily
            WHERE day BETWEEN :first AND :last
            GROUP BY group_id, group_path, currency
        """),  # noqa: S608 - only the fixed sums are formatted in
        days.params(),
    )
    return [dict(row) for row in rows.mappings()]


async def team_people(gw: AsyncSession, days: DayRange) -> list[tuple[str | None, list[str] | None, str]]:
    """Every ``(home team, recorded path, person)`` of *days* (from the per-person rollup)."""
    rows = await gw.execute(
        text("""
            SELECT DISTINCT group_id, group_path, user_id FROM usage_daily_person
            WHERE day BETWEEN :first AND :last
        """),
        days.params(),
    )
    return [(row[0], list(row[1]) if row[1] else None, str(row[2])) for row in rows]


async def people_ids(gw: AsyncSession, days: DayRange) -> list[str]:
    """The people active in *days* (from the per-person rollup)."""
    rows = await gw.execute(
        text("SELECT DISTINCT user_id FROM usage_daily_person WHERE day BETWEEN :first AND :last"),
        days.params(),
    )
    return [str(row[0]) for row in rows]


# ── Distinct people (from the per-person rollup) ────────────────────


async def people_per_key(gw: AsyncSession, days: DayRange, key_sql: str) -> dict[Hashable, int]:
    """Distinct people per value of *key_sql* (a ``PERSON_KEYS`` or ``PERIODS`` expression) over *days*."""
    rows = await gw.execute(
        text(f"""
            SELECT {key_sql} AS key, COUNT(DISTINCT user_id) AS people
            FROM usage_daily_person
            WHERE day BETWEEN :first AND :last
            GROUP BY 1
        """),  # noqa: S608 - key_sql is one of the module's fixed expressions
        days.params(),
    )
    return {row["key"]: int(row["people"]) for row in rows.mappings()}


async def people_in(gw: AsyncSession, days: DayRange, key_sql: str | None, keys: Iterable[Hashable]) -> int:
    """Distinct people over the cells *keys* of *key_sql* (``None``: the whole range) in *days*."""
    params = days.params()
    where = ""
    if key_sql is not None:
        wanted = list(keys)
        params |= {"keys": [k for k in wanted if k is not None], "with_null": None in wanted}
        where = f"AND ({key_sql} = ANY(:keys) OR (:with_null AND {key_sql} IS NULL))"
    row = await gw.execute(
        text(f"""
            SELECT COUNT(DISTINCT user_id) FROM usage_daily_person
            WHERE day BETWEEN :first AND :last {where}
        """),  # noqa: S608 - key_sql is one of the module's fixed expressions
        params,
    )
    return int(row.scalar_one() or 0)


# ── One person's figures ────────────────────────────────────────────


async def own_cells(
    gw: AsyncSession, user_id: str, days: DayRange, key_sql: str | None,
) -> dict[Hashable, Measures]:
    """*user_id*'s figures over *days*, per value of *key_sql*; ``None``: one total under the key ``None``."""
    key, group = (key_sql, "1, currency") if key_sql else ("NULL", "currency")
    rows = await gw.execute(
        text(f"""
            SELECT {key} AS key, currency, {SUMS_SQL}
            FROM usage_daily_person
            WHERE user_id = :user_id AND day BETWEEN :first AND :last
            GROUP BY {group}
        """),  # noqa: S608 - key_sql is one of the module's fixed expressions
        {"user_id": user_id, **days.params()},
    )
    return {key: cell.measures for key, cell in _cells(rows.mappings(), floor=False).items()}


async def person_rows(
    gw: AsyncSession,
    days: DayRange,
    period_sql: str,
    *,
    team: str | None = None,
    person: str | None = None,
) -> dict[tuple[str, Hashable], Measures]:
    """Per person and period: the figures of one team's people, or of one person (the auditor's view).

    A team's people are those whose usage was attributed to the team (``group_id`` of their requests).
    """
    params = days.params()
    if team is not None:
        where, params["team"] = "AND group_id = :team", team
    elif person is not None:
        where, params["person"] = "AND user_id = :person", person
    else:
        msg = "A team or a person is needed."
        raise ValueError(msg)
    rows = await gw.execute(
        text(f"""
            SELECT user_id, {period_sql} AS period, currency, {SUMS_SQL}
            FROM usage_daily_person
            WHERE day BETWEEN :first AND :last {where}
            GROUP BY 1, 2, currency
        """),  # noqa: S608 - period_sql is one of PERIODS, the filters fixed strings
        params,
    )
    found: dict[tuple[str, Hashable], Measures] = {}
    for row in rows.mappings():
        found.setdefault((str(row["user_id"]), row["period"]), Measures()).add(row)
    return found


async def own_rollup_rows(gw: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    """Every per-person rollup row of *user_id* still kept, oldest first (the Art. 15 export)."""
    rows = await gw.execute(
        text(f"""
            SELECT day, group_id, project_id, agent_id, model_alias, currency, {", ".join(COUNTS)}, cost
            FROM usage_daily_person
            WHERE user_id = :user_id
            ORDER BY day, model_alias NULLS FIRST, agent_id NULLS FIRST, project_id NULLS FIRST
        """),  # noqa: S608 - only the fixed measure names are formatted in
        {"user_id": user_id},
    )
    return [dict(row) for row in rows.mappings()]


async def own_requests(gw: AsyncSession, user_id: str, limit: int) -> list[dict[str, Any]]:
    """*user_id*'s requests still in the request log, oldest first: metadata only, no content, no device."""
    rows = await gw.execute(
        text("""
            SELECT created_at, model_alias, agent_id, status_code,
                   prompt_tokens AS input_tokens, cached_tokens AS cached_input_tokens,
                   completion_tokens AS output_tokens, reasoning_tokens,
                   estimated_total_cost AS cost, currency
            FROM llm_gateway_request_log
            WHERE user_id = :user_id
            ORDER BY created_at
            LIMIT :limit
        """),
        {"user_id": user_id, "limit": limit},
    )
    return [dict(row) for row in rows.mappings()]


# ── Today against the instance's caps ───────────────────────────────


async def tenant_day(gw: AsyncSession, day: date, user_id: str) -> tuple[int, int, bool]:
    """The tenant's tokens on *day*, its distinct people that day, and whether *user_id* is one of them."""
    tokens = await gw.execute(
        text("""
            SELECT COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0)
            FROM usage_daily WHERE tenant_id = :tenant AND day = :day
        """),
        {"tenant": ACCOUNT_TENANT, "day": day},
    )
    people = await gw.execute(
        text("""
            SELECT COUNT(DISTINCT user_id) AS people, COALESCE(BOOL_OR(user_id = :user_id), false) AS mine
            FROM usage_daily_person WHERE tenant_id = :tenant AND day = :day
        """),
        {"tenant": ACCOUNT_TENANT, "day": day, "user_id": user_id},
    )
    row = people.mappings().one()
    return int(tokens.scalar_one() or 0), int(row["people"] or 0), bool(row["mine"])


# ── The instance's caps (tenant_llm_policy) ─────────────────────────

LIMITS = ("rpm_limit", "tpm_limit", "tokens_per_day")


async def read_limits(gw: AsyncSession) -> dict[str, int | None]:
    """The tenant's caps; ``None`` each when it has no policy row (no cap)."""
    query = text(
        "SELECT rpm_limit, tpm_limit, tokens_per_day FROM tenant_llm_policy WHERE tenant_id = :tenant",
    )
    row = (await gw.execute(query, {"tenant": ACCOUNT_TENANT})).mappings().first()
    return {name: (int(row[name]) if row and row[name] is not None else None) for name in LIMITS}


async def write_limits(gw: AsyncSession, changes: dict[str, int | None]) -> None:
    """Set the caps in *changes* (the others stay); a tenant without a policy row gets one with defaults."""
    sets = ", ".join(f"{name} = EXCLUDED.{name}" for name in changes)
    await gw.execute(
        text(f"""
            INSERT INTO tenant_llm_policy (tenant_id, {", ".join(changes)}, created_at, updated_at)
            VALUES (:tenant, {", ".join(f":{name}" for name in changes)}, NOW(), NOW())
            ON CONFLICT (tenant_id) DO UPDATE SET {sets}, updated_at = NOW()
        """),  # noqa: S608 - the names are LIMITS, checked by the caller
        {"tenant": ACCOUNT_TENANT, **changes},
    )
