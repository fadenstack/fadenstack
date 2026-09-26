"""The dashboard fact cache: fresh for its TTL, computed once per key, never keeps errors."""

from __future__ import annotations

import asyncio

import pytest

from llm_port_backend.services.dashboard.cache import FactCache

pytestmark = pytest.mark.anyio()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_a_value_is_reused_until_its_ttl_runs_out() -> None:
    clock = _Clock()
    cache = FactCache(clock=clock)
    calls = 0

    async def compute() -> int:
        nonlocal calls
        calls += 1
        return calls

    assert await cache.get_or_compute("k", 10, compute) == 1
    clock.now += 9
    assert await cache.get_or_compute("k", 10, compute) == 1
    clock.now += 2
    assert await cache.get_or_compute("k", 10, compute) == 2


async def test_concurrent_misses_share_one_computation() -> None:
    cache = FactCache()
    calls = 0
    release = asyncio.Event()

    async def compute() -> str:
        nonlocal calls
        calls += 1
        await release.wait()
        return "facts"

    waiters = [asyncio.create_task(cache.get_or_compute("k", 10, compute)) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*waiters) == ["facts"] * 5
    assert calls == 1


async def test_an_error_is_not_kept() -> None:
    cache = FactCache()
    attempts = 0

    async def compute() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("gateway DB down")
        return "facts"

    with pytest.raises(RuntimeError):
        await cache.get_or_compute("k", 10, compute)
    assert await cache.get_or_compute("k", 10, compute) == "facts"


async def test_a_caller_that_goes_away_does_not_cancel_the_computation_for_others() -> None:
    cache = FactCache()
    release = asyncio.Event()

    async def compute() -> str:
        await release.wait()
        return "facts"

    leaver = asyncio.create_task(cache.get_or_compute("k", 10, compute))
    stayer = asyncio.create_task(cache.get_or_compute("k", 10, compute))
    await asyncio.sleep(0)
    leaver.cancel()
    await asyncio.sleep(0)
    release.set()

    assert await stayer == "facts"
    with pytest.raises(asyncio.CancelledError):
        await leaver


async def test_the_oldest_entries_go_first_when_full() -> None:
    cache = FactCache(max_entries=2)
    computed: list[str] = []

    def compute_for(key: str):  # noqa: ANN202
        async def compute() -> str:
            computed.append(key)
            return key

        return compute

    await cache.get_or_compute("a", 60, compute_for("a"))
    await cache.get_or_compute("b", 60, compute_for("b"))
    await cache.get_or_compute("a", 60, compute_for("a"))  # a is now the most recent
    await cache.get_or_compute("c", 60, compute_for("c"))  # evicts b
    await cache.get_or_compute("b", 60, compute_for("b"))

    assert computed == ["a", "b", "c", "b"]
