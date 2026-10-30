"""NotificationDispatcher — polls the outbox and hands each message to the registered mail sender."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.services.extensions import mail_available, registry
from faden_backend.services.notifications.retention import prune_expired_notifications

log = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 10
_MAX_ATTEMPTS = 5
#: How many messages one poll delivers at most.
_BATCH = 20
#: How long a claimed message is this worker's. A delivery that takes longer is taken as lost (the worker
#: stopped while sending), and the message is claimed again.
LEASE = timedelta(minutes=10)

#: Waiting to go out, and being delivered by a worker (whose lease is in ``next_attempt_at``).
PENDING, SENDING = "pending", "sending"


@dataclass(frozen=True)
class _Claim:
    """A message this worker holds until ``lease_until``; the final update needs both to still match."""

    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempt: int
    lease_until: datetime


def is_permanent(exc: BaseException) -> bool:
    """Whether a sender said the failure is final: trying again would fail the same way."""
    return getattr(exc, "permanent", False) is True


class NotificationDispatcher:
    """Background worker that drains the notification outbox.

    Each poll first deletes what expired before it was sent, whether or not mail can go out; then, while a
    sender is registered and available (``registry.mail_sender``; core has none), it delivers the due
    messages one by one. Every backend worker runs a dispatcher: a message is claimed before it is sent
    (``SELECT ... FOR UPDATE SKIP LOCKED``, then status ``sending`` with a lease in ``next_attempt_at``), so
    only one worker sends it; a worker that stops while sending leaves the message to be claimed again once
    the lease is over. Failures are retried with exponential back-off up to ``_MAX_ATTEMPTS``, never past
    the message's expiry; a failure the sender marks ``permanent`` is final at once. Every attempt hands the
    sender the message's key, its id, the same on each attempt, so a sender can send a message once even when
    an attempt is repeated.
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
        async with self._session_factory() as session:
            dropped = await prune_expired_notifications(session, datetime.now(tz=UTC))
        if dropped:
            log.info("Deleted %d notifications that expired before they were sent.", dropped)
        if not mail_available():
            return

        for _ in range(_BATCH):
            if registry.mail_sender is None:
                return
            claim = await self._claim()
            if claim is None:
                return
            await self._deliver(claim)

    async def _claim(self) -> _Claim | None:
        """The oldest due message, now this worker's until its lease ends; ``None`` when none is due.

        Due: waiting and its next attempt has come, or claimed by a worker whose lease is over. A row another
        worker is claiming at this moment is skipped, not waited for. The claim counts as an attempt.
        """
        now = datetime.now(tz=UTC)
        async with self._session_factory() as session:
            row_id = (
                await session.execute(
                    select(NotificationOutbox.id)
                    .where(
                        NotificationOutbox.status.in_((PENDING, SENDING)),
                        NotificationOutbox.next_attempt_at <= now,
                        NotificationOutbox.expires_at > now,
                    )
                    .order_by(NotificationOutbox.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True),
                )
            ).scalar_one_or_none()
            if row_id is None:
                await session.commit()
                return None
            claimed = (
                await session.execute(
                    update(NotificationOutbox)
                    .where(NotificationOutbox.id == row_id)
                    .values(
                        status=SENDING,
                        attempt_count=NotificationOutbox.attempt_count + 1,
                        next_attempt_at=now + LEASE,
                    )
                    .returning(
                        NotificationOutbox.kind,
                        NotificationOutbox.payload_json,
                        NotificationOutbox.attempt_count,
                        NotificationOutbox.next_attempt_at,
                    ),
                )
            ).one()
            await session.commit()
        return _Claim(
            id=row_id,
            kind=claimed.kind,
            payload=claimed.payload_json,
            attempt=claimed.attempt_count,
            lease_until=claimed.next_attempt_at,
        )

    async def _deliver(self, claim: _Claim) -> None:
        sender = registry.mail_sender
        if sender is None:
            # Gone since the claim: back to waiting, this attempt not counted.
            now = datetime.now(tz=UTC)
            await self._finish(claim, status=PENDING, attempt_count=claim.attempt - 1, next_attempt_at=now)
            return
        try:
            await sender.send(claim.kind, claim.payload, key=str(claim.id))
        except Exception as exc:
            log.warning("Delivery failed for %s (%s): %s", claim.id, claim.kind, exc)
            now = datetime.now(tz=UTC)
            if is_permanent(exc) or claim.attempt >= _MAX_ATTEMPTS:
                status, next_at = "failed", now
            else:
                status, next_at = PENDING, now + timedelta(seconds=30 * (2**claim.attempt))
            await self._finish(claim, status=status, last_error=str(exc)[:2000], next_attempt_at=next_at)
            return
        await self._finish(claim, status="sent", sent_at=datetime.now(tz=UTC))

    async def _finish(self, claim: _Claim, **values: Any) -> None:
        """Write the outcome, only while the claim still holds (a worker past its lease changes nothing)."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(NotificationOutbox)
                .where(
                    NotificationOutbox.id == claim.id,
                    NotificationOutbox.status == SENDING,
                    NotificationOutbox.next_attempt_at == claim.lease_until,
                )
                .values(**values),
            )
            await session.commit()
        if not result.rowcount:  # type: ignore[attr-defined]
            log.warning("The claim on %s (%s) ended before its outcome was written.", claim.id, claim.kind)
