"""Core module registrations.

Registers all built-in optional modules into the :data:`module_registry`
singleton.  Called once during backend startup (from ``lifespan_setup``).

Each module owns its metadata **and** its lifecycle sync callback so
``views.py`` never hard-codes module names.
"""

from __future__ import annotations

import logging
from typing import Any

from faden_backend.services.module_registry import ModuleDef, module_registry
from faden_backend.services.system_settings import SystemSettingsService
from faden_backend.settings import settings

logger = logging.getLogger(__name__)


# ── Sync callbacks ────────────────────────────────────────────────────
# Each function matches the SyncCallback signature:
#   (service, enabled, actor_id) -> list[str]


async def _sync_pii_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``faden_gateway.pii_enabled`` and trigger gateway apply flow."""
    try:
        result = await service.update_value(
            key="faden_gateway.pii_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync faden_gateway.pii_enabled")
        return [f"Failed to sync faden_gateway.pii_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply faden_gateway.pii_enabled={enabled}: {details}"]
    return []


async def _sync_rag_lite_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``rag_lite.enabled`` as module lifecycle flag."""
    try:
        result = await service.update_value(
            key="rag_lite.enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync rag_lite.enabled")
        return [f"Failed to sync rag_lite.enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply rag_lite.enabled={enabled}: {details}"]
    return []


async def _sync_sessions_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``faden_gateway.sessions_enabled`` as module lifecycle flag."""
    try:
        result = await service.update_value(
            key="faden_gateway.sessions_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync faden_gateway.sessions_enabled")
        return [f"Failed to sync faden_gateway.sessions_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply faden_gateway.sessions_enabled={enabled}: {details}"]
    return []


async def _sync_mcp_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``faden_gateway.mcp_enabled`` and trigger gateway apply flow."""
    try:
        result = await service.update_value(
            key="faden_gateway.mcp_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync faden_gateway.mcp_enabled")
        return [f"Failed to sync faden_gateway.mcp_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply faden_gateway.mcp_enabled={enabled}: {details}"]
    return []


async def _sync_skills_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``faden_gateway.skills_enabled`` and trigger gateway apply flow."""
    try:
        result = await service.update_value(
            key="faden_gateway.skills_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync faden_gateway.skills_enabled")
        return [f"Failed to sync faden_gateway.skills_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply faden_gateway.skills_enabled={enabled}: {details}"]
    return []


async def _sync_provenance_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``faden_gateway.provenance_enabled`` and trigger gateway apply flow."""
    try:
        result = await service.update_value(
            key="faden_gateway.provenance_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync faden_gateway.provenance_enabled")
        return [f"Failed to sync faden_gateway.provenance_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply faden_gateway.provenance_enabled={enabled}: {details}"]
    return []


#: What switching the provenance module off loses; the console shows its own translation of it.
PROVENANCE_DISABLE_WARNING = (
    "Generated content will no longer be marked as AI-generated. The EU AI Act (Art. 50(2)) requires "
    "content generated by AI systems to be marked in a machine-readable format as artificially generated."
)


# ── Registration ──────────────────────────────────────────────────────


def register_core_modules() -> None:
    """Register all built-in optional modules.

    Safe to call multiple times (e.g. in tests) — silently skips
    modules that are already registered.
    """
    _defs: list[ModuleDef] = [
        ModuleDef(
            name="chat",
            display_name="Chat & Sessions",
            description=(
                "Session-aware chat with message history, rolling summaries, "
                "memory facts, and file attachments. Managed by the gateway."
            ),
            module_type="plugin",
            settings_flag="sessions_enabled",
            is_available_fn=lambda: settings.sessions_enabled,
            on_enable=_sync_sessions_enabled,
            on_disable=_sync_sessions_enabled,
        ),
        ModuleDef(
            name="rag_lite",
            display_name="RAG Lite",
            description=(
                "Embedded Retrieval-Augmented Generation with pgvector. "
                "Upload documents, chunk, embed, and search — no external "
                "RAG service required."
            ),
            module_type="plugin",
            settings_flag="rag_lite_enabled",
            is_available_fn=lambda: settings.rag_lite_enabled,
            on_enable=_sync_rag_lite_enabled,
            on_disable=_sync_rag_lite_enabled,
        ),
        ModuleDef(
            name="pii",
            display_name="PII Guard",
            description=(
                "Personally Identifiable Information detection and redaction "
                "service for request / response payloads."
            ),
            module_type="container",
            settings_flag="pii_enabled",
            health_url_fn=lambda: f"{settings.pii_service_url}/health",
            compose_profile="pii",
            compose_services=[
                "faden-pii",
                "faden-pii-worker",
                "faden-pii-migrator",
            ],
            container_names=[
                "faden-pii",
                "faden-pii-worker",
            ],
            on_enable=_sync_pii_enabled,
            on_disable=_sync_pii_enabled,
        ),
        ModuleDef(
            name="mcp",
            display_name="MCP Tool Registry",
            description=(
                "Model Context Protocol server registry — registers external "
                "MCP servers, discovers tools, and routes tool calls through "
                "a governed privacy proxy."
            ),
            module_type="container",
            settings_flag="mcp_enabled",
            health_url_fn=lambda: f"{settings.mcp_service_url}/api/health",
            compose_profile="mcp",
            compose_services=[
                "faden-mcp",
                "faden-mcp-migrator",
            ],
            container_names=[
                "faden-mcp",
            ],
            on_enable=_sync_mcp_enabled,
            on_disable=_sync_mcp_enabled,
        ),
        ModuleDef(
            name="skills",
            display_name="Skills Registry",
            description=(
                "Centralized skill management — create, version, and assign "
                "reusable LLM instruction sets (skills) with YAML frontmatter "
                "and Markdown body. Skills are injected into the gateway "
                "pipeline as system prompts."
            ),
            module_type="container",
            settings_flag="skills_enabled",
            health_url_fn=lambda: f"{settings.skills_service_url}/api/health",
            compose_profile="skills",
            compose_services=[
                "faden-skills",
                "faden-skills-migrator",
            ],
            container_names=[
                "faden-skills",
            ],
            on_enable=_sync_skills_enabled,
            on_disable=_sync_skills_enabled,
        ),
        ModuleDef(
            name="provenance",
            display_name="Provenance",
            description=(
                "Marks AI-generated content as such in a machine-readable way "
                "(EU AI Act Art. 50(2)). On by default."
            ),
            module_type="container",
            settings_flag="provenance_enabled",
            health_url_fn=lambda: f"{settings.provenance_service_url}/api/health",
            compose_profile="provenance",
            compose_services=[
                "faden-provenance",
                "faden-provenance-migrator",
            ],
            container_names=[
                "faden-provenance",
            ],
            on_enable=_sync_provenance_enabled,
            on_disable=_sync_provenance_enabled,
            disable_warning=PROVENANCE_DISABLE_WARNING,
        ),
    ]

    for mod in _defs:
        if mod.name not in module_registry:
            module_registry.register(mod)
