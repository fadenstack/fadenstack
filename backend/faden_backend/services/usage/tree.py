"""Usage by team as a tree: each team with everything below it, at least k people per figure shown.

A person's usage counts toward their home team and every team above it. Two structures:

- ``recorded`` (the default): the path the gateway recorded with each request (``group_path``, root first),
  so a report after a reorganisation shows usage where it was booked, as Google Cloud's billing export keeps
  the ancestry at the time of use. A team moved since appears under each parent it had, once per path.
- ``current``: each home team's usage under the team's place in today's tree (``services/teams.py``).

Rows without a team (people with no home team) are "Unassigned": ``key`` null, ``other`` false.

The minimum group size (M04, M05) counts the distinct people of a team and all its sub-teams. A team under k
is not shown: it folds into its parent's ``other`` row, which holds everything of the parent that no shown
sub-team row holds (the smaller sub-teams, and the people whose home is the parent itself). That row must hold
k people, else the smallest shown sibling folds in too (complementary suppression); so the parent minus its
shown sub-teams never singles out fewer than k people. The same holds for the top level and its ``other``
row (``parent_key`` null). Counts beyond the per-person retention err towards hiding (``suppression.py``).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from faden_backend.services.teams import Tree
from faden_backend.services.usage.measures import Measures, hidden

Structure = Literal["recorded", "current"]
Path = tuple[str, ...]


@dataclass
class Part:
    """Figures of a part of the tree, and its people: those known by id, and at least ``floor`` of them."""

    measures: Measures = field(default_factory=Measures)
    users: set[str] = field(default_factory=set)
    floor: int = 0

    @property
    def people(self) -> int:
        """At least this many distinct people."""
        return max(self.floor, len(self.users))

    @property
    def empty(self) -> bool:
        """Nothing in it."""
        return self.measures.requests == 0 and self.people == 0


def path_of(
    group_id: str | None, group_path: list[str] | None, structure: Structure, tree: Tree,
) -> Path | None:
    """The path a rollup row's usage counts toward; ``None``: Unassigned."""
    if group_id is None and not group_path:
        return None
    if structure == "recorded":
        return tuple(group_path) if group_path else (str(group_id),)
    home = str(group_id) if group_id is not None else str(group_path[-1])  # type: ignore[index]
    try:
        team = uuid.UUID(home)
    except ValueError:
        return (home,)
    if team not in tree.parents:  # a team deleted since: on its own at the top
        return (home,)
    return tuple(str(step) for step in tree.path(team))


@dataclass
class Built:
    """Every team's whole subtree, the part homed directly on each, and the Unassigned part."""

    subtree: dict[Path, Part] = field(default_factory=dict)
    direct: dict[Path, Part] = field(default_factory=dict)
    unassigned: Part = field(default_factory=Part)

    def add_cells(self, path: Path | None, row: dict[str, Any]) -> None:
        """One summed rollup row (its figures and the largest ``people`` of its cells)."""
        for part in self._parts(path):
            part.measures.add(row)
            part.floor = max(part.floor, int(row.get("floor") or 0))

    def add_person(self, path: Path | None, user_id: str) -> None:
        """A person active on *path*."""
        for part in self._parts(path):
            part.users.add(user_id)

    def _parts(self, path: Path | None) -> list[Part]:
        if path is None:
            return [self.unassigned]
        parts = [self.subtree.setdefault(path[:n], Part()) for n in range(1, len(path) + 1)]
        return [*parts, self.direct.setdefault(path, Part())]

    def children(self, path: Path) -> list[Path]:
        """The teams right below *path* (``()``: the top teams)."""
        return [p for p in self.subtree if len(p) == len(path) + 1 and p[: len(path)] == path]


def _together(parts: list[Part]) -> int:
    users: set[str] = set().union(*(part.users for part in parts)) if parts else set()
    return max([len(users), *(part.people for part in parts)], default=0)


def rows(built: Built, k: int, label: Callable[[str], str | None]) -> list[dict[str, Any]]:
    """The tree's rows, top-down, siblings by name: shown teams, ``other`` rows, and Unassigned."""
    found: list[dict[str, Any]] = []
    _level(built, (), k, label, found)
    return found


def _sort_key(label: Callable[[str], str | None], path: Path | None) -> tuple[bool, str]:
    if path is None:
        return True, ""  # Unassigned after the teams
    return False, str(label(path[-1]) or path[-1]).lower()


def _level(
    built: Built, parent: Path, k: int, label: Callable[[str], str | None], found: list[dict[str, Any]],
) -> None:
    cells: dict[Path | None, Part] = {path: built.subtree[path] for path in built.children(parent)}
    if not parent and not built.unassigned.empty:
        cells[None] = built.unassigned
    direct = built.direct.get(parent) if parent else None
    rest: list[Part] = [direct] if direct is not None and not direct.empty else []
    folded = [path for path, part in cells.items() if not part.empty and part.people < k]
    shown = sorted(
        (path for path, part in cells.items() if not part.empty and part.people >= k),
        key=lambda path: (cells[path].people, cells[path].measures.requests, str(path)),
    )
    if folded or rest:
        while _together(rest + [cells[p] for p in folded]) < k and shown:
            folded.append(shown.pop(0))
    for path in sorted(shown, key=lambda path: _sort_key(label, path)):
        part = cells[path]
        if path is None:
            found.append({
                "key": None, "label": None, "parent_key": None, "path": [], "other": False,
                "people": part.people, "suppressed": False, **part.measures.as_json(),
            })
            continue
        found.append({
            "key": path[-1], "label": label(path[-1]), "parent_key": path[-2] if len(path) > 1 else None,
            "path": list(path), "other": False, "people": part.people, "suppressed": False,
            **part.measures.as_json(),
        })
        _level(built, path, k, label, found)
    if folded:
        held = rest + [cells[p] for p in folded]
        together = Measures()
        for part in held:
            together.merge(part.measures)
        found.append({
            "key": None, "label": None, "parent_key": parent[-1] if parent else None, "path": list(parent),
            "other": True, "people": None, "suppressed": True,
            **(together.as_json() if _together(held) >= k else hidden()),
        })
