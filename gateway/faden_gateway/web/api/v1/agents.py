"""``/v1/agents/{agent}/...``: the routes an SDK agent calls.

The endpoint governs; the agent loop runs in the SDK. Each request:

1. verifies the token -- an agent token must name this agent (``aud``), any
   other token must belong to one of its maintainers;
2. checks access on cached data: grants to the user or their teams, the
   agent's state, Enterprise rules;
3. applies the agent's version: its instructions go first, its model is the
   model, the caller's tools must be in its host tool contract, and the
   gateway tools it offers are limited to those it allows;
4. runs the one chat pipeline the plain ``/v1/chat/completions`` runs, with no
   server session -- an agent's conversation stays on the device;
5. logs the agent, device, conversation and turn with the usage.

Responses are exactly the OpenAI shapes, streamed or whole.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette import status

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.db.models.gateway import AgentClientEvent
from faden_gateway.services.extensions import registry
from faden_gateway.services.gateway import tool_classes
from faden_gateway.services.gateway.agents import (
    AgentLookupError,
    AgentProfile,
    AgentRequest,
    decide,
)
from faden_gateway.services.gateway.auth import (
    AGENT_TOKEN_TYPE,
    AuthContext,
    get_agent_route_claims,
    get_auth_context_from_claims,
)
from faden_gateway.services.gateway.errors import GatewayError, error_from, error_response
from faden_gateway.services.gateway.service import GatewayService
from faden_gateway.web.api.v1.views import get_gateway_service, serve_chat_completions

router = APIRouter()

_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CONVERSATION_HEADER = "x-faden-conversation-id"
TURN_HEADER = "x-faden-turn-id"


def _error(exc: GatewayError) -> JSONResponse:
    return error_from(exc)


def _header_id(request: Request, name: str) -> str | None:
    value = request.headers.get(name, "").strip()
    if not value:
        return None
    if not _ID.match(value):
        raise GatewayError(
            status_code=400,
            message=f"{name} must be 1-128 characters of letters, digits and . _ : -",
            code="invalid_header",
            param=name,
        )
    return value


def _audiences(claims: dict[str, Any]) -> list[str]:
    aud = claims.get("aud")
    if isinstance(aud, list):
        return [str(a) for a in aud]
    return [str(aud)] if aud else []


async def agent_request(
    agent: str,
    request: Request,
    verified: Annotated[tuple[dict[str, Any], str], Depends(get_agent_route_claims)],
) -> tuple[AuthContext, AgentRequest]:
    """Who is calling which agent, once the token and access are checked."""
    claims, token = verified
    auth = get_auth_context_from_claims(claims, token)
    directory = getattr(request.app.state, "agent_directory", None)
    if directory is None:
        raise GatewayError(
            status_code=503,
            message="Agents are not available: the gateway cannot read the backend database.",
            error_type="server_error",
            code="agents_unavailable",
        )
    try:
        profile = await directory.agent(auth.tenant_id, agent)
        user = await directory.user(auth.user_id) if profile is not None else None
    except AgentLookupError as exc:
        raise GatewayError(
            status_code=503,
            message="The agent could not be looked up; try again.",
            error_type="server_error",
            code="agent_lookup_failed",
        ) from exc
    if profile is None:
        raise GatewayError(status_code=404, message=f"No agent '{agent}'.", code="agent_not_found")

    agent_token = claims.get("typ") == AGENT_TOKEN_TYPE
    if agent_token and f"agent:{profile.id}" not in _audiences(claims):
        raise GatewayError(
            status_code=403,
            message="This token is for another agent.",
            error_type="permission_error",
            code="agent_mismatch",
        )
    groups_resolver = getattr(request.app.state, "group_membership_resolver", None)
    groups = (await groups_resolver.resolve(auth.user_id) or ()) if groups_resolver is not None else ()
    decision = await decide(profile, user_id=auth.user_id, user=user, groups=groups, registry=registry)
    if not decision.allowed:
        raise GatewayError(
            status_code=decision.status_code,
            message=decision.message,
            error_type="permission_error",
            code=decision.code,
        )
    if not agent_token and not decision.maintainer:
        raise GatewayError(
            status_code=403,
            message="Use an agent token: sign in to the agent (POST /api/agent-tokens).",
            error_type="permission_error",
            code="agent_token_required",
        )
    return auth, AgentRequest(
        agent=profile,
        device_id=str(claims["did"]) if claims.get("did") else None,
        conversation_id=_header_id(request, CONVERSATION_HEADER),
        turn_id=_header_id(request, TURN_HEADER),
        developer_mode=bool(claims.get("dev")),
        maintainer=decision.maintainer,
    )


def apply_agent(profile: AgentProfile, payload: dict[str, Any]) -> dict[str, Any]:
    """The request body as the agent's version shapes it."""
    if "session_id" in payload:
        raise GatewayError(
            status_code=400,
            message="An agent's conversation stays on the device: session_id is not accepted here.",
            code="session_not_supported",
            param="session_id",
        )
    if profile.host_tool_contract:
        allowed = profile.host_tools
        declared = [
            (tool.get("function") or {}).get("name") or ""
            for tool in payload.get("tools") or []
            if isinstance(tool, dict)
        ]
        outside = sorted({name for name in declared if name not in allowed})
        if outside:
            raise GatewayError(
                status_code=400,
                message="Tools not in the agent's host tool contract: " + ", ".join(outside),
                code="tool_not_in_contract",
                param="tools",
            )
    shaped = dict(payload)
    if profile.model_alias:
        shaped["model"] = profile.model_alias
    if profile.instructions:
        messages = shaped.get("messages")
        if isinstance(messages, list):
            shaped["messages"] = [{"role": "system", "content": profile.instructions}, *messages]
    return shaped


@router.post("/v1/agents/{agent}/chat/completions", response_model=None)
async def agent_chat_completions(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> Response:
    """OpenAI-compatible chat under the agent's rules; streamed or whole."""
    auth, agent = caller
    return await serve_chat_completions(
        request,
        auth=auth,
        service=service,
        agent=agent,
        prepare=lambda payload: apply_agent(agent.agent, payload),
    )


@router.get("/v1/agents/{agent}/profile")
async def agent_profile(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
) -> dict[str, Any]:
    """What the SDK caches about the agent: never its instructions, only their hash."""
    auth, agent = caller
    profile = agent.agent
    groups: tuple[str, ...] = ()
    resolver = getattr(request.app.state, "group_membership_resolver", None)
    if registry.tool_mode_ceilings and resolver is not None:
        groups = tuple(await resolver.resolve(auth.user_id) or ())
    top = await tool_classes.ceiling(registry.tool_mode_ceilings, tool_classes.CeilingContext(
        tenant_id=auth.tenant_id, user_id=auth.user_id, agent_id=profile.id, groups=groups,
    ))
    return {
        "object": "agent.profile",
        "id": profile.id,
        "slug": profile.slug,
        "name": profile.name,
        "version": profile.version,
        "draft": not profile.published,
        "model": profile.model_alias,
        "instructions_sha256": profile.instructions_sha256,
        "host_tool_contract": list(profile.host_tool_contract),
        "server_tools": list(profile.server_tools) if profile.server_tools is not None else None,
        "knowledge": list(profile.knowledge),
        "local_mcp": {
            "mode": profile.local_mcp_mode,
            # The user's developer mode lets them attach any local server,
            # unless an Enterprise lock says otherwise (in ``extensions``).
            "developer_mode": agent.developer_mode,
        },
        # Sessions start in "ask"; the SDK sends its session's mode
        # (X-Faden-Tool-Mode), never wider than the ceiling (EE).
        "tool_modes": {
            "default": tool_classes.DEFAULT_MODE,
            "ceiling": top.value if top else None,
            "open_world_enforced": tool_classes.OPEN_WORLD_ENFORCED,
        },
        "ui": profile.ui,
        "extensions": profile.extensions,
        "required_client_modules": registry.client_modules(profile.extensions),
        "edition": registry.edition,
        "features": sorted(registry.features),
    }


@router.get("/v1/agents/{agent}/models")
async def agent_models(
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> Response:
    """The agent's model, in the OpenAI list shape; the tenant's models when it names none."""
    auth, agent = caller
    if agent.agent.model_alias:
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {"id": agent.agent.model_alias, "object": "model", "created": 0, "owned_by": "faden"},
                ],
            },
        )
    try:
        return JSONResponse(await service.list_models(auth))
    except GatewayError as exc:
        return _error(exc)


# -- events -------------------------------------------------------------------------


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime | None = None
    conversation_id: str | None = Field(default=None, max_length=128, pattern=_ID.pattern)


class ToolExecuted(_Event):
    """A tool ran on the device: which, where, how long, and how it ended."""

    type: Literal["tool.executed"]
    tool: str = Field(max_length=128)
    source: Literal["host", "mcp", "gateway"] | None = None
    server: str | None = Field(default=None, max_length=128)
    duration_ms: int = Field(ge=0)
    outcome: Literal["ok", "error", "denied", "cancelled"]


class ApprovalDecided(_Event):
    """The user decided on a tool that needed approval."""

    type: Literal["approval.decided"]
    tool: str = Field(max_length=128)
    decision: Literal["approved", "denied", "timeout"]
    wait_ms: int | None = Field(default=None, ge=0)


class LocalMcpAttached(_Event):
    """A local MCP server was attached, and the tools it offers."""

    type: Literal["local_mcp.attached"]
    label: str = Field(max_length=128)
    command: str | None = Field(default=None, max_length=512)
    url: str | None = Field(default=None, max_length=512)
    tools: list[str] = Field(default_factory=list, max_length=500)


class SessionStats(_Event):
    """Counts of one local session, and the local retention settings."""

    type: Literal["session.stats"]
    turns: int = Field(ge=0)
    compactions: int = Field(default=0, ge=0)
    retention_sessions: int | None = Field(default=None, ge=0)
    retention_days: int | None = Field(default=None, ge=0)


ClientEvent = Annotated[
    ToolExecuted | ApprovalDecided | LocalMcpAttached | SessionStats,
    Field(discriminator="type"),
]


class ClientEvents(BaseModel):
    """A batch of what the SDK did -- names, counts, outcomes; never content."""

    model_config = ConfigDict(extra="forbid")

    events: list[ClientEvent] = Field(min_length=1, max_length=100)


@router.post("/v1/agents/{agent}/events", status_code=status.HTTP_202_ACCEPTED, response_model=None)
async def agent_events(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> Response:
    """Store the SDK's content-free events for usage and audit."""
    auth, agent = caller
    try:
        batch = ClientEvents.model_validate_json(await request.body())
    except ValidationError as exc:
        return error_response(
            status_code=400,
            message=str(exc),
            error_type="invalid_request_error",
            code="invalid_events",
        )
    dao.session.add_all([
        AgentClientEvent(
            tenant_id=auth.tenant_id,
            agent_id=agent.id,
            user_id=auth.user_id,
            device_id=agent.device_id,
            conversation_id=event.conversation_id or agent.conversation_id,
            type=event.type,
            data=event.model_dump(mode="json", exclude={"type", "occurred_at", "conversation_id"}),
            occurred_at=event.occurred_at,
        )
        for event in batch.events
    ])
    await dao.session.commit()
    return JSONResponse({"accepted": len(batch.events)}, status_code=status.HTTP_202_ACCEPTED)


# -- features --------------------------------------------------------------------------


@router.get("/v1/features")
async def features(
    verified: Annotated[tuple[dict[str, Any], str], Depends(get_agent_route_claims)],
) -> dict[str, Any]:
    """The edition and what it offers, for any signed-in caller (agent tokens too)."""
    del verified
    return {"edition": registry.edition, "features": sorted(registry.features)}
