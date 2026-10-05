from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.db.dao.session_dao import SessionDAO
from faden_gateway.db.models.gateway import TURNS_RETRYABLE, ChatMessage, TurnStatus
from faden_gateway.services.gateway import faden_events, tool_classes
from faden_gateway.services.gateway.agents import AgentRequest
from faden_gateway.services.gateway.audit import AuditService
from faden_gateway.services.gateway.auth import (
    PII_WEAKEN_HEADER,
    AuthContext,
    get_auth_context,
    pii_weaken_granted,
)
from faden_gateway.services.gateway.errors import GatewayError, error_from, error_response
from faden_gateway.services.gateway.lease import LeaseManager
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from faden_gateway.services.gateway.observability import GatewayObservability
from faden_gateway.services.gateway.mcp_client import MCPClient
from faden_gateway.services.gateway.mcp_tool_cache import MCPToolCache
from faden_gateway.services.gateway.pii_client import PIIClient
from faden_gateway.services.gateway.skills_client import SkillsClient
from faden_gateway.services.gateway.proxy import UpstreamProxy
from faden_gateway.services.gateway.ratelimit import RateLimiter
from faden_gateway.services.gateway.routing import RouterService
from faden_gateway.services.gateway.schemas import (
    ChatCompletionRequest,
    EmbeddingsRequest,
    RerankRequest,
    SessionPIIPolicyDTO,
    SessionPIIPolicyPatchDTO,
    SessionPIIOverrideDTO,
    SessionToolPolicyDTO,
    SessionToolPolicyPatchDTO,
    ToolAvailabilityResponse,
)
from faden_gateway.services.gateway.service import GatewayService, StreamingGatewayResponse
from faden_gateway.services.gateway.stream_buffer import StreamBuffer
from faden_gateway.services.gateway.turn_log import TurnLog
from faden_gateway.services.registry import service_registry
from faden_gateway.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
async def public_health_check() -> None:
    """Public health endpoint for L7 probes."""


@router.get("/v1/services")
async def list_services(request: Request) -> JSONResponse:
    """Return the manifest of optional service modules.

    The frontend uses this to discover which features (PII, Auth, RAG, ...)
    are available and healthy so it can show/hide UI sections accordingly.
    """
    registry = request.app.state.service_registry
    # Run async health checks against enabled services
    await registry.check_health(request.app.state.http_client)
    return JSONResponse(status_code=200, content=registry.to_dict())


def get_gateway_service(
    request: Request,
    dao: GatewayDAO = Depends(),
    session_dao: SessionDAO = Depends(),
) -> GatewayService:
    """Build gateway service with request-scoped dependencies."""
    cache = request.app.state.cache_backend
    lease_manager = LeaseManager(cache, ttl_sec=settings.lease_ttl_sec)
    router_service = RouterService(
        dao=dao,
        cache=cache,
        lease_manager=lease_manager,
        capacity_wait_sec=settings.capacity_wait_sec,
    )
    proxy = UpstreamProxy(client=request.app.state.http_client)
    limiter = RateLimiter(cache)
    audit = AuditService(dao, pricing_service=getattr(request.app.state, "pricing_service", None))
    observability: GatewayObservability = request.app.state.gateway_observability

    # PII client (optional - only when PII module is enabled in registry)
    pii_client: PIIClient | None = None
    pii_url = service_registry.get_url("pii")
    if pii_url:
        pii_client = PIIClient(
            base_url=pii_url,
            http_client=request.app.state.http_client,
        )

    # MCP client + cache (optional - when MCP module is enabled in registry)
    mcp_client: MCPClient | None = None
    mcp_tool_cache: MCPToolCache | None = None
    mcp_url = service_registry.get_url("mcp")
    if mcp_url and settings.mcp_service_token:
        mcp_client = MCPClient(
            base_url=mcp_url,
            http_client=request.app.state.http_client,
            service_token=settings.mcp_service_token,
        )
        mcp_tool_cache = _shared_tool_cache(request, mcp_client)

    # Skills client (optional - when Skills module is enabled in registry)
    skills_client: SkillsClient | None = None
    skills_url = service_registry.get_url("skills")
    if skills_url and settings.skills_service_token:
        skills_client = SkillsClient(
            base_url=skills_url,
            http_client=request.app.state.http_client,
            service_token=settings.skills_service_token,
        )

    # RAG Lite client (optional - when RAG Lite is enabled)
    from faden_gateway.services.gateway.rag_lite_client import RagLiteClient  # noqa: PLC0415

    rag_lite_client: RagLiteClient | None = None
    if settings.rag_lite_enabled and not settings.rag_enabled:
        rag_lite_client = RagLiteClient(
            base_url=settings.rag_lite_backend_url,
            http_client=request.app.state.http_client,
        )

    # Tool Router (optional – when MCP or client tools are available)
    from faden_gateway.services.gateway.tool_router import (  # noqa: PLC0415
        ServerToolExecutor,
        ToolRouter,
        ToolRouterConfig,
    )
    from faden_gateway.services.gateway.client_broker import ClientToolBroker  # noqa: PLC0415
    from faden_gateway.services.gateway.policy_engine import PolicyEngine  # noqa: PLC0415

    tool_router: ToolRouter | None = None
    if mcp_client:
        server_executor = ServerToolExecutor(mcp_client)
        client_broker = ClientToolBroker()
        policy_engine = PolicyEngine()
        tool_router = ToolRouter(
            dao=dao,
            config=ToolRouterConfig(
                server_executor=server_executor,
                client_broker=client_broker,
                policy_engine=policy_engine,
            ),
        )

    service = GatewayService(
        dao=dao,
        router=router_service,
        proxy=proxy,
        adapter=LLMAdapter(http_client=request.app.state.http_client),
        limiter=limiter,
        audit=audit,
        observability=observability,
        pii_client=pii_client,
        rag_lite_client=rag_lite_client,
        session_dao=session_dao if settings.sessions_enabled else None,
        file_store=getattr(request.app.state, "chat_file_store", None),
        mcp_client=mcp_client,
        mcp_tool_cache=mcp_tool_cache,
        skills_client=skills_client,
        tool_router=tool_router,
        usage_groups=getattr(request.app.state, "usage_group_resolver", None),
        group_memberships=getattr(request.app.state, "group_membership_resolver", None),
    )
    service.stream_buffer = _stream_buffer(request)
    return service


def _stream_buffer(request: Request) -> StreamBuffer:
    """The app's one stream buffer: what a chat page's reply is read from."""
    buffer = getattr(request.app.state, "stream_buffer", None)
    if buffer is None:
        log = TurnLog.of(getattr(request.app.state, "cache_backend", None))
        buffer = request.app.state.stream_buffer = StreamBuffer(log=log)
    return buffer


def _shared_tool_cache(request: Request, mcp_client: MCPClient) -> MCPToolCache:
    """The app's one MCP tool cache.

    It was built per request, so it held nothing: every chat turn fetched the
    whole catalog from the MCP service (~90 ms idle with 13 tools).
    """
    cache = getattr(request.app.state, "mcp_tool_cache", None)
    if cache is None:
        cache = request.app.state.mcp_tool_cache = MCPToolCache(mcp_client)
    return cache


@router.get("/v1/models")
async def list_models(
    auth: AuthContext = Depends(get_auth_context),
    service: GatewayService = Depends(get_gateway_service),
) -> JSONResponse:
    """List model aliases available for tenant."""
    try:
        payload = await service.list_models(auth)
        return JSONResponse(status_code=200, content=payload)
    except GatewayError as exc:
        return error_from(exc)


# ── Tool Availability ────────────────────────────────────────────


@router.get("/v1/tools/catalog", response_model=ToolAvailabilityResponse)
async def get_tool_catalog(
    request: Request,
    execution_mode: str = "server_only",
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Return the global tool catalog (no session required).

    Used for pre-session discovery so users can browse available tools
    before starting a conversation.
    """
    from faden_gateway.services.gateway.tool_availability import (  # noqa: PLC0415
        ToolAvailabilityService,
    )

    mcp_client = None
    mcp_tool_cache = None
    mcp_url = service_registry.get_url("mcp")
    if mcp_url and settings.mcp_service_token:

        mcp_client = MCPClient(
            base_url=mcp_url,
            http_client=request.app.state.http_client,
            service_token=settings.mcp_service_token,
        )
        mcp_tool_cache = _shared_tool_cache(request, mcp_client)

    service = ToolAvailabilityService(
        dao=dao,
        mcp_client=mcp_client,
        mcp_tool_cache=mcp_tool_cache,
    )

    result = await service.get_global_catalog(
        tenant_id=auth.tenant_id,
        execution_mode=execution_mode,
        tool_mode=await _effective_tool_mode(request, auth, None, None),
        class_rules=await tool_classes.class_rules.for_tenant(dao.session, auth.tenant_id),
    )
    return JSONResponse(
        status_code=200,
        content=result.model_dump(mode="json"),
    )


@router.get("/v1/tools/available", response_model=ToolAvailabilityResponse)
async def get_available_tools(
    request: Request,
    session_id: str,
    include_disabled: bool = True,
    include_unavailable: bool = True,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Return the effective tool catalog for a session.

    Merges MCP tools with session execution policy and per-tool overrides
    to compute effective availability for each tool.
    """
    from faden_gateway.services.gateway.tool_availability import (  # noqa: PLC0415
        ToolAvailabilityService,
    )

    # Build MCP client + cache (same pattern as get_gateway_service)
    mcp_client = None
    mcp_tool_cache = None
    mcp_url = service_registry.get_url("mcp")
    if mcp_url and settings.mcp_service_token:

        mcp_client = MCPClient(
            base_url=mcp_url,
            http_client=request.app.state.http_client,
            service_token=settings.mcp_service_token,
        )
        mcp_tool_cache = _shared_tool_cache(request, mcp_client)

    service = ToolAvailabilityService(
        dao=dao,
        mcp_client=mcp_client,
        mcp_tool_cache=mcp_tool_cache,
    )

    try:
        import uuid as _uuid

        sid = _uuid.UUID(session_id)
    except ValueError:
        return error_response(
            status_code=400,
            message="Invalid session_id format.",
            error_type="invalid_request_error",
            code="invalid_session_id",
        )

    # The chat's tools are the user's own business.
    await _require_session_owner(sid, auth, dao)
    policy = await dao.get_session_execution_policy(sid)
    result = await service.get_available_tools(
        session_id=sid,
        tenant_id=auth.tenant_id,
        include_disabled=include_disabled,
        include_unavailable=include_unavailable,
        tool_mode=await _effective_tool_mode(request, auth, session_id, policy.tool_mode if policy else None),
        class_rules=await tool_classes.class_rules.for_tenant(dao.session, auth.tenant_id),
    )
    return JSONResponse(
        status_code=200,
        content=result.model_dump(mode="json"),
    )


# ── Session Tool Policy ─────────────────────────────────────────


async def _effective_tool_mode(
    request: Request, auth: AuthContext, session_id: str | None, stored: str | None,
) -> tool_classes.EffectiveMode:
    """A chat's tool mode as the user picked it (Ask until they do), and after any ceiling (EE)."""
    from faden_gateway.services.extensions import registry  # noqa: PLC0415

    groups: tuple[str, ...] = ()
    resolver = getattr(request.app.state, "group_membership_resolver", None)
    if registry.tool_mode_ceilings and resolver is not None:
        groups = tuple(await resolver.resolve(auth.user_id) or ())
    return await tool_classes.effective(
        tool_classes.parse_mode(stored), registry.tool_mode_ceilings,
        tool_classes.CeilingContext(
            tenant_id=auth.tenant_id, user_id=auth.user_id, session_id=session_id, groups=groups,
        ),
    )


def _parse_session_id(session_id: str) -> uuid.UUID:
    """Validate and parse a session ID string to UUID."""
    try:
        return uuid.UUID(session_id)
    except ValueError as exc:
        raise GatewayError(
            status_code=400,
            message="Invalid session_id format.",
            code="invalid_session_id",
        ) from exc


async def _require_session_owner(
    sid: uuid.UUID,
    auth: AuthContext,
    dao: GatewayDAO,
) -> None:
    """Verify the caller owns the session, or raise 404."""
    owns = await dao.verify_session_ownership(sid, auth.tenant_id, auth.user_id)
    if not owns:
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            code="session_not_found",
        )


@router.get(
    "/v1/sessions/{session_id}/tool-policy",
    response_model=SessionToolPolicyDTO,
)
async def get_session_tool_policy(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Return the current tool execution policy for a session."""
    sid = _parse_session_id(session_id)
    await _require_session_owner(sid, auth, dao)
    policy = await dao.get_session_execution_policy(sid)
    mode = await dao.get_session_execution_mode(sid)
    tool_mode = await _effective_tool_mode(request, auth, session_id, policy.tool_mode if policy else None)
    dto = SessionToolPolicyDTO(
        session_id=session_id,
        execution_mode=mode.value,
        hybrid_preference=policy.hybrid_preference if policy else None,
        effective_catalog_version=policy.catalog_version if policy else 0,
        **tool_mode.as_dict(),
    )
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))


@router.patch(
    "/v1/sessions/{session_id}/tool-policy",
    response_model=SessionToolPolicyDTO,
)
async def patch_session_tool_policy(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Update the execution mode, hybrid preference, and/or tool overrides."""
    sid = _parse_session_id(session_id)
    await _require_session_owner(sid, auth, dao)
    body = await _get_json_payload(request)
    try:
        patch = SessionToolPolicyPatchDTO.model_validate(body)
    except ValidationError as exc:
        # It was a 500.
        raise GatewayError(status_code=400, message=str(exc), code="validation_error") from exc

    from faden_gateway.db.models.gateway import ExecutionMode  # noqa: PLC0415

    # Update execution policy
    mode_arg = ExecutionMode(patch.execution_mode.value) if patch.execution_mode else None
    policy = await dao.upsert_session_execution_policy(
        sid,
        execution_mode=mode_arg,
        hybrid_preference=patch.hybrid_preference if patch.hybrid_preference is not None else ...,
        tool_mode=patch.tool_mode,
    )

    # Apply tool overrides
    if patch.tool_overrides:
        await dao.upsert_session_tool_overrides(
            sid,
            [(o.tool_id, o.enabled) for o in patch.tool_overrides],
        )

    tool_mode = await _effective_tool_mode(request, auth, session_id, policy.tool_mode)
    dto = SessionToolPolicyDTO(
        session_id=session_id,
        execution_mode=policy.execution_mode.value,
        hybrid_preference=policy.hybrid_preference,
        effective_catalog_version=policy.catalog_version,
        **tool_mode.as_dict(),
    )
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))


# ── Session PII Policy ──────────────────────────────────────────


def _pii_policy_to_dict(policy: Any) -> dict[str, Any]:
    """Serialise a PIIPolicy dataclass to a plain dict."""
    from dataclasses import asdict  # noqa: PLC0415

    return asdict(policy) if policy else {}


def _override_row_to_dto(row: Any) -> SessionPIIOverrideDTO:
    return SessionPIIOverrideDTO(
        pii_enabled=row.pii_enabled,
        egress_enabled_for_cloud=row.egress_enabled_for_cloud,
        egress_enabled_for_local=row.egress_enabled_for_local,
        egress_mode=row.egress_mode,
        egress_fail_action=row.egress_fail_action,
        telemetry_enabled=row.telemetry_enabled,
        telemetry_mode=row.telemetry_mode,
        presidio_threshold=row.presidio_threshold,
        presidio_entities_add=row.presidio_entities_add,
    )


def _pii_policy_dto(
    session_id: str,
    resolved: Any,
    *,
    override_row: Any | None,
    caller_may_weaken: bool,
) -> SessionPIIPolicyDTO:
    """The panel's view of a chat's PII policy: the floor, the override, and what requests get."""
    return SessionPIIPolicyDTO(
        session_id=session_id,
        has_override=override_row is not None,
        override=_override_row_to_dto(override_row) if override_row is not None else None,
        floor=_pii_policy_to_dict(resolved.floor) if resolved.floor else None,
        effective=_pii_policy_to_dict(resolved.effective) if resolved.effective else None,
        mode_switch_allowed=resolved.mode_switch_allowed,
        can_switch_mode=resolved.mode_switch_allowed or caller_may_weaken,
        mode_applied=resolved.mode_applied,
    )


@router.get(
    "/v1/pii-defaults",
    response_model=SessionPIIPolicyDTO,
)
async def get_pii_defaults(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Return the tenant PII floor policy (no session required).

    This lets the frontend show PII controls before a session exists,
    mirroring the pre-session tool-catalog pattern.
    """
    from faden_gateway.services.gateway.pii_policy import resolve_session_pii  # noqa: PLC0415

    tenant_policy = await dao.get_tenant_policy(auth.tenant_id)
    resolved = resolve_session_pii(tenant_policy)
    may_weaken = pii_weaken_granted(request.headers.get(PII_WEAKEN_HEADER), auth, "")
    dto = _pii_policy_dto("", resolved, override_row=None, caller_may_weaken=may_weaken)
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))


@router.get(
    "/v1/sessions/{session_id}/pii-policy",
    response_model=SessionPIIPolicyDTO,
)
async def get_session_pii_policy(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Return the session PII policy: floor, override, and effective -- as its requests get it."""
    from faden_gateway.services.gateway.pii_policy import resolve_session_pii  # noqa: PLC0415

    sid = _parse_session_id(session_id)
    await _require_session_owner(sid, auth, dao)

    tenant_policy = await dao.get_tenant_policy(auth.tenant_id)
    override_row = await dao.get_session_pii_override(sid, auth.tenant_id, auth.user_id)
    resolved = resolve_session_pii(tenant_policy, override_row)
    may_weaken = pii_weaken_granted(request.headers.get(PII_WEAKEN_HEADER), auth, session_id)
    dto = _pii_policy_dto(session_id, resolved, override_row=override_row, caller_may_weaken=may_weaken)
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))


@router.patch(
    "/v1/sessions/{session_id}/pii-policy",
    response_model=SessionPIIPolicyDTO,
)
async def patch_session_pii_policy(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Create or update a session PII override.

    The right to weaken is stored with it -- whether this save carries the
    backend's grant (``X-PII-Allow-Weaken``) -- and the chat's requests keep it.
    The body cannot set it.
    """
    from faden_gateway.services.gateway.pii_policy import resolve_session_pii  # noqa: PLC0415

    sid = _parse_session_id(session_id)
    await _require_session_owner(sid, auth, dao)
    body = await _get_json_payload(request)
    patch = SessionPIIPolicyPatchDTO.model_validate(body)

    # Only the backend can sign the grant, after its own check (pii.session:weaken or a superuser).
    allow_weaken = pii_weaken_granted(request.headers.get(PII_WEAKEN_HEADER), auth, session_id)

    fields = patch.model_dump(exclude_unset=True)
    await dao.upsert_session_pii_override(
        sid, auth.tenant_id, auth.user_id,
        updated_by=auth.user_id,
        allow_weaken=allow_weaken,
        **fields,
    )

    # Re-read to build response
    tenant_policy = await dao.get_tenant_policy(auth.tenant_id)
    override_row = await dao.get_session_pii_override(sid, auth.tenant_id, auth.user_id)
    resolved = resolve_session_pii(tenant_policy, override_row)
    dto = _pii_policy_dto(session_id, resolved, override_row=override_row, caller_may_weaken=allow_weaken)
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))


@router.delete(
    "/v1/sessions/{session_id}/pii-policy",
    status_code=204,
)
async def delete_session_pii_policy(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> Response:
    """Remove the session PII override, reverting to floor policy."""
    sid = _parse_session_id(session_id)
    await _require_session_owner(sid, auth, dao)
    await dao.delete_session_pii_override(sid, auth.tenant_id, auth.user_id)
    return Response(status_code=204)


@router.post("/v1/embeddings")
async def create_embeddings(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    service: GatewayService = Depends(get_gateway_service),
) -> JSONResponse:
    """Proxy embeddings request through shared pipeline."""
    try:
        payload = await _get_json_payload(request)
        EmbeddingsRequest.model_validate(payload)
        request_id = _request_id(request)
        routed = await service.route_non_stream(
            auth=auth,
            endpoint="/v1/embeddings",
            payload=payload,
            request_id=request_id,
        )
        response = JSONResponse(status_code=routed.status_code, content=routed.payload)
        response.headers["x-request-id"] = request_id
        response.headers["x-provider-instance-id"] = routed.provider_instance_id
        if routed.trace_id:
            response.headers["x-langfuse-trace-id"] = routed.trace_id
        return response
    except GatewayError as exc:
        return error_from(exc)


@router.post("/v1/rerank")
async def create_rerank(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    service: GatewayService = Depends(get_gateway_service),
) -> JSONResponse:
    """Score documents against a query with a reranking model (Cohere / Jina shape)."""
    try:
        payload = await _get_json_payload(request)
        RerankRequest.model_validate(payload)
        request_id = _request_id(request)
        routed = await service.route_non_stream(
            auth=auth,
            endpoint="/v1/rerank",
            payload=payload,
            request_id=request_id,
        )
        response = JSONResponse(status_code=routed.status_code, content=routed.payload)
        response.headers["x-request-id"] = request_id
        response.headers["x-provider-instance-id"] = routed.provider_instance_id
        if routed.trace_id:
            response.headers["x-langfuse-trace-id"] = routed.trace_id
        return response
    except ValidationError as exc:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(exc),
            error_type="invalid_request_error",
            code="validation_error",
        )
    except GatewayError as exc:
        return error_from(exc)


def _requested_skills(value: Any) -> list[str]:
    """A request's ``skills``: slugs or ids, as strings; anything else ignored."""
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()][:20]


@router.post("/v1/chat/completions", response_model=None)
async def create_chat_completions(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    service: GatewayService = Depends(get_gateway_service),
) -> Response:
    """Proxy chat completions with stream and non-stream support."""
    return await serve_chat_completions(request, auth=auth, service=service)


async def serve_chat_completions(
    request: Request,
    *,
    auth: AuthContext,
    service: GatewayService,
    agent: AgentRequest | None = None,
    prepare: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Response:
    """One chat completion, whole or streamed: the plain route and an agent's.

    *prepare* applies an agent to the request body before it is routed; the
    rest -- routing, PII, tools, streaming, the request log -- is one path.
    """
    payload: dict[str, Any] = {}
    try:
        payload = await _get_json_payload(request)
        if prepare is not None:
            payload = prepare(payload)
        parsed = ChatCompletionRequest.model_validate(payload)
        request_id = _request_id(request)

        # Strip session_id from upstream payload but pass to service
        session_id = payload.pop("session_id", None)
        # The skills the user picked for this message, by slug or id: the
        # model never sees the field, only the skills.
        skills = _requested_skills(payload.pop("skills", None))
        events = faden_events.wanted(request.headers.get(faden_events.HEADER))
        tool_mode = request.headers.get(tool_classes.MODE_HEADER)
        # The chat page's replies run on their own: the page may leave and
        # come back. Not the model's field either.
        background = payload.pop("background", None) is True

        if parsed.stream:
            streamed = await service.route_stream_chat(
                auth=auth,
                payload=payload,
                request_id=request_id,
                session_id=session_id,
                skills=skills,
                agent=agent,
                faden_events=events,
                tool_mode=tool_mode,
                background=background,
            )
            return _streamed_response(streamed, request_id)

        non_stream = await service.route_non_stream(
            auth=auth,
            endpoint="/v1/chat/completions",
            payload=payload,
            request_id=request_id,
            session_id=session_id,
            skills=skills,
            agent=agent,
            faden_events=events,
            tool_mode=tool_mode,
        )
        json_response = JSONResponse(
            status_code=non_stream.status_code,
            content=non_stream.payload,
        )
        json_response.headers["x-request-id"] = request_id
        json_response.headers["x-provider-instance-id"] = (
            non_stream.provider_instance_id
        )
        if non_stream.trace_id:
            json_response.headers["x-langfuse-trace-id"] = non_stream.trace_id
        return json_response
    except ValidationError as exc:
        await _log_refusal(request, service, auth, agent, payload, 400, "validation_error")
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(exc),
            error_type="invalid_request_error",
            code="validation_error",
        )
    except GatewayError as exc:
        if not exc.logged:
            await _log_refusal(request, service, auth, agent, payload, exc.status_code, exc.code)
        return error_from(exc)


async def _log_refusal(
    request: Request,
    service: GatewayService,
    auth: AuthContext,
    agent: AgentRequest | None,
    payload: dict[str, Any],
    status_code: int,
    code: str | None,
) -> None:
    """A request refused before the pipeline logs it still gets its row.

    A bad body, no model, an agent's rules.

    They were missing from the request log, so a client failing on every call
    left no trace an admin could find.
    """
    fields: dict[str, Any] = {}
    if agent is not None:
        fields = {"agent_id": agent.id, "device_id": agent.device_id,
                  "conversation_id": agent.conversation_id, "turn_id": agent.turn_id}
    try:
        await service.audit.log(
            request_id=_request_id(request), trace_id=None, tenant_id=auth.tenant_id, user_id=auth.user_id,
            model_alias=str(payload.get("model") or "") or None, provider_instance_id=None,
            endpoint="/v1/chat/completions", status_code=status_code, latency_ms=0, ttft_ms=None,
            prompt_tokens=None, completion_tokens=None, total_tokens=None, error_code=code,
            stream=bool(payload.get("stream")),
            session_id=str(payload["session_id"]) if payload.get("session_id") else None, **fields,
        )
    except Exception:
        logger.warning("Could not log the refused request", exc_info=True)


def _streamed_response(streamed: StreamingGatewayResponse, request_id: str) -> StreamingResponse:
    """A streamed chat completion, with the ids the client reads from its headers."""
    response = StreamingResponse(streamed.stream, media_type="text/event-stream")
    response.headers["x-request-id"] = request_id
    response.headers["x-provider-instance-id"] = streamed.provider_instance_id
    response.headers["cache-control"] = "no-cache"
    if streamed.trace_id:
        response.headers["x-langfuse-trace-id"] = streamed.trace_id
    # The reply's row in the chat: what the chat page tries again or stops.
    if streamed.turn_id:
        response.headers["x-faden-turn-id"] = streamed.turn_id
    return response


# ── Turns ─────────────────────────────────────────────────────────


@router.post("/v1/sessions/{session_id}/turns/{turn_id}/retry", response_model=None)
async def retry_turn(
    request: Request,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    service: GatewayService = Depends(get_gateway_service),
) -> Response:
    """Try the chat's last reply again, once it has failed or stopped.

    The new reply replaces it and answers the same question. The body may
    name another model (``model``; the failed reply's by default) and the
    skills to use (``skills``). It streams as a chat completion does.
    """
    try:
        dao = service.session_dao
        sess = await dao.get_session(
            session_id=session_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
        ) if dao is not None else None
        if dao is None or sess is None:
            raise GatewayError(status_code=404, message="Session not found.", code="session_not_found")
        last = await dao.get_recent_messages(session_id=session_id, limit=2)
        if (
            len(last) < 2
            or last[-1].id != turn_id
            or last[-1].role != "assistant"
            or last[-1].status not in TURNS_RETRYABLE
            or last[-2].role != "user"
        ):
            raise GatewayError(
                status_code=409,
                message="Only the chat's last reply can be tried again, once it has failed or stopped.",
                code="turn_not_retryable",
            )
        raw = await request.body()
        try:
            body = json.loads(raw) if raw else {}
        except ValueError as exc:
            raise GatewayError(status_code=400, message="The body is not JSON.", code="invalid_json") from exc
        body = body if isinstance(body, dict) else {}
        model = body.get("model") or last[-1].model_alias
        if not model:
            raise GatewayError(
                status_code=400, message="Name the model to try again with.", code="model_required",
            )
        question = last[-2]
        request_id = _request_id(request)
        # The question as it was asked: the session sees it is already there,
        # and the reply it replaces goes (``_inject_session_context``).
        streamed = await service.route_stream_chat(
            auth=auth,
            payload={
                "model": model,
                "stream": True,
                "messages": [{"role": "user", "content": question.content_parts_json or question.content}],
            },
            request_id=request_id,
            session_id=str(session_id),
            skills=_requested_skills(body.get("skills")),
            background=True,
        )
        return _streamed_response(streamed, request_id)
    except GatewayError as exc:
        return error_response(
            status_code=exc.status_code,
            message=exc.message,
            error_type=exc.error_type,
            param=exc.param,
            code=exc.code,
        )


@router.post("/v1/sessions/{session_id}/turns/{turn_id}/stop")
async def stop_turn(
    request: Request,
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
    session_dao: SessionDAO = Depends(),
) -> JSONResponse:
    """Stop a reply being written in the chat, whichever worker writes it; what was written is kept.

    ``stopping`` says whether the reply was running.
    """
    try:
        await _require_session_owner(session_id, auth, dao)
    except GatewayError as exc:
        return error_response(status_code=exc.status_code, message=exc.message, code=exc.code)
    turn = await _last_turn(session_dao, session_id)
    if turn is None or turn.id != turn_id or turn.status != TurnStatus.RUNNING.value:
        return JSONResponse({"stopping": False})
    stopping = await _stream_buffer(request).request_stop(str(session_id), str(turn_id))
    return JSONResponse({"stopping": stopping})


async def _last_turn(session_dao: SessionDAO, session_id: uuid.UUID) -> ChatMessage | None:
    """The chat's latest reply; the request's transaction is ended after it.

    A reader of a reply holds its request for as long as the reply is
    written: left in its transaction, the session would hold a pooled
    connection all that time.
    """
    turn = await session_dao.last_reply(session_id=session_id)
    await session_dao.session.commit()
    return turn


def _alive(turn: ChatMessage) -> bool:
    """Whether *turn* is being written: running, with a heartbeat that has not stopped."""
    beat = turn.updated_at
    if turn.status != TurnStatus.RUNNING.value or beat is None:
        return False
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=timezone.utc)
    return beat > datetime.now(timezone.utc) - timedelta(seconds=settings.chat_turn_stale_sec)


async def _owned_session(session_id: str, auth: AuthContext, dao: GatewayDAO) -> uuid.UUID:
    """*session_id* as a UUID, when it is the caller's chat."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError as exc:
        raise GatewayError(
            status_code=400, message="Invalid session_id format.", code="invalid_session_id",
        ) from exc
    await _require_session_owner(sid, auth, dao)
    return sid


# ── Stream reconnection ──────────────────────────────────────────

@router.get("/v1/sessions/{session_id}/stream/status")
async def stream_status(
    request: Request,
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
    session_dao: SessionDAO = Depends(),
) -> JSONResponse:
    """Check whether a reply is being written in the chat, by this worker or another."""
    try:
        sid = await _owned_session(session_id, auth, dao)
    except GatewayError as exc:
        return error_response(status_code=exc.status_code, message=exc.message, code=exc.code)
    if _stream_buffer(request).is_active(session_id):
        return JSONResponse({"active": True})
    turn = await _last_turn(session_dao, sid)
    return JSONResponse({"active": turn is not None and _alive(turn)})


@router.get("/v1/sessions/{session_id}/stream")
async def stream_resume(
    request: Request,
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
    session_dao: SessionDAO = Depends(),
) -> Response:
    """Reconnect to a reply being written (or recently finished), on whichever worker writes it.

    Replays it from the start, then follows it until it ends. This worker's
    buffer when it writes the reply; the shared log (Redis) when another
    does. 204 when there is nothing to read.
    """
    try:
        sid = await _owned_session(session_id, auth, dao)
    except GatewayError as exc:
        return error_response(status_code=exc.status_code, message=exc.message, code=exc.code)
    buf = _stream_buffer(request)
    turn = await _last_turn(session_dao, sid)
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    if buf.has_buffer(session_id) and (turn is None or buf.turn_of(session_id) in (None, str(turn.id))):
        # As on the stream it resumes: the reply's info button needs it.
        trace_id = buf.trace_id(session_id)
        if trace_id:
            headers["x-langfuse-trace-id"] = trace_id
        return StreamingResponse(
            await buf.subscribe(session_id), media_type="text/event-stream", headers=headers,
        )

    # Another worker writes it: its events are in the shared log, or about to be.
    if turn is not None and buf.log is not None and (_alive(turn) or await buf.log.exists(str(turn.id))):
        bind = session_dao.session.bind
        turn_id = turn.id

        async def still_written() -> bool:
            async with AsyncSession(bind, expire_on_commit=False) as own:
                row = await own.get(ChatMessage, turn_id)
                await own.commit()
            return row is not None and _alive(row)

        if turn.trace_id:
            headers["x-langfuse-trace-id"] = turn.trace_id
        return StreamingResponse(
            buf.log.read(str(turn_id), still_written=still_written),
            media_type="text/event-stream",
            headers=headers,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _request_id(request: Request) -> str:
    header_val = request.headers.get("x-request-id", "").strip()
    return header_val or str(uuid.uuid4())


async def _get_json_payload(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.request_max_body_bytes:
                raise GatewayError(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    message="Request body exceeds size limit.",
                    code="request_too_large",
                )
        except ValueError:
            pass
    body = await request.body()
    if len(body) > settings.request_max_body_bytes:
        raise GatewayError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            message="Request body exceeds size limit.",
            code="request_too_large",
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayError(
            status_code=400,
            message="Request body must be valid JSON.",
            code="invalid_json",
        ) from exc
    if not isinstance(payload, dict):
        raise GatewayError(
            status_code=400,
            message="Request body must be a JSON object.",
            code="invalid_json_body",
        )
    return payload
