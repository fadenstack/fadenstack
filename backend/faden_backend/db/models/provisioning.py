"""Accounts an outside source keeps (a provisioning source, e.g. an identity provider pushing over SCIM).

``services/provisioning.py`` is the only writer. Only what finds the person again is kept beside the account:
the source's own ID for them and their login name there; everything else about them stays with the source.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base


class ProvisionedAccount(Base):
    """The source that made or took over an account, and how it names the person.

    ``suspended_by_source``: the account's suspension was the source's, so the source may lift it; an
    administrator's suspension it leaves alone.
    """

    __tablename__ = "provisioned_accounts"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_provisioned_accounts_source_external_id"),
        Index("ux_provisioned_accounts_source_login", "source", text("lower(login)"), unique=True),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), primary_key=True,
    )
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    login: Mapped[str] = mapped_column(String(320), nullable=False)
    suspended_by_source: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    #: When the source removed the person: no longer shown to it, the account suspended and kept; the source
    #: adding the same ID again brings the same account back.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provisioned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )
