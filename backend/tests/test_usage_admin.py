"""``GET /api/admin/usage``: figures of teams, agents, models and providers, each about at least k people.

A group or a period of fewer than k distinct people is not shown on its own; the hidden ones together hold at
least k people, else the smallest shown ones are hidden with them (M04, M05). Hours only where k people are
(M10). No person anywhere in the answer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.groups import Group
from faden_backend.services.extensions import registry
from tests.usage_fixtures import gateway, hourly, person, set_usage, sign_in, usage

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)
RANGE = {"from": "2026-10-01", "to": "2026-10-03"}


def _people(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{n}" for n in range(count)]


async def _team(db: AsyncSession, name: str) -> str:
    group = Group(id=uuid.uuid4(), name=f"{name}-{uuid.uuid4().hex[:6]}")
    db.add(group)
    await db.flush()
    return str(group.id)


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> Any:
    user = await person(dbsession, f"admin-{uuid.uuid4().hex[:6]}@example.com", roles=["admin"])
    sign_in(fastapi_app, user)
    return user


async def _get(client: AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get("/api/admin/usage", params={**RANGE, **params})
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


def _by_key(body: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    return {row["key"]: row for row in body["groups"] if not row["other"]}


async def test_teams_under_k_are_merged_into_one_other_row(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    sales, ops, small_a, small_b = [await _team(gateway, n) for n in ("Sales", "Ops", "A", "B")]
    await usage(gateway, D1, _people("s", 7), group=sales)
    await usage(gateway, D1, _people("o", 6), group=ops)
    await usage(gateway, D1, ["a1", "a2", "a3"], group=small_a)
    await usage(gateway, D1, ["b1", "b2"], group=small_b, requests=4)

    body = await _get(client, group_by="team")

    shown = _by_key(body)
    assert set(shown) == {sales, ops}
    assert shown[sales]["people"] == 7 and shown[sales]["requests"] == 7 and shown[sales]["suppressed"] is False
    assert shown[sales]["label"].startswith("Sales-")
    [other] = [row for row in body["groups"] if row["other"]]
    assert other == {
        "key": None, "label": None, "parent_key": None, "path": [], "other": True, "people": None,
        "suppressed": True,
        "requests": 3 + 8, "failed": 0, "refused": 0, "input_tokens": 50, "cached_input_tokens": 0,
        "output_tokens": 25, "reasoning_tokens": 0, "cost": {},
    }
    assert body["totals"]["requests"] == 7 + 6 + 3 + 8
    assert body["totals"]["people"] == 18
    assert body["min_group_size"] == 5 and body["small_instance"] is False


async def test_a_small_rest_takes_the_smallest_shown_team_along(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """Two people alone in "other" would be the total minus the teams shown (complementary suppression)."""
    sales, ops, tiny = [await _team(gateway, n) for n in ("Sales", "Ops", "Tiny")]
    await usage(gateway, D1, _people("s", 7), group=sales)
    await usage(gateway, D2, _people("o", 6), group=ops)
    await usage(gateway, D3, ["t1", "t2"], group=tiny)

    body = await _get(client, group_by="team")

    assert set(_by_key(body)) == {sales}
    [other] = [row for row in body["groups"] if row["other"]]
    assert other["requests"] == 6 + 2 and other["suppressed"] is True


async def test_the_same_people_in_two_small_groups_count_once(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """Agents x and y are used by the same three people: three, not six, so "other" needs company."""
    await usage(gateway, D1, _people("s", 7), agent="big")
    await usage(gateway, D1, _people("o", 6), agent="mid")
    await usage(gateway, D1, ["p1", "p2", "p3"], agent="x")
    await usage(gateway, D2, ["p1", "p2", "p3"], agent="y")

    body = await _get(client, group_by="agent")

    assert set(_by_key(body)) == {"big"}


async def test_fewer_than_k_people_in_all_hide_every_group_and_flag_a_small_instance(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    await usage(gateway, D1, ["p1", "p2"], model="m1")
    await usage(gateway, D2, ["p1", "p3"], model="m2")

    body = await _get(client, group_by="model")

    assert body["small_instance"] is True
    assert body["totals"]["people"] is None and body["totals"]["requests"] is None, "M04: totals too"
    [other] = body["groups"]
    assert other["other"] is True and other["requests"] is None and other["cost"] is None
    assert all(row["suppressed"] for row in body["series"] if row["period"] in ("2026-10-01", "2026-10-02"))


async def test_beyond_the_per_person_retention_the_count_errs_towards_hiding(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """Without per-person rows only each day's own count is known: three a day may be the same three."""
    for day in (D1, D2, D3):
        await usage(gateway, day, _people(f"d{day.day}-", 3), model="old", person_rows=False)
    await usage(gateway, D1, _people("s", 8), model="big", person_rows=False)

    body = await _get(client, group_by="model")

    assert set(_by_key(body)) == set()  # "old" counts at least 3; "big" goes with it (3 alone would show)
    assert body["groups"][0]["other"] is True


async def test_days_under_k_are_hidden_with_their_complement(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    await usage(gateway, D1, _people("a", 9))
    await usage(gateway, D2, _people("b", 6))
    await usage(gateway, D3, ["c1", "c2"])

    body = await _get(client, bucket="day")

    series = {row["period"]: row for row in body["series"]}
    assert list(series) == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert series["2026-10-01"]["suppressed"] is False and series["2026-10-01"]["requests"] == 9
    assert series["2026-10-02"] == {"period": "2026-10-02", "suppressed": True, **_hidden()}
    assert series["2026-10-03"]["suppressed"] is True


async def test_months_are_named_by_their_first_day(client: AsyncClient, gateway: AsyncSession, admin: Any) -> None:
    await usage(gateway, D1, _people("a", 5))
    await usage(gateway, date(2026, 11, 2), _people("a", 5))

    response = await client.get(
        "/api/admin/usage", params={"from": "2026-09-30", "to": "2026-11-05", "bucket": "month"},
    )

    assert [row["period"] for row in response.json()["series"]] == ["2026-09-01", "2026-10-01", "2026-11-01"]
    assert [row["requests"] for row in response.json()["series"]] == [0, 5, 5]


async def test_hours_carry_figures_only_with_k_people(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    await hourly(gateway, datetime(2026, 10, 1, 8, tzinfo=UTC), people=8, requests=20)
    await hourly(gateway, datetime(2026, 10, 1, 9, tzinfo=UTC), people=6, requests=12)
    await hourly(gateway, datetime(2026, 10, 1, 10, tzinfo=UTC), people=2, requests=3)

    response = await client.get("/api/admin/usage", params={"from": "2026-10-01", "to": "2026-10-01", "bucket": "hour"})

    series = {row["period"]: row for row in response.json()["series"]}
    assert len(series) == 24  # the usage time zone is UTC by default
    assert series["2026-10-01T08:00:00Z"]["requests"] == 20
    assert series["2026-10-01T09:00:00Z"]["suppressed"] is True  # the complement of 10:00
    assert series["2026-10-01T10:00:00Z"]["suppressed"] is True
    assert series["2026-10-01T11:00:00Z"] == {"period": "2026-10-01T11:00:00Z", "suppressed": False, **_zero()}


async def test_hours_follow_the_usage_time_zone(client: AsyncClient, gateway: AsyncSession, admin: Any) -> None:
    await set_usage(gateway, time_zone="Europe/Berlin")
    response = await client.get("/api/admin/usage", params={"from": "2026-10-01", "to": "2026-10-01", "bucket": "hour"})
    body = response.json()
    assert body["time_zone"] == "Europe/Berlin"
    assert body["series"][0]["period"] == "2026-09-30T22:00:00Z"


async def test_a_larger_k_hides_more(client: AsyncClient, gateway: AsyncSession, admin: Any) -> None:
    team = await _team(gateway, "Sales")
    await usage(gateway, D1, _people("s", 7), group=team)
    await usage(gateway, D1, _people("o", 9), group=None)
    await set_usage(gateway, min_group_size=8)

    body = await _get(client)

    assert body["min_group_size"] == 8
    assert _by_key(body) == {}  # 7 hidden; the 9 without a team go with them (7 alone would show)


async def test_costs_are_per_currency(client: AsyncClient, gateway: AsyncSession, admin: Any) -> None:
    await usage(gateway, D1, _people("a", 5), currency="EUR", cost_each="0.5")
    await usage(gateway, D1, _people("a", 5), currency="USD", cost_each="1.25", model="m2")
    body = await _get(client, group_by="model")
    assert body["totals"]["cost"] == {"EUR": 2.5, "USD": 6.25}
    assert _by_key(body)["m1"]["cost"] == {"EUR": 2.5}


async def test_the_answer_names_nobody(client: AsyncClient, gateway: AsyncSession, admin: Any) -> None:
    await usage(gateway, D1, _people("someone-", 6))
    text = (await client.get("/api/admin/usage", params=RANGE)).text
    assert "someone-" not in text


async def test_a_section_an_extension_adds_shows_and_a_failing_one_does_not(
    client: AsyncClient, gateway: AsyncSession, admin: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def budgets(session: Any, user: Any, scope: str) -> list[dict[str, Any]]:
        seen.append(scope)
        return [{"kind": "budgets", "data": {"count": 2}}]

    async def broken(session: Any, user: Any, scope: str) -> list[dict[str, Any]]:
        raise RuntimeError

    monkeypatch.setattr(registry, "usage_sections", [broken, budgets])
    body = await _get(client)
    assert body["sections"] == [{"kind": "budgets", "data": {"count": 2}}] and seen == ["admin"]


async def test_the_platform_auditor_reads_it_and_a_viewer_does_not(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession,
) -> None:
    sign_in(fastapi_app, await person(gateway, f"aud-{uuid.uuid4().hex[:6]}@example.com", roles=["auditor"]))
    assert (await client.get("/api/admin/usage", params=RANGE)).status_code == status.HTTP_200_OK
    sign_in(fastapi_app, await person(gateway, f"view-{uuid.uuid4().hex[:6]}@example.com", roles=["viewer"]))
    assert (await client.get("/api/admin/usage", params=RANGE)).status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"from": "2026-13-01"}, "from"),
        ({"from": "2026-10-05", "to": "2026-10-01"}, "from"),
        ({"from": "2026-01-01", "to": "2026-03-01", "bucket": "hour"}, "to"),
    ],
)
async def test_a_bad_range_is_named(
    client: AsyncClient, gateway: AsyncSession, admin: Any, params: dict[str, str], field: str,
) -> None:
    response = await client.get("/api/admin/usage", params=params)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"]["problems"][0]["field"] == field


def _hidden() -> dict[str, Any]:
    names = ("requests", "failed", "refused", "input_tokens", "cached_input_tokens", "output_tokens",
             "reasoning_tokens", "cost")
    return dict.fromkeys(names)


def _zero() -> dict[str, Any]:
    return {**dict.fromkeys(_hidden(), 0), "cost": {}}
