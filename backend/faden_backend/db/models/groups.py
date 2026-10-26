"""Group models: groups, group-role and user-group associations."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from faden_backend.db.base import Base
from faden_backend.db.models.rbac import Role


class Group(Base):
    """A named collection of roles that can be assigned to users."""

    __tablename__ = "groups"
    __table_args__ = (
        Index(
            "ux_groups_source_external_id", "source", "external_id", unique=True,
            postgresql_where=text("source IS NOT NULL AND external_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Who keeps the team: ``None``, an administrator; ``provider:<key>``, that provisioning source, which
    # names it and keeps its memberships (``services/provisioning.py``); ``external_id`` is the source's own
    # ID for it.
    source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # relationships
    roles: Mapped[list[Role]] = relationship(
        secondary="group_roles",
        lazy="selectin",
    )


class GroupRole(Base):
    """Association table linking groups to roles."""

    __tablename__ = "group_roles"

    group_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )


class UserGroup(Base):
    """Association table linking users to groups."""

    __tablename__ = "user_groups"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Who keeps this membership: ``None``, an administrator; ``provider:<key>``, that sign-in provider, which
    # takes it away again when the person no longer belongs (services/sign_in/accounts.py).
    source: Mapped[str | None] = mapped_column(String(80), nullable=True)
