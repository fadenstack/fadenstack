"""SQLAlchemy ORM models for the Skills Registry.

A skill is a row in ``skills`` -- who owns it, who may see it, when it comes
into a chat -- and its content is a series of immutable rows in
``skill_versions``. The row points at two of them: ``latest_version``, what
the editor shows, and ``published_version``, what chats get. Saving adds a
version; publishing moves the pointer; nothing overwrites or deletes a
version, so the audit log's ``skill@version`` always names content that still
exists.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Shared declarative base for Skills models."""


# ── Enums ─────────────────────────────────────────────────────────────


class SkillVisibility(str, enum.Enum):
    """Who can see and use a skill."""

    #: The owner, the people and groups it is shared with, and admins.
    PRIVATE = "private"
    #: Everyone in the tenant.
    TENANT = "tenant"
    #: Everyone in every tenant.
    GLOBAL = "global"


class SkillActivation(str, enum.Enum):
    """When a skill comes into a chat without being asked for by name."""

    #: In every chat of everyone it applies to (see ``resolve``).
    ALWAYS = "always"
    #: When its triggers or an assignment match the request.
    AUTO = "auto"
    #: Only when asked for by name (``$slug`` or the chat's skill picker).
    MANUAL = "manual"


class SkillStatus(str, enum.Enum):
    """Skill lifecycle states."""

    #: No version has been published yet.
    DRAFT = "draft"
    #: ``published_version`` is served to chats.
    PUBLISHED = "published"
    #: Served to nobody; restoring brings back the published version.
    ARCHIVED = "archived"


class GrantAccess(str, enum.Enum):
    """What a grant allows."""

    READ = "read"
    WRITE = "write"


class PrincipalType(str, enum.Enum):
    """Who a grant is for."""

    USER = "user"
    GROUP = "group"


class AssignmentTargetType(str, enum.Enum):
    """Target types for skill assignments."""

    ASSISTANT = "assistant"
    WORKSPACE = "workspace"
    PROJECT = "project"
    TENANT = "tenant"
    GLOBAL = "global"


# ── Models ────────────────────────────────────────────────────────────


class SkillModel(Base):
    """A reusable instruction pack (reasoning playbook)."""

    __tablename__ = "skills"
    __table_args__ = (
        # A slug is unique per owner: two people can each have a private
        # "code-review". Organization-owned skills (no owner) share one space.
        Index(
            "uq_skill_owner_slug",
            "tenant_id",
            text("coalesce(owner_id, '')"),
            "slug",
            unique=True,
        ),
        Index("ix_skills_tenant_owner", "tenant_id", "owner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="default",
        index=True,
    )
    #: Display title ("Financial Analysis").
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The Agent Skills ``name`` ("financial-analysis"): lowercase, digits and
    #: hyphens, at most 64. What ``$slug`` asks for; fixed once the skill
    #: exists. (The column is wider for slugs made before the limit.)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The latest version's description, kept here for lists.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: The user who owns it; ``None`` for skills the organization owns.
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SkillVisibility.PRIVATE.value,
    )
    activation: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SkillActivation.AUTO.value,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SkillStatus.DRAFT.value,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    tags: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=list)
    latest_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
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


class SkillVersionModel(Base):
    """An immutable snapshot of everything that shapes what a skill does."""

    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint(
            "skill_id",
            "version",
            name="uq_skill_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: SKILL.md's ``description``: what the skill does and when to use it.
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: SKILL.md's Markdown body -- what a chat is given.
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    #: SKILL.md's other frontmatter fields (``license``, ``compatibility``,
    #: ``metadata``, ``allowed-tools``, ...), kept as written for export.
    frontmatter: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    #: ``faden.yaml``: allowed/preferred/forbidden tools, knowledge
    #: sources, trigger rules. Versioned, so a rollback rolls them back too.
    manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    #: The folder's other files: ``{path: {"text": ...} | {"base64": ...}}``.
    files: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SkillGrantModel(Base):
    """Shares a skill with one user or group."""

    __tablename__ = "skill_grants"
    __table_args__ = (
        UniqueConstraint(
            "skill_id",
            "principal_type",
            "principal_id",
            name="uq_skill_grant_principal",
        ),
        Index("ix_skill_grants_principal", "principal_type", "principal_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    principal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    #: A backend user or group id. The backend's database is not this one,
    #: so there is no foreign key; the backend clears a principal's grants
    #: when it deletes one (``DELETE /api/internal/principals/...``).
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    access: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=GrantAccess.READ.value,
    )
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SkillAssignmentModel(Base):
    """Binds a skill to a specific target (assistant, workspace, etc.)."""

    __tablename__ = "skill_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SkillUsageDailyModel(Base):
    """How often each version of a skill reached a chat, per day."""

    __tablename__ = "skill_usage_daily"

    skill_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="CASCADE"),
        primary_key=True,
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
