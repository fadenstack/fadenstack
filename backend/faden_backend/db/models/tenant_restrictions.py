"""Restrictions an extension keeps on a whole tenant (Enterprise: tool mode ceilings).

A row says that an extension restricts the tenant under *name*. While nothing installed enforces it, core
fails closed in the way it knows for that name (``services/tenant_restrictions.py``), never open.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base


class TenantRestriction(Base):
    """One restriction an extension keeps on the tenant, set and cleared by that extension."""

    __tablename__ = "tenant_restrictions"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
