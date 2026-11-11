"""The gateway's tool catalog: one query for the servers, none per tool.

Every chat turn fetched it, and it loaded the tool's whole server per tool --
with all that server's tools: 48 GitHub tools were 48 queries of 48 tools. A
disabled server's tools were offered too, and each call to one was refused.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from faden_mcp.services.dao import MCPDao
from faden_mcp.web.api.internal import views

ON, OFF = uuid.uuid4(), uuid.uuid4()


def _tool(name: str, server_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(
        qualified_name=name,
        openai_schema_json={"type": "function", "function": {"name": name}},
        server_id=server_id,
    )


@pytest.mark.anyio
async def test_the_catalog_reads_the_servers_once_and_skips_disabled_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each tool carries its server's PII mode; a disabled server's tools go."""

    async def tools(
        self: MCPDao, tenant_id: str, **_: Any,
    ) -> list[SimpleNamespace]:
        return [
            _tool("mcp.a.one", ON),
            _tool("mcp.b.two", OFF),
            _tool("mcp.a.three", ON),
        ]

    async def modes(self: MCPDao, tenant_id: str) -> dict[uuid.UUID, str]:
        return {ON: "allow"}  # OFF is disabled: not among the enabled servers

    async def per_tool(self: MCPDao, server_id: uuid.UUID) -> None:
        raise AssertionError("the server was fetched per tool")

    async def version(request: Any, tenant_id: str) -> int:
        return 3

    monkeypatch.setattr(MCPDao, "list_tools_by_tenant", tools)
    monkeypatch.setattr(MCPDao, "server_pii_modes", modes)
    monkeypatch.setattr(MCPDao, "get_server", per_tool)
    monkeypatch.setattr(views, "_get_catalog_version", version)

    catalog = await views.get_tool_catalog(
        request=SimpleNamespace(), tenant_id="default", session=SimpleNamespace(),
    )
    assert catalog.catalog_version == 3
    assert [(e.qualified_name, e.pii_mode) for e in catalog.tools] == [
        ("mcp.a.one", "allow"),
        ("mcp.a.three", "allow"),
    ]


def test_the_catalog_entry_carries_the_servers_annotations() -> None:
    """The gateway classifies tools; it gets the server's hints (and trusts them only where told)."""
    from faden_mcp.web.api.internal.views import ToolCatalogEntry  # noqa: PLC0415

    entry = ToolCatalogEntry(
        qualified_name="mcp.github.get_issue", openai_tool={}, server_id="s", pii_mode="redact",
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    assert entry.model_dump()["annotations"] == {"readOnlyHint": True, "openWorldHint": True}


@pytest.mark.anyio
async def test_the_listing_names_every_tool_with_its_server_and_whether_it_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Switched-off tools and tools of a disabled server are listed, as off; nothing else leaves."""

    def tool(name: str, server_id: uuid.UUID, *, enabled: bool = True, description: Any = "Does it.") -> SimpleNamespace:
        row = _tool(name, server_id)
        row.openai_schema_json = {"type": "function", "function": {"name": name, "description": description,
                                                                   "parameters": {"secret": 1}}}
        row.enabled = enabled
        return row

    async def tools(self: MCPDao, tenant_id: str, **kwargs: Any) -> list[SimpleNamespace]:
        assert kwargs == {"enabled_only": False}
        return [
            tool("mcp.gh.one", ON),
            tool("mcp.gh.two", ON, enabled=False),
            tool("mcp.old.three", OFF),
            tool("mcp.gh.four", ON, description=None),
            tool("mcp.gone.five", uuid.uuid4()),
        ]

    async def labels(self: MCPDao, tenant_id: str) -> dict[uuid.UUID, tuple[str, bool]]:
        return {ON: ("GitHub", True), OFF: ("Old server", False)}

    monkeypatch.setattr(MCPDao, "list_tools_by_tenant", tools)
    monkeypatch.setattr(MCPDao, "server_labels", labels)

    listing = await views.get_tool_listing(tenant_id="default", session=SimpleNamespace())
    assert [e.model_dump() for e in listing.tools] == [
        {"qualified_name": "mcp.gh.one", "server_name": "GitHub", "description": "Does it.", "enabled": True},
        {"qualified_name": "mcp.gh.two", "server_name": "GitHub", "description": "Does it.", "enabled": False},
        {"qualified_name": "mcp.old.three", "server_name": "Old server", "description": "Does it.",
         "enabled": False},
        {"qualified_name": "mcp.gh.four", "server_name": "GitHub", "description": "", "enabled": True},
    ]
    assert "secret" not in listing.model_dump_json()
