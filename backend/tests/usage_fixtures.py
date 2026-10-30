"""The gateway's usage tables for the backend's tests, as the usage contract (C1) gives them, and helpers.

They live in the gateway's database, which the backend's test fixture does not create; here they are made on
the test's connection (Postgres DDL is transactional, so they go with the test's rollback), and the app's
gateway session factory points there.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.dao.system_settings_dao import SystemSettingsDAO
from faden_backend.db.models.users import User, current_active_user

_MEASURES = """
    requests int NOT NULL DEFAULT 0, failed int NOT NULL DEFAULT 0, refused int NOT NULL DEFAULT 0,
    input_tokens bigint NOT NULL DEFAULT 0, cached_input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0, reasoning_tokens bigint NOT NULL DEFAULT 0,
    cost numeric(18, 10), currency varchar(3)
"""

GATEWAY_DDL = (
    f"""
    CREATE TABLE usage_hourly (
        hour timestamptz NOT NULL, tenant_id varchar(128) NOT NULL, group_id varchar(128),
        group_path varchar(128)[], agent_id varchar(64), model_alias varchar(256), provider_instance_id uuid,
        people int NOT NULL DEFAULT 0, {_MEASURES}
    )""",
    """CREATE UNIQUE INDEX ux_usage_hourly ON usage_hourly (hour, tenant_id, group_id, group_path, agent_id,
        model_alias, provider_instance_id, currency) NULLS NOT DISTINCT""",
    f"""
    CREATE TABLE usage_daily (
        day date NOT NULL, tenant_id varchar(128) NOT NULL, group_id varchar(128), group_path varchar(128)[],
        agent_id varchar(64), model_alias varchar(256), provider_instance_id uuid,
        people int NOT NULL DEFAULT 0, {_MEASURES}
    )""",
    """CREATE UNIQUE INDEX ux_usage_daily ON usage_daily (day, tenant_id, group_id, group_path, agent_id,
        model_alias, provider_instance_id, currency) NULLS NOT DISTINCT""",
    f"""
    CREATE TABLE usage_daily_person (
        day date NOT NULL, tenant_id varchar(128) NOT NULL, user_id varchar(128) NOT NULL,
        group_id varchar(128), group_path varchar(128)[], project_id varchar(128), agent_id varchar(64),
        model_alias varchar(256), {_MEASURES}
    )""",
    """CREATE UNIQUE INDEX ux_usage_daily_person ON usage_daily_person (day, tenant_id, user_id, group_id,
        group_path, project_id, agent_id, model_alias, currency) NULLS NOT DISTINCT""",
    """
    CREATE TABLE usage_rollup_state (
        id int PRIMARY KEY DEFAULT 1, backfilled_through timestamptz, last_run_at timestamptz,
        last_purge_at timestamptz, purge_from timestamptz
    )""",
    """
    CREATE TABLE llm_gateway_request_log (
        id uuid PRIMARY KEY, request_id varchar(128) NOT NULL, trace_id varchar(128),
        tenant_id varchar(128) NOT NULL, user_id varchar(128) NOT NULL, model_alias varchar(256),
        provider_instance_id uuid, endpoint varchar(128) NOT NULL, status_code int NOT NULL,
        latency_ms int NOT NULL DEFAULT 0, ttft_ms int, prompt_tokens int, completion_tokens int,
        total_tokens int, error_code varchar(128), estimated_input_cost numeric(18, 10),
        estimated_output_cost numeric(18, 10), estimated_total_cost numeric(18, 10), currency varchar(3),
        price_catalog_id uuid, cost_estimate_status varchar(16), cached_tokens int, stream boolean,
        session_id varchar(128), group_id varchar(128), group_path varchar(128)[], project_id varchar(128),
        agent_id varchar(64), device_id varchar(64), conversation_id varchar(128), turn_id varchar(128),
        finish_reason varchar(32), retry_count int DEFAULT 0, skills_used json, rag_context json,
        mcp_tool_call_count int, mcp_tool_loop_iterations int,
        reasoning_tokens int, cache_write_tokens int, usage_source varchar(16),
        created_at timestamptz NOT NULL
    )""",
    """
    CREATE TABLE tenant_llm_policy (
        tenant_id varchar(128) PRIMARY KEY, privacy_mode varchar(32) NOT NULL DEFAULT 'metadata_only',
        allowed_model_aliases json, allowed_provider_types json, rpm_limit int, tpm_limit int,
        tokens_per_day int, pii_config json, allow_mode_override boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
    )""",
    """
    CREATE TABLE llm_provider_instance (
        id uuid PRIMARY KEY, type varchar(64) NOT NULL DEFAULT 'vllm', base_url text NOT NULL DEFAULT '',
        source_kind varchar(64), source_id varchar(128)
    )""",
)


@pytest.fixture
async def gateway(fastapi_app: FastAPI, dbsession: AsyncSession) -> AsyncIterator[AsyncSession]:
    """The gateway's tables on the test's connection; the app reads them as its gateway database."""
    for statement in GATEWAY_DDL:
        await dbsession.execute(text(statement))
    saved = getattr(fastapi_app.state, "llm_graph_trace_session_factory", None)
    fastapi_app.state.llm_graph_trace_session_factory = async_sessionmaker(dbsession.bind, expire_on_commit=False)
    yield dbsession
    fastapi_app.state.llm_graph_trace_session_factory = saved


_MEASURE_NAMES = (
    "requests", "failed", "refused", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
)


def _measures(each: dict[str, int], count: int) -> dict[str, int]:
    return {name: each.get(name, 0) * count for name in _MEASURE_NAMES}


async def usage(
    db: AsyncSession,
    day: date,
    users: Iterable[str],
    *,
    group: str | None = None,
    agent: str | None = None,
    model: str = "m1",
    project: str | None = None,
    provider: str | None = None,
    currency: str | None = None,
    cost_each: str | None = None,
    person_rows: bool = True,
    tenant: str = "default",
    path: list[str] | None = None,
    **each: int,
) -> None:
    """What the rollup job writes for *users* on *day* in one cell: a daily row, and a row per person.

    *each* holds each person's measures (default one request, 10 tokens in, 5 out). *path* is the recorded
    team path (root first; its last team the home team); default ``[group]``.
    """
    people = list(users)
    each = {"requests": 1, "input_tokens": 10, "output_tokens": 5, **each}
    cost = Decimal(cost_each) if cost_each else None
    if path is not None:
        group = path[-1]
    else:
        path = [group] if group else None
    await db.execute(
        text("""
            INSERT INTO usage_daily (day, tenant_id, group_id, group_path, agent_id, model_alias,
                provider_instance_id, people, requests, failed, refused, input_tokens, cached_input_tokens,
                output_tokens, reasoning_tokens, cost, currency)
            VALUES (:day, :tenant, :group, :path, :agent, :model, :provider, :people, :requests, :failed,
                :refused, :input_tokens, :cached_input_tokens, :output_tokens, :reasoning_tokens, :cost, :currency)
        """),
        {
            "day": day, "tenant": tenant, "group": group, "path": path, "agent": agent, "model": model,
            "provider": uuid.UUID(provider) if provider else None, "people": len(people),
            **_measures(each, len(people)), "cost": cost * len(people) if cost else None, "currency": currency,
        },
    )
    if not person_rows:
        return
    for user in people:
        await db.execute(
            text("""
                INSERT INTO usage_daily_person (day, tenant_id, user_id, group_id, group_path, project_id,
                    agent_id, model_alias, requests, failed, refused, input_tokens, cached_input_tokens,
                    output_tokens, reasoning_tokens, cost, currency)
                VALUES (:day, :tenant, :user, :group, :path, :project, :agent, :model, :requests, :failed,
                    :refused, :input_tokens, :cached_input_tokens, :output_tokens, :reasoning_tokens, :cost,
                    :currency)
            """),
            {
                "day": day, "tenant": tenant, "user": user, "group": group, "path": path, "project": project,
                "agent": agent, "model": model, **_measures(each, 1), "cost": cost, "currency": currency,
            },
        )


async def hourly(db: AsyncSession, hour: datetime, people: int, *, requests: int = 1, model: str = "m1") -> None:
    """One ``usage_hourly`` cell."""
    await db.execute(
        text("""
            INSERT INTO usage_hourly (hour, tenant_id, model_alias, people, requests, input_tokens)
            VALUES (:hour, 'default', :model, :people, :requests, :requests * 10)
        """),
        {"hour": hour, "model": model, "people": people, "requests": requests},
    )


async def request_log(db: AsyncSession, user: str, at: datetime, **columns: Any) -> None:
    """One row of the request log, with a device, a conversation and a session (none of which is exported)."""
    row = {
        "id": uuid.uuid4(), "request_id": uuid.uuid4().hex, "tenant_id": "default", "user_id": user,
        "endpoint": "/v1/chat/completions", "status_code": 200, "model_alias": "m1", "prompt_tokens": 12,
        "completion_tokens": 7, "cached_tokens": 2, "reasoning_tokens": 3, "estimated_total_cost": Decimal("0.5"),
        "currency": "EUR", "device_id": "device-secret-1", "conversation_id": "conv-secret-1",
        "session_id": "session-secret-1", "created_at": at, **columns,
    }
    names = ", ".join(row)
    values = ", ".join(f":{name}" for name in row)
    await db.execute(text(f"INSERT INTO llm_gateway_request_log ({names}) VALUES ({values})"), row)  # noqa: S608


async def person(db: AsyncSession, email: str, *, roles: Iterable[str] = (), superuser: bool = False) -> User:
    """An account with *roles* (the built-in roles seeded)."""
    rbac = RbacDAO(db)
    await rbac.seed_defaults()
    user = User(
        id=uuid.uuid4(), email=email, hashed_password="x", is_active=True, is_verified=True,  # noqa: S106
        is_superuser=superuser,
    )
    db.add(user)
    await db.flush()
    for name in roles:
        role = await rbac.get_role_by_name(name)
        assert role is not None, name
        await rbac.assign_role(user.id, role.id)
    await db.flush()
    return user


def sign_in(app: FastAPI, user: User) -> None:
    """Make *user* the one signed in."""
    app.dependency_overrides[current_active_user] = lambda: user


async def set_usage(db: AsyncSession, **values: object) -> None:
    """Store ``usage.<name>`` settings directly, as if changed earlier."""
    dao = SystemSettingsDAO(db)
    for name, value in values.items():
        await dao.upsert_value(f"usage.{name}", {"value": value}, None)
    await db.flush()
