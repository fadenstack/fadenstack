"""A short-lived cache for dashboard facts: platform state, never a caller's view.

Several widgets, on several admins' screens, poll the same figures every 30 to
60 seconds. Computing each answer once per few seconds keeps that from turning
into a query per widget per screen.

What goes in is an aggregate of the platform -- request counts, machine
health -- the same for every caller, so keys hold no user. The endpoints that
read it are superuser-only, and FastAPI resolves that check before the
endpoint body runs, so a warm entry never answers anyone the check would have
refused. A figure that depends on who asks must put the principal in its key
or stay out of here.

Per process: each uvicorn worker keeps its own. Concurrent misses on one key
share a single computation, which runs as its own task so a caller that
disconnects does not cancel it for the others. Errors are not kept.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any, TypeVar

from starlette.requests import Request

T = TypeVar("T")

_DEFAULT_MAX_ENTRIES = 256


class FactCache:
    """TTL + LRU cache with single-flight computation per key."""

    def __init__(
        self,
        *,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Task[Any]] = {}
        self._max_entries = max_entries
        self._clock = clock

    async def get_or_compute(
        self,
        key: Hashable,
        ttl: float,
        compute: Callable[[], Awaitable[T]],
    ) -> T:
        """Return the fresh value under *key*, computing it once if there is none.

        *compute* must not use anything tied to the calling request (its DB
        session, say): it may outlive that request and serve another.
        """
        entry = self._entries.get(key)
        if entry is not None and entry[0] > self._clock():
            self._entries.move_to_end(key)
            return entry[1]  # type: ignore[no-any-return]

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._run(key, ttl, compute))
            task.add_done_callback(_retrieve_exception)
            self._inflight[key] = task
        return await asyncio.shield(task)  # type: ignore[no-any-return]

    async def _run(self, key: Hashable, ttl: float, compute: Callable[[], Awaitable[T]]) -> T:
        try:
            value = await compute()
        finally:
            self._inflight.pop(key, None)
        self._entries[key] = (self._clock() + ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return value

    def clear(self) -> None:
        """Forget every entry (tests, or after a change the figures must show)."""
        self._entries.clear()


def _retrieve_exception(task: asyncio.Task[Any]) -> None:
    # Every caller may have gone before the task failed; reading the exception
    # here keeps asyncio from logging "Task exception was never retrieved".
    if not task.cancelled():
        task.exception()


def get_fact_cache(request: Request) -> FactCache:
    """The app's dashboard cache, created on first use.

    It lives on ``app.state`` rather than in a module global, so every app --
    each test builds its own -- starts empty.
    """
    cache: FactCache | None = getattr(request.app.state, "dashboard_fact_cache", None)
    if cache is None:
        cache = FactCache()
        request.app.state.dashboard_fact_cache = cache
    return cache
