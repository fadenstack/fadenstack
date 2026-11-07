"""Schemas for group management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from faden_backend.web.api.admin.users.schema import RoleDTO


class GroupDTO(BaseModel):
    """Group with its roles and member count."""

    id: uuid.UUID
    name: str
    description: str | None = None
    created_at: datetime
    roles: list[RoleDTO] = Field(default_factory=list)
    member_count: int = 0
    #: Who keeps the team: ``None``, an administrator; ``provider:<key>``, that sign-in provider, which names
    #: it and keeps its memberships (services/provisioning.py).
    source: str | None = None
    #: The team above it (services/teams.py); ``None`` for a top team.
    parent_id: uuid.UUID | None = None
    #: Its place in the tree: 1 for a top team, at most 6.
    level: int = 1
    #: A finance code its usage is booked to.
    cost_center: str | None = None
    #: When it ended: it keeps its history, takes no new members and is nobody's new home team.
    archived_at: datetime | None = None
    #: How many people have it as their home team (whose usage goes to it).
    home_count: int = 0


class GroupMemberDTO(BaseModel):
    """Minimal user info for group membership listing."""

    id: uuid.UUID
    email: str


class CreateGroupRequest(BaseModel):
    """Create a new group."""

    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    parent_id: uuid.UUID | None = None
    cost_center: str | None = Field(default=None, max_length=64)


class UpdateGroupRequest(BaseModel):
    """Update a group; ``parent_id`` and ``cost_center`` change when sent (``null`` clears them)."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    role_ids: list[uuid.UUID] | None = None
    parent_id: uuid.UUID | None = None
    cost_center: str | None = Field(default=None, max_length=64)


class UpdateGroupMembersRequest(BaseModel):
    """Replace or add members to a group."""

    user_ids: list[uuid.UUID] = Field(default_factory=list)
