"""A chat reply's events where every gateway worker can read them: a Redis stream.

The worker writing a reply serves its own page from memory (``StreamBuffer``).
A page that comes back may reach another worker -- the gateway runs several --
which reads the reply from here; a stop asked of any worker reaches the one
writing it the same way (a key it looks at with each batch of events).

The events go in batches (``StreamBuffer``'s pump, every 100 ms), not one
per token. Redis runs with ``noeviction``, so every key here has a TTL.

The events are chat text, and this Redis snapshots to disk: with encryption
on they are encrypted with the key of the chat's messages ("chat-content"),
and a finished reply's are kept only a minute -- a page that comes back later
reads it from the database. Kept 15 minutes in plain text, they came to 110 MB
after 9,500 turns on .61 (2026-10-02), while the same replies were encrypted
in Postgres.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from cryptography.fernet import InvalidToken
from redis.asyncio import ConnectionPool, Redis

from faden_gateway.db.crypto import seal, unseal

log = logging.getLogger(__name__)

#: How long a reply's events are kept while it is written, from its last batch:
#: longer than the longest a reply may take to start (a deployment's
#: first_token_timeout_sec, up to 1800 s), so a slow start keeps them.
LIVE_SEC = 3600
#: How long a finished reply's events are kept: for a page reading it as it
#: ended to catch up. A page that comes back later reads the database.
DONE_SEC = 60
#: The key the events are encrypted with: the one the chat's messages use.
_PURPOSE = "chat-content"
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
            pipe.expire(_events(turn_id), LIVE_SEC)
            await pipe.execute()

    async def append(self, turn_id: str, data: bytes) -> bool:
        """Add *data* (whole SSE events) to the reply; whether a stop was asked for, in the same trip."""
        async with Redis(connection_pool=self._pool) as redis:
            pipe = redis.pipeline(transaction=False)
            if data:
                # "x" encrypted, "d" plain: encryption is off, or was when it was written.
                sealed = seal(_PURPOSE, data)
                fields = {"x": sealed} if sealed is not None else {"d": data}
                pipe.xadd(_events(turn_id), fields, maxlen=MAX_BATCHES, approximate=True)
                # Until the end sets the real one: a writer that dies leaves nothing for good.
                pipe.expire(_events(turn_id), LIVE_SEC)
            pipe.exists(_stop(turn_id))
            answers = await pipe.execute()
        return bool(answers[-1])

    async def end(self, turn_id: str) -> None:
        """Mark the reply's end, for its readers; its events stay a minute."""
        async with Redis(connection_pool=self._pool) as redis:
            pipe = redis.pipeline(transaction=False)
            pipe.xadd(_events(turn_id), {"end": b"1"}, maxlen=MAX_BATCHES, approximate=True)
            pipe.expire(_events(turn_id), DONE_SEC)
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
                    sealed = fields.get(b"x")
                    if sealed:
                        try:
                            data = unseal(_PURPOSE, sealed)
                        except InvalidToken:
                            # Another key: the page reads the reply from the database instead.
                            log.error("A chat reply's events could not be decrypted (turn %s)", turn_id)
                            return
                    if data:
                        yield data
