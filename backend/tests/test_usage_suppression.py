"""The minimum group size k with complementary suppression, on its own (M04, M05)."""

from __future__ import annotations

from collections.abc import Hashable

import pytest

from faden_backend.services.usage.measures import Measures
from faden_backend.services.usage.suppression import Cell, hide

pytestmark = pytest.mark.anyio()


def _cell(key: str, people: set[str], requests: int = 1) -> tuple[Cell, set[str]]:
    measures = Measures()
    measures.add({"requests": requests})
    return Cell(key=key, measures=measures, floor=len(people)), people


def _counter(members: dict[Hashable, set[str]]):  # noqa: ANN202 - a test helper
    async def together(cells: list[Cell]) -> int:
        return len(set().union(*(members[cell.key] for cell in cells)))

    return together


async def _hide(*cells: tuple[Cell, set[str]], k: int = 5) -> tuple[set[Hashable], int]:
    members = {cell.key: people for cell, people in cells}
    return await hide([cell for cell, _ in cells], k, _counter(members))


async def test_nothing_is_hidden_when_every_cell_has_k_people() -> None:
    hidden, together = await _hide(_cell("a", {f"a{i}" for i in range(5)}), _cell("b", {f"b{i}" for i in range(9)}))
    assert hidden == set() and together == 0


async def test_small_cells_together_with_k_people_hide_alone() -> None:
    hidden, together = await _hide(
        _cell("big", {f"b{i}" for i in range(7)}),
        _cell("x", {"x1", "x2", "x3"}),
        _cell("y", {"y1", "y2"}),
    )
    assert hidden == {"x", "y"} and together == 5


async def test_small_cells_under_k_together_take_the_smallest_shown_cell_with_them() -> None:
    """Else the one hidden cell would be the total minus the shown ones."""
    hidden, together = await _hide(
        _cell("sales", {f"s{i}" for i in range(7)}),
        _cell("ops", {f"o{i}" for i in range(6)}),
        _cell("tiny", {"t1", "t2"}),
    )
    assert hidden == {"tiny", "ops"} and together == 8


async def test_people_in_two_small_cells_are_counted_once() -> None:
    """Two cells of the same three people hold three people, not six."""
    hidden, _ = await _hide(
        _cell("sales", {f"s{i}" for i in range(7)}),
        _cell("ops", {f"o{i}" for i in range(6)}),
        _cell("x", {"p1", "p2", "p3"}),
        _cell("y", {"p1", "p2", "p3"}),
    )
    assert hidden == {"x", "y", "ops"}


async def test_of_two_shown_cells_of_equal_size_the_one_with_fewer_requests_goes() -> None:
    hidden, _ = await _hide(
        _cell("busy", {f"a{i}" for i in range(6)}, requests=50),
        _cell("quiet", {f"b{i}" for i in range(6)}, requests=3),
        _cell("tiny", {"t1"}),
    )
    assert hidden == {"tiny", "quiet"}


async def test_a_table_of_fewer_than_k_people_hides_everything() -> None:
    hidden, together = await _hide(_cell("x", {"p1", "p2"}), _cell("y", {"p3"}))
    assert hidden == {"x", "y"} and together == 3


async def test_empty_cells_are_neither_hidden_nor_taken_along() -> None:
    empty = Cell(key="empty")
    hidden, _ = await hide(
        [empty, Cell(key="tiny", measures=_cell("t", {"a"})[0].measures, floor=1)], 5,
        lambda cells: _async(max(cell.floor for cell in cells)),
    )
    assert hidden == {"tiny"}


async def _async(value: int) -> int:
    return value
