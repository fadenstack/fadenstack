"""Shared runtime settings key mapping.

Centralizes DB setting-key -> ``settings`` attribute mapping so both startup
hydration and live apply paths use the same source of truth.
"""

from __future__ import annotations

from typing import Any

_RUNTIME_VALUE_KEY_MAP: dict[str, str] = {
    "faden_gateway.pii_enabled": "pii_enabled",
    "faden_gateway.pii_service_url": "pii_service_url",
    "faden_gateway.mcp_enabled": "mcp_enabled",
    "faden_gateway.mcp_service_url": "mcp_service_url",
    "faden_gateway.skills_enabled": "skills_enabled",
    "faden_gateway.skills_service_url": "skills_service_url",
    "faden_gateway.provenance_enabled": "provenance_enabled",
    "faden_gateway.provenance_service_url": "provenance_service_url",
    "faden_gateway.sessions_enabled": "sessions_enabled",
    "rag_lite.enabled": "rag_lite_enabled",
    "rag_lite.embedding_provider_id": "rag_lite_embedding_provider_id",
    "rag_lite.embedding_model": "rag_lite_embedding_model",
    "rag_lite.embedding_dim": "rag_lite_embedding_dim",
    "rag_lite.chunk_max_tokens": "rag_lite_chunk_max_tokens",
    "rag_lite.chunk_overlap_tokens": "rag_lite_chunk_overlap_tokens",
    "rag_lite.file_store_root": "rag_lite_file_store_root",
    "rag_lite.upload_max_file_mb": "rag_lite_upload_max_file_mb",
    "rag_lite.hybrid_search": "rag_lite_hybrid_search",
    "rag_lite.rerank_provider_id": "rag_lite_rerank_provider_id",
    "rag_lite.rerank_model": "rag_lite_rerank_model",
    "rag_lite.rerank_template": "rag_lite_rerank_template",
    "rag_lite.rerank_candidates": "rag_lite_rerank_candidates",
    "llm.residency.internal_networks": "residency_internal_networks",
    "users.login_lifetime_s": "users_login_lifetime_s",
    "users.api_token_default_lifetime_s": "api_token_default_lifetime_s",
    "users.api_token_max_lifetime_s": "api_token_max_lifetime_s",
    "agents.access_token_ttl_s": "agent_access_token_ttl_s",
    "agents.refresh_token_ttl_s": "agent_refresh_token_ttl_s",
    "agents.refresh_idle_s": "agent_refresh_idle_s",
    "updates.check_enabled": "updates_check_enabled",
    "updates.channel": "updates_channel",
    "updates.feed_url": "updates_feed_url",
    "updates.proxy_url": "updates_proxy_url",
}

_RUNTIME_SECRET_KEY_MAP: dict[str, str] = {
    "faden_backend.users_secret": "users_secret",
    "faden_gateway.mcp_service_token": "mcp_service_token",
    "faden_gateway.skills_service_token": "skills_service_token",
    "faden_gateway.provenance_service_token": "provenance_service_token",
}


#: Keys whose value goes onto an extension's own settings object rather than core's ``settings``.
_RUNTIME_TARGETS: dict[str, Any] = {}

#: Moves with each registration, so a process loads a key registered after it last loaded the settings.
_GENERATION = 0


def register_runtime_value_key(key: str, attr: str, *, target: Any = None) -> None:
    """Register additional runtime value mapping (for EE/plugin extension); onto *target* when given."""
    global _GENERATION  # noqa: PLW0603
    _RUNTIME_VALUE_KEY_MAP[key] = attr
    if target is not None:
        _RUNTIME_TARGETS[key] = target
    _GENERATION += 1


def register_runtime_secret_key(key: str, attr: str, *, target: Any = None) -> None:
    """Register additional runtime secret mapping (for EE/plugin extension); onto *target* when given."""
    global _GENERATION  # noqa: PLW0603
    _RUNTIME_SECRET_KEY_MAP[key] = attr
    if target is not None:
        _RUNTIME_TARGETS[key] = target
    _GENERATION += 1


def registry_generation() -> int:
    """How many keys have been registered so far: a process that loaded at another count loads again."""
    return _GENERATION


def runtime_target(key: str) -> Any:
    """The object *key*'s value is set on: core's ``settings``, or the one an extension registered for it."""
    if key in _RUNTIME_TARGETS:
        return _RUNTIME_TARGETS[key]
    from faden_backend.settings import settings  # noqa: PLC0415

    return settings


def get_runtime_value_key_map() -> dict[str, str]:
    """Return mutable value-key map used by runtime hydration/apply paths."""
    return _RUNTIME_VALUE_KEY_MAP


def get_runtime_secret_key_map() -> dict[str, str]:
    """Return mutable secret-key map used by runtime hydration/apply paths."""
    return _RUNTIME_SECRET_KEY_MAP


def resolve_runtime_attr(key: str) -> str | None:
    """Resolve DB setting key to ``settings`` attribute name."""
    return _RUNTIME_VALUE_KEY_MAP.get(key) or _RUNTIME_SECRET_KEY_MAP.get(key)


async def refresh_runtime_values(session: Any, *, prefix: str) -> None:
    """Bring this process's runtime settings up to date with the database, read through *session*.

    A live-reload setting used to be applied only in the process that handled the change: after RAG Lite's
    embedding provider was changed, the taskiq worker still embedded documents with the old one while searches
    used the new one. Each request and task now takes up the changes before it starts
    (``runtime_sync.py``); this checks once more, for every key (values and secrets, *prefix* is no longer
    needed), and costs one read when nothing changed.
    """
    from faden_backend.services.system_settings import runtime_sync  # noqa: PLC0415

    await runtime_sync.current(session)

