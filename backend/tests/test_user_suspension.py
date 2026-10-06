"""An administrator suspends an account and lets it back in; nothing of the person's is deleted.

It could only be deleted from the console -- with everything it owned -- or switched off through the
API without a trace in the audit log.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.containers import AuditEvent
from tests.test_api_tokens import _As, _user, signed_in  # noqa: F401 - fixture used by name


@pytest.mark.anyio
async def test_an_administrator_suspends_and_reactivates_an_account(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    admin = await _user(dbsession, admin=True)
    person = await _user(dbsession)
    signed_in.user = admin

    r = await client.put(f"/api/admin/users/{person.id}/active", json={"active": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    await dbsession.refresh(person)
    assert person.is_active is False

    r = await client.put(f"/api/admin/users/{person.id}/active", json={"active": True})
    assert r.json()["is_active"] is True

    actions = (
        await dbsession.execute(select(AuditEvent.action).where(AuditEvent.target_id == str(person.id)))
    ).scalars().all()
    assert sorted(actions) == ["user.reactivate", "user.suspend"]


@pytest.mark.anyio
async def test_nobody_suspends_themselves_or_the_last_superuser(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    admin = await _user(dbsession, admin=True)
    signed_in.user = admin
    r = await client.put(f"/api/admin/users/{admin.id}/active", json={"active": False})
    assert r.status_code == 409 and "yourself" in r.text

    other_admin = await _user(dbsession, admin=True)
    admins = (await dbsession.execute(select(type(admin)).where(type(admin).is_superuser.is_(True)))).scalars().all()
    for extra in admins:
        if extra.id not in (admin.id, other_admin.id):
            extra.is_active = False
    await dbsession.flush()
    admin.is_active = False  # the signed-in one, as far as the count goes
    await dbsession.flush()
    r = await client.put(f"/api/admin/users/{other_admin.id}/active", json={"active": False})
    assert r.status_code == 409 and "last active superuser" in r.text


@pytest.mark.anyio
async def test_only_an_administrator_suspends(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    person = await _user(dbsession)
    signed_in.user = await _user(dbsession)
    r = await client.put(f"/api/admin/users/{person.id}/active", json={"active": False})
    assert r.status_code == 403
