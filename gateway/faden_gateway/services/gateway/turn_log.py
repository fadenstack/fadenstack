"""A chat reply's events where every gateway worker can read them: a Redis stream.

The worker writing a reply serves its own page from memory (``StreamBuffer``).
A page that comes back may reach another worker -- the gateway runs several --
which reads the reply from here; a stop asked of any worker reaches the one
writing it the same way (a key it looks at with each batch of events).

The events go in batches (``StreamBuffer``'s pump, every 100 ms), not one
per token. Redis runs with ``noeviction``, so every key here has a TTL.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from redis.asyncio import ConnectionPool, Redis

log = logging.getLogger(__name__)

#: How long a finished reply's events are kept, for a page that comes back late.
KEEP_SEC = 900
#: How long a stop request waits for the worker writing the reply.
STOP_SEC = 600
#: At most this many batches are kept per reply (about 30 minutes of them).
MAX_BATCHES = 20_000
#: How long a reader waits for the next batch before it asks whether the reply is still written.
_BLOCK_MS = 5_000


def _events(turn_id: str) -> str:
    return f"faden:turn:{turn_id}:events"


def _stop(turn_id: str) -> str:
    return f"faden:turn:{turn_id}:stop"


class TurnLog:
    """The replies' events and stop requests, in Redis."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def of(cls, cache: Any) -> TurnLog | None:
        """The log for the gateway's cache, when that is Redis; ``None`` without Redis."""
        pool = getattr(cache, "_pool", None)
        return cls(pool) if isinstance(pool, ConnectionPool) else None

    async def begin(self, turn_id: str) -> None:
        """Open the reply's events, so a reader finds them before the first batch."""
        async with Redis(connection_pool=self._pool) as redis:
            pipe = redis.pipeline(transaction=False)
            pipe.xadd(_events(turn_id), {"begin": b"1"}, maxlen=MAX_BATCHES, approximate=True)
            pipe.expire(_events(turn_id), KEEP_SEC)
            await pipe.execute()

    async def append(self, turn_id: str, data: bytes) -> bool:
        """Add *data* (whole SSE events) to the reply; whether a stop was asked for, in the same trip."""
        async with Redis(connection_pool=self._pool) as redis:
            pipe = redis.pipeline(transaction=False)
            if data:
                pipe.xadd(_events(turn_id), {"d": data}, maxlen=MAX_BATCHES, approximate=True)
                # Until the end sets the real one: a writer that dies leaves nothing for good.
                pipe.expire(_events(turn_id), KEEP_SEC)
            pipe.exists(_stop(turn_id))
            answers = await pipe.execute()
        return bool(answers[-1])

    async def end(self, turn_id: str) -> None:
        """Mark the reply's end, for its readers; its events stay a while."""
        async with Redis(connection_pool=self._pool) as redis:
            pipe = redis.pipeline(transaction=False)
            pipe.xadd(_events(turn_id), {"end": b"1"}, maxlen=MAX_BATCHES, approximate=True)
            pipe.expire(_events(turn_id), KEEP_SEC)
            pipe.delete(_stop(turn_id))
            await pipe.execute()

    async def request_stop(self, turn_id: str) -> None:
        """Ask whichever worker writes the reply to stop it."""
        async with Redis(connection_pool=self._pool) as redis:
            await redis.set(_stop(turn_id), b"1", ex=STOP_SEC)

    async def exists(self, turn_id: str) -> bool:
        """Whether the reply's events are here."""
        async with Redis(connection_pool=self._pool) as redis:
            return bool(await redis.exists(_events(turn_id)))

    async def read(
        self, turn_id: str, *, still_written: Callable[[], Awaitable[bool]],
    ) -> AsyncIterator[bytes]:
        """The reply from its start, then as it is written, until its end.

        When nothing comes for a while, *still_written* is asked: a reply
        whose writer went away ends there rather than holding the reader.
        """
        last = "0-0"
        async with Redis(connection_pool=self._pool) as redis:
            while True:
                found = await redis.xread({_events(turn_id): last}, block=_BLOCK_MS, count=500)
                if not found:
                    if not await still_written():
                        return
                    continue
                for entry_id, fields in found[0][1]:
                    last = entry_id
                    if b"end" in fields:
                        return
                    data = fields.get(b"d")
                    if data:
                        yield data
