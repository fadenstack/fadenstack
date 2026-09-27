"""Session-scoped tool availability service.

Builds the effective tool catalog for a session by merging MCP tools
with session execution policy and per-tool overrides.

An MCP tool is off in a chat until it is switched on -- by the user, or by
the model with get_available_tools (see ``system_tools``). They were all on,
and every one of them went to the model on every turn.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.db.models.gateway import ExecutionMode, ToolRealm
from faden_gateway.services.gateway.mcp_client import MCPClient
from faden_gateway.services.gateway.mcp_tool_cache import MCP_TOOL_PREFIX, MCPToolCache
from faden_gateway.services.gateway.policy_engine import PolicyAction, PolicyEngine
from faden_gateway.services.gateway.schemas import (
    ExecutionModeEnum,
    ToolAvailabilityDTO,
    ToolAvailabilityResponse,
)

logger = logging.getLogger(__name__)

# Realms permitted per execution mode.
_MODE_ALLOWED_REALMS: dict[str, set[str]] = {
    ExecutionMode.SERVER_ONLY: {
        ToolRealm.SERVER_MANAGED,
        ToolRealm.MCP_REMOTE,
    },
    ExecutionMode.LOCAL_ONLY: {
        ToolRealm.CLIENT_LOCAL,
        ToolRealm.CLIENT_PROXIED,
    },
    ExecutionMode.HYBRID: {
        ToolRealm.SERVER_MANAGED,
        ToolRealm.MCP_REMOTE,
        ToolRealm.CLIENT_LOCAL,
        ToolRealm.CLIENT_PROXIED,
    },
}


def _on_by_default(tool_id: str) -> bool:
    """MCP tools start off; a connected client's own tools start on."""
    return not tool_id.startswith(MCP_TOOL_PREFIX)


class ToolAvailabilityService:
    """Computes the effective tool catalog for a chat session."""

    def __init__(
        self,
        *,
        dao: GatewayDAO,
        mcp_client: MCPClient | None = None,
        mcp_tool_cache: MCPToolCache | None = None,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self._dao = dao
        self._mcp_client = mcp_client
        self._mcp_tool_cache = mcp_tool_cache
        self._policy_engine = policy_engine or PolicyEngine()

    async def get_global_catalog(
        self,
        *,
        tenant_id: str,
        execution_mode: str = ExecutionMode.SERVER_ONLY,
    ) -> ToolAvailabilityResponse:
        """Return the global tool catalog without requiring a session.

        Used for pre-session discovery so users can browse available tools
        before starting a conversation.  No session-specific overrides or
        policy checks are applied — every tool is shown with default state.
        """
        mode_str = execution_mode
        allowed_realms = _MODE_ALLOWED_REALMS.get(mode_str, set())

        raw_tools = await self._gather_tools(tenant_id, session_id=None)

        entries: list[ToolAvailabilityDTO] = []
        for tool in raw_tools:
            realm = tool.get("realm", "mcp_remote")
            tool_id = tool["tool_id"]
            available = tool.get("available", True)
            mode_allows = realm in allowed_realms
            user_enabled = _on_by_default(tool_id)
            effective_enabled = mode_allows and available and user_enabled

            reason = None
            if not mode_allows:
                reason = f"realm_not_allowed_in_{mode_str}_mode"
            elif not available:
                reason = "tool_unavailable"

            entries.append(
                ToolAvailabilityDTO(
                    tool_id=tool_id,
                    display_name=tool.get("display_name"),
                    description=tool.get("description"),
                    realm=realm,
                    source=tool.get("source", "mcp"),
                    effective_enabled=effective_enabled,
                    policy_allowed=True,
                    user_enabled=user_enabled,
                    available=available,
                    availability_reason=reason,
                ),
            )

        return ToolAvailabilityResponse(
            session_id=None,
            execution_mode=ExecutionModeEnum(mode_str),
            effective_catalog_version=0,
            tools=entries,
        )

    async def get_available_tools(
        self,
        *,
        session_id: uuid.UUID,
        tenant_id: str,
        include_disabled: bool = True,
        include_unavailable: bool = True,
        mcp_catalog: list[dict[str, Any]] | None = None,
    ) -> ToolAvailabilityResponse:
        """Return the merged effective tool catalog for a session.

        *mcp_catalog* is the MCP catalog when the caller has it already (a
        chat request does); it is fetched otherwise.
        """

        # 1. Resolve execution mode
        execution_mode = await self._dao.get_session_execution_mode(session_id)
        mode_str = execution_mode.value

        # 2. Load policy (catalog version)
        policy = await self._dao.get_session_execution_policy(session_id)
        catalog_version = policy.catalog_version if policy else 0

        # 3. Load user overrides
        overrides = await self._dao.get_session_tool_overrides(session_id)
        override_map: dict[str, bool] = {o.tool_id: o.enabled for o in overrides}

        # 4. Gather raw tools from all sources
        raw_tools = await self._gather_tools(
            tenant_id, session_id=session_id, mcp_catalog=mcp_catalog,
        )

        # 5. Compute effective state for each tool
        allowed_realms = _MODE_ALLOWED_REALMS.get(mode_str, set())
        entries: list[ToolAvailabilityDTO] = []

        for tool in raw_tools:
            realm = tool.get("realm", "mcp_remote")
            tool_id = tool["tool_id"]

            # Policy engine evaluation
            policy_decision = await self._policy_engine.evaluate(
                tool_id=tool_id,
                arguments={},
                session_id=session_id,
                tenant_id=tenant_id,
                realm=realm,
            )
            policy_allowed = policy_decision.action != PolicyAction.DENY

            # Mode realm filter
            mode_allows = realm in allowed_realms

            # User override
            user_enabled = override_map.get(tool_id, _on_by_default(tool_id))

            # Availability (connectivity/health)
            available = tool.get("available", True)

            # Effective enabled
            effective_enabled = (
                policy_allowed and mode_allows and user_enabled and available
            )

            # Why it cannot be used. A tool switched off can be: the switch
            # says so, and the model can switch it on.
            reason = None
            if not policy_allowed:
                reason = policy_decision.reason or "denied_by_policy"
            elif not mode_allows:
                reason = f"realm_not_allowed_in_{mode_str}_mode"
            elif not available:
                reason = "tool_unavailable"

            # Apply inclusion filters
            if not include_disabled and not effective_enabled:
                continue
            if not include_unavailable and not available:
                continue

            entries.append(
                ToolAvailabilityDTO(
                    tool_id=tool_id,
                    display_name=tool.get("display_name"),
                    description=tool.get("description"),
                    realm=realm,
                    source=tool.get("source", "mcp"),
                    effective_enabled=effective_enabled,
                    policy_allowed=policy_allowed,
                    user_enabled=user_enabled,
                    available=available,
                    availability_reason=reason,
                ),
            )

        return ToolAvailabilityResponse(
            session_id=str(session_id),
            execution_mode=ExecutionModeEnum(mode_str),
            effective_catalog_version=catalog_version,
            tools=entries,
        )

    async def chat_mcp_tools(
        self,
        *,
        session_id: uuid.UUID,
        tenant_id: str,
        mcp_catalog: list[dict[str, Any]],
    ) -> tuple[set[str], set[str]]:
        """The MCP tools of *mcp_catalog* a chat may use, and those switched on.

        One may be used when the policy and the chat's execution mode allow
        it and it is available; switched on, it is also offered to the model.
        """
        catalog = await self.get_available_tools(
            session_id=session_id, tenant_id=tenant_id, mcp_catalog=mcp_catalog,
        )
        usable = {
            t.tool_id for t in catalog.tools
            if t.tool_id.startswith(MCP_TOOL_PREFIX) and t.availability_reason is None
        }
        return usable, {t.tool_id for t in catalog.tools if t.tool_id in usable and t.user_enabled}

    async def _gather_tools(
        self,
        tenant_id: str,
        session_id: uuid.UUID | None = None,
        mcp_catalog: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Collect tools from all registered sources into a flat list."""
        tools: list[dict[str, Any]] = []

        # MCP tools
        if mcp_catalog is not None or self._mcp_tool_cache is not None:
            try:
                catalog = mcp_catalog
                if catalog is None:
                    assert self._mcp_tool_cache is not None  # noqa: S101
                    catalog = await self._mcp_tool_cache.get_tools(tenant_id)
                for entry in catalog:
                    qualified_name = entry.get("qualified_name", "")
                    openai_tool = entry.get("openai_tool", {})
                    func_spec = openai_tool.get("function", {})

                    tools.append({
                        "tool_id": qualified_name,
                        "display_name": func_spec.get("name", qualified_name),
                        "description": func_spec.get("description", ""),
                        "realm": entry.get("realm", "mcp_remote"),
                        "source": entry.get("source", "mcp"),
                        "available": True,
                    })
            except Exception:
                logger.debug("Failed to load MCP tools for catalog", exc_info=True)

        # Client-local tools (from connected agents)
        if session_id is not None:
            try:
                capabilities = await self._dao.get_session_client_capabilities(session_id)
                for cap in capabilities:
                    tools.append({
                        "tool_id": cap.tool_id,
                        "display_name": cap.tool_id.rsplit(".", 1)[-1],
                        "description": (cap.schema_json or {}).get("description", ""),
                        "realm": cap.realm.value if hasattr(cap.realm, "value") else str(cap.realm),
                        "source": "local_agent",
                        "available": cap.available,
                    })
            except Exception:
                logger.debug("Failed to load client capabilities", exc_info=True)

        return tools
