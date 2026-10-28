"""``GET /api/usage/me`` and its download: a person's own usage, nobody else's (M03, M13, M14).

The download carries the daily figures, the request log's metadata (no content, no device, no conversation)
and the dates and purposes of views of the person's figures.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.agents import Agent
from faden_backend.services.extensions import registry
from tests.usage_fixtures import gateway, person, request_log, set_usage, sign_in, usage

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

D1, D2 = date(2026, 10, 1), date(2026, 10, 2)
RANGE = {"from": "2026-10-01", "to": "2026-10-03"}


@pytest.fixture
async def me(fastapi_app: FastAPI, gateway: AsyncSession) -> Any:
    user = await person(gateway, f"me-{uuid.uuid4().hex[:6]}@example.com")
    sign_in(fastapi_app, user)
    return user


async def _agent(db: AsyncSession, name: str) -> str:
    agent = Agent(id=uuid.uuid4(), slug=f"a-{uuid.uuid4().hex[:6]}", name=name)
    db.add(agent)
    await db.flush()
    return str(agent.id)


async def test_only_the_callers_own_figures(client: AsyncClient, gateway: AsyncSession, me: Any) -> None:
    helper = await _agent(gateway, "Helper")
    mine = str(me.id)
    await usage(gateway, D1, [mine, "other-1", "other-2"], model="m1", requests=2)
    await usage(gateway, D2, [mine], agent=helper, project="p-1", model="m2", currency="EUR", cost_each="0.25")
    await usage(gateway, D2, ["other-1"], model="m3", requests=50)

    response = await client.get("/api/usage/me", params=RANGE)

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body["from"], body["to"], body["bucket"], body["time_zone"]) == ("2026-10-01", "2026-10-03", "day", "UTC")
    assert body["totals"]["requests"] == 3 and body["totals"]["cost"] == {"EUR": 0.25}
    assert [(row["period"], row["requests"]) for row in body["series"]] == [
        ("2026-10-01", 2), ("2026-10-02", 1), ("2026-10-03", 0),
    ]
    assert {row["key"]: row["requests"] for row in body["by_model"]} == {"m1": 2, "m2": 1}
    assert [(row["key"], row["label"]) for row in body["by_agent"]] == [(None, None), (helper, "Helper")]
    assert {row["key"]: row["label"] for row in body["by_project"]} == {None: None, "p-1": None}
    assert body["privacy"] == {
        "person_reporting": False, "granularity": "day", "retention_days": 90, "views_of_my_data": 0,
    }
    assert body["caps"] == [] and body["sections"] == []


async def test_by_month(client: AsyncClient, gateway: AsyncSession, me: Any) -> None:
    await usage(gateway, D1, [str(me.id)])
    await usage(gateway, date(2026, 11, 3), [str(me.id)], requests=4)
    response = await client.get("/api/usage/me", params={"from": "2026-10-01", "to": "2026-11-30", "bucket": "month"})
    assert [(row["period"], row["requests"]) for row in response.json()["series"]] == [
        ("2026-10-01", 1), ("2026-11-01", 4),
    ]


async def test_the_privacy_notice_follows_the_settings(client: AsyncClient, gateway: AsyncSession, me: Any) -> None:
    await set_usage(gateway, person_reporting=True, person_granularity="month", person_retention_days=120)
    privacy = (await client.get("/api/usage/me")).json()["privacy"]
    assert privacy == {"person_reporting": True, "granularity": "month", "retention_days": 120, "views_of_my_data": 0}


async def test_the_caps_and_todays_use_only_with_k_others_that_day(
    client: AsyncClient, gateway: AsyncSession, me: Any,
) -> None:
    await gateway.execute(text(
        "INSERT INTO tenant_llm_policy (tenant_id, rpm_limit, tokens_per_day) VALUES ('default', 60, 100000)",
    ))
    today = datetime.now(UTC).date()
    await usage(gateway, today, [str(me.id), "o1", "o2", "o3", "o4"])  # four others: fewer than k

    caps = {cap["kind"]: cap for cap in (await client.get("/api/usage/me")).json()["caps"]}
    assert set(caps) == {"rpm", "tokens_per_day"}
    assert caps["rpm"]["limit"] == 60 and caps["rpm"]["used"] is None
    assert caps["tokens_per_day"]["limit"] == 100000 and caps["tokens_per_day"]["used"] is None
    midnight = datetime.combine(today + timedelta(days=1), datetime.min.time(), UTC)
    assert datetime.fromisoformat(caps["tokens_per_day"]["resets_at"]) == midnight

    await usage(gateway, today, ["o5"], model="m2")  # five others
    caps = {cap["kind"]: cap for cap in (await client.get("/api/usage/me")).json()["caps"]}
    assert caps["tokens_per_day"]["used"] == 6 * 15


async def test_a_section_an_extension_adds_is_shown_to_the_person(
    client: AsyncClient, gateway: AsyncSession, me: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def budgets(session: Any, user: Any, scope: str) -> list[dict[str, Any]]:
        return [{"kind": "budgets", "data": {"user": str(user.id), "scope": scope}}]

    monkeypatch.setattr(registry, "usage_sections", [budgets])
    sections = (await client.get("/api/usage/me")).json()["sections"]
    assert sections == [{"kind": "budgets", "data": {"user": str(me.id), "scope": "me"}}]


async def test_signed_out_is_refused(client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession) -> None:
    from faden_backend.db.models.users import current_active_user

    fastapi_app.dependency_overrides.pop(current_active_user, None)
    assert (await client.get("/api/usage/me")).status_code == status.HTTP_401_UNAUTHORIZED


async def _seed_export(db: AsyncSession, me: Any) -> str:
    helper = await _agent(db, "Helper")
    mine = str(me.id)
    await usage(db, D1, [mine, "other-1"], agent=helper, group="team-1", project="p-9", currency="EUR",
                cost_each="0.5")
    await request_log(db, mine, datetime(2026, 10, 1, 9, 30, tzinfo=UTC), agent_id=helper,
                      model_alias="=HYPERLINK(1)")
    await request_log(db, "other-1", datetime(2026, 10, 1, 9, 31, tzinfo=UTC), model_alias="theirs")
    return helper


async def test_the_json_download_holds_the_persons_data_and_no_content_or_device(
    client: AsyncClient, gateway: AsyncSession, me: Any,
) -> None:
    helper = await _seed_export(gateway, me)

    response = await client.get("/api/usage/me/export", params={"format": "json"})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert data["person"] == {"id": str(me.id), "email": me.email}
    [daily] = data["daily"]
    assert daily | {} == {
        "date": "2026-10-01", "team_id": "team-1", "team": None, "project_id": "p-9", "agent_id": helper,
        "agent": "Helper", "model": "m1", "requests": 1, "failed": 0, "refused": 0, "input_tokens": 10,
        "cached_input_tokens": 0, "output_tokens": 5, "reasoning_tokens": 0, "cost": 0.5, "currency": "EUR",
    }
    [request] = data["requests"]
    assert set(request) == {
        "time", "model", "agent_id", "agent", "status", "input_tokens", "cached_input_tokens", "output_tokens",
        "reasoning_tokens", "cost", "currency",
    }
    assert (request["input_tokens"], request["cached_input_tokens"], request["reasoning_tokens"]) == (12, 2, 3)
    assert data["requests_truncated"] is False and data["views_of_my_data"] == []
    for secret in ("device-secret-1", "conv-secret-1", "session-secret-1", "other-1", "theirs"):
        assert secret not in response.text


async def test_the_csv_download_is_one_table_with_a_kind_per_row(
    client: AsyncClient, gateway: AsyncSession, me: Any,
) -> None:
    await _seed_export(gateway, me)

    response = await client.get("/api/usage/me/export", params={"format": "csv"})

    assert response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["record"] for row in rows] == ["daily", "request"]
    assert rows[1]["model"] == "'=HYPERLINK(1)"  # a spreadsheet does not run it
    assert rows[1]["date"] == "2026-10-01" and rows[1]["input_tokens"] == "12"
    for secret in ("device-secret-1", "conv-secret-1", "session-secret-1", "other-1"):
        assert secret not in response.text
