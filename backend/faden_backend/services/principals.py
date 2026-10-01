"""Users and groups to grant something to: searched by e-mail or group name, or named by id.

Shared by everything that grants access to people and teams (skills, agents),
so they find and label principals the same way.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.groups import Group
from faden_backend.db.models.users import User

#: How many users and how many groups a search returns at most.
SEARCH_LIMIT = 10


class PrincipalOption(BaseModel):
    """A user or group, with what to show for it."""

    type: str
    id: str
    label: str


def parse_ids(ids: str) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """``user:<id>,group:<id>,...`` as user and group ids; malformed entries are skipped."""
    users: list[uuid.UUID] = []
    groups: list[uuid.UUID] = []
    for item in ids.split(","):
        kind, _, raw = item.strip().partition(":")
        try:
            value = uuid.UUID(raw)
        except ValueError:
            continue
        if kind == "user":
            users.append(value)
        elif kind == "group":
            groups.append(value)
    return users, groups


async def find_principals(
    session: AsyncSession, *, q: str | None = None, ids: str | None = None,
) -> list[PrincipalOption]:
    """Groups, then users: those named by *ids*, else those matching *q* (active users only)."""
    users: list[User] = []
    groups: list[Group] = []
    if ids:
        wanted_users, wanted_groups = parse_ids(ids)
        if wanted_users:
            users = list((await session.execute(select(User).where(User.id.in_(wanted_users)))).scalars())
        if wanted_groups:
            groups = list((await session.execute(select(Group).where(Group.id.in_(wanted_groups)))).scalars())
    elif q and q.strip():
        pattern = f"%{q.strip()}%"
        users = list(
            (
                await session.execute(
                    select(User)
                    .where(User.is_active.is_(True), User.email.ilike(pattern))
                    .order_by(User.email)
                    .limit(SEARCH_LIMIT),
                )
            ).scalars(),
        )
        groups = list(
            (
                await session.execute(
                    select(Group)
                    .where(or_(Group.name.ilike(pattern), Group.description.ilike(pattern)))
                    .order_by(Group.name)
                    .limit(SEARCH_LIMIT),
                )
            ).scalars(),
        )
    return [PrincipalOption(type="group", id=str(g.id), label=g.name) for g in groups] + [
        PrincipalOption(type="user", id=str(u.id), label=u.email) for u in users
    ]
