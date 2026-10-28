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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable
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
