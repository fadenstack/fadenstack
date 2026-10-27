"""Restrictions an extension keeps on a whole tenant, and what core does while nothing enforces one.

An extension marks the tenant (``set_restricted``) while it keeps a restriction there, and clears the mark
when it keeps none any more. Names core knows, and how each fails closed while no installed extension
enforces it (``registry.tenant_restrictions`` lists those that are):

- ``tool_mode_ceilings`` (Enterprise: the most a tenant, team, agent or person may pick as a chat's tool
  mode): every chat's tool mode is at most ``read_only``; the gateway applies it.

Administrators see a notice for each unenforced one and may lift it (the restriction's own data, kept by the
extension, is not touched; the extension may mark the tenant again when it is back).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.tenant_restrictions import TenantRestriction
from faden_backend.services.extensions import AdminNotice, ExtensionRegistry, registry

#: Tool mode ceilings: while unenforced, every chat's tools are at most read-only.
TOOL_MODE_CEILINGS = "tool_mode_ceilings"
#: The names core knows how to fail closed for; an extension marks only these.
KNOWN = frozenset({TOOL_MODE_CEILINGS})
#: The tenant the backend serves (as the agents' routes).
TENANT = "default"


async def set_restricted(session: AsyncSession, name: str, *, on: bool, tenant_id: str = TENANT) -> None:
    """Mark the tenant as restricted by *name*, or no longer: for the extension that keeps the restriction."""
    if name not in KNOWN:
        raise ValueError(f"Core does not know how to keep {name!r} closed without its extension")
    if on:
        await session.execute(
            pg_insert(TenantRestriction).values(tenant_id=tenant_id, name=name).on_conflict_do_nothing(),
        )
    else:
        await session.execute(
            delete(TenantRestriction).where(
                TenantRestriction.tenant_id == tenant_id, TenantRestriction.name == name,
            ),
        )


async def marked(session: AsyncSession, tenant_id: str = TENANT) -> set[str]:
    """The names the tenant is marked with, enforced or not."""
    rows = await session.scalars(
        select(TenantRestriction.name).where(TenantRestriction.tenant_id == tenant_id),
    )
    return set(rows)


async def unenforced(
    session: AsyncSession, reg: ExtensionRegistry = registry, tenant_id: str = TENANT,
) -> list[TenantRestriction]:
    """The tenant's restrictions no installed extension enforces, the oldest first."""
    rows = (await session.execute(
        select(TenantRestriction)
        .where(TenantRestriction.tenant_id == tenant_id)
        .order_by(TenantRestriction.since),
    )).scalars()
    return [row for row in rows if row.name not in reg.tenant_restrictions]


async def notices(session: Any) -> list[AdminNotice]:
    """Core's own notice source: one per unenforced restriction (the console offers to lift it)."""
    return [
        AdminNotice(code=f"restriction_{row.name}", severity="warning", params={"name": row.name})
        for row in await unenforced(session)
    ]
