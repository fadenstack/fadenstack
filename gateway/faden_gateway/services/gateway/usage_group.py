"""The group a user's LLM usage is attributed to.

The backend stores it on the user (``user.usage_group_id``); the gateway stamps
it on every request-log row so usage can be reported per team without joining
across databases. Resolving it must cost the request nothing: answers are
cached in-process for a few minutes, a miss is one indexed query on the
backend database over a small connection pool, concurrent misses for one user
share a single query, and any failure resolves to ``None`` (unattributed)
rather than failing the request.

The same cache answers which groups a user is in, for the skills a chat may
use: a skill shared with a group reaches its members (``memberships``).

With the team comes its path, as it is at the time of use: its ancestors from
the root down to the team itself. Usage is booked on it (the request log, the
rollups), so a report shows usage as it was booked after a reorganisation, as
Google Cloud's billing export records project ancestry at the time of usage.
Teams form a tree once the backend has ``groups.parent_id``; until then a
team's path is the team alone.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import asyncpg

from faden_gateway.services.gateway import access_epoch

logger = logging.getLogger(__name__)

V = TypeVar("V")

Fetch = Callable[[str], Awaitable[Any]]
Close = Callable[[], Awaitable[None]]

_SQL = 'SELECT usage_group_id::text FROM "user" WHERE id = $1::uuid'
_MEMBERSHIPS_SQL = "SELECT group_id::text FROM user_groups WHERE user_id = $1::uuid ORDER BY 1"
#: Whether teams form a tree yet (the backend adds ``groups.parent_id``).
_TREE_SQL = (
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = current_schema() AND table_name = 'groups' AND column_name = 'parent_id')"
)
#: The most levels a team's path holds, the team itself included.
PATH_LEVELS = 6
#: A team's path, root first: up its parents, at most ``PATH_LEVELS`` levels, stopping at a cycle (a team
#: met twice). A team that is gone has no row: its path is the team alone. $2: ``PATH_LEVELS``.
_PATH_SQL = """
WITH RECURSIVE up(id, parent_id, depth, path) AS (
    SELECT g.id, g.parent_id, 1, ARRAY[g.id] FROM groups g WHERE g.id = $1::uuid
    UNION ALL
    SELECT p.id, p.parent_id, up.depth + 1, ARRAY[p.id] || up.path
    FROM up JOIN groups p ON p.id = up.parent_id
    WHERE up.depth < $2::int AND p.id <> ALL(up.path)
)
SELECT path::text[] FROM up ORDER BY depth DESC LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class UsageTeam:
    """The team a person's usage is booked on, and its path from the root down to it."""

    group_id: str
    path: tuple[str, ...]


def usage_team(value: Any) -> UsageTeam | None:
    """*value* as a ``UsageTeam``: a resolver may also answer with the team's id alone."""
    if isinstance(value, UsageTeam):
        return value
    if isinstance(value, str) and value:
        return UsageTeam(group_id=value, path=(value,))
    return None


class UsageGroupResolver(Generic[V]):
    """``user_id -> value`` with a TTL cache in front of ``fetch``.

    The value is the usage group id, or (``memberships``) the user's groups.
    """

    def __init__(
        self,
        fetch: Fetch,
        *,
        ttl_sec: float = 300.0,
        error_ttl_sec: float = 15.0,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
        close: Close | None = None,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_sec
        self._error_ttl = error_ttl_sec
        self._max = max_entries
        self._clock = clock
        self._close = close
        self._cache: dict[str, tuple[V | None, float, int | None]] = {}
        self._inflight: dict[tuple[str, int | None], asyncio.Future[V | None]] = {}

    async def resolve(self, user_id: str) -> V | None:
        """The user's value, or ``None`` when unattributed, unknown or failed.

        A team joined or left is seen at the next request: an answer read before the count of changes to
        access last moved is read again (``access_epoch``).
        """
        now = self._clock()
        hit = self._cache.get(user_id)
        if hit is not None and hit[1] > now and access_epoch.usable(hit[2]):
            return hit[0]

        epoch = access_epoch.current()
        pending = self._inflight.get((user_id, epoch))
        if pending is not None:
            return await pending

        fut: asyncio.Future[V | None] = asyncio.get_running_loop().create_future()
        self._inflight[(user_id, epoch)] = fut
        try:
            try:
                value = await self._fetch(user_id)
                ttl = self._ttl
            except Exception as exc:  # attribution never fails the request
                logger.warning("Usage group lookup failed for user %s: %s", user_id, exc)
                value, ttl = None, self._error_ttl
            self._remember(user_id, value, now + ttl, epoch)
            fut.set_result(value)
            return value
        finally:
            self._inflight.pop((user_id, epoch), None)

    def invalidate(self, user_id: str | None = None) -> None:
        """Forget one user, or everyone."""
        if user_id is None:
            self._cache.clear()
        else:
            self._cache.pop(user_id, None)

    async def close(self) -> None:
        if self._close is not None:
            await self._close()

    def _remember(self, user_id: str, value: V | None, expires_at: float, epoch: int | None) -> None:
        kept = self._cache.get(user_id)
        if kept is not None and epoch is not None and kept[2] is not None and kept[2] > epoch:
            return  # a newer read is kept already
        if len(self._cache) >= self._max and user_id not in self._cache:
            now = self._clock()
            for key in [k for k, (_, exp, _) in self._cache.items() if exp <= now]:
                del self._cache[key]
            if len(self._cache) >= self._max:
                # Still full: drop the entry that expires soonest.
                del self._cache[min(self._cache, key=lambda k: self._cache[k][1])]
        self._cache[user_id] = (value, expires_at, epoch)


class BackendUserGroups:
    """``fetch`` over a small asyncpg pool on the backend database."""

    def __init__(self, dsn: str, *, max_size: int = 2, timeout_sec: float = 2.0) -> None:
        self._dsn = dsn
        self._max_size = max_size
        self._timeout = timeout_sec
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()
        #: Whether ``groups.parent_id`` exists: looked up once, on the first use.
        self._tree: bool | None = None

    async def __call__(self, user_id: str) -> UsageTeam | None:
        """The user's usage team and its path; ``None`` without one."""
        pool = await self._get_pool()
        group_id = await pool.fetchval(_SQL, user_id, timeout=self._timeout)
        if not group_id:
            return None
        if self._tree is None:
            self._tree = bool(await pool.fetchval(_TREE_SQL, timeout=self._timeout))
        path = None
        if self._tree:
            path = await pool.fetchval(_PATH_SQL, group_id, PATH_LEVELS, timeout=self._timeout)
        return UsageTeam(group_id=group_id, path=tuple(path) if path else (group_id,))

    async def memberships(self, user_id: str) -> tuple[str, ...]:
        """The ids of the groups *user_id* is in."""
        pool = await self._get_pool()
        rows = await pool.fetch(_MEMBERSHIPS_SQL, user_id, timeout=self._timeout)
        return tuple(row[0] for row in rows)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        self._dsn,
                        min_size=0,
                        max_size=self._max_size,
                        timeout=self._timeout,
                    )
        return self._pool


def build_user_resolvers(
    settings: object,
) -> tuple[UsageGroupResolver[UsageTeam] | None, UsageGroupResolver[tuple[str, ...]] | None]:
    """``(usage team, group memberships)`` resolvers over the backend database.

    Both ``None`` when no backend database is configured. They share one small
    pool; closing the first closes it.
    """
    backend_db = getattr(settings, "backend_db_base", "")
    if not backend_db:
        return None, None
    dsn = (
        f"postgresql://{settings.db_user}:{settings.db_pass}"  # type: ignore[attr-defined]
        f"@{settings.db_host}:{settings.db_port}/{backend_db}"  # type: ignore[attr-defined]
    )
    fetch = BackendUserGroups(dsn)
    usage: UsageGroupResolver[UsageTeam] = UsageGroupResolver(fetch, close=fetch.close)
    # A minute, not five: being taken out of a group takes away the skills
    # shared with it, and that should not wait long.
    groups: UsageGroupResolver[tuple[str, ...]] = UsageGroupResolver(
        fetch.memberships,
        ttl_sec=60.0,
    )
    return usage, groups
