"""Usage rollups: the request log's reasoning and cache-write tokens and their source, and the rollup tables.

``usage_hourly`` (UTC hours, no person, no project), ``usage_daily`` (days in the usage time zone) and
``usage_daily_person`` (person-level, a day at the finest: M10), written by the gateway's rollup job from
the request log; ``usage_rollup_state`` is the job's one row, with how far it has rolled up and from when
it may purge (a week after the first backfill). A cell's keys may be NULL: the unique indexes treat NULLs
as equal (``NULLS NOT DISTINCT``, Postgres 15+).

``group_path`` (the request log's and each rollup's, part of the key) is the usage team's path at the time
of use, root first: usage stays booked where it was after a reorganisation.

Revision ID: u5ag3r0ll
Revises: r3c31pt1d
Create Date: 2026-10-28 12:00:00
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "u5ag3r0ll"
down_revision = "r3c31pt1d"
branch_labels = None
depends_on = None

_LOG = "llm_gateway_request_log"


def _measures() -> list[sa.Column[Any]]:
    counted = [
        sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        for name in ("requests", "failed", "refused")
    ]
    tokens = [
        sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")
        for name in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")
    ]
    return [
        *counted,
        *tokens,
        sa.Column("cost", sa.Numeric(18, 10), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
    ]


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True)


def _group_path() -> sa.Column[Any]:
    return sa.Column("group_path", postgresql.ARRAY(sa.String(128)), nullable=True)


def _cell() -> list[sa.Column[Any]]:
    """The keys the hourly and the daily table share, beside the period and the currency."""
    return [
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("group_id", sa.String(128), nullable=True),
        _group_path(),
        sa.Column("agent_id", sa.String(64), nullable=True),
        sa.Column("model_alias", sa.String(256), nullable=True),
        sa.Column("provider_instance_id", postgresql.UUID(as_uuid=True), nullable=True),
    ]


def upgrade() -> None:
    """Add the log's columns, the three rollup tables and the job's state row."""
    op.add_column(_LOG, sa.Column("reasoning_tokens", sa.Integer(), nullable=True))
    op.add_column(_LOG, sa.Column("cache_write_tokens", sa.Integer(), nullable=True))
    op.add_column(_LOG, sa.Column("usage_source", sa.String(16), nullable=True))
    op.add_column(_LOG, _group_path())

    keys = ["tenant_id", "group_id", "group_path", "agent_id", "model_alias", "provider_instance_id", "currency"]
    op.create_table(
        "usage_hourly",
        _id(),
        sa.Column("hour", sa.DateTime(timezone=True), nullable=False),
        *_cell(),
        *_measures(),
        sa.Column("people", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "uq_usage_hourly_cell", "usage_hourly", ["hour", *keys], unique=True, postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "usage_daily",
        _id(),
        sa.Column("day", sa.Date(), nullable=False),
        *_cell(),
        *_measures(),
        sa.Column("people", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "uq_usage_daily_cell", "usage_daily", ["day", *keys], unique=True, postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "usage_daily_person",
        _id(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("group_id", sa.String(128), nullable=True),
        _group_path(),
        sa.Column("project_id", sa.String(128), nullable=True),
        sa.Column("agent_id", sa.String(64), nullable=True),
        sa.Column("model_alias", sa.String(256), nullable=True),
        *_measures(),
    )
    op.create_index(
        "uq_usage_daily_person_cell",
        "usage_daily_person",
        [
            "day", "tenant_id", "user_id", "group_id", "group_path", "project_id", "agent_id", "model_alias",
            "currency",
        ],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_usage_daily_person_user", "usage_daily_person", ["user_id", "day"])
    op.create_table(
        "usage_rollup_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("backfilled_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_purge_at", sa.DateTime(timezone=True), nullable=True),
        # Nothing is purged before it: a week after the first backfill, to set the retention after an upgrade.
        sa.Column("purge_from", sa.DateTime(timezone=True), nullable=True),
    )
    # The one row; NULL until the job's first run has backfilled the history.
    op.execute("INSERT INTO usage_rollup_state (id) VALUES (1)")


def downgrade() -> None:
    """Drop them again."""
    op.drop_table("usage_rollup_state")
    op.drop_index("ix_usage_daily_person_user", table_name="usage_daily_person")
    op.drop_index("uq_usage_daily_person_cell", table_name="usage_daily_person")
    op.drop_table("usage_daily_person")
    op.drop_index("uq_usage_daily_cell", table_name="usage_daily")
    op.drop_table("usage_daily")
    op.drop_index("uq_usage_hourly_cell", table_name="usage_hourly")
    op.drop_table("usage_hourly")
    op.drop_column(_LOG, "group_path")
    op.drop_column(_LOG, "usage_source")
    op.drop_column(_LOG, "cache_write_tokens")
    op.drop_column(_LOG, "reasoning_tokens")
