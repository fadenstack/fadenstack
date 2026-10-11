"""What the update check last found: the release feed as published, and how the last attempt went."""

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base


class ReleaseFeedState(Base):
    """The one row (``id`` 1) the update check keeps (services/updates/check.py).

    The feed is kept as published, signature and all, and verified again
    whenever it is read: a row edited in the database is not believed.
    """

    __tablename__ = "release_feed_state"

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, default=1)
    #: ``index.json`` and ``index.json.sig`` as they were fetched or uploaded.
    index_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    signature_text: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: The feed's own date: a feed older than this one is refused.
    generated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    #: url (read by the check) or upload (an administrator's copy, for a site without internet).
    source: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    url: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    etag: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    #: The last attempt, and the last that worked; ``error`` says why the last one failed.
    checked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    succeeded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    #: Releases the administrators were already sent an email about.
    notified_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
