"""The chat is for the roles that include it.

The gateway checks the ``chat.*`` permissions now. ``default_user`` and ``admin`` carry them; migration
``ch4tr0l3s`` gives ``admin`` them on existing installs and no account a role.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.rbac import Permission, Role, RolePermission

migration = importlib.import_module("faden_backend.db.migrations.versions.2026-10-11-11-00_ch4tr0l3s")
CHAT = {f"{resource}:{action}" for resource, action in migration.CHAT_PERMISSIONS}


async def _roles(db: AsyncSession) -> dict[str, Role]:
    await RbacDAO(db).seed_defaults()
    await db.flush()
    return {role.name: role for role in (await db.execute(select(Role))).scalars()}


async def _permissions(db: AsyncSession, role: Role) -> set[str]:
    rows = await db.execute(
        select(Permission.resource, Permission.action)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role.id),
    )
    return {f"{resource}:{action}" for resource, action in rows.all()}


@pytest.mark.anyio
async def test_the_roles_that_chat_carry_the_chat_permissions(dbsession: AsyncSession) -> None:
    roles = await _roles(dbsession)
    assert await _permissions(dbsession, roles["default_user"]) >= CHAT
    assert await _permissions(dbsession, roles["admin"]) >= CHAT, "admin gives every permission"
    for name in ("viewer", "operator", "rag_manager", "rag_editor", "rag_viewer"):
        assert not await _permissions(dbsession, roles[name]) & CHAT, name


@pytest.mark.anyio
async def test_the_upgrade_gives_admin_the_chat(dbsession: AsyncSession) -> None:
    roles = await _roles(dbsession)
    chat_ids = select(Permission.id).where(
        Permission.resource.in_(["chat.projects", "chat.sessions", "chat.attachments"]),
    )
    await dbsession.execute(
        delete(RolePermission).where(
            RolePermission.role_id == roles["admin"].id, RolePermission.permission_id.in_(chat_ids),
        ),
    )
    assert not await _permissions(dbsession, roles["admin"]) & CHAT, "as before the upgrade"
    await dbsession.execute(text(migration.ADMIN_CHATS))
    await dbsession.execute(text(migration.ADMIN_CHATS))  # once is enough; twice changes nothing
    assert await _permissions(dbsession, roles["admin"]) >= CHAT


def test_the_upgrade_gives_nobody_a_role() -> None:
    assert not hasattr(migration, "KEEP_CHATTING")
    assert "user_roles" not in migration.ADMIN_CHATS
