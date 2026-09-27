"""Request and response bodies of the skills API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Visibility = Literal["private", "tenant", "global"]
Activation = Literal["always", "auto", "manual"]
AccessLabel = Literal["read", "write", "owner", "admin"]


# ── Requests ──────────────────────────────────────────────────────────


class CreateSkillRequest(BaseModel):
    """A new skill: its fields, or a whole SKILL.md in ``content``."""

    #: Display title; from ``content`` or the slug when missing.
    name: str | None = Field(default=None, max_length=255)
    #: The skill name (``code-review``); made from the title when missing.
    slug: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    body_markdown: str | None = None
    #: A whole SKILL.md; its fields fill in whatever is not given above.
    content: str | None = None
    frontmatter: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    files: dict[str, dict[str, str]] | None = None
    #: ``organization``: owned by the organization rather than the caller
    #: (administrators only).
    owner: Literal["me", "organization"] = "me"
    visibility: Visibility = "private"
    activation: Activation | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    tags: list[str] | None = None
    enabled: bool = True
    #: Serve the first version to chats straight away.
    publish: bool = False
    change_note: str | None = Field(default=None, max_length=500)


class UpdateSkillRequest(BaseModel):
    """Catalog fields of a skill; content changes are new versions."""

    name: str | None = Field(default=None, max_length=255)
    tags: list[str] | None = None
    activation: Activation | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None


class NewVersionRequest(BaseModel):
    """A new version; a field left out keeps the latest version's value."""

    description: str | None = Field(default=None, max_length=1024)
    body_markdown: str | None = None
    #: A whole SKILL.md in place of ``description``/``body_markdown``/``frontmatter``.
    content: str | None = None
    frontmatter: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    files: dict[str, dict[str, str]] | None = None
    #: The version the editor started from; a save on top of someone
    #: else's newer one is refused (409) instead of replacing it.
    base_version: int | None = Field(default=None, ge=1)
    publish: bool = False
    change_note: str | None = Field(default=None, max_length=500)


class PublishRequest(BaseModel):
    """Which version chats get; the latest when left out."""

    version: int | None = Field(default=None, ge=1)


class VisibilityRequest(BaseModel):
    """Who sees the skill."""

    visibility: Visibility


class GrantItem(BaseModel):
    """One person or group a skill is shared with."""

    principal_type: Literal["user", "group"]
    principal_id: str = Field(min_length=1, max_length=128)
    access: Literal["read", "write"] = "read"


class GrantsRequest(BaseModel):
    """Everyone a skill is shared with (replaces the current list)."""

    grants: list[GrantItem]


class CreateAssignmentRequest(BaseModel):
    """Create a skill assignment."""

    target_type: str = Field(
        pattern=r"^(assistant|workspace|project|tenant|global)$",
    )
    target_id: str | None = None
    enabled: bool = True
    priority_override: int | None = Field(default=None, ge=0, le=100)


# ── Responses ─────────────────────────────────────────────────────────


class FileInfo(BaseModel):
    """A file in a skill's folder."""

    path: str
    size: int
    kind: Literal["text", "binary"]
    #: A script. Kept for export; nothing here runs it.
    executable: bool


class GrantResponse(BaseModel):
    """A grant."""

    principal_type: str
    principal_id: str
    access: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillAssignmentResponse(BaseModel):
    """A skill assignment."""

    id: uuid.UUID
    skill_id: uuid.UUID
    target_type: str
    target_id: str | None
    enabled: bool
    priority_override: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SkillSummaryResponse(BaseModel):
    """A skill in a list."""

    id: uuid.UUID
    tenant_id: str
    name: str
    slug: str
    description: str
    owner_id: str | None
    visibility: str
    activation: str
    status: str
    enabled: bool
    priority: int
    tags: list[str]
    latest_version: int
    published_version: int | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    #: What the caller may do with it.
    access: AccessLabel


class SkillDetailResponse(SkillSummaryResponse):
    """A skill with the content of the version the caller works on.

    Editors (write access and up) get the latest version; people who may only
    read it get the published one, not the unpublished drafts.
    """

    version: int
    body_markdown: str
    frontmatter: dict[str, Any]
    manifest: dict[str, Any]
    files: list[FileInfo]
    content_sha256: str
    #: Who it is shared with; for its owner and administrators.
    grants: list[GrantResponse] | None = None
    uses_30d: int = 0


class SkillListResponse(BaseModel):
    """A page of skills."""

    items: list[SkillSummaryResponse]
    total: int
    offset: int
    limit: int


class VersionSummaryResponse(BaseModel):
    """A version in the history."""

    version: int
    description: str
    content_sha256: str
    change_note: str | None
    created_by: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionDetailResponse(VersionSummaryResponse):
    """A version with its content."""

    body_markdown: str
    frontmatter: dict[str, Any]
    manifest: dict[str, Any]
    files: list[FileInfo]


class SaveVersionResponse(BaseModel):
    """The result of saving: the version, and whether it was new."""

    version: VersionSummaryResponse
    #: False when the content was the same as the latest version's.
    created: bool
    skill: SkillDetailResponse


class CompletionsResponse(BaseModel):
    """Intellisense completion data for the skill editor."""

    frontmatter_keys: list[dict[str, str]]
    extension_keys: list[dict[str, str]]
    visibility_values: list[str]
    activation_values: list[str]
    status_values: list[str]
    section_templates: list[dict[str, str]]
    file_types: list[str]
