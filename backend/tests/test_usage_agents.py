"""An agent's usage and activity name nobody, and show figures only about at least k people (M04, M05).

An agent only the caller used is theirs: they see their own figures.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.agents import Agent
from tests.usage_fixtures import gateway, person, request_log, sign_in

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

_EVENTS = """
CREATE TABLE agent_client_event (
    id uuid PRIMARY KEY, tenant_id varchar(128) NOT NULL, agent_id varchar(64) NOT NULL,
    user_id varchar(128) NOT NULL, device_id varchar(64), conversation_id varchar(128), type varchar(64) NOT NULL,
    data json NOT NULL, occurred_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
)"""
_MANIFESTS = """
CREATE TABLE agent_client_manifest (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id varchar(128) NOT NULL, agent_id varchar(64) NOT NULL,
    sha256 varchar(64) NOT NULL, source varchar(16) NOT NULL, app_name varchar(100) NOT NULL DEFAULT '',
    app_version varchar(50) NOT NULL DEFAULT '', manifest json NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
    reports integer NOT NULL DEFAULT 1, last_user_id varchar(128) NOT NULL, last_device_id varchar(64)
)"""


@pytest.fixture
async def setup(fastapi_app: FastAPI, gateway: AsyncSession) -> dict[str, Any]:
    admin = await person(gateway, f"admin-{uuid.uuid4().hex[:6]}@example.com", roles=["admin"], superuser=True)
    sign_in(fastapi_app, admin)
    agent = Agent(id=uuid.uuid4(), slug=f"helper-{uuid.uuid4().hex[:6]}", name="Helper")
    gateway.add(agent)
    await gateway.flush()
    return {"admin": admin, "agent": agent}


async def _use(db: AsyncSession, agent: Agent, users: list[str], *, days_ago: int = 1) -> None:
    at = datetime.now(UTC) - timedelta(days=days_ago)
    for user in users:
        await request_log(db, user, at, agent_id=str(agent.id), total_tokens=10, conversation_id=f"c-{user}")


async def test_an_agent_of_fewer_than_k_people_shows_no_figures(
    client: AsyncClient, gateway: AsyncSession, setup: dict[str, Any],
) -> None:
    await _use(gateway, setup["agent"], ["p1", "p2", "p3"])
    response = await client.get(f"/api/agents/{setup['agent'].slug}/usage")
    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["suppressed"] is True and body["min_group_size"] == 5
    assert body["requests"] is None and body["users"] is None and body["by_day"] == []


async def test_the_callers_own_agent_shows_them_their_figures(
    client: AsyncClient, gateway: AsyncSession, setup: dict[str, Any],
) -> None:
    mine = str(setup["admin"].id)
    await _use(gateway, setup["agent"], [mine], days_ago=1)
    await _use(gateway, setup["agent"], [mine], days_ago=2)
    body = (await client.get(f"/api/agents/{setup['agent'].slug}/usage")).json()
    assert body["suppressed"] is False and body["requests"] == 2 and body["users"] == 1
    assert [day["suppressed"] for day in body["by_day"]] == [False, False]


async def test_a_day_of_fewer_than_k_people_is_hidden_with_its_complement(
    client: AsyncClient, gateway: AsyncSession, setup: dict[str, Any],
) -> None:
    agent = setup["agent"]
    await _use(gateway, agent, [f"a{n}" for n in range(8)], days_ago=3)
    await _use(gateway, agent, [f"b{n}" for n in range(6)], days_ago=2)
    await _use(gateway, agent, ["c1", "c2"], days_ago=1)

    body = (await client.get(f"/api/agents/{agent.slug}/usage")).json()

    assert body["suppressed"] is False and body["requests"] == 16 and body["users"] == 16
    days = body["by_day"]
    assert [(d["suppressed"], d["requests"]) for d in days] == [(False, 8), (True, None), (True, None)]


async def test_events_and_apps_name_only_the_caller(
    client: AsyncClient, gateway: AsyncSession, setup: dict[str, Any],
) -> None:
    agent, admin = setup["agent"], setup["admin"]
    other = await person(gateway, f"other-{uuid.uuid4().hex[:6]}@example.com")
    await gateway.execute(text(_EVENTS))
    await gateway.execute(text(_MANIFESTS))
    for user in (str(admin.id), str(other.id)):
        await gateway.execute(
            text("INSERT INTO agent_client_event (id, tenant_id, agent_id, user_id, type, data) "
                 "VALUES (:id, 'default', :agent, :user, 'tool.executed', '{}')"),
            {"id": uuid.uuid4(), "agent": str(agent.id), "user": user},
        )
        await gateway.execute(
            text("INSERT INTO agent_client_manifest (tenant_id, agent_id, sha256, source, app_name, manifest, "
                 "last_user_id) VALUES ('default', :agent, :sha, 'app', :name, CAST(:manifest AS json), :user)"),
            {"agent": str(agent.id), "sha": uuid.uuid4().hex, "name": f"app-{user[:4]}",
             "manifest": json.dumps({"app": {"name": f"app-{user[:4]}"}}), "user": user},
        )

    events = (await client.get(f"/api/agents/{agent.slug}/events")).json()
    assert sorted(str(e["user_id"]) for e in events) == sorted([str(admin.id), "None"])

    apps = (await client.get(f"/api/agents/{agent.slug}/apps")).json()["apps"]
    labels = {(a["last_user_id"], a["last_user_label"]) for a in apps}
    assert labels == {(str(admin.id), admin.email), (None, None)}
    assert other.email not in json.dumps(apps)
