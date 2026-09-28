"""Agents: tenant-level assistants that SDK integrations run under.

An agent is what an add-in in TIA Portal, EPLAN or Office points at. Its
instructions, model and tool contract live here, versioned, so changing them
does not mean redeploying the add-in; who may use it is decided by grants to
users and teams (default deny). A chat project stays the user's own
workspace; an agent belongs to the tenant.

Per-agent settings of Enterprise extensions (token lifetimes, a local MCP
filter, device-only PII, ...) go into ``AgentVersion.extensions``, one key per
extension: core stores and snapshots them without knowing their shape.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base


class AgentStatus(enum.StrEnum):
    """Where an agent is in its life."""

    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class LocalMcpMode(enum.StrEnum):
    """Local MCP servers an SDK may attach for this agent (core modes)."""

    DISABLE = "disable"
    ALLOW = "allow"


class PrincipalType(enum.StrEnum):
    """Who a grant is for."""

    USER = "user"
    GROUP = "group"


class AgentMemberRole(enum.StrEnum):
    """What a member does for an agent. EE adds ``auditor``."""

    MAINTAINER = "maintainer"


def _now_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Agent(Base):
    """An agent of the tenant, addressed by its slug."""

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_agents_tenant_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default", index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=AgentStatus.DRAFT,
    )
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "agent_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_agents_published_version",
        ),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentVersion(Base):
    """One version of an agent's definition. Immutable once published."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The model alias every request of this agent uses; None lets the client pick.
    model_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    #: Knowledge collection ids the agent may search.
    knowledge: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: Gateway-run tools the model may be offered (names or ``fnmatch``
    #: patterns); None offers what a chat would get.
    server_tools: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    #: Tools the host application provides: [{name, schema_sha256?, risk?, approval?}].
    #: Empty means the agent does not restrict the caller's tools.
    host_tool_contract: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    local_mcp_mode: Mapped[LocalMcpMode] = mapped_column(
        Enum(LocalMcpMode, name="agent_local_mcp_mode", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=LocalMcpMode.DISABLE,
    )
    #: Greeting, starter prompts, icon.
    ui: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: One key per Enterprise extension, validated by that extension.
    extensions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _now_column()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentGrant(Base):
    """Allows a user, or every member of a group, to use an agent."""

    __tablename__ = "agent_grants"
    __table_args__ = (
        UniqueConstraint("agent_id", "principal_type", "principal_id", name="uq_agent_grants_principal"),
        Index("ix_agent_grants_principal", "principal_type", "principal_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    principal_type: Mapped[PrincipalType] = mapped_column(
        Enum(PrincipalType, name="agent_principal_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    #: A user id or a group id -- by id, so a directory sync keeps it valid.
    principal_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = _now_column()


class AgentMember(Base):
    """A user who looks after an agent: versions, grants, members."""

    __tablename__ = "agent_members"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    #: A string, not a database enum: EE adds roles without a migration.
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=AgentMemberRole.MAINTAINER.value)
    created_at: Mapped[datetime] = _now_column()


class AgentRefreshToken(Base):
    """One refresh token of an agent sign-in on one device.

    Only the SHA-256 of the token is stored. Refreshing replaces the row with
    a new one in the same ``family_id``; presenting a replaced token again
    means it was copied, and revokes the whole family.
    """

    __tablename__ = "agent_refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: The sign-in this token belongs to; the device id in access tokens.
    family_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    device_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = _now_column()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
