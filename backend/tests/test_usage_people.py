"""``GET /api/admin/usage/people``: named people's usage, behind the switch, the grant and a reason.

Superuser status alone does not open it (M06); every view and every refusal is recorded with whose figures,
which range and the reason (M07); rows are sorted by name, never by usage (M11).
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

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.db.models.groups import Group, GroupRole, UserGroup
from tests.usage_fixtures import gateway, person, set_usage, sign_in, usage

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

REASON = "Budget dispute raised by the team lead"
D1, D2 = date(2026, 10, 1), date(2026, 10, 2)


def _tag() -> str:
    return uuid.uuid4().hex[:6]


@pytest.fixture
async def team(gateway: AsyncSession) -> dict[str, Any]:
    """A team of three; Cleo uses most, Abe least: the answer is in order of the names all the same."""
    group = Group(id=uuid.uuid4(), name=f"Sales-{_tag()}")
    gateway.add(group)
    tag = _tag()
    abe = await person(gateway, f"abe-{tag}@example.com")
    bea = await person(gateway, f"bea-{tag}@example.com")
    cleo = await person(gateway, f"cleo-{tag}@example.com")
    team_id = str(group.id)
    await usage(gateway, D1, [str(cleo.id)], group=team_id, requests=30, model="m1")
    await usage(gateway, D1, [str(bea.id)], group=team_id, requests=20, model="m2")
    await usage(gateway, D2, [str(abe.id)], group=team_id, requests=1, model="m1")
    await usage(gateway, D2, [str(cleo.id)], group=team_id, requests=5, model="m2")
    await usage(gateway, D1, ["someone-else"], group=str(uuid.uuid4()), requests=9)
    return {"id": team_id, "abe": abe, "bea": bea, "cleo": cleo}


def _params(**extra: str) -> dict[str, str]:
    return {"from": "2026-10-01", "to": "2026-10-02", "reason": REASON, **extra}


async def _events(db: AsyncSession) -> list[AuditEvent]:
    rows = await db.execute(select(AuditEvent).where(AuditEvent.action == "usage.people.view"))
    return list(rows.scalars())


async def test_with_the_switch_off_nobody_sees_people_not_even_a_usage_auditor(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any],
) -> None:
    sign_in(fastapi_app, await person(gateway, f"ua-{_tag()}@example.com", roles=["usage_auditor"]))
    response = await client.get("/api/admin/usage/people", params=_params(team=team["id"]))
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == {"code": "person_reporting_off"}
    [refused] = await _events(gateway)
    assert refused.result == AuditResult.DENY
    assert json.loads(refused.metadata_json or "{}")["refused"] == "person_reporting_off"


@pytest.mark.parametrize("roles", [[], ["admin"], ["auditor"]])
async def test_a_superuser_or_an_administrator_without_the_grant_is_refused(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any], roles: list[str],
) -> None:
    await set_usage(gateway, person_reporting=True)
    sign_in(fastapi_app, await person(gateway, f"root-{_tag()}@example.com", roles=roles, superuser=True))
    response = await client.get("/api/admin/usage/people", params=_params(team=team["id"]))
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == {"code": "not_usage_auditor"}


@pytest.mark.parametrize("reason", [None, "", "too short", "x" * 501])
async def test_a_reason_of_10_to_500_characters_is_required(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any], reason: str | None,
) -> None:
    await set_usage(gateway, person_reporting=True)
    sign_in(fastapi_app, await person(gateway, f"ua-{_tag()}@example.com", roles=["usage_auditor"]))
    params = _params(team=team["id"])
    if reason is None:
        params.pop("reason")
    else:
        params["reason"] = reason
    response = await client.get("/api/admin/usage/people", params=params)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["detail"] == {"code": "reason_required"}


async def test_a_usage_auditor_sees_the_team_by_name_and_the_view_is_recorded(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any],
) -> None:
    await set_usage(gateway, person_reporting=True)
    auditor = await person(gateway, f"ua-{_tag()}@example.com", roles=["usage_auditor"])
    sign_in(fastapi_app, auditor)

    response = await client.get("/api/admin/usage/people", params=_params(team=team["id"]))

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body["granularity"] == "day"
    order = [(row["person"]["email"].split("-")[0], row["period"], row["requests"]) for row in body["rows"]]
    assert order == [
        ("abe", "2026-10-02", 1), ("bea", "2026-10-01", 20), ("cleo", "2026-10-01", 30), ("cleo", "2026-10-02", 5),
    ]
    assert body["rows"][0]["person"] == {
        "id": str(team["abe"].id), "name": team["abe"].email, "email": team["abe"].email,
    }
    [event] = await _events(gateway)
    assert (event.result, event.actor_id, event.target_type, event.target_id, event.severity) == (
        AuditResult.ALLOW, auditor.id, "usage_team", team["id"], "high",
    )
    details = json.loads(event.metadata_json or "{}")
    assert details["person_ids"] == sorted(str(team[name].id) for name in ("abe", "bea", "cleo"))
    assert (details["from"], details["to"], details["reason"]) == ("2026-10-01", "2026-10-02", REASON)


async def test_monthly_granularity_gives_months(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any],
) -> None:
    await set_usage(gateway, person_reporting=True, person_granularity="month")
    sign_in(fastapi_app, await person(gateway, f"ua-{_tag()}@example.com", roles=["usage_auditor"]))
    response = await client.get("/api/admin/usage/people", params=_params(person=str(team["cleo"].id)))
    body = response.json()
    assert body["granularity"] == "month"
    assert [(row["period"], row["requests"]) for row in body["rows"]] == [("2026-10-01", 35)]
    [event] = await _events(gateway)
    assert event.target_type == "usage_person"
    assert json.loads(event.metadata_json or "{}")["person_ids"] == [str(team["cleo"].id)]


async def test_the_grant_through_a_team_counts(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any],
) -> None:
    await set_usage(gateway, person_reporting=True)
    holder = await person(gateway, f"ua-{_tag()}@example.com")
    role = await RbacDAO(gateway).get_role_by_name("usage_auditor")
    assert role is not None
    works_council = Group(id=uuid.uuid4(), name=f"Works council {_tag()}")
    gateway.add(works_council)
    await gateway.flush()
    gateway.add_all([GroupRole(group_id=works_council.id, role_id=role.id),
                     UserGroup(user_id=holder.id, group_id=works_council.id)])
    await gateway.flush()
    sign_in(fastapi_app, holder)
    response = await client.get("/api/admin/usage/people", params=_params(team=team["id"]))
    assert response.status_code == status.HTTP_200_OK, response.text


async def test_exactly_one_valid_team_or_person_is_asked_for(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any],
) -> None:
    sign_in(fastapi_app, await person(gateway, f"ua-{_tag()}@example.com", roles=["usage_auditor"]))
    for params in (_params(), _params(team=team["id"], person=str(team["abe"].id)), _params(team="not-an-id")):
        response = await client.get("/api/admin/usage/people", params=params)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, params


async def test_the_person_viewed_sees_when_and_why_but_not_who(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any],
) -> None:
    await set_usage(gateway, person_reporting=True)
    auditor = await person(gateway, f"ua-{_tag()}@example.com", roles=["usage_auditor"])
    sign_in(fastapi_app, auditor)
    await client.get("/api/admin/usage/people", params=_params(team=team["id"]))

    sign_in(fastapi_app, team["bea"])
    mine = (await client.get("/api/usage/me")).json()
    assert mine["privacy"]["views_of_my_data"] == 1
    export = (await client.get("/api/usage/me/export")).json()
    [view] = export["views_of_my_data"]
    assert view["purpose"] == REASON and set(view) == {"at", "purpose"}
    assert auditor.email not in json.dumps(export)

    sign_in(fastapi_app, await person(gateway, f"x-{_tag()}@example.com"))
    assert (await client.get("/api/usage/me")).json()["privacy"]["views_of_my_data"] == 0


# ── One person's download, for an access request (M13) ──────────────


async def _exports(db: AsyncSession) -> list[AuditEvent]:
    rows = await db.execute(select(AuditEvent).where(AuditEvent.action == "usage.people.export"))
    return list(rows.scalars())


async def test_the_auditors_download_is_the_persons_own_and_is_recorded_as_an_export(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any],
) -> None:
    cleo = team["cleo"]
    sign_in(fastapi_app, cleo)
    own = (await client.get("/api/usage/me/export")).json()

    await set_usage(gateway, person_reporting=True)
    auditor = await person(gateway, f"ua-{_tag()}@example.com", roles=["usage_auditor"])
    sign_in(fastapi_app, auditor)
    response = await client.get(f"/api/admin/usage/people/{cleo.id}/export", params={"reason": REASON})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert "attachment" in response.headers["content-disposition"]
    theirs = response.json()
    assert {k: v for k, v in theirs.items() if k != "generated_at"} == {
        k: v for k, v in own.items() if k != "generated_at"
    }
    [event] = await _exports(gateway)
    assert (event.result, event.actor_id, event.target_id) == (AuditResult.ALLOW, auditor.id, str(cleo.id))
    assert json.loads(event.metadata_json or "{}") | {} == {
        "person_ids": [str(cleo.id)], "format": "json", "reason": REASON,
    }
    assert await _events(gateway) == [], "a download is not recorded as a view"

    csv_export = await client.get(f"/api/admin/usage/people/{cleo.id}/export", params={"reason": REASON,
                                                                                         "format": "csv"})
    assert csv_export.headers["content-type"].startswith("text/csv")

    sign_in(fastapi_app, cleo)
    looks = (await client.get("/api/usage/me/export")).json()["views_of_my_data"]
    assert [look["purpose"] for look in looks] == [REASON, REASON] and all(set(x) == {"at", "purpose"} for x in looks)


@pytest.mark.parametrize(
    ("switch", "roles", "superuser", "reason", "code"),
    [
        (False, ["usage_auditor"], False, REASON, "person_reporting_off"),
        (True, ["admin"], True, REASON, "not_usage_auditor"),
        (True, ["usage_auditor"], False, "short", "reason_required"),
    ],
)
async def test_the_download_has_the_same_gates(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, team: dict[str, Any],
    switch: bool, roles: list[str], superuser: bool, reason: str, code: str,
) -> None:
    await set_usage(gateway, person_reporting=switch)
    sign_in(fastapi_app, await person(gateway, f"x-{_tag()}@example.com", roles=roles, superuser=superuser))
    response = await client.get(f"/api/admin/usage/people/{team['abe'].id}/export", params={"reason": reason})
    assert response.json()["detail"] == {"code": code}
    [refused] = await _exports(gateway)
    assert refused.result == AuditResult.DENY


async def test_the_download_of_an_unknown_account_is_404(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession,
) -> None:
    await set_usage(gateway, person_reporting=True)
    sign_in(fastapi_app, await person(gateway, f"ua-{_tag()}@example.com", roles=["usage_auditor"]))
    response = await client.get(f"/api/admin/usage/people/{uuid.uuid4()}/export", params={"reason": REASON})
    assert response.status_code == status.HTTP_404_NOT_FOUND
