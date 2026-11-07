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


async def test_usage_figures_for_administrators_and_auditors_people_for_nobody_yet(dbsession: AsyncSession) -> None:
    """``usage:read`` (aggregates of at least k people) for the admin, auditor and usage auditor roles (the
    last opens the per-person view from the Usage page); the usage of named people (``usage.people:audit``)
    only in ``usage_auditor``, which nobody has at install (M06).
    """
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    holders: dict[tuple[str, str], set[str]] = {}
    for role in await rbac.list_roles():
        for perm in role.permissions:
            holders.setdefault((perm.resource, perm.action), set()).add(role.name)
    assert holders[("usage", "read")] == {"admin", "auditor", "usage_auditor"}
    assert holders[("usage.people", "audit")] == {"usage_auditor"}
    usage_auditor = await rbac.get_role_by_name("usage_auditor")
    assert usage_auditor is not None and usage_auditor.is_builtin
    assert await rbac.count_role_users(usage_auditor.id) == 0
