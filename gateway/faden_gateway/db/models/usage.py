"""Usage rolled up from the request log: per hour, per day, and per person and day (M09, M10).

The rollup job (``services/gateway/usage_rollup.py``) writes these from ``llm_gateway_request_log``; the
backend reads them for the usage pages. Every table has the same measures, counted as the OpenTelemetry
GenAI conventions count: ``input_tokens`` includes the cached ones, ``output_tokens`` the reasoning ones.

- ``usage_hourly``: hour (UTC), tenant, team, agent, model, provider instance. No person, no project.
- ``usage_daily``: day in the usage time zone (``usage.time_zone``), the same keys.
- ``usage_daily_person``: day, tenant, person, team, project, agent, model -- person-level data at day
  granularity at most (M10), kept for ``usage.person_retention_days``.

A key may be NULL (no team, no agent); the unique indexes treat NULLs as equal (``NULLS NOT DISTINCT``,
Postgres 15+), so each cell is one row. ``group_path`` is the team's path at the time of use (its ancestors
from the root down to the team, ``services/gateway/usage_group.py``), part of the key: usage stays booked
where it was after a reorganisation. ``currency`` is part of the key too: one row per currency of the
prices that applied, NULL for requests no price applied to (``cost`` NULL there). ``people`` counts the
distinct persons in a cell: it is not additive across cells.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Date, DateTime, Identity, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from faden_gateway.db.base import Base

#: A big integer key that SQLite (the unit tests) also counts up.
_ID = BigInteger().with_variant(Integer, "sqlite")
#: A team's path, root first (``varchar(128)[]``; a JSON list where SQLite runs the unit tests).
GROUP_PATH = ARRAY(String(128)).with_variant(JSON(), "sqlite")


def _counter(kind: type[Integer | BigInteger]) -> Mapped[int]:
    return mapped_column(kind, nullable=False, default=0, server_default="0")


class _Measures:
    """What each rollup row counts."""

    #: Every request that reached the gateway: ``failed`` and ``refused`` are parts of it.
    requests: Mapped[int] = _counter(Integer)
    #: Status 400 or more, refusals by a cap or an admission check not included.
    failed: Mapped[int] = _counter(Integer)
    #: Refused by a cap or an admission check (``error_code`` ``rate_limit_*``, ``quota_*``, ``budget_*``).
    refused: Mapped[int] = _counter(Integer)
    #: Including the cached input.
    input_tokens: Mapped[int] = _counter(BigInteger)
    cached_input_tokens: Mapped[int] = _counter(BigInteger)
    #: Including the reasoning.
    output_tokens: Mapped[int] = _counter(BigInteger)
    reasoning_tokens: Mapped[int] = _counter(BigInteger)
    #: The estimated cost in ``currency``; NULL when no price applied to any request of the cell.
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)


class UsageHourly(_Measures, Base):
    """Usage per UTC hour, tenant, team, agent, model and provider instance; no person, no project."""

    __tablename__ = "usage_hourly"

    id: Mapped[int] = mapped_column(_ID, Identity(), primary_key=True)
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    group_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    group_path: Mapped[list[str] | None] = mapped_column(GROUP_PATH, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_instance_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: Distinct persons in the cell.
    people: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        Index(
            "uq_usage_hourly_cell",
            "hour", "tenant_id", "group_id", "group_path", "agent_id", "model_alias", "provider_instance_id",
            "currency",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class UsageDaily(_Measures, Base):
    """Usage per day in the usage time zone, with the hourly table's keys."""

    __tablename__ = "usage_daily"

    id: Mapped[int] = mapped_column(_ID, Identity(), primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    group_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    group_path: Mapped[list[str] | None] = mapped_column(GROUP_PATH, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_instance_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: Distinct persons in the cell.
    people: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        Index(
            "uq_usage_daily_cell",
            "day", "tenant_id", "group_id", "group_path", "agent_id", "model_alias", "provider_instance_id",
            "currency",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class UsageDailyPerson(_Measures, Base):
    """Usage per day, person, team, project, agent and model: person-level, a day at the finest (M10)."""

    __tablename__ = "usage_daily_person"

    id: Mapped[int] = mapped_column(_ID, Identity(), primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    group_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    group_path: Mapped[list[str] | None] = mapped_column(GROUP_PATH, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)

    __table_args__ = (
        Index(
            "uq_usage_daily_person_cell",
            "day", "tenant_id", "user_id", "group_id", "group_path", "project_id", "agent_id", "model_alias",
            "currency",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        # A person's own view, their export and their erasure look them up by person.
        Index("ix_usage_daily_person_user", "user_id", "day"),
    )


class UsageRollupState(Base):
    """The rollup job's one row: how far it has rolled up, and when it last ran and purged."""

    __tablename__ = "usage_rollup_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    #: Every hour and day that ended before this was rolled up from the request log after it ended; NULL
    #: before the first run, which backfills all history up to the previous hour first. The purge runs only
    #: once that is done, and deletes no request log row at or after it.
    backfilled_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Nothing is purged before this: a week after the first backfill was done, so that administrators can
    #: set the retention after an upgrade before anything is deleted (the backend shows a notice until
    #: then). NULL until the first backfill is done.
    purge_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
