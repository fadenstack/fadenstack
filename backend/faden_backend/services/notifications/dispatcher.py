"""NotificationDispatcher — polls the outbox and hands each message to the registered mail sender."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.services.extensions import mail_available, registry
from faden_backend.services.notifications.retention import prune_expired_notifications

log = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 10
_MAX_ATTEMPTS = 5


class NotificationDispatcher:
    """Background worker that drains the notification outbox.

    Each poll first deletes what expired before it was sent, whether or not mail can go out; then, while a
    sender is registered and available (``registry.mail_sender``; core has none), it delivers the
    ``status='pending'`` rows whose ``next_attempt_at`` is past. Failures are retried with exponential
    back-off up to ``_MAX_ATTEMPTS``, never past the message's expiry.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Launch the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.get_event_loop().create_task(self._run(), name="notification-dispatcher")

    async def stop(self) -> None:
        """Gracefully cancel the polling task."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Notification dispatcher poll error.")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _poll_once(self) -> None:
        now = datetime.now(tz=UTC)
        async with self._session_factory() as session:
            dropped = await prune_expired_notifications(session, now)
        if dropped:
            log.info("Deleted %d notifications that expired before they were sent.", dropped)
        if not mail_available():
            return

        async with self._session_factory() as session:
            result = await session.execute(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.status == "pending",
                    NotificationOutbox.next_attempt_at <= now,
                    NotificationOutbox.expires_at > now,
                )
                .order_by(NotificationOutbox.created_at)
                .limit(20),
            )
            rows = result.scalars().all()

        for row in rows:
            await self._deliver(row)

    async def _deliver(self, row: NotificationOutbox) -> None:
        sender = registry.mail_sender
        if sender is None:
            return
        try:
            await sender.send(row.kind, row.payload_json)

            async with self._session_factory() as session:
                await session.execute(
                    update(NotificationOutbox)
                    .where(NotificationOutbox.id == row.id)
                    .values(
                        status="sent",
                        sent_at=datetime.now(tz=UTC),
                    ),
                )
                await session.commit()
        except Exception as exc:
            log.warning("Delivery failed for %s (%s): %s", row.id, row.kind, exc)
            new_count = row.attempt_count + 1
            if new_count >= _MAX_ATTEMPTS:
                new_status = "failed"
                next_at = datetime.now(tz=UTC)
            else:
                new_status = "pending"
                next_at = datetime.now(tz=UTC) + timedelta(seconds=30 * (2 ** new_count))

            async with self._session_factory() as session:
                await session.execute(
                    update(NotificationOutbox)
                    .where(NotificationOutbox.id == row.id)
                    .values(
                        attempt_count=new_count,
                        status=new_status,
                        last_error=str(exc)[:2000],
                        next_attempt_at=next_at,
                    ),
                )
                await session.commit()
