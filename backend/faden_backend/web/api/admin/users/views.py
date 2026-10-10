"""Admin user-management and RBAC endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.agents_dao import AgentsDAO
from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.groups_dao import GroupDAO
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.api_tokens import API_TOKEN_TYPE, ApiToken
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.groups import Group
from faden_backend.db.models.rbac import Permission, Role
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.password_policy import password_problem
from faden_backend.services.skills.client import forget_principal
from faden_backend.web.api.admin.dependencies import require_superuser
from faden_backend.web.api.admin.users.preferences import (
    router as preferences_router,
)
from faden_backend.web.api.admin.users.schema import (
    AdminUserDTO,
    ApiTokenDTO,
    ApiTokenResponse,
    ChangePasswordRequest,
    CreateRoleRequest,
    CreateUserRequest,
    GenerateApiTokenRequest,
    MeAccessDTO,
    PermissionDTO,
    RoleDTO,
    SetUserActiveRequest,
    UpdateRoleRequest,
    UpdateUserRolesRequest,
    UpdateUserUsageGroupRequest,
)
from faden_backend.web.api.agents.schema import DeveloperModeDTO, DeveloperModeRequest

router = APIRouter()
router.include_router(preferences_router)


def _role_to_dto(role: Role, user_count: int = 0) -> RoleDTO:
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
        user_count=user_count,
    )


def _permissions_to_dto(permissions: list[Permission]) -> list[PermissionDTO]:
    return [
        PermissionDTO(id=perm.id, resource=perm.resource, action=perm.action)
        for perm in sorted(permissions, key=lambda p: (p.resource, p.action))
    ]


async def _build_admin_user_dto(user: User, rbac_dao: RbacDAO) -> AdminUserDTO:
    roles = await rbac_dao.get_user_roles(user.id)
    permissions = await rbac_dao.get_user_permissions(user.id)
    usage_group = (
        await rbac_dao.session.get(Group, user.usage_group_id) if user.usage_group_id else None
    )
    return AdminUserDTO(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        roles=[_role_to_dto(role) for role in sorted(roles, key=lambda r: r.name)],
        permissions=_permissions_to_dto(permissions),
        usage_group_id=user.usage_group_id,
        usage_group_name=usage_group.name if usage_group else None,
        developer_mode=user.developer_mode,
        developer_mode_until=user.developer_mode_until,
    )


async def _resolve_users_secret(session: AsyncSession) -> str:
    """Resolve backend JWT signing secret with DB fallback."""
    from faden_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from faden_backend.settings import settings as backend_settings  # noqa: PLC0415

    secret = backend_settings.users_secret.strip()
    if secret:
        return secret
    if not backend_settings.settings_master_key:
        return ""

    row = await session.execute(
        text("SELECT ciphertext FROM system_setting_secret WHERE key = :k"),
        {"k": "faden_backend.users_secret"},
    )
    ciphertext = row.scalar_one_or_none()
    if not ciphertext:
        return ""
    try:
        secret = SettingsCrypto(backend_settings.settings_master_key).decrypt(ciphertext).strip()
    except Exception:  # noqa: BLE001
        return ""
    if secret:
        object.__setattr__(backend_settings, "users_secret", secret)
    return secret


# ── Current user access ──────────────────────────────────────────────


@router.get("/me/access", response_model=MeAccessDTO, name="admin_me_access")
async def me_access(
    user: Annotated[User, Depends(current_active_user)],
    rbac_dao: RbacDAO = Depends(),
    agents_dao: AgentsDAO = Depends(),
) -> MeAccessDTO:
    """Return effective roles and permissions for the current user."""
    roles = await rbac_dao.get_user_roles(user.id)
    permissions = await rbac_dao.get_user_permissions(user.id)
    return MeAccessDTO(
        id=user.id,
        email=user.email,
        is_superuser=user.is_superuser,
        roles=[_role_to_dto(role) for role in sorted(roles, key=lambda r: r.name)],
        permissions=_permissions_to_dto(permissions),
        maintains_agents=await agents_dao.maintains_any(user.id),
    )


# ── Roles CRUD ────────────────────────────────────────────────────────


@router.get("/roles", response_model=list[RoleDTO], name="list_roles")
async def list_roles(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> list[RoleDTO]:
    """List all roles and their permissions."""
    roles = await rbac_dao.list_roles()
    result = []
    for role in roles:
        count = await rbac_dao.count_role_users(role.id)
        result.append(_role_to_dto(role, user_count=count))
    return result


@router.get("/roles/{role_id}", response_model=RoleDTO, name="get_role")
async def get_role(
    role_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> RoleDTO:
    """Get a single role by ID."""
    role = await rbac_dao.get_role_by_id(role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    count = await rbac_dao.count_role_users(role.id)
    return _role_to_dto(role, user_count=count)


@router.post("/roles", response_model=RoleDTO, status_code=status.HTTP_201_CREATED, name="create_role")
async def create_role(
    payload: CreateRoleRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> RoleDTO:
    """Create a new custom role."""
    existing = await rbac_dao.get_role_by_name(payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role '{payload.name}' already exists",
        )
    role = await rbac_dao.create_role(payload.name, payload.description, payload.permission_ids)
    return _role_to_dto(role)


@router.patch("/roles/{role_id}", response_model=RoleDTO, name="update_role")
async def update_role(
    role_id: uuid.UUID,
    payload: UpdateRoleRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> RoleDTO:
    """Update a custom role. Built-in roles cannot be modified."""
    existing = await rbac_dao.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if existing.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in roles cannot be modified",
        )
    role = await rbac_dao.update_role(
        role_id,
        name=payload.name,
        description=payload.description,
        permission_ids=payload.permission_ids,
    )
    count = await rbac_dao.count_role_users(role_id)
    return _role_to_dto(role, user_count=count)  # type: ignore[arg-type]


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, name="delete_role")
async def delete_role(
    role_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> None:
    """Delete a custom role. Built-in roles cannot be deleted."""
    existing = await rbac_dao.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if existing.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in roles cannot be deleted",
        )
    await rbac_dao.delete_role(role_id)


# ── Permissions ───────────────────────────────────────────────────────


@router.get("/permissions", response_model=list[PermissionDTO], name="list_permissions")
async def list_permissions(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> list[PermissionDTO]:
    """List all available permissions."""
    permissions = await rbac_dao.list_permissions()
    return _permissions_to_dto(permissions)


# ── Users ─────────────────────────────────────────────────────────────


@router.get("/", response_model=list[AdminUserDTO], name="list_users_with_roles")
async def list_users_with_roles(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminUserDTO]:
    """List users with assigned roles and effective permissions."""
    rbac_dao = RbacDAO(session)
    result = await session.execute(select(User).order_by(User.email.asc()))
    users = list(result.scalars().all())
    return [await _build_admin_user_dto(user, rbac_dao) for user in users]


@router.put("/{user_id}/roles", response_model=AdminUserDTO, name="set_user_roles")
async def set_user_roles(
    user_id: uuid.UUID,
    payload: UpdateUserRolesRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserDTO:
    """Replace role assignments for a user."""
    rbac_dao = RbacDAO(session)

    user_result = await session.execute(select(User).where(User.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing_roles = await rbac_dao.list_roles()
    existing_role_ids = {role.id for role in existing_roles}
    for role_id in payload.role_ids:
        if role_id not in existing_role_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown role id: {role_id}",
            )

    await rbac_dao.set_user_roles(user_id, payload.role_ids)
    await session.flush()

    return await _build_admin_user_dto(target_user, rbac_dao)


@router.put("/{user_id}/usage-group", response_model=AdminUserDTO, name="set_user_usage_group")
async def set_user_usage_group(
    user_id: uuid.UUID,
    payload: UpdateUserUsageGroupRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserDTO:
    """Set the group a user's LLM usage is attributed to; ``null`` clears it."""
    user_result = await session.execute(select(User).where(User.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if payload.usage_group_id is not None:
        group = await GroupDAO(session).get_group_by_id(payload.usage_group_id)
        if group is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown group id: {payload.usage_group_id}",
            )

    target_user.usage_group_id = payload.usage_group_id
    await session.flush()

    return await _build_admin_user_dto(target_user, RbacDAO(session))


@router.put("/{user_id}/developer-mode", response_model=DeveloperModeDTO, name="set_user_developer_mode")
async def set_user_developer_mode(
    user_id: uuid.UUID,
    payload: DeveloperModeRequest,
    admin: Annotated[User, Depends(require_superuser)],
    session: AsyncSession = Depends(get_db_session),
) -> DeveloperModeDTO:
    """Switch a user's developer mode on or off -- by an admin, never by the user.

    It lets agents attach any local MCP server on that user's machine; it
    never widens what the server allows. It reaches the user's agent tokens
    at their next refresh.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    target.developer_mode = payload.enabled
    target.developer_mode_until = payload.until if payload.enabled else None
    await AuditDAO(session).log(
        action="user.developer_mode",
        target_type="user",
        target_id=str(user_id),
        result=AuditResult.ALLOW,
        actor_id=admin.id,
        metadata_json=json.dumps({"enabled": payload.enabled, "until": payload.until}, default=str),
    )
    await session.flush()
    return DeveloperModeDTO(
        user_id=target.id,
        developer_mode=target.developer_mode,
        developer_mode_until=target.developer_mode_until,
    )


@router.post("/", response_model=AdminUserDTO, status_code=status.HTTP_201_CREATED, name="create_user")
async def create_user(
    payload: CreateUserRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserDTO:
    """Admin-initiated user creation."""
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    problem = password_problem(payload.password, payload.email)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    # Check if email already exists
    existing = await session.execute(select(User).where(User.email == payload.email))  # type: ignore[arg-type]
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{payload.email}' already exists",
        )

    password_helper = PasswordHelper()
    new_user = User(
        id=uuid.uuid4(),
        email=payload.email,
        hashed_password=password_helper.hash(payload.password),
        is_active=True,
        is_superuser=payload.is_superuser,
        is_verified=True,
    )
    session.add(new_user)
    await session.flush()

    rbac_dao = RbacDAO(session)
    if payload.role_ids:
        await rbac_dao.set_user_roles(new_user.id, payload.role_ids)
        await session.flush()
    elif not payload.is_superuser:
        default_role = await rbac_dao.get_role_by_name("default_user")
        if default_role is not None:
            await rbac_dao.assign_role(new_user.id, default_role.id)
            await session.flush()

    return await _build_admin_user_dto(new_user, rbac_dao)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, name="delete_user")
async def delete_user(
    user_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a user by ID."""
    result = await session.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await session.delete(target)
    await session.flush()
    await forget_principal("user", str(user_id))


@router.put("/{user_id}/active", response_model=AdminUserDTO, name="set_user_active")
async def set_user_active(
    user_id: uuid.UUID,
    payload: SetUserActiveRequest,
    admin: Annotated[User, Depends(require_superuser)],
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserDTO:
    """Suspend an account, or let it back in.

    A suspended person cannot sign in, and their sign-ins, API tokens and agents stop working: the
    backend checks the flag on every request, the gateway within a minute, and the device channel
    when it next checks the caller. Nothing of theirs is deleted.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not payload.active:
        if target.id == admin.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You cannot suspend yourself.")
        if target.is_superuser:
            others = await session.scalar(
                select(func.count()).select_from(User).where(
                    User.is_superuser.is_(True), User.is_active.is_(True), User.id != target.id,
                ),
            )
            if not others:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This is the last active superuser; nobody could administer the system.",
                )
    if target.is_active != payload.active:
        target.is_active = payload.active
        await session.flush()
        await AuditDAO(session).log(
            action="user.reactivate" if payload.active else "user.suspend",
            target_type="user",
            target_id=str(target.id),
            result=AuditResult.ALLOW,
            actor_id=admin.id,
            metadata_json=json.dumps({"email": target.email}),
        )
    return await _build_admin_user_dto(target, RbacDAO(session))


# ── Self-service profile endpoints ────────────────────────────────────


@router.post("/me/change-password", status_code=status.HTTP_204_NO_CONTENT, name="change_password")
async def change_password(
    payload: ChangePasswordRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Change the current user's password."""
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    password_helper = PasswordHelper()
    verified, _ = password_helper.verify_and_update(payload.current_password, user.hashed_password)
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    problem = password_problem(payload.new_password, user.email)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)
    user.hashed_password = password_helper.hash(payload.new_password)
    session.add(user)
    await session.flush()


@router.post("/me/api-token", response_model=ApiTokenResponse, name="generate_api_token")
async def generate_api_token(
    payload: GenerateApiTokenRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> ApiTokenResponse:
    """Generate a JWT token for the LLM API gateway.

    Uses ``settings.users_secret`` which is loaded from the
    ``faden_backend.users_secret`` system setting at startup.
    Both backend and faden_gateway share the same DB-stored secret.
    """
    import time  # noqa: PLC0415

    import jwt as pyjwt  # noqa: PLC0415

    from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values  # noqa: PLC0415
    from faden_backend.settings import settings  # noqa: PLC0415

    # The lifetimes as they are now: with several workers, a change reaches only the one that took it.
    await refresh_runtime_values(session, prefix="users.api_token_")
    secret = await _resolve_users_secret(session)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT secret is not configured. Set 'Backend Users JWT Secret' in System Settings.",
        )

    # Every token expires: one that never did was a key for good to whoever found it.
    longest = max(settings.api_token_max_lifetime_s, 1)
    lifetime = payload.expires_in or min(settings.api_token_default_lifetime_s, longest)
    if lifetime > longest:
        raise HTTPException(
            status_code=422,
            detail=f"An API token lasts at most {longest // 86400} days ({longest} s).",
        )
    now = int(time.time())
    record = ApiToken(
        id=uuid.uuid4(),
        user_id=user.id,
        tenant_id=payload.tenant_id,
        name=(payload.name or "").strip() or None,
        expires_at=datetime.fromtimestamp(now + lifetime, tz=UTC),
    )
    session.add(record)
    await session.flush()

    claims: dict = {
        "sub": str(user.id),
        "tenant_id": payload.tenant_id,
        "email": user.email,
        "iat": now,
        # The gateway looks the token up by its jti, so revoking the row ends the token.
        "jti": str(record.id),
        "typ": API_TOKEN_TYPE,
        "exp": now + lifetime,
    }

    token = pyjwt.encode(claims, secret, algorithm="HS256")
    await AuditDAO(session).log(
        action="user.api_token.create",
        target_type="api_token",
        target_id=str(record.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        metadata_json=json.dumps({"name": record.name, "expires_in": lifetime}),
    )
    return ApiTokenResponse(
        token=token,
        expires_in=lifetime,
        id=record.id,
        name=record.name,
        expires_at=record.expires_at,
    )


async def _list_api_tokens(session: AsyncSession, user_id: uuid.UUID) -> list[ApiTokenDTO]:
    result = await session.execute(
        select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at.desc()),
    )
    return [ApiTokenDTO.model_validate(t, from_attributes=True) for t in result.scalars()]


async def _revoke_api_token(
    session: AsyncSession, *, owner_id: uuid.UUID, token_id: uuid.UUID, actor: User,
) -> None:
    record = await session.get(ApiToken, token_id)
    if record is None or record.user_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API token not found")
    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await AuditDAO(session).log(
            action="user.api_token.revoke",
            target_type="api_token",
            target_id=str(token_id),
            result=AuditResult.ALLOW,
            actor_id=actor.id,
            metadata_json=json.dumps({"owner_id": str(owner_id)}),
        )
        await session.flush()


@router.get("/me/api-tokens", response_model=list[ApiTokenDTO], name="list_my_api_tokens")
async def list_my_api_tokens(
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> list[ApiTokenDTO]:
    """The current user's API tokens, newest first, revoked ones included."""
    return await _list_api_tokens(session, user.id)


@router.delete(
    "/me/api-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, name="revoke_my_api_token",
)
async def revoke_my_api_token(
    token_id: uuid.UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke one of the current user's API tokens. The gateway refuses it within a minute."""
    await _revoke_api_token(session, owner_id=user.id, token_id=token_id, actor=user)


@router.get("/{user_id}/api-tokens", response_model=list[ApiTokenDTO], name="list_user_api_tokens")
async def list_user_api_tokens(
    user_id: uuid.UUID,
    _admin: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> list[ApiTokenDTO]:
    """A user's API tokens, for an administrator."""
    return await _list_api_tokens(session, user_id)


@router.delete(
    "/{user_id}/api-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT, name="revoke_user_api_token",
)
async def revoke_user_api_token(
    user_id: uuid.UUID,
    token_id: uuid.UUID,
    admin: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke a user's API token, as an administrator."""
    await _revoke_api_token(session, owner_id=user_id, token_id=token_id, actor=admin)
