"""Teams in a tree: where each team sits, for usage and its reports.

Each team has at most one parent (``groups.parent_id``); a path from a top team down holds at most
``MAX_LEVELS`` teams, the top team counting as level 1, as Azure's management groups do; there are no cycles.
Every change is checked here, and refused with a code the console words (422 ``team_cycle``,
``team_too_deep``, ``team_archived``).

The tree is for usage only: rights do not flow down it, each team's roles are its own (least privilege; adding
a parent gives nobody new access). A person's usage goes to their home team (``user.usage_group_id``, at any
level) and to every team above it; the gateway records that path with each request (its ``usage_group.py``).

An archived team (``archived_at``) keeps its history and its place in the tree; it takes no new members,
is nobody's new home team and gets no new sub-teams, and pickers leave it out. Deleting a team moves its
sub-teams to its parent, so no part of the tree is cut off.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.groups import Group

#: The most teams on a path from a top team down, the top team included (as the gateway's ``PATH_LEVELS``).
MAX_LEVELS = 6


class TeamRefusal(Exception):  # noqa: N818 - a refusal, not an error of the program
    """A change the tree does not take: ``team_cycle``, ``team_too_deep`` or ``team_archived``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Tree:
    """Every team's parent and whether it is archived, read at once (an organisation has some hundreds)."""

    parents: dict[uuid.UUID, uuid.UUID | None]
    archived: set[uuid.UUID]

    def ancestors(self, team: uuid.UUID) -> list[uuid.UUID]:
        """The teams above *team*, from the top down; stops at a cycle a change outside here may have made."""
        found: list[uuid.UUID] = []
        current = self.parents.get(team)
        while current is not None and current not in found and current != team:
            found.append(current)
            current = self.parents.get(current)
        return list(reversed(found))

    def path(self, team: uuid.UUID) -> list[uuid.UUID]:
        """*team*'s path from the top down, *team* last."""
        return [*self.ancestors(team), team]

    def children(self, team: uuid.UUID) -> list[uuid.UUID]:
        """The teams right below *team*."""
        return [child for child, parent in self.parents.items() if parent == team]

    def height(self, team: uuid.UUID) -> int:
        """How many levels *team* and the teams below it take (1 for a team without sub-teams)."""
        below, levels, seen = [team], 0, {team}
        while below:
            levels += 1
            below = [c for t in below for c in self.children(t) if c not in seen]
            seen.update(below)
        return levels


async def load(session: AsyncSession) -> Tree:
    """The tree as it is now."""
    rows = await session.execute(select(Group.id, Group.parent_id, Group.archived_at))
    parents: dict[uuid.UUID, uuid.UUID | None] = {}
    archived: set[uuid.UUID] = set()
    for team, parent, archived_at in rows:
        parents[team] = parent
        if archived_at is not None:
            archived.add(team)
    return Tree(parents, archived)


def check_parent(tree: Tree, team: uuid.UUID | None, parent: uuid.UUID | None) -> None:
    """Refuse *parent* for *team* (``None``: a team still to be made) when the tree would not take it."""
    if parent is None:
        return
    if parent == team or (team is not None and team in tree.ancestors(parent)):
        raise TeamRefusal("team_cycle")
    if parent in tree.archived:
        raise TeamRefusal("team_archived")
    height = tree.height(team) if team is not None else 1
    if len(tree.path(parent)) + height > MAX_LEVELS:
        raise TeamRefusal("team_too_deep")


def check_open(tree: Tree, teams: Iterable[uuid.UUID]) -> None:
    """Refuse ``team_archived`` when one of *teams* is archived (a new member, a new home team)."""
    if any(team in tree.archived for team in teams):
        raise TeamRefusal("team_archived")


async def move_children_up(session: AsyncSession, team: Group) -> list[uuid.UUID]:
    """Before *team* is deleted: its sub-teams move to its parent (the top, if it has none). Their ids."""
    children = list(await session.scalars(select(Group.id).where(Group.parent_id == team.id)))
    if children:
        await session.execute(
            update(Group).where(Group.parent_id == team.id).values(parent_id=team.parent_id),
        )
        await session.flush()
    return children


def now() -> datetime:
    """The time an archive is stamped with."""
    return datetime.now(UTC)
