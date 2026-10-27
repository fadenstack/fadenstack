"""Admin group-management endpoints."""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.groups_dao import GroupDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.groups import Group
from faden_backend.db.models.users import User
from faden_backend.services.rights import members_of, rights_changed
from faden_backend.services.skills.client import forget_principal
from faden_backend.web.api.admin.dependencies import require_superuser
from faden_backend.web.api.admin.groups.schema import (
    CreateGroupRequest,
    GroupDTO,
    GroupMemberDTO,
    UpdateGroupMembersRequest,
    UpdateGroupRequest,
)
from faden_backend.web.api.admin.users.schema import PermissionDTO, RoleDTO

router = APIRouter()


async def _log(
    session: AsyncSession, action: str, group_id: uuid.UUID, admin: User, **details: object,
) -> None:
    """Record a change to a team: its roles and members decide what people may do (ASVS 5.0 V16.3.3)."""
    await AuditDAO(session).log(
        action=action, target_type="group", target_id=str(group_id), result=AuditResult.ALLOW,
        actor_id=admin.id, severity="high", metadata_json=json.dumps(details, default=str),
    )


async def _members_changed(session: AsyncSession, group: Group, before: set[uuid.UUID], admin: User) -> None:
    """Record who joined and left *group*, and end their sign-ins: their rights changed (services/rights)."""
    after = await members_of(session, group.id)
    added, removed = sorted(after - before), sorted(before - after)
    if not added and not removed:
        return
    await _log(session, "team.members.set", group.id, admin, name=group.name, added=added, removed=removed)
    await rights_changed(
        session, [*added, *removed], cause="team.members", actor_id=admin.id, team=group.name,
    )


def _role_to_dto(role) -> RoleDTO:
    """Convert a Role model to RoleDTO (without user_count — not needed here)."""
    permissions = sorted(role.permissions, key=lambda p: (p.resource, p.action))
    return RoleDTO(
        id=role.id,
        name=role.name,
        description=role.description,
        is_builtin=role.is_builtin,
        created_at=role.created_at,
        permissions=[
            PermissionDTO(id=perm.id, resource=perm.resource, action=perm.action) for perm in permissions
        ],
        user_count=0,
    )


def _group_to_dto(group: Group, member_count: int = 0) -> GroupDTO:
    return GroupDTO(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        roles=[_role_to_dto(role) for role in sorted(group.roles, key=lambda r: r.name)],
        member_count=member_count,
        source=group.source,
    )


# ── Groups CRUD ───────────────────────────────────────────────────────


@router.get("/", response_model=list[GroupDTO], name="list_groups")
async def list_groups(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
) -> list[GroupDTO]:
    """List all groups with their roles and member counts."""
    groups = await group_dao.list_groups()
    result = []
    for group in groups:
        count = await group_dao.count_group_members(group.id)
        result.append(_group_to_dto(group, member_count=count))
    return result


@router.get("/{group_id}", response_model=GroupDTO, name="get_group")
async def get_group(
    group_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
) -> GroupDTO:
    """Get a single group by ID."""
    group = await group_dao.get_group_by_id(group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    count = await group_dao.count_group_members(group.id)
    return _group_to_dto(group, member_count=count)


@router.post("/", response_model=GroupDTO, status_code=status.HTTP_201_CREATED, name="create_group")
async def create_group(
    payload: CreateGroupRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
) -> GroupDTO:
    """Create a new group."""
    existing = await group_dao.get_group_by_name(payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Group '{payload.name}' already exists",
        )
    group = await group_dao.create_group(payload.name, payload.description, payload.role_ids)
    await _log(
        group_dao.session, "team.create", group.id, _user, name=group.name,
        roles=sorted(role.name for role in group.roles),
    )
    return _group_to_dto(group)


@router.patch("/{group_id}", response_model=GroupDTO, name="update_group")
async def update_group(
    group_id: uuid.UUID,
    payload: UpdateGroupRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
) -> GroupDTO:
    """Update a group's name, description, or roles."""
    existing = await group_dao.get_group_by_id(group_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    had = {role.id for role in existing.roles}
    group = await group_dao.update_group(
        group_id,
        name=payload.name,
        description=payload.description,
        role_ids=payload.role_ids,
    )
    roles = sorted(role.name for role in group.roles)  # type: ignore[union-attr]
    await _log(group_dao.session, "team.update", group_id, _user, name=existing.name, roles=roles)
    if payload.role_ids is not None and set(payload.role_ids) != had:
        # Its members have other rights now (services/rights.py).
        members = await members_of(group_dao.session, group_id)
        await rights_changed(
            group_dao.session, members, cause="team.roles", actor_id=_user.id, team=existing.name,
        )
    count = await group_dao.count_group_members(group_id)
    return _group_to_dto(group, member_count=count)  # type: ignore[arg-type]


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT, name="delete_group")
async def delete_group(
    group_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
) -> None:
    """Delete a group."""
    existing = await group_dao.get_group_by_id(group_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    members = await members_of(group_dao.session, group_id)
    await _log(group_dao.session, "team.delete", group_id, _user, name=existing.name, members=len(members))
    await group_dao.delete_group(group_id)
    await rights_changed(
        group_dao.session, members, cause="team.deleted", actor_id=_user.id, team=existing.name,
    )
    await forget_principal("group", str(group_id))


# ── Group members ─────────────────────────────────────────────────────


@router.get("/{group_id}/members", response_model=list[GroupMemberDTO], name="list_group_members")
async def list_group_members(
    group_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> list[GroupMemberDTO]:
    """List all members of a group."""
    group = await group_dao.get_group_by_id(group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    member_ids = await group_dao.get_group_members(group_id)
    if not member_ids:
        return []
    result = await session.execute(
        select(User).where(User.id.in_(member_ids)).order_by(User.email),  # type: ignore[arg-type]
    )
    users = result.scalars().all()
    return [GroupMemberDTO(id=u.id, email=u.email) for u in users]


@router.put("/{group_id}/members", response_model=list[GroupMemberDTO], name="set_group_members")
async def set_group_members(
    group_id: uuid.UUID,
    payload: UpdateGroupMembersRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
    session: AsyncSession = Depends(get_db_session),
) -> list[GroupMemberDTO]:
    """Replace all members of a group."""
    group = await group_dao.get_group_by_id(group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    before = await members_of(session, group_id)
    await group_dao.set_group_members(group_id, payload.user_ids)
    await _members_changed(session, group, before, _user)

    member_ids = await group_dao.get_group_members(group_id)
    if not member_ids:
        return []
    result = await session.execute(
        select(User).where(User.id.in_(member_ids)).order_by(User.email),  # type: ignore[arg-type]
    )
    users = result.scalars().all()
    return [GroupMemberDTO(id=u.id, email=u.email) for u in users]


@router.post("/{group_id}/members", status_code=status.HTTP_201_CREATED, name="add_group_members")
async def add_group_members(
    group_id: uuid.UUID,
    payload: UpdateGroupMembersRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
) -> None:
    """Add users to a group (idempotent)."""
    group = await group_dao.get_group_by_id(group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    before = await members_of(group_dao.session, group_id)
    await group_dao.add_members(group_id, payload.user_ids)
    await _members_changed(group_dao.session, group, before, _user)


@router.delete(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="remove_group_member",
)
async def remove_group_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    group_dao: GroupDAO = Depends(),
) -> None:
    """Remove a user from a group."""
    group = await group_dao.get_group_by_id(group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    before = await members_of(group_dao.session, group_id)
    await group_dao.remove_member(group_id, user_id)
    await _members_changed(group_dao.session, group, before, _user)
