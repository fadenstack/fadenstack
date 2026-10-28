"""``GET /api/admin/usage?group_by=team``: teams as a tree, each with its sub-teams, at least k people per row.

As recorded (the gateway's ``group_path``) by default, or in today's structure. A team under k folds into its
parent's ``other`` row, which must hold k people itself (complementary suppression among siblings, the people
homed in the parent itself counted with them). Below k for the whole range the totals and series are hidden
unless a usage auditor gives a reason (person-level reporting on), which is recorded (M04, M05, M07).
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.db.models.groups import Group
from tests.usage_fixtures import gateway, person, set_usage, sign_in, usage

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

D1 = date(2026, 10, 1)
RANGE = {"from": "2026-10-01", "to": "2026-10-03", "group_by": "team"}
REASON = "Checking the bill of a small office"


def _people(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{n}" for n in range(count)]


async def _team(db: AsyncSession, name: str, parent: str | None = None) -> str:
    group = Group(id=uuid.uuid4(), name=f"{name}-{uuid.uuid4().hex[:4]}",
                  parent_id=uuid.UUID(parent) if parent else None)
    db.add(group)
    await db.flush()
    return str(group.id)


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> Any:
    user = await person(dbsession, f"admin-{uuid.uuid4().hex[:6]}@example.com", roles=["admin"])
    sign_in(fastapi_app, user)
    return user


async def _groups(client: AsyncClient, **params: str) -> list[dict[str, Any]]:
    response = await client.get("/api/admin/usage", params={**RANGE, **params})
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()["groups"]


def _view(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [(r["key"], r["parent_key"], r["other"], r["requests"], r["people"]) for r in rows]


async def test_a_team_counts_its_sub_teams_and_small_ones_fold_with_the_next_smallest(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    org = await _team(gateway, "Org")
    sales, ops, tiny = [await _team(gateway, n, org) for n in ("Sales", "Ops", "Tiny")]
    await usage(gateway, D1, _people("s", 7), path=[org, sales])
    await usage(gateway, D1, _people("o", 6), path=[org, ops])
    await usage(gateway, D1, ["t1", "t2"], path=[org, tiny])

    rows = await _groups(client)

    assert _view(rows) == [
        (org, None, False, 15, 15),
        (sales, org, False, 7, 7),
        (None, org, True, 8, None),  # Tiny alone would be Org minus Sales minus Ops: Ops goes with it
    ]
    assert rows[1]["path"] == [org, sales] and rows[2]["path"] == [org] and rows[2]["suppressed"] is True
    assert rows[0]["label"].startswith("Org-")


async def test_people_homed_in_the_parent_itself_count_as_a_sibling(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """Org minus its shown teams would single out the two homed in Org: the smallest team goes with them."""
    org = await _team(gateway, "Org")
    sales, ops = [await _team(gateway, n, org) for n in ("Sales", "Ops")]
    await usage(gateway, D1, ["h1", "h2"], path=[org])
    await usage(gateway, D1, _people("s", 7), path=[org, sales])
    await usage(gateway, D1, _people("o", 6), path=[org, ops])

    assert _view(await _groups(client)) == [
        (org, None, False, 15, 15), (sales, org, False, 7, 7), (None, org, True, 8, None),
    ]


async def test_a_team_counts_distinct_people_across_its_sub_teams(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """Two teams of three: neither is shown, their parent of six is, and its rest holds six."""
    org = await _team(gateway, "Org")
    a, b = [await _team(gateway, n, org) for n in ("A", "B")]
    await usage(gateway, D1, _people("a", 3), path=[org, a])
    await usage(gateway, D1, _people("b", 3), path=[org, b])
    assert _view(await _groups(client)) == [(org, None, False, 6, 6), (None, org, True, 6, None)]


async def test_people_without_a_home_team_are_unassigned(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    org = await _team(gateway, "Org")
    await usage(gateway, D1, _people("s", 6), path=[org])
    await usage(gateway, D1, _people("u", 5), group=None, model="m2")
    rows = await _groups(client)
    assert _view(rows) == [(org, None, False, 6, 6), (None, None, False, 5, 5)]
    assert rows[1]["path"] == [] and rows[1]["label"] is None


async def test_a_few_unassigned_fold_into_the_top_levels_other_row(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    big, mid = await _team(gateway, "Big"), await _team(gateway, "Mid")
    await usage(gateway, D1, _people("b", 8), path=[big])
    await usage(gateway, D1, _people("m", 6), path=[mid])
    await usage(gateway, D1, ["u1"], group=None, model="m2")
    assert _view(await _groups(client)) == [(big, None, False, 8, 8), (None, None, True, 7, None)]


async def test_as_recorded_or_in_todays_structure(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    """Sales was under Old when it was used; today it is under New."""
    old, new = await _team(gateway, "Old"), await _team(gateway, "New")
    sales = await _team(gateway, "Sales", new)
    await usage(gateway, D1, _people("s", 6), path=[old, sales])

    recorded = await _groups(client)
    assert [(r["path"], r["requests"]) for r in recorded] == [([old], 6), ([old, sales], 6)]

    response = await client.get("/api/admin/usage", params={**RANGE, "structure": "current"})
    assert response.json()["structure"] == "current"
    assert [(r["path"], r["requests"]) for r in response.json()["groups"]] == [([new], 6), ([new, sales], 6)]


async def test_a_team_deleted_since_stands_alone_in_todays_structure(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    gone = str(uuid.uuid4())
    await usage(gateway, D1, _people("g", 5), path=[gone])
    rows = (await client.get("/api/admin/usage", params={**RANGE, "structure": "current"})).json()["groups"]
    assert [(r["key"], r["label"], r["path"]) for r in rows] == [(gone, None, [gone])]


# ── M04 strictly: totals and series too ─────────────────────────────


async def test_below_k_the_totals_and_the_series_are_hidden(
    client: AsyncClient, gateway: AsyncSession, admin: Any,
) -> None:
    await usage(gateway, D1, ["p1", "p2", "p3"])
    body = (await client.get("/api/admin/usage", params=RANGE)).json()
    assert body["small_instance"] is True and body["unhidden"] is False
    assert body["totals"]["requests"] is None and body["totals"]["cost"] is None
    assert body["totals"]["people"] is None
    assert [row["suppressed"] for row in body["series"]] == [True, False, False]


async def test_a_usage_auditor_with_a_reason_sees_them_and_it_is_recorded(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession,
) -> None:
    await usage(gateway, D1, ["p1", "p2", "p3"])
    await set_usage(gateway, person_reporting=True)
    auditor = await person(gateway, f"ua-{uuid.uuid4().hex[:6]}@example.com", roles=["admin", "usage_auditor"])
    sign_in(fastapi_app, auditor)

    body = (await client.get("/api/admin/usage", params={**RANGE, "reason": REASON})).json()

    assert body["unhidden"] is True and body["totals"]["requests"] == 3 and body["totals"]["people"] == 3
    assert body["series"][0] | {} == {**body["series"][0], "suppressed": False, "requests": 3}
    assert all(row["suppressed"] for row in body["groups"]), "the groups keep the k rule"
    rows = await gateway.execute(select(AuditEvent).where(AuditEvent.action == "usage.people.view"))
    [event] = rows.scalars()
    assert (event.result, event.target_type) == (AuditResult.ALLOW, "usage_aggregate")
    details = json.loads(event.metadata_json or "{}")
    assert details["person_ids"] == ["p1", "p2", "p3"] and details["reason"] == REASON

    sign_in(fastapi_app, await person(gateway, f"a-{uuid.uuid4().hex[:6]}@example.com", roles=["admin"]))
    refused = await client.get("/api/admin/usage", params={**RANGE, "reason": REASON})
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert refused.json()["detail"] == {"code": "not_usage_auditor"}


async def test_with_the_switch_off_a_reason_gets_nothing(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession,
) -> None:
    sign_in(fastapi_app, await person(gateway, f"ua-{uuid.uuid4().hex[:6]}@example.com",
                                      roles=["admin", "usage_auditor"]))
    refused = await client.get("/api/admin/usage", params={**RANGE, "reason": REASON})
    assert refused.json()["detail"] == {"code": "person_reporting_off"}
