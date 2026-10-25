"""How long the outbox keeps a message: it holds an e-mail address, and a reset mail's row its live link.

- Sent: a week (for "was it sent?" questions), then deleted.
- Not sent by its ``expires_at``: deleted then, never sent late (a reset token or a code that no longer works,
  an alert that is stale).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.notifications import NotificationOutbox

#: How long a sent notification is kept (for "was it sent?" questions), then deleted.
SENT_RETENTION = timedelta(days=7)


async def prune_sent_notifications(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete notifications sent longer ago than :data:`SENT_RETENTION`; commits; returns how many."""
    before = (now or datetime.now(UTC)) - SENT_RETENTION
    result = await session.execute(
        delete(NotificationOutbox).where(NotificationOutbox.status == "sent", NotificationOutbox.sent_at < before),
    )
    await session.commit()
    return result.rowcount or 0


async def prune_expired_notifications(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete notifications that expired before they were sent; commits; returns how many."""
    result = await session.execute(
        delete(NotificationOutbox).where(
            NotificationOutbox.status != "sent", NotificationOutbox.expires_at <= (now or datetime.now(UTC)),
        ),
    )
    await session.commit()
    return result.rowcount or 0
