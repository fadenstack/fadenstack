"""Restrictions on a whole tenant nothing enforces: tool mode ceilings fail closed at read_only."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from faden_gateway.services.extensions import ExtensionRegistry
from faden_gateway.services.gateway import tenant_restrictions, tool_classes
from faden_gateway.services.gateway.tool_classes import CeilingContext, ToolMode

pytestmark = pytest.mark.anyio

CTX = CeilingContext(tenant_id="default", user_id="u-1")


class _Pool:
    """The backend database as far as the names go: rows per tenant, or a failure."""

    def __init__(self, names: dict[str, list[str]] | None = None, *, broken: bool = False) -> None:
        self.names = names or {}
        self.broken = broken
        self.reads = 0

    async def fetch(self, _sql: str, tenant_id: str, timeout: float) -> list[dict[str, Any]]:
        assert timeout > 0
        self.reads += 1
        if self.broken:
            msg = "the backend database is down"
            raise ConnectionError(msg)
        return [{"name": name} for name in self.names.get(tenant_id, [])]

    async def close(self) -> None:
        return None


def _restrictions(pool: _Pool, registry: ExtensionRegistry | None = None) -> tenant_restrictions.TenantRestrictions:
    found = tenant_restrictions.TenantRestrictions("postgresql://unused", registry or ExtensionRegistry())
    found._pool = pool  # noqa: SLF001 -- the database stands in
    return found


async def test_a_tenant_with_ceilings_nothing_enforces_gets_read_only() -> None:
    pool = _Pool({"default": ["tool_mode_ceilings"]})
    found = _restrictions(pool)
    assert await tool_classes.ceiling([found.tool_mode_ceiling], CTX) == ToolMode.READ_ONLY
    picked = await tool_classes.effective(ToolMode.AUTO, [found.tool_mode_ceiling], CTX)
    assert (picked.mode, picked.chosen, picked.ceiling) == (ToolMode.READ_ONLY, ToolMode.AUTO, ToolMode.READ_ONLY)
    await found.tool_mode_ceiling(CTX)
    assert pool.reads == 1, "kept for half a minute"


async def test_none_where_the_tenant_has_none_or_an_extension_enforces_them() -> None:
    assert await _restrictions(_Pool()).tool_mode_ceiling(CTX) is None
    other = CeilingContext(tenant_id="other", user_id="u-1")
    assert await _restrictions(_Pool({"default": ["tool_mode_ceilings"]})).tool_mode_ceiling(other) is None

    enforcing = ExtensionRegistry(tenant_restrictions={"tool_mode_ceilings"})
    pool = _Pool({"default": ["tool_mode_ceilings"]})
    assert await _restrictions(pool, enforcing).tool_mode_ceiling(CTX) is None
    assert pool.reads == 0, "not even read"


async def test_names_that_cannot_be_read_count_as_read_only() -> None:
    found = _restrictions(_Pool(broken=True))
    assert await tool_classes.ceiling([found.tool_mode_ceiling], CTX) == ToolMode.READ_ONLY


def test_only_with_a_backend_database() -> None:
    assert tenant_restrictions.from_settings(SimpleNamespace(backend_db_base=""), ExtensionRegistry()) is None
    named = SimpleNamespace(
        backend_db_base="faden_backend", db_user="u", db_pass="p", db_host="h", db_port=5432,  # noqa: S106
    )
    assert tenant_restrictions.from_settings(named, ExtensionRegistry()) is not None
