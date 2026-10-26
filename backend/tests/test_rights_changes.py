"""Any change of a person's rights ends their sign-ins, gained or lost, however it is made (services/rights.py).

The OWASP Session Management Cheat Sheet: a new session after any privilege level change. Each ending is
recorded with its cause; the changes to teams and roles themselves are recorded too.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.groups import Group, GroupRole, UserGroup
from faden_backend.db.models.rbac import Permission, Role
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import provisioning
from faden_backend.services.provisioning import Person, Team
from faden_backend.web.api.admin.dependencies import get_root_mode_active


async def _person(db: AsyncSession, *, admin: bool = False) -> User:
    user = User(
        id=uuid.uuid4(), email=f"p-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x", is_active=True,  # noqa: S106
        is_verified=True, is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    return user


@pytest.fixture
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession) -> User:
    me = await _person(dbsession, admin=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: me
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: True
    return me


async def _role(db: AsyncSession) -> Role:
    return await RbacDAO(db).create_role(f"r-{uuid.uuid4().hex[:8]}", None, [])


async def _ended(db: AsyncSession, *people: User) -> list[bool]:
    """Whether each person's sign-ins were ended (and set them going again for the next step)."""
    answers = []
    for person in people:
        await db.refresh(person)
        answers.append(person.sessions_valid_after is not None)
        person.sessions_valid_after = None
    await db.flush()
    return answers


async def _causes(db: AsyncSession, person: User) -> list[str]:
    events = (await db.execute(select(AuditEvent).where(
        AuditEvent.action == "user.sign_ins.ended", AuditEvent.target_id == str(person.id),
    ))).scalars()
    return [json.loads(event.metadata_json or "{}")["cause"] for event in events]


@pytest.mark.anyio
async def test_a_role_given_or_taken_ends_the_persons_sign_ins(
    client: AsyncClient, dbsession: AsyncSession, admin: User,
) -> None:
    person, role = await _person(dbsession), await _role(dbsession)
    r = await client.put(f"/api/admin/users/{person.id}/roles", json={"role_ids": [str(role.id)]})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert await _ended(dbsession, person) == [True], "given"
    r = await client.put(f"/api/admin/users/{person.id}/roles", json={"role_ids": [str(role.id)]})
    assert await _ended(dbsession, person) == [False], "the same roles again change nothing"
    await client.put(f"/api/admin/users/{person.id}/roles", json={"role_ids": []})
    assert await _ended(dbsession, person) == [True], "taken"
    assert await _causes(dbsession, person) == ["roles", "roles"]


@pytest.mark.anyio
async def test_joining_or_leaving_a_team_ends_sign_ins_and_is_recorded(
    client: AsyncClient, dbsession: AsyncSession, admin: User,
) -> None:
    joins, stays = await _person(dbsession), await _person(dbsession)
    team = (await client.post("/api/admin/groups/", json={"name": f"t-{uuid.uuid4().hex[:6]}"})).json()
    await client.put(f"/api/admin/groups/{team['id']}/members", json={"user_ids": [str(stays.id)]})
    await _ended(dbsession, stays)

    r = await client.post(f"/api/admin/groups/{team['id']}/members", json={"user_ids": [str(joins.id)]})
    assert r.status_code == status.HTTP_201_CREATED, r.text
    assert await _ended(dbsession, joins, stays) == [True, False]
    r = await client.delete(f"/api/admin/groups/{team['id']}/members/{joins.id}")
    assert r.status_code == status.HTTP_204_NO_CONTENT, r.text
    assert await _ended(dbsession, joins, stays) == [True, False]
    r = await client.put(f"/api/admin/groups/{team['id']}/members", json={"user_ids": [str(joins.id)]})
    assert await _ended(dbsession, joins, stays) == [True, True], "one joined, one left"

    changes = (await dbsession.execute(select(AuditEvent).where(
        AuditEvent.action == "team.members.set", AuditEvent.target_id == team["id"],
    ))).scalars().all()
    assert len(changes) == 4
    assert json.loads(changes[-1].metadata_json or "{}")["removed"] == [str(stays.id)]
    assert (await _causes(dbsession, joins))[0] == "team.members"


@pytest.mark.anyio
async def test_a_teams_roles_changed_end_its_members_sign_ins(
    client: AsyncClient, dbsession: AsyncSession, admin: User,
) -> None:
    member, outsider, role = await _person(dbsession), await _person(dbsession), await _role(dbsession)
    team = (await client.post("/api/admin/groups/", json={"name": f"t-{uuid.uuid4().hex[:6]}"})).json()
    await client.put(f"/api/admin/groups/{team['id']}/members", json={"user_ids": [str(member.id)]})
    await _ended(dbsession, member)

    r = await client.patch(f"/api/admin/groups/{team['id']}", json={"role_ids": [str(role.id)]})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert [given["name"] for given in r.json()["roles"]] == [role.name], "the reply shows the roles just set"
    assert await _ended(dbsession, member, outsider) == [True, False]
    r = await client.patch(f"/api/admin/groups/{team['id']}", json={"description": "the same roles"})
    assert await _ended(dbsession, member) == [False]
    assert (await _causes(dbsession, member))[-1] == "team.roles"

    r = await client.delete(f"/api/admin/groups/{team['id']}")
    assert r.status_code == status.HTTP_204_NO_CONTENT, r.text
    assert await _ended(dbsession, member) == [True]
    assert (await _causes(dbsession, member))[-1] == "team.deleted"
    recorded = {e.action for e in (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.target_id == team["id"]),
    )).scalars()}
    assert {"team.create", "team.update", "team.delete"} <= recorded


@pytest.mark.anyio
async def test_a_roles_permissions_changed_end_its_holders_sign_ins(
    client: AsyncClient, dbsession: AsyncSession, admin: User,
) -> None:
    given, through_team, other = await _person(dbsession), await _person(dbsession), await _person(dbsession)
    role = await _role(dbsession)
    await RbacDAO(dbsession).assign_role(given.id, role.id)
    team = Group(id=uuid.uuid4(), name=f"t-{uuid.uuid4().hex[:6]}")
    dbsession.add(team)
    await dbsession.flush()
    dbsession.add_all([
        GroupRole(group_id=team.id, role_id=role.id), UserGroup(user_id=through_team.id, group_id=team.id),
    ])
    await dbsession.flush()
    permission = Permission(id=uuid.uuid4(), resource=f"test.{uuid.uuid4().hex[:6]}", action="read")
    dbsession.add(permission)
    await dbsession.flush()

    r = await client.patch(f"/api/admin/users/roles/{role.id}", json={"permission_ids": [str(permission.id)]})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert [p["id"] for p in r.json()["permissions"]] == [str(permission.id)], "the reply shows them"
    assert await _ended(dbsession, given, through_team, other) == [True, True, False]
    assert (await _causes(dbsession, through_team)) == ["role.permissions"]

    r = await client.delete(f"/api/admin/users/roles/{role.id}")
    assert r.status_code == status.HTTP_204_NO_CONTENT, r.text
    assert await _ended(dbsession, given, through_team, other) == [True, True, False]
    recorded = {e.action for e in (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.target_id == str(role.id)),
    )).scalars()}
    assert {"role.update", "role.delete"} <= recorded


@pytest.mark.anyio
async def test_the_administrator_right_given_ends_sign_ins_too(
    client: AsyncClient, dbsession: AsyncSession, admin: User,
) -> None:
    person = await _person(dbsession)
    r = await client.put(
        f"/api/admin/users/{person.id}/administrator",
        json={"administrator": True, "reason": "Runs the platform from now on"},
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    assert await _ended(dbsession, person) == [True]
    assert await _causes(dbsession, person) == ["administrator"]


@pytest.mark.anyio
async def test_a_providers_team_changes_end_sign_ins(dbsession: AsyncSession) -> None:
    source, tag = "provider:corp", uuid.uuid4().hex[:8]
    found, _ = await provisioning.put_person(
        dbsession, source, Person(external_id=f"e-{tag}", login=f"j-{tag}@corp.test", email=f"j-{tag}@corp.test"),
    )
    joins = found.user
    await _ended(dbsession, joins)
    team, _ = await provisioning.put_team(dbsession, source, Team(name=f"Sales {uuid.uuid4().hex[:6]}"))
    await provisioning.change_members(dbsession, source, team.id, add=[joins.id])
    assert await _ended(dbsession, joins) == [True]
    await provisioning.change_members(dbsession, source, team.id, remove=[joins.id])
    assert await _ended(dbsession, joins) == [True]
    assert await _causes(dbsession, joins) == ["team.members", "team.members"]
