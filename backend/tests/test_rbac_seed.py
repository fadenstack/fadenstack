"""The built-in roles hold every permission an endpoint asks for.

A permission exists once a built-in role has it, and a custom role can grant only permissions that exist.
Machine profiles and the cost pages asked for permissions no role had, so nobody but a superuser could use
them, whatever their roles.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rbac_dao import RbacDAO


async def test_every_permission_an_endpoint_asks_for_exists(dbsession: AsyncSession) -> None:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    kinds = {(p.resource, p.action) for p in await rbac.list_permissions()}
    for wanted in [
        ("system.node_profiles", "read"),
        ("system.node_profiles", "manage"),
        ("observability", "read"),
        ("observability", "write"),
    ]:
        assert wanted in kinds, wanted
