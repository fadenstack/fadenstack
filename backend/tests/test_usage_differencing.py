"""No series of ``GET /api/admin/usage`` gives a hidden figure of another away (M05).

The months of a range, the days of each month and the hours of each day nest: a month minus its shown days,
or a day minus its shown hours, must never single out fewer than k people. The structures of the team tree
(as recorded, as today) are protected against each other the same way.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.groups import Group
from tests.usage_fixtures import gateway, hourly, person, set_usage, sign_in, usage

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

CROWD = [f"p{n}" for n in range(6)]


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> Any:
    user = await person(dbsession, f"admin-{uuid.uuid4().hex[:6]}@example.com", roles=["admin"])
    sign_in(fastapi_app, user)
    return user


async def _series(client: AsyncClient, **params: str) -> list[dict[str, Any]]:
    response = await client.get("/api/admin/usage", params=params)
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()["series"]


async def test_a_month_minus_its_shown_days_never_gives_a_hidden_day(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """The reviewer's case: six people every day, one alone on 31 October with 777 requests."""
    await usage(gateway, date(2026, 9, 30), CROWD)
    day = date(2026, 10, 1)
    while day <= date(2026, 10, 30):
        await usage(gateway, day, CROWD)
        day += timedelta(days=1)
    await usage(gateway, date(2026, 10, 31), ["lonely"], requests=777)
    span = {"from": "2026-09-30", "to": "2026-10-31"}

    days = await _series(client, **span, bucket="day")
    months = await _series(client, **span, bucket="month")

    october = next(row for row in months if row["period"] == "2026-10-01")
    assert october["suppressed"] is False and october["requests"] == 30 * 6 + 777
    shown = sum(row["requests"] for row in days if row["period"].startswith("2026-10") and not row["suppressed"])
    assert october["requests"] - shown != 777, "October minus its shown days is the one person's day"
    hidden = [row["period"] for row in days if row["suppressed"]]
    assert hidden == ["2026-10-01", "2026-10-31"], "the day joins a day of its own month, not 30 September"
    assert october["requests"] - shown == 6 + 777


async def test_a_day_minus_its_shown_hours_never_gives_a_hidden_hour(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """One person at 03:00 on 1 October: the hour that goes with it is one of 1 October, not of the 2nd."""
    await usage(gateway, date(2026, 10, 1), [f"a{n}" for n in range(20)])
    await usage(gateway, date(2026, 10, 2), [f"a{n}" for n in range(20)])
    await hourly(gateway, datetime(2026, 10, 1, 3, tzinfo=UTC), people=1, requests=7)
    await hourly(gateway, datetime(2026, 10, 1, 9, tzinfo=UTC), people=20, requests=40)
    await hourly(gateway, datetime(2026, 10, 2, 9, tzinfo=UTC), people=20, requests=10)

    hours = {row["period"]: row for row in await _series(client, **{"from": "2026-10-01", "to": "2026-10-02"},
                                                         bucket="hour")}

    assert hours["2026-10-01T03:00:00Z"]["suppressed"] is True
    assert hours["2026-10-01T09:00:00Z"]["suppressed"] is True, "1 October minus 09:00 would be 03:00"
    assert hours["2026-10-02T09:00:00Z"] | {} == {**hours["2026-10-02T09:00:00Z"], "suppressed": False,
                                                  "requests": 10}


async def test_a_month_hidden_with_a_small_one_keeps_a_day_hidden_too(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """September (one person) hides October with it; October's days alone would add up to October."""
    await usage(gateway, date(2026, 9, 30), ["lonely"])
    for day in (1, 2, 3):
        await usage(gateway, date(2026, 10, day), CROWD, requests=day)
    span = {"from": "2026-09-30", "to": "2026-10-03"}

    months = await _series(client, **span, bucket="month")
    days = await _series(client, **span, bucket="day")

    assert [row["suppressed"] for row in months] == [True, True]
    assert [row["period"] for row in days if row["suppressed"]] == ["2026-09-30", "2026-10-01"]


async def test_the_half_hour_zones_hours_are_whole_utc_hours(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """Asia/Kolkata (+05:30): the day's hours start at 18:00 UTC the day before, and all 24 carry figures."""
    await set_usage(gateway, time_zone="Asia/Kolkata")
    await usage(gateway, date(2026, 10, 1), [f"a{n}" for n in range(8)])
    start = datetime(2026, 9, 30, 18, tzinfo=UTC)
    for hour in range(24):
        await hourly(gateway, start + timedelta(hours=hour), people=8, requests=10)

    series = await _series(client, **{"from": "2026-10-01", "to": "2026-10-01"}, bucket="hour")

    assert len(series) == 24
    assert series[0]["period"] == "2026-09-30T18:00:00Z" and series[-1]["period"] == "2026-10-01T17:00:00Z"
    assert sum(row["requests"] for row in series) == 240 and not any(row["suppressed"] for row in series)


async def _team(db: AsyncSession, name: str, parent: str | None = None) -> str:
    group = Group(id=uuid.uuid4(), name=f"{name}-{uuid.uuid4().hex[:4]}",
                  parent_id=uuid.UUID(parent) if parent else None)
    db.add(group)
    await db.flush()
    return str(group.id)


async def _tree(client: AsyncClient, structure: str) -> dict[Any, dict[str, Any]]:
    response = await client.get("/api/admin/usage", params={
        "from": "2026-10-01", "to": "2026-10-02", "group_by": "team", "structure": structure,
    })
    assert response.status_code == status.HTTP_200_OK, response.text
    return {tuple(row["path"]): row for row in response.json()["groups"] if not row["other"]}


async def test_a_team_hidden_in_one_structure_is_hidden_in_the_other(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """Sales moved from Old to New: two of its people used it under Old, six under New.

    Today Sales holds all eight; as recorded, New > Sales holds six. Sales today minus New > Sales as recorded
    would be the two under Old: Sales is hidden in both structures, and Old and New with it.
    """
    old, new = await _team(gateway, "Old"), await _team(gateway, "New")
    sales = await _team(gateway, "Sales", new)
    await usage(gateway, date(2026, 10, 1), ["x1", "x2"], path=[old, sales])
    await usage(gateway, date(2026, 10, 2), [f"s{n}" for n in range(6)], path=[new, sales])
    await usage(gateway, date(2026, 10, 1), [f"o{n}" for n in range(9)], path=[old])
    await usage(gateway, date(2026, 10, 1), [f"n{n}" for n in range(9)], path=[new], model="m2")

    recorded = {path[-1]: row for path, row in (await _tree(client, "recorded")).items()}
    current = {path[-1]: row for path, row in (await _tree(client, "current")).items()}

    assert sales not in recorded and sales not in current
    for team in recorded.keys() & current.keys():
        assert recorded[team]["requests"] == current[team]["requests"], (
            "a team today minus the same team as recorded would be the two who used Sales under Old"
        )


async def test_without_a_move_both_structures_show_the_same(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    org = await _team(gateway, "Org")
    sales = await _team(gateway, "Sales", org)
    await usage(gateway, date(2026, 10, 1), [f"s{n}" for n in range(6)], path=[org, sales])
    await usage(gateway, date(2026, 10, 1), [f"o{n}" for n in range(6)], path=[org])
    recorded, current = await _tree(client, "recorded"), await _tree(client, "current")
    assert set(recorded) == set(current) == {(org,), (org, sales)}
