"""Teams in a tree: a parent, a cost centre, archiving; one parent, at most six teams on a path, no cycles.

Refusals are 422 ``team_cycle``, ``team_too_deep`` or ``team_archived``; deleting a team moves its sub-teams to
its parent; an archived team takes no new members and is nobody's new home team; each change is recorded.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.services import provisioning, teams
from tests.usage_fixtures import person, sign_in

pytestmark = pytest.mark.anyio()

URL = "/api/admin/groups"


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> Any:
    user = await person(dbsession, f"admin-{uuid.uuid4().hex[:6]}@example.com", superuser=True)
    sign_in(fastapi_app, user)
    return user


async def _team(client: AsyncClient, name: str, parent: str | None = None, **extra: Any) -> dict[str, Any]:
    body = {"name": f"{name}-{uuid.uuid4().hex[:6]}", "parent_id": parent, **extra}
    response = await client.post(f"{URL}/", json=body)
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _chain(client: AsyncClient, length: int) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for level in range(length):
        found.append(await _team(client, f"L{level + 1}", found[-1]["id"] if found else None))
    return found


async def _events(db: AsyncSession, action: str) -> list[AuditEvent]:
    rows = await db.execute(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq))
    return list(rows.scalars())


async def test_a_team_takes_a_parent_and_a_cost_centre(client: AsyncClient, admin: Any) -> None:
    top = await _team(client, "Org")
    sales = await _team(client, "Sales", top["id"], cost_center="  4711 ")
    assert (sales["parent_id"], sales["level"], sales["cost_center"], sales["archived_at"]) == (
        top["id"], 2, "4711", None,
    )
    listed = {team["id"]: team for team in (await client.get(f"{URL}/")).json()}
    assert listed[top["id"]]["level"] == 1 and listed[sales["id"]]["level"] == 2


async def test_no_cycles(client: AsyncClient, admin: Any) -> None:
    top, middle, low = await _chain(client, 3)
    for parent in (low["id"], middle["id"], top["id"]):
        response = await client.patch(f"{URL}/{top['id']}", json={"parent_id": parent})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        assert response.json()["detail"] == {"code": "team_cycle"}


async def test_at_most_six_teams_on_a_path(client: AsyncClient, admin: Any) -> None:
    chain = await _chain(client, 6)
    too_deep = await client.post(f"{URL}/", json={"name": f"L7-{uuid.uuid4().hex[:6]}", "parent_id": chain[-1]["id"]})
    assert too_deep.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert too_deep.json()["detail"] == {"code": "team_too_deep"}

    # A team with one sub-team takes two levels: under level 5 it would make seven.
    branch = await _team(client, "Branch")
    await _team(client, "Leaf", branch["id"])
    moved = await client.patch(f"{URL}/{branch['id']}", json={"parent_id": chain[4]["id"]})
    assert moved.json()["detail"] == {"code": "team_too_deep"}
    ok = await client.patch(f"{URL}/{branch['id']}", json={"parent_id": chain[3]["id"]})
    assert ok.status_code == status.HTTP_200_OK and ok.json()["level"] == 5


async def test_a_move_is_recorded_with_where_from_and_where_to(
    client: AsyncClient, dbsession: AsyncSession, admin: Any,
) -> None:
    old, new = await _team(client, "Old"), await _team(client, "New")
    team = await _team(client, "Team", old["id"])
    response = await client.patch(f"{URL}/{team['id']}", json={"parent_id": new["id"], "cost_center": "CC-9"})
    assert response.status_code == status.HTTP_200_OK
    [event] = [e for e in await _events(dbsession, "team.update") if e.target_id == team["id"]]
    details = json.loads(event.metadata_json or "{}")
    assert details["parent_id"] == {"from": old["id"], "to": new["id"]}
    assert details["cost_center"] == {"from": None, "to": "CC-9"}

    untouched = await client.patch(f"{URL}/{team['id']}", json={"description": "x"})
    assert untouched.json()["parent_id"] == new["id"] and untouched.json()["cost_center"] == "CC-9"
    to_top = await client.patch(f"{URL}/{team['id']}", json={"parent_id": None, "cost_center": None})
    assert (to_top.json()["parent_id"], to_top.json()["cost_center"], to_top.json()["level"]) == (None, None, 1)


async def test_deleting_a_team_moves_its_sub_teams_to_its_parent(
    client: AsyncClient, dbsession: AsyncSession, admin: Any,
) -> None:
    top, middle, low = await _chain(client, 3)
    assert (await client.delete(f"{URL}/{middle['id']}")).status_code == status.HTTP_204_NO_CONTENT
    assert (await client.get(f"{URL}/{low['id']}")).json()["parent_id"] == top["id"]
    [event] = await _events(dbsession, "team.delete")
    assert json.loads(event.metadata_json or "{}")["sub_teams"] == [low["id"]]


async def test_an_archived_team_takes_nobody_new_and_no_sub_teams(
    client: AsyncClient, dbsession: AsyncSession, admin: Any,
) -> None:
    team = await _team(client, "Ended")
    member = await person(dbsession, f"m-{uuid.uuid4().hex[:6]}@example.com")
    newcomer = await person(dbsession, f"n-{uuid.uuid4().hex[:6]}@example.com")
    await client.post(f"{URL}/{team['id']}/members", json={"user_ids": [str(member.id)]})
    await client.put(f"/api/admin/users/{member.id}/usage-group", json={"usage_group_id": team["id"]})

    archived = await client.post(f"{URL}/{team['id']}/archive")
    assert archived.status_code == status.HTTP_200_OK and archived.json()["archived_at"] is not None
    assert archived.json()["home_count"] == 1
    assert [e.target_id for e in await _events(dbsession, "team.archive")] == [team["id"]]

    refusals = [
        await client.post(f"{URL}/{team['id']}/members", json={"user_ids": [str(newcomer.id)]}),
        await client.put(f"{URL}/{team['id']}/members", json={"user_ids": [str(member.id), str(newcomer.id)]}),
        await client.put(f"/api/admin/users/{newcomer.id}/usage-group", json={"usage_group_id": team["id"]}),
        await client.post(f"{URL}/", json={"name": f"Sub-{uuid.uuid4().hex[:6]}", "parent_id": team["id"]}),
    ]
    for response in refusals:
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, response.text
        assert response.json()["detail"] == {"code": "team_archived"}

    # Leaving works, and the one homed there keeps it.
    assert (await client.put(f"{URL}/{team['id']}/members", json={"user_ids": []})).status_code == 200
    kept = await client.put(f"/api/admin/users/{member.id}/usage-group", json={"usage_group_id": team["id"]})
    assert kept.status_code == status.HTTP_200_OK

    found = await client.get("/api/agents/principals", params={"q": team["name"][:5]})
    assert team["id"] not in {p["id"] for p in found.json()}, "pickers leave it out"

    back = await client.post(f"{URL}/{team['id']}/unarchive")
    assert back.json()["archived_at"] is None
    assert [e.target_id for e in await _events(dbsession, "team.unarchive")] == [team["id"]]
    added = await client.post(f"{URL}/{team['id']}/members", json={"user_ids": [str(newcomer.id)]})
    assert added.status_code == status.HTTP_201_CREATED


async def test_a_team_a_provisioning_source_keeps_takes_a_parent_too(
    client: AsyncClient, dbsession: AsyncSession, admin: Any,
) -> None:
    top = await _team(client, "Org")
    kept = Group(id=uuid.uuid4(), name=f"Directory team {uuid.uuid4().hex[:6]}", source="provider:entra")
    dbsession.add(kept)
    await dbsession.flush()
    response = await client.patch(f"{URL}/{kept.id}", json={"parent_id": top["id"], "cost_center": "7"})
    assert response.status_code == status.HTTP_200_OK, response.text
    assert (response.json()["parent_id"], response.json()["source"]) == (top["id"], "provider:entra")


async def test_provisioning_adds_nobody_to_an_archived_team(dbsession: AsyncSession) -> None:
    user = await person(dbsession, f"p-{uuid.uuid4().hex[:6]}@example.com")
    open_team = Group(id=uuid.uuid4(), name=f"Open {uuid.uuid4().hex[:6]}")
    ended = Group(id=uuid.uuid4(), name=f"Ended {uuid.uuid4().hex[:6]}", archived_at=teams.now())
    dbsession.add_all([open_team, ended])
    await dbsession.flush()
    added, _ = await provisioning.sync_memberships(dbsession, user, "provider:x", [open_team.id, ended.id])
    assert added == [open_team.id]
    rows = await dbsession.execute(select(UserGroup.group_id).where(UserGroup.user_id == user.id))
    assert set(rows.scalars()) == {open_team.id}


def test_the_tree_rules_on_their_own() -> None:
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    tree = teams.Tree(parents={a: None, b: a, c: b, d: None}, archived={d})
    assert tree.path(c) == [a, b, c] and tree.height(a) == 3 and tree.children(a) == [b]
    teams.check_parent(tree, d, c)  # three above, one: four
    for team, parent, code in ((a, c, "team_cycle"), (a, a, "team_cycle"), (b, d, "team_archived")):
        with pytest.raises(teams.TeamRefusal) as refused:
            teams.check_parent(tree, team, parent)
        assert refused.value.code == code
    looped = teams.Tree(parents={a: b, b: a}, archived=set())
    assert looped.ancestors(a) == [b], "a cycle made outside stops the walk"
