"""Notification outbox and alert deduplication models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base

#: How long a message without an expiry of its own is of use (an administrators' alert).
DEFAULT_TTL = timedelta(days=1)


class NotificationOutbox(Base):
    """Reliable outbox for deferred notifications.

    Each message expires with what it carries (a reset token, a code). One not sent by then is deleted, never
    sent late: it holds an e-mail address, kept no longer than it is of use (GDPR Art. 5(1)(c, e)). Sent
    ones go after a week (``services/notifications/retention.py``).
    """

    __tablename__ = "notification_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, default=lambda: datetime.now(UTC) + DEFAULT_TTL,
    )


class NotificationAlertState(Base):
    """State table for alert deduplication/cooldown."""

    __tablename__ = "notification_alert_state"

    fingerprint: Mapped[str] = mapped_column(String(256), primary_key=True)
    last_sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

