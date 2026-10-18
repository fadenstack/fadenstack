"""Sent notifications are deleted after a week: a password-reset mail's row holds its live link."""

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
