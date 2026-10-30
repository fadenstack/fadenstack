"""The minimum group size k, with complementary suppression (M04, M05).

A figure about fewer than k distinct people is not shown on its own: it is personal data of those people
(GDPR Art. 4(1), recital 26; BAG 1 ABR 6/94). Such cells are hidden; the hidden cells of a table together must
hold at least k people, else the smallest shown cells are hidden with them until they do, so that no hidden
cell follows from the total minus the shown ones (complementary suppression). If the whole table holds fewer
than k people, every cell is hidden.

How many distinct people a set of cells holds is known exactly only where per-person rollups still exist;
beyond their retention only each rollup cell's own count is kept. Every count here is therefore a lower
bound (``floor``): the largest of the cells' own counts and the exact count where there is one. A floor errs
towards hiding, never towards showing.

Tables that nest -- the months of a range, the days of each month, the hours of each day -- are protected
together (:func:`hide_nested`), so that one table cannot be subtracted from another: a month minus its shown
days, a day minus its shown hours. Whatever bucket is asked for, every coarser level is worked out first and
the finer one follows from it, so the answers for all buckets of one range fit one pattern.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable, Sequence
from dataclasses import dataclass, field

from faden_backend.services.usage.measures import Measures


@dataclass
class Cell:
    """One figure of a table (a team, an agent, a day): its measures and how many people it holds at least."""

    key: Hashable
    measures: Measures = field(default_factory=Measures)
    floor: int = 0

    @property
    def empty(self) -> bool:
        """No requests and nobody: nothing to protect, nothing to hide."""
        return self.measures.requests == 0 and self.floor == 0


#: ``(cells) -> at least how many distinct people the cells hold together``.
UnionFloor = Callable[[list[Cell]], Awaitable[int]]


async def hide(cells: list[Cell], k: int, union_floor: UnionFloor) -> tuple[set[Hashable], int]:
    """The keys of the cells not to show, and at least how many people they hold together (0 if none).

    Cells under k are hidden; while the hidden ones hold fewer than k people together, the smallest shown
    cell (fewest people, then fewest requests) joins them. Empty cells are neither hidden nor added.
    """
    candidates = [cell for cell in cells if not cell.empty]
    hidden = [cell for cell in candidates if cell.floor < k]
    if not hidden:
        return set(), 0
    shown = sorted(
        (cell for cell in candidates if cell.floor >= k),
        key=lambda cell: (cell.floor, cell.measures.requests, str(cell.key)),
    )
    together = await union_floor(hidden)
    while together < k and shown:
        hidden.append(shown.pop(0))
        together = await union_floor(hidden)
    return {cell.key for cell in hidden}, together


# ── Nested tables ───────────────────────────────────────────────────

#: ``(level, cells) -> at least how many distinct people the cells of that level hold together``.
LevelFloor = Callable[[int, list[Cell]], Awaitable[int]]


@dataclass
class _Family:
    """Cells of one level whose sum is known from the level above.

    Either the children of one shown parent, or every child of a group of hidden parents whose sum is known
    together (the parent level's hidden cells of one family): their total minus their shown children is.
    """

    parents: list[Hashable]
    parents_hidden: bool
    #: At least how many people the parents hold together: all their children together hold as many.
    people: int


async def hide_nested(
    levels: Sequence[dict[Hashable, Cell]],
    parent_of: Sequence[Callable[[Hashable], Hashable]],
    total_people: int,
    k: int,
    union_floor: LevelFloor,
) -> list[set[Hashable]]:
    """The keys not to show on each level of nested tables (top first), so no level gives another away.

    ``levels[0]`` are the top level's cells (whose parent is the whole range, holding *total_people*);
    ``parent_of[i]`` maps a key of ``levels[i]`` to its parent's key in ``levels[i - 1]`` (``parent_of[0]`` is
    not used). Per family of cells (the children of one shown cell, or all children of one group of hidden
    cells, whose sum is known): the cells under k are hidden; each hidden parent keeps at least one hidden
    child, else it would be the sum of its children; and the hidden cells of the family hold at least k people
    together, else the smallest shown ones join them -- in the end all of them, which hold the parents'
    people.
    A range of fewer than k people hides everything. Cells whose parent has no cell (rollups of different
    retention) form a family of their own, about nobody known.
    """
    found: list[set[Hashable]] = [set() for _ in levels]
    families = [_Family(parents=[None], parents_hidden=total_people < k, people=total_people)]
    for level, cells in enumerate(levels):
        children: dict[Hashable, list[Cell]] = {}
        for cell in cells.values():
            parent = parent_of[level](cell.key) if level else None
            children.setdefault(parent, []).append(cell)
        known = {parent for family in families for parent in family.parents}
        orphans = [parent for parent in children if parent not in known]
        if orphans:
            families.append(_Family(parents=orphans, parents_hidden=False, people=0))
        following: list[_Family] = []
        for family in families:
            members = [cell for parent in family.parents for cell in children.get(parent, [])]
            hidden, people = await _hide_family(family, children, level, k, union_floor)
            found[level] |= {cell.key for cell in hidden}
            following.extend(
                _Family(parents=[cell.key], parents_hidden=False, people=cell.floor)
                for cell in members if cell.key not in found[level]
            )
            if hidden:
                group = [cell.key for cell in hidden]
                following.append(_Family(parents=group, parents_hidden=True, people=people))
        families = following
    return found


def _smallest_first(cell: Cell) -> tuple[int, int, str]:
    return cell.floor, cell.measures.requests, str(cell.key)


async def _hide_family(
    family: _Family, children: dict[Hashable, list[Cell]], level: int, k: int, union_floor: LevelFloor,
) -> tuple[list[Cell], int]:
    """The family's cells to hide, and at least how many people they hold together."""
    members = [cell for parent in family.parents for cell in children.get(parent, []) if not cell.empty]
    hidden = [cell for cell in members if cell.floor < k]
    keys = {cell.key for cell in hidden}
    if family.parents_hidden:
        for parent in family.parents:
            own = sorted((cell for cell in children.get(parent, []) if not cell.empty), key=_smallest_first)
            if own and not any(cell.key in keys for cell in own):
                hidden.append(own[0])
                keys.add(own[0].key)
    if not hidden:
        return [], 0
    shown = sorted((cell for cell in members if cell.key not in keys), key=_smallest_first)

    async def together() -> int:
        floor = await union_floor(level, hidden)
        return floor if shown else max(floor, family.people)  # all of them hold the parents' people

    people = await together()
    while people < k and shown:
        hidden.append(shown.pop(0))
        people = await together()
    return hidden, people
