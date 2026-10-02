"""In-process stream buffer for SSE reconnection.

Buffers SSE chunks keyed by session ID so that a client that
reloads mid-stream can reconnect and receive the full response.

Each active stream has:
  - a list of accumulated SSE byte chunks
  - an asyncio.Event that fires on each new chunk
  - a ``done`` flag + cleanup timer

Entries auto-expire after ``ttl_sec`` seconds once the stream ends.

A chat page's reply (one with a turn) is also sent, in batches, to a log
every gateway worker reads (``TurnLog``, Redis): a page that comes back to
another worker reads it there, and a stop asked of another worker comes back
with the next batch.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from faden_gateway.services.gateway.turn_log import TurnLog

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SEC = 120  # keep finished buffers for 2 minutes
#: How often a reply's new events go to the shared log (and a stop is looked for).
_PUMP_SEC = 0.1


@dataclass
class _StreamEntry:
    """Internal bookkeeping for a single session stream."""

    chunks: list[bytes] = field(default_factory=list)
    new_chunk_event: asyncio.Event = field(default_factory=asyncio.Event)
    done: bool = False
    finished_at: float | None = None
    #: The request's trace id, for a reconnecting client's debug panel.
    trace_id: str | None = None
    #: The reply's row in the chat (its turn), when there is one.
    turn_id: str | None = None
    #: Whether the user asked for the reply to stop.
    stop_requested: bool = False
    #: The reading of the model's stream: what a stop cancels.
    reading: asyncio.Future[None] | None = None
    #: Events not yet in the shared log.
    pending: list[bytes] = field(default_factory=list)
    #: The task that sends them there.
    pump: asyncio.Task[None] | None = None


class StreamBuffer:
    """Process-local SSE stream buffer.

    Thread-safety: designed for single-process ``asyncio`` servers
    (the standard FastAPI deployment model).  For multi-process
    setups, this would need a Redis Streams backend — left as a
    future enhancement.
    """

    def __init__(self, ttl_sec: int = _DEFAULT_TTL_SEC, log: TurnLog | None = None) -> None:
        self._buffers: dict[str, _StreamEntry] = {}
        self._ttl_sec = ttl_sec
        self._cleanup_task: asyncio.Task[None] | None = None
        #: The log every worker reads replies from; ``None`` without Redis.
        self.log = log

    # ── producer API ──────────────────────────────────────────

    def start(self, session_id: str, trace_id: str | None = None, turn_id: str | None = None) -> None:
        """Register a new active stream for *session_id* (the reply *turn_id*, when it has a row)."""
        old = self._buffers.get(session_id)
        if old and not old.done:
            logger.warning("Overwriting active stream buffer for session %s", session_id)
        entry = self._buffers[session_id] = _StreamEntry(trace_id=trace_id, turn_id=turn_id)
        if self.log is not None and turn_id:
            entry.pump = asyncio.create_task(self._pump(entry, turn_id))
        self._ensure_cleanup()
        logger.debug("Stream buffer started for session %s", session_id)

    def reading(self, session_id: str, reading: asyncio.Future[None]) -> None:
        """Note the task reading *session_id*'s reply from the model: a stop cancels it."""
        entry = self._buffers.get(session_id)
        if entry is not None:
            entry.reading = reading

    def writes(self, session_id: str, turn_id: str) -> bool:
        """Whether this process is writing the reply *turn_id* of *session_id*."""
        entry = self._buffers.get(session_id)
        return entry is not None and not entry.done and entry.turn_id == turn_id

    async def request_stop(self, session_id: str, turn_id: str) -> bool:
        """Stop the reply *turn_id* of *session_id*: here, or through the log where another worker writes it.

        Whether a stop was asked for (not whether the reply was running).
        """
        entry = self._buffers.get(session_id)
        if entry is not None and not entry.done and entry.turn_id == turn_id:
            self._stop(entry)
            return True
        if self.log is None:
            return False
        await self.log.request_stop(turn_id)
        return True

    @staticmethod
    def _stop(entry: _StreamEntry) -> None:
        entry.stop_requested = True
        if entry.reading is not None:
            entry.reading.cancel()

    def stop_requested(self, session_id: str) -> bool:
        """Whether the user asked for *session_id*'s reply to stop."""
        entry = self._buffers.get(session_id)
        return entry is not None and entry.stop_requested

    def push(self, session_id: str, chunk: bytes) -> None:
        """Append an SSE chunk to the buffer and notify subscribers."""
        entry = self._buffers.get(session_id)
        if entry is None or entry.done:
            return
        entry.chunks.append(chunk)
        if entry.pump is not None:
            entry.pending.append(chunk)
        # Wake up all subscribers waiting for new data
        entry.new_chunk_event.set()
        entry.new_chunk_event = asyncio.Event()

    def finish(self, session_id: str) -> None:
        """Mark the stream as complete."""
        entry = self._buffers.get(session_id)
        if entry is None:
            return
        entry.done = True
        entry.finished_at = time.monotonic()
        # Final wake-up so subscribers exit their loop
        entry.new_chunk_event.set()
        logger.debug("Stream buffer finished for session %s", session_id)

    # ── consumer API ──────────────────────────────────────────

    def is_active(self, session_id: str) -> bool:
        """Return True if there is an active (not finished) stream."""
        entry = self._buffers.get(session_id)
        return entry is not None and not entry.done

    def has_buffer(self, session_id: str) -> bool:
        """Return True if there is any buffer (active or recently finished)."""
        return session_id in self._buffers

    def turn_of(self, session_id: str) -> str | None:
        """The reply (turn) buffered for *session_id*, when it has one."""
        entry = self._buffers.get(session_id)
        return entry.turn_id if entry else None

    def trace_id(self, session_id: str) -> str | None:
        """The trace id of the stream buffered for *session_id*, if known."""
        entry = self._buffers.get(session_id)
        return entry.trace_id if entry else None

    async def subscribe(self, session_id: str) -> AsyncSSEIterator:
        """Return an async iterator that yields buffered + live SSE chunks.

        If the stream is still active, the iterator will block waiting
        for new chunks until the stream finishes.  If already finished,
        it replays all buffered chunks and returns.
        """
        return AsyncSSEIterator(self, session_id)

    # ── the shared log ────────────────────────────────────────

    async def _pump(self, entry: _StreamEntry, turn_id: str) -> None:
        """Send a reply's new events to the shared log every 100 ms, and heed a stop asked of another worker.

        A log that cannot be reached costs only the other workers' view: the
        reply and this worker's readers go on.
        """
        assert self.log is not None  # noqa: S101
        warned = False
        try:
            await self.log.begin(turn_id)
        except Exception:
            logger.warning("A chat reply's events did not reach the shared log", exc_info=True)
            warned = True
        while True:
            done = entry.done
            data = b"".join(entry.pending)
            entry.pending.clear()
            try:
                stop = await self.log.append(turn_id, data)
                if done and not entry.pending:
                    await self.log.end(turn_id)
                    return
            except Exception:
                if not warned:
                    logger.warning("A chat reply's events did not reach the shared log", exc_info=True)
                    warned = True
                if done:
                    return
                stop = False
            if stop and not entry.done and not entry.stop_requested:
                self._stop(entry)
            if not done:
                await asyncio.sleep(_PUMP_SEC)

    # ── housekeeping ──────────────────────────────────────────

    def _ensure_cleanup(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        """Periodically remove expired finished buffers."""
        while self._buffers:
            await asyncio.sleep(30)
            now = time.monotonic()
            expired = [
                sid
                for sid, e in self._buffers.items()
                if e.done and e.finished_at and (now - e.finished_at) > self._ttl_sec
            ]
            for sid in expired:
                del self._buffers[sid]
                logger.debug("Cleaned up expired stream buffer for session %s", sid)
            if not self._buffers:
                break


class AsyncSSEIterator:
    """Async iterator that replays buffered SSE chunks then tails live ones."""

    def __init__(self, buffer: StreamBuffer, session_id: str) -> None:
        self._buffer = buffer
        self._session_id = session_id
        self._cursor = 0

    def __aiter__(self) -> AsyncSSEIterator:
        return self

    async def __anext__(self) -> bytes:
        entry = self._buffer._buffers.get(self._session_id)
        if entry is None:
            raise StopAsyncIteration

        # If there are buffered chunks we haven't sent yet, yield them
        while self._cursor >= len(entry.chunks):
            if entry.done:
                raise StopAsyncIteration
            # Wait for the next chunk to arrive
            event = entry.new_chunk_event
            await event.wait()
            # Re-fetch entry in case it was replaced
            entry = self._buffer._buffers.get(self._session_id)
            if entry is None:
                raise StopAsyncIteration

        chunk = entry.chunks[self._cursor]
        self._cursor += 1
        return chunk
