"""The count of changes to access moves with each change the gateway decides access from, and only then."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.users import User


async def _epoch(db: AsyncSession) -> int:
    return int((await db.execute(text("SELECT epoch FROM access_epoch WHERE id = 1"))).scalar_one())


async def _person(db: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(), email=f"e-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True,
    )
    db.add(user)
    await db.flush()
    return user


@pytest.mark.anyio
async def test_a_change_to_access_moves_the_count(dbsession: AsyncSession) -> None:
    person = await _person(dbsession)
    team = Group(id=uuid.uuid4(), name=f"t-{uuid.uuid4().hex[:6]}")
    dbsession.add(team)
    await dbsession.flush()

    before = await _epoch(dbsession)
    dbsession.add(UserGroup(user_id=person.id, group_id=team.id))
    await dbsession.flush()
    joined = await _epoch(dbsession)
    assert joined > before, "a membership"

    await dbsession.execute(update(User).where(User.id == person.id).values(sessions_valid_after=datetime.now(UTC)))
    ended = await _epoch(dbsession)
    assert ended > joined, "sign-ins ended"

    await dbsession.execute(update(User).where(User.id == person.id).values(is_active=False))
    assert await _epoch(dbsession) > ended, "a suspension"


@pytest.mark.anyio
async def test_other_writes_leave_it(dbsession: AsyncSession) -> None:
    person = await _person(dbsession)
    before = await _epoch(dbsession)
    await dbsession.execute(update(User).where(User.id == person.id).values(email=f"x-{person.email}"))
    await dbsession.execute(text('UPDATE "user" SET is_verified = false WHERE id = :id'), {"id": person.id})
    assert await _epoch(dbsession) == before
