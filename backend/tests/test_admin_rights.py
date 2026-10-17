"""The administrator right and the auditor role: granted, removed and handed over, recorded with the reason."""

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
from faden_backend.db.models.users import User, current_active_user
from faden_backend.web.api.admin.dependencies import get_root_mode_active

REASON = "Pat moves to another team"


async def _person(db: AsyncSession, email: str, *, admin: bool = False) -> User:
    user = User(
        id=uuid.uuid4(), email=email, hashed_password="x", is_active=True, is_verified=True, is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    return user


@pytest.fixture
async def people(fastapi_app: FastAPI, dbsession: AsyncSession) -> tuple[User, User]:
    admin = await _person(dbsession, f"pat-{uuid.uuid4().hex[:6]}@example.com", admin=True)
    other = await _person(dbsession, f"sam-{uuid.uuid4().hex[:6]}@example.com")
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: True
    return admin, other


async def _event(db: AsyncSession, action: str) -> AuditEvent:
    return (await db.execute(select(AuditEvent).where(AuditEvent.action == action))).scalar_one()


@pytest.mark.anyio
async def test_granting_needs_root_mode_and_a_reason(
    client: AsyncClient, fastapi_app: FastAPI, people: tuple[User, User],
) -> None:
    _, other = people
    short = await client.put(f"/api/admin/users/{other.id}/administrator", json={"administrator": True, "reason": "x"})
    assert short.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: False
    no_root = await client.put(
        f"/api/admin/users/{other.id}/administrator", json={"administrator": True, "reason": REASON},
    )
    assert no_root.status_code == status.HTTP_403_FORBIDDEN
    assert "Root Mode" in no_root.json()["detail"]


@pytest.mark.anyio
async def test_granting_and_removing_are_recorded_with_the_reason(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    _, other = people
    r = await client.put(f"/api/admin/users/{other.id}/administrator", json={"administrator": True, "reason": REASON})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json()["is_superuser"] is True
    grant = await _event(dbsession, "user.administrator.grant")
    assert json.loads(grant.metadata_json or "{}") == {"email": other.email, "reason": REASON}
    assert grant.severity == "high"

    r = await client.put(f"/api/admin/users/{other.id}/administrator", json={"administrator": False, "reason": REASON})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert (await _event(dbsession, "user.administrator.remove")).target_id == str(other.id)


@pytest.mark.anyio
async def test_the_last_administrator_keeps_the_right(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    admin, _ = people
    others = (await dbsession.execute(
        select(User).where(User.is_superuser.is_(True), User.id != admin.id),
    )).scalars().all()
    for user in others:  # this test's install has one administrator
        user.is_superuser = False
    await dbsession.flush()
    r = await client.put(f"/api/admin/users/{admin.id}/administrator", json={"administrator": False, "reason": REASON})
    assert r.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
async def test_the_administrator_right_is_handed_over_in_one_step(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    admin, other = people
    r = await client.post(
        f"/api/admin/users/{admin.id}/hand-over",
        json={"right": "administrator", "to_user_id": str(other.id), "reason": REASON},
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    await dbsession.refresh(admin)
    await dbsession.refresh(other)
    assert (admin.is_superuser, other.is_superuser) == (False, True)
    event = await _event(dbsession, "user.right.hand_over")
    assert json.loads(event.metadata_json or "{}") == {
        "right": "administrator", "from": admin.email, "to": other.email, "reason": REASON,
    }


@pytest.mark.anyio
async def test_the_auditor_role_is_handed_over(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    _, other = people
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    auditor = await rbac.get_role_by_name("auditor")
    assert auditor is not None
    holder = await _person(dbsession, f"kim-{uuid.uuid4().hex[:6]}@example.com")
    await rbac.assign_role(holder.id, auditor.id)
    await dbsession.flush()

    r = await client.post(
        f"/api/admin/users/{holder.id}/hand-over",
        json={"right": "auditor", "to_user_id": str(other.id), "reason": REASON},
    )
    assert r.status_code == status.HTTP_200_OK, r.text
    assert "auditor" in {role.name for role in await rbac.get_user_roles(other.id)}
    assert "auditor" not in {role.name for role in await rbac.get_user_roles(holder.id)}


@pytest.mark.anyio
async def test_the_auditor_reads_the_audit_and_viewers_do_not(dbsession: AsyncSession) -> None:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    for name, expected in (("auditor", True), ("admin", True), ("viewer", False), ("operator", False)):
        role = await rbac.get_role_by_name(name)
        assert role is not None
        person = await _person(dbsession, f"{name}-{uuid.uuid4().hex[:6]}@example.com")
        await rbac.assign_role(person.id, role.id)
        await dbsession.flush()
        assert await rbac.has_permission(person.id, "audit", "read") is expected, name
