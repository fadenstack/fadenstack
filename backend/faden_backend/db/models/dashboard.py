"""What the dashboard keeps beyond each user's layout: problems someone has muted."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base


class AttentionAcknowledgement(Base):
    """A Needs attention item an administrator has seen and muted, for every administrator.

    It lasts while the problem does: once the item clears it is dropped, so
    the same problem coming back is shown again; and an item that gets worse
    than it was when muted is shown again too (services/dashboard/attention.py).
    """

    __tablename__ = "dashboard_attention_acks"

    #: The item's id from the attention rules (``deployment_failed:<uuid>``, ``disk_low``).
    item_id: Mapped[str] = mapped_column(sa.String(255), primary_key=True)
    #: Its severity when muted.
    severity: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    acknowledged_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
