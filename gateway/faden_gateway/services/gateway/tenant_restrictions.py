"""Restrictions an extension keeps on a whole tenant, applied while nothing installed enforces one.

The backend keeps them (its ``tenant_restrictions`` table; ``faden_backend/services/tenant_restrictions.py``
says what each name means), and an installed extension that enforces one lists it in
``registry.tenant_restrictions``. The gateway reads a tenant's names over a small pool of its own, kept for
:data:`TTL_S` seconds, and fails closed for each name nothing enforces:

- ``tool_mode_ceilings``: every chat's tool mode is at most ``read_only`` (a ceiling, ``tool_classes``).

When the names cannot be read the ceiling fails, which counts as ``read_only`` (core's rule for a ceiling
that fails): a restriction that cannot be checked must not widen what tools may do.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from faden_gateway.services.extensions import ExtensionRegistry
from faden_gateway.services.gateway.tool_classes import CeilingContext, ToolMode

logger = logging.getLogger(__name__)

#: Tool mode ceilings: while unenforced, every chat's tools are at most read-only.
TOOL_MODE_CEILINGS = "tool_mode_ceilings"
#: How long a tenant's names are kept, in seconds.
TTL_S = 30.0
_SQL = "SELECT name FROM tenant_restrictions WHERE tenant_id = $1"


class TenantRestrictions:
    """A tenant's restriction names from the backend database, and the ceiling they make."""

    def __init__(self, dsn: str, registry: ExtensionRegistry, *, timeout_s: float = 2.0) -> None:
        self._dsn = dsn
        self._registry = registry
        self._timeout = timeout_s
        self._pool: Any = None
        self._lock = asyncio.Lock()
        self._kept: dict[str, tuple[frozenset[str], float]] = {}

    async def names(self, tenant_id: str) -> frozenset[str]:
        """The names *tenant_id* is marked with (raises when they cannot be read)."""
        now = time.monotonic()
        kept = self._kept.get(tenant_id)
        if kept is not None and kept[1] > now:
            return kept[0]
        pool = await self._get_pool()
        rows = await pool.fetch(_SQL, tenant_id, timeout=self._timeout)
        names = frozenset(str(row["name"]) for row in rows)
        self._kept[tenant_id] = (names, now + TTL_S)
        return names

    async def tool_mode_ceiling(self, ctx: CeilingContext) -> ToolMode | None:
        """``read_only`` while the tenant has tool mode ceilings nothing installed enforces; else none."""
        if TOOL_MODE_CEILINGS in self._registry.tenant_restrictions:
            return None
        if TOOL_MODE_CEILINGS in await self.names(ctx.tenant_id):
            return ToolMode.READ_ONLY
        return None

    def forget(self) -> None:
        """Drop what is kept (tests)."""
        self._kept.clear()

    async def close(self) -> None:
        """Close the pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self) -> Any:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    import asyncpg  # noqa: PLC0415

                    self._pool = await asyncpg.create_pool(
                        self._dsn, min_size=0, max_size=2, timeout=self._timeout,
                    )
        return self._pool


def from_settings(settings: Any, registry: ExtensionRegistry) -> TenantRestrictions | None:
    """Over the backend database the gateway's settings name; ``None`` when they name none."""
    backend_db = getattr(settings, "backend_db_base", "")
    if not backend_db:
        return None
    return TenantRestrictions(
        f"postgresql://{settings.db_user}:{settings.db_pass}@{settings.db_host}:{settings.db_port}/{backend_db}",
        registry,
    )
