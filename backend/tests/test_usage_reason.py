"""A usage auditor's reason: in a header, never in the URL, and what it may unhide.

- The reason travels in ``X-Faden-Reason`` (percent-encoded UTF-8); one in the query string is refused, so no
  client leaks it into access logs (ASVS 5.0 14.2.1).
- It unhides only a small instance's totals and series, never by hour, and by day only while per-person
  figures are by day (M10, M07); the record names the people of the unhidden cells.
- The people and teams to pick from for the per-person view, for a usage auditor who is no administrator.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.db.models.groups import Group
from tests.usage_fixtures import gateway, hourly, person, set_usage, sign_in, usage

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

REASON = "Prüfung der Kosten mit dem Betriebsrat"
HEADERS = {"X-Faden-Reason": quote(REASON, safe="")}
D1 = date(2026, 10, 1)
DAY = {"from": "2026-10-01", "to": "2026-10-01"}


def _tag() -> str:
    return uuid.uuid4().hex[:6]


async def _looks(db: AsyncSession) -> list[AuditEvent]:
    rows = await db.execute(select(AuditEvent).where(AuditEvent.action == "usage.people.view"))
    return list(rows.scalars())


@pytest.fixture
async def auditor(fastapi_app: FastAPI, gateway: AsyncSession) -> Any:
    """A usage auditor and administrator, with person-level reporting on."""
    await set_usage(gateway, person_reporting=True)
    user = await person(gateway, f"ua-{_tag()}@example.com", roles=["admin", "usage_auditor"])
    sign_in(fastapi_app, user)
    return user


# ── In a header, never in the URL ───────────────────────────────────


@pytest.mark.parametrize("path", [
    "/api/admin/usage", "/api/admin/usage/people", "/api/admin/usage/people/choices",
    f"/api/admin/usage/people/{uuid.uuid4()}/export",
])
async def test_a_reason_in_the_query_string_is_refused(
    client: AsyncClient, gateway: AsyncSession, auditor: Any, path: str,
) -> None:
    response = await client.get(path, params={"reason": "Checking the bill of a team", "team": str(uuid.uuid4())})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"] == {"code": "reason_in_query"}
    assert await _looks(gateway) == []


async def test_the_reason_header_is_percent_encoded_utf8_and_recorded_decoded(
    client: AsyncClient, gateway: AsyncSession, auditor: Any,
) -> None:
    await usage(gateway, D1, ["p1", "p2"])
    body = (await client.get("/api/admin/usage", params=DAY, headers=HEADERS)).json()
    assert body["unhidden"] is True
    [look] = await _looks(gateway)
    assert json.loads(look.metadata_json or "{}")["reason"] == REASON


@pytest.mark.parametrize("raw", [REASON.encode("utf-8"), b"%FF%FE%FD not UTF-8 at all"])
async def test_a_header_not_percent_encoded_counts_as_no_reason(
    client: AsyncClient, gateway: AsyncSession, auditor: Any, raw: bytes,
) -> None:
    team = str(uuid.uuid4())
    response = await client.get("/api/admin/usage/people", params={"team": team}, headers={"X-Faden-Reason": raw})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"] == {"code": "reason_required"}


# ── What a reason may unhide ─────────────────────────────────────────


async def test_on_an_instance_of_k_people_or_more_a_reason_unhides_no_hour(
    client: AsyncClient, gateway: AsyncSession, auditor: Any,
) -> None:
    """The reviewer's case: twenty people that day, one alone at 03:00."""
    await usage(gateway, D1, [f"p{n}" for n in range(20)])
    await hourly(gateway, datetime(2026, 10, 1, 9, tzinfo=UTC), people=20, requests=40)
    await hourly(gateway, datetime(2026, 10, 1, 3, tzinfo=UTC), people=1, requests=7)

    body = (await client.get("/api/admin/usage", params={**DAY, "bucket": "hour"}, headers=HEADERS)).json()

    series = {row["period"]: row for row in body["series"]}
    assert body["small_instance"] is False and body["unhidden"] is False
    assert series["2026-10-01T03:00:00Z"]["suppressed"] is True and series["2026-10-01T03:00:00Z"]["requests"] is None
    assert await _looks(gateway) == [], "nothing was unhidden, nothing is recorded"


async def test_on_a_small_instance_a_reason_unhides_no_hour_either(
    client: AsyncClient, gateway: AsyncSession, auditor: Any,
) -> None:
    await usage(gateway, D1, ["p1", "p2", "p3"])
    await hourly(gateway, datetime(2026, 10, 1, 3, tzinfo=UTC), people=1, requests=7)
    body = (await client.get("/api/admin/usage", params={**DAY, "bucket": "hour"}, headers=HEADERS)).json()
    assert body["small_instance"] is True and body["unhidden"] is False
    assert body["totals"]["requests"] is None
    assert all(row["suppressed"] for row in body["series"] if row["period"] == "2026-10-01T03:00:00Z")
    assert await _looks(gateway) == []


async def test_with_monthly_person_figures_a_reason_unhides_months_not_days(
    client: AsyncClient, gateway: AsyncSession, auditor: Any,
) -> None:
    await set_usage(gateway, person_granularity="month")
    await usage(gateway, D1, ["p1", "p2", "p3"])

    by_day = (await client.get("/api/admin/usage", params={**DAY, "bucket": "day"}, headers=HEADERS)).json()
    assert by_day["unhidden"] is False and by_day["series"][0]["suppressed"] is True
    by_month = (await client.get("/api/admin/usage", params={**DAY, "bucket": "month"}, headers=HEADERS)).json()
    assert by_month["unhidden"] is True and by_month["series"][0]["requests"] == 3

    [look] = await _looks(gateway)
    details = json.loads(look.metadata_json or "{}")
    assert (look.result, details["bucket"], details["person_ids"]) == (AuditResult.ALLOW, "month", ["p1", "p2", "p3"])


# ── The people and teams to pick from ────────────────────────────────


async def test_a_usage_auditor_who_is_no_administrator_can_pick_and_open_usage(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession,
) -> None:
    await set_usage(gateway, person_reporting=True)
    tag = _tag()
    team = Group(id=uuid.uuid4(), name=f"Sales-{tag}")
    gone = Group(id=uuid.uuid4(), name=f"Archived-{tag}", archived_at=datetime.now(UTC))
    gateway.add_all([team, gone])
    holder = await person(gateway, f"ua-{tag}@example.com", roles=["usage_auditor"])
    sign_in(fastapi_app, holder)

    response = await client.get("/api/admin/usage/people/choices")

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert {"id": str(holder.id), "email": holder.email} in body["people"]
    assert set(body["people"][0]) == {"id", "email"}
    teams = {row["id"]: row for row in body["teams"]}
    assert teams[str(team.id)] == {"id": str(team.id), "name": team.name, "archived": False}
    assert teams[str(gone.id)]["archived"] is True
    emails = [row["email"].lower() for row in body["people"]]
    assert emails == sorted(emails), "by address, never by usage"
    assert await _looks(gateway) == []
    assert (await client.get("/api/admin/usage", params=DAY)).status_code == status.HTTP_200_OK


@pytest.mark.parametrize(
    ("switch", "roles", "superuser", "code"),
    [
        (False, ["usage_auditor"], False, "person_reporting_off"),
        (True, ["admin"], True, "not_usage_auditor"),
        (True, ["auditor"], False, "not_usage_auditor"),
    ],
)
async def test_the_choices_have_the_switch_and_the_grant_as_gates(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession,
    switch: bool, roles: list[str], superuser: bool, code: str,
) -> None:
    await set_usage(gateway, person_reporting=switch)
    sign_in(fastapi_app, await person(gateway, f"x-{_tag()}@example.com", roles=roles, superuser=superuser))
    response = await client.get("/api/admin/usage/people/choices")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == {"code": code}
