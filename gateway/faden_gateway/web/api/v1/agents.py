"""``/v1/agents/{agent}/...``: the routes an SDK agent calls.

The endpoint governs; the agent loop runs in the SDK. Each request:

1. verifies the token -- an agent token must name this agent (``aud``), any
   other token must belong to one of its maintainers;
2. checks access on cached data: grants to the user or their teams, the
   agent's state, Enterprise rules;
3. applies the agent's version: its instructions go first, its model is the
   model, the caller's tools outside its host tool contract are held back
   (and kept for review), and the gateway tools it offers are limited to
   those it allows;
4. runs the one chat pipeline the plain ``/v1/chat/completions`` runs, with no
   server session -- an agent's conversation stays on the device;
5. logs the agent, device, conversation and turn with the usage.

Responses are exactly the OpenAI shapes, streamed or whole.
"""

from __future__ import annotations

import dataclasses
import logging
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
from faden_gateway.services.gateway import agent_manifests, tool_classes
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
from faden_gateway.services.gateway.pii_policy import VALID_EGRESS_MODES, mode_switch_allowed
from faden_gateway.services.gateway.service import GatewayService
from faden_gateway.web.api.v1.views import get_gateway_service, serve_chat_completions, serve_images

router = APIRouter()
logger = logging.getLogger(__name__)

_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CONVERSATION_HEADER = "x-faden-conversation-id"
TURN_HEADER = "x-faden-turn-id"
#: The tools held back from the request, because the agent's contract does not list them.
HELD_TOOLS_HEADER = "x-faden-held-tools"
#: The PII egress mode the conversation asks for (``redact``, ``tokenize_reversible``). Applied when
#: the agent (``pii.allow_mode_override``), or with none set the tenant, lets conversations switch it;
#: otherwise the request gets the tenant's mode.
PII_MODE_HEADER = "x-faden-pii-mode"
#: The model the SDKs send to an agent's route: "the agent's" -- the field is required.
SDK_MODEL_PLACEHOLDER = "agent"


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


def _pii_mode(request: Request) -> str | None:
    value = request.headers.get(PII_MODE_HEADER, "").strip()
    if not value:
        return None
    if value not in VALID_EGRESS_MODES:
        raise GatewayError(
            status_code=400,
            message=f"{PII_MODE_HEADER} must be one of: " + ", ".join(sorted(VALID_EGRESS_MODES)),
            code="invalid_header",
            param=PII_MODE_HEADER,
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
    agent_token = claims.get("typ") == AGENT_TOKEN_TYPE
    try:
        profile = await directory.agent(auth.tenant_id, agent)
        if agent_token and (profile is None or f"agent:{profile.id}" not in _audiences(claims)):
            # The cache may hold an agent deleted since and made again under the same address, or miss one
            # made within the minute: the token's agent decides, so read it once more before refusing.
            profile = await directory.agent(auth.tenant_id, agent, fresh=True)
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
        pii_mode=_pii_mode(request),
    )


def _tool_name(tool: Any) -> str:
    return str((tool.get("function") or {}).get("name") or "") if isinstance(tool, dict) else ""


def hold_back(
    payload: dict[str, Any], allowed: frozenset[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The request without the tools *allowed* does not name, and those tools.

    The model never sees a held tool, and a ``tool_choice`` that named one becomes ``auto``; with no tool
    left, the request has none.
    """
    tools = payload.get("tools") or []
    held = [t for t in tools if isinstance(t, dict) and _tool_name(t) not in allowed]
    if not held:
        return payload, []
    kept = [t for t in tools if not (isinstance(t, dict) and _tool_name(t) not in allowed)]
    shaped = dict(payload)
    if not kept:
        for field in ("tools", "tool_choice", "parallel_tool_calls"):
            shaped.pop(field, None)
        return shaped, held
    shaped["tools"] = kept
    choice = shaped.get("tool_choice")
    if isinstance(choice, dict) and _tool_name(choice) in {_tool_name(t) for t in held}:
        shaped["tool_choice"] = "auto"
    return shaped, held


def apply_agent(
    profile: AgentProfile, payload: dict[str, Any], *, local_mcp: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The request body as the agent's version shapes it, and the tools it held back.

    Tools outside the host tool contract are held back, not refused: an app updated with new tools keeps
    working with the ones the agent allows, and the new ones wait for review (``agent_manifests``). Unless
    the device may attach local MCP servers (*local_mcp*): their tools are in no contract, and the tool mode
    treats them as unclassified.
    """
    if "session_id" in payload:
        raise GatewayError(
            status_code=400,
            message="An agent's conversation stays on the device: session_id is not accepted here.",
            code="session_not_supported",
            param="session_id",
        )
    held: list[dict[str, Any]] = []
    if profile.host_tool_contract and not local_mcp:
        payload, held = hold_back(payload, profile.host_tools)
    shaped = dict(payload)
    if profile.model_alias:
        shaped["model"] = profile.model_alias
    elif shaped.get("model") in (None, "", SDK_MODEL_PLACEHOLDER):
        # The SDKs leave the model to the agent; an agent made without one cannot answer until it has one.
        raise GatewayError(
            status_code=409,
            message="The agent has no model yet: its maintainers pick one in the console "
            "(the agent's Definition, Model).",
            code="agent_has_no_model",
            param="model",
        )
    if profile.instructions:
        messages = shaped.get("messages")
        if isinstance(messages, list):
            shaped["messages"] = [{"role": "system", "content": profile.instructions}, *messages]
    return shaped, held


async def _keep_held(
    request: Request, auth: AuthContext, agent: AgentRequest, held: list[dict[str, Any]],
) -> None:
    """The held-back tools, for the agent's review: once in a while per tool set, never failing the chat.

    On a session of their own: the request's may still serve a stream.
    """
    manifest = agent_manifests.held_manifest(held)
    digest = agent_manifests.sha256_of(manifest)
    if not manifest["tools"] or agent_manifests.recently_recorded(agent.id, digest):
        return
    try:
        async with request.app.state.db_session_factory() as own:
            await agent_manifests.record(
                own, tenant_id=auth.tenant_id, agent_id=agent.id, user_id=auth.user_id,
                device_id=agent.device_id, manifest=manifest, source="request",
            )
    except Exception:  # the answer matters more than the note
        logger.warning("Could not keep the tools held back for agent %s", agent.id, exc_info=True)


@router.post("/v1/agents/{agent}/chat/completions", response_model=None)
async def agent_chat_completions(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> Response:
    """OpenAI-compatible chat under the agent's rules; streamed or whole.

    Tools the agent's contract does not list are held back: ``X-Faden-Held-Tools`` names them.
    """
    auth, agent = caller
    # Its knowledge searches stay within the collections it was given; with none, what its person may read.
    auth = dataclasses.replace(auth, knowledge=tuple(agent.agent.knowledge) or None)
    held: list[dict[str, Any]] = []

    def prepare(payload: dict[str, Any]) -> dict[str, Any]:
        shaped, held_now = apply_agent(agent.agent, payload, local_mcp=agent.local_mcp_allowed)
        held.extend(held_now)
        return shaped

    response = await serve_chat_completions(request, auth=auth, service=service, agent=agent, prepare=prepare)
    if held:
        response.headers[HELD_TOOLS_HEADER] = ",".join(sorted({_tool_name(t) or "?" for t in held}))
        await _keep_held(request, auth, agent, held)
    return response


@router.post("/v1/agents/{agent}/images/generations", response_model=None)
async def agent_images(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    service: Annotated[GatewayService, Depends(get_gateway_service)],
) -> Response:
    """Pictures for an agent's tools (a slide's picture): the tenant's image model, under its access."""
    auth, agent = caller
    return await serve_images(request, auth=auth, service=service, agent=agent)


@router.get("/v1/agents/{agent}/profile")
async def agent_profile(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> dict[str, Any]:
    """What the SDK caches about the agent: never its instructions, only their hash."""
    auth, agent = caller
    profile = agent.agent
    tenant_policy = await dao.get_tenant_policy(auth.tenant_id)
    tenant_allows = bool(getattr(tenant_policy, "allow_mode_override", False)) if tenant_policy else False
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
        # Whether that model sees pictures, so the SDK sends a tool's picture (a rendered slide) or not.
        "vision": await dao.alias_vision(profile.model_alias) if profile.model_alias else None,
        # Whether its tools can make pictures (an image model is set up for the tenant).
        "images": await dao.image_alias(auth.tenant_id) is not None,
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
        # Whether the conversation's own PII mode (X-Faden-PII-Mode) is applied: the agent's
        # setting, or the tenant's when the agent has none.
        "pii": {
            "allow_mode_override": profile.pii_allow_mode_override,
            "mode_switch_allowed": mode_switch_allowed(
                tenant_default=tenant_allows, agent_setting=profile.pii_allow_mode_override,
            ),
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


# -- what the app brings --------------------------------------------------------------


@router.put("/v1/agents/{agent}/manifest", response_model=None)
async def agent_manifest(
    request: Request,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> Response:
    """Keep what the app brings -- its tools with their classes, its context and defaults -- for review.

    Sent by the SDK when it starts; the same manifest again is only counted. Nothing in it changes what the
    agent may do: its maintainers compare it with the contract in the console and take in what they accept.
    """
    auth, agent = caller
    body = await request.body()
    if len(body) > agent_manifests.MAX_MANIFEST_BYTES:
        return error_response(
            status_code=413,
            message=f"A manifest may be {agent_manifests.MAX_MANIFEST_BYTES} bytes at most.",
            error_type="invalid_request_error",
            code="manifest_too_large",
        )
    try:
        manifest = agent_manifests.AppManifest.model_validate_json(body)
    except ValidationError as exc:
        return error_response(
            status_code=400, message=str(exc), error_type="invalid_request_error", code="invalid_manifest",
        )
    try:
        digest, new = await agent_manifests.record(
            dao.session, tenant_id=auth.tenant_id, agent_id=agent.id, user_id=auth.user_id,
            device_id=agent.device_id, manifest=manifest.stored(), source="app",
        )
    except agent_manifests.ManifestLimitError as exc:
        return error_response(
            status_code=429, message=str(exc), error_type="rate_limit_error", code="too_many_manifests",
        )
    return JSONResponse({"sha256": digest, "new": new})


# -- features --------------------------------------------------------------------------


@router.get("/v1/features")
async def features(
    verified: Annotated[tuple[dict[str, Any], str], Depends(get_agent_route_claims)],
) -> dict[str, Any]:
    """The edition and what it offers, for any signed-in caller (agent tokens too)."""
    del verified
    return {"edition": registry.edition, "features": sorted(registry.features)}
