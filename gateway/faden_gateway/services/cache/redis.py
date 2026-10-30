"""RedisCache — production cache adapter backed by redis.asyncio."""

from __future__ import annotations

import logging
from typing import Any, Sequence, cast

from redis.asyncio import ConnectionPool, Redis

log = logging.getLogger(__name__)


class RedisCache:
    """Cache adapter backed by a ``redis.asyncio.ConnectionPool``."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        log.info("CacheBackend initialised as RedisCache.")

    def client(self) -> Redis:
        """A client on the pool, for what the cache ops do not cover (pub/sub)."""
        return Redis(connection_pool=self._pool)

    # ── simple ops ────────────────────────────────────────────

    async def get(self, key: str) -> bytes | None:
        async with Redis(connection_pool=self._pool) as redis:
            return await redis.get(key)

    async def set(
        self,
        key: str,
        value: bytes | str,
        *,
        ttl_sec: int = 0,
    ) -> None:
        async with Redis(connection_pool=self._pool) as redis:
            if ttl_sec > 0:
                await redis.set(key, value, ex=ttl_sec)
            else:
                await redis.set(key, value)

    async def incr(
        self,
        key: str,
        amount: int = 1,
        *,
        ttl_sec: int = 0,
    ) -> int:
        async with Redis(connection_pool=self._pool) as redis:
            current = int(await redis.incrby(key, amount))
            if current == amount and ttl_sec > 0:
                await redis.expire(key, ttl_sec)
            return current

    async def mget(self, keys: Sequence[str]) -> list[bytes | None]:
        async with Redis(connection_pool=self._pool) as redis:
            return await redis.mget(keys)  # type: ignore[return-value]

    # ── Lua scripting (used by LeaseManager) ──────────────────

    async def eval_lua(
        self,
        script: str,
        num_keys: int,
        *args: str,
    ) -> str:
        async with Redis(connection_pool=self._pool) as redis:
            result = await cast(Any, redis.eval(script, num_keys, *args))
            # A script's string reply comes as bytes: its text, not "b'...'" (the caps' script answers so).
            return result.decode() if isinstance(result, bytes) else str(result)

    # ── lifecycle ─────────────────────────────────────────────

    async def close(self) -> None:
        await self._pool.disconnect()
        log.info("RedisCache connection pool closed.")
