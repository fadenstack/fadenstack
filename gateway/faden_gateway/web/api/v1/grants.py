"""Grants: what a person lets an agent on a device run for a while without asking (sdk/38 §6; sdk/40 A2.1).

- ``POST /v1/agents/{agent}/grants``: the device asks with the person's answer. The gateway may grant part of
  it (RFC 9396: a shorter time, fewer classes) and answers with what it granted and its own time. No answer,
  no grant (L-59): the device relies on a grant only once it has this answer.
- ``GET /v1/agents/{agent}/grants``: the device's live grants for the agent, for the marker and Settings.
- ``DELETE /v1/agents/{agent}/grants/{id}`` and ``DELETE /v1/grants/{id}``: the person ends a grant, from the
  agent or signed in anywhere. It holds from the next call on; a call already running finishes.
- ``POST /v1/agents/{agent}/grants/{id}/pause`` and ``.../resume``: only the grant's device; the clock runs
  on.
- ``POST /v1/agents/{agent}/grants/{id}/extend``: more time or places, from the grant's device, in place and
  never past the longest grant from when it was issued.

A grant is the device's: only the agent's route with a device sign-in (the agent token's ``did``) makes,
lists, pauses and resumes one. The gateway checks and counts every covered call itself
(``services/gateway/grants.py``); what the device holds is a copy.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.services.gateway import grants
from faden_gateway.services.gateway.agents import AgentRequest
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.web.api.v1.agents import CONVERSATION_HEADER, agent_request

router = APIRouter()

_Name = Annotated[str, Field(min_length=1, max_length=256)]
_Place = Annotated[str, Field(min_length=1, max_length=2048)]


class GrantTarget(BaseModel):
    """A place a ``work`` grant covers, in the shape of RFC 9396 ``authorization_details``."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["document", "folder", "site", "app", "desk"]
    #: Documents, apps and desks by name or path, folders by path (and what lies under them), sites by origin.
    identifiers: list[_Place] = Field(min_length=1, max_length=50)
    #: What the grant lets the agent do there; none: every class the grant covers.
    actions: list[Literal["read", "write"]] | None = None


class GrantRequest(BaseModel):
    """What the person allowed on the device's grant card."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["time", "work", "plan", "bypass"]
    #: ``time``: the one tool.
    tools: list[_Name] = Field(default_factory=list, max_length=8)
    #: ``work``: the classes, never ``destructive`` or unclassified (sdk/38 §4.3).
    classes: list[Literal["read", "write"]] = Field(default_factory=list, max_length=2)
    #: ``work``: the places.
    targets: list[GrantTarget] = Field(default_factory=list, max_length=20)
    minutes: int = Field(ge=1, le=1440)
    max_calls: int | None = Field(default=None, ge=1, le=100_000)
    #: ``time``: only in this conversation (``x-faden-conversation-id``). A ``work`` grant always is.
    conversation: bool = False
    #: The SHA-256 of the text the card showed the person.
    shown_text_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _device(caller: tuple[AuthContext, AgentRequest]) -> tuple[AuthContext, AgentRequest, str]:
    auth, agent = caller
    if not agent.device_id:
        raise GatewayError(
            status_code=403, error_type="permission_error", code="grant_needs_device",
            message="A grant belongs to a device: sign in to the agent on it (an agent token).",
        )
    return auth, agent, agent.device_id


def _owner(
    auth: AuthContext, agent: AgentRequest, device_id: str, conversation_id: str | None = None,
) -> grants.Owner:
    return grants.Owner(
        tenant_id=auth.tenant_id, user_id=auth.user_id, agent_id=agent.agent.id,
        agent_version=agent.agent.version, device_id=device_id, conversation_id=conversation_id,
    )


def _grant_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _not_found() from None


def _not_found() -> GatewayError:
    return GatewayError(status_code=404, code="grant_not_found", message="You have no such grant.")


async def _answer(dao: GatewayDAO, grant: Any | None, status_code: int = 200) -> JSONResponse:
    if grant is None:
        raise _not_found()
    now = await grants.server_now(dao.session)
    return JSONResponse(
        {"object": "agent.grant", **grants.as_dict(grant, now), "server_now": now.isoformat()},
        status_code=status_code,
    )


@router.post("/v1/agents/{agent}/grants", status_code=status.HTTP_201_CREATED, response_model=None)
async def issue_grant(
    body: GrantRequest,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """Store what the person allowed, within the agent's limits; ``narrowed`` names what was cut."""
    auth, agent, device_id = _device(caller)
    bound = body.kind == "work" or body.conversation
    if bound and not agent.conversation_id:
        raise GatewayError(
            status_code=422, code="grant_conversation_required", param=CONVERSATION_HEADER,
            message=f"This grant belongs to one conversation: send {CONVERSATION_HEADER}.",
        )
    try:
        granted = grants.limit(
            kind=body.kind, tools=body.tools, classes=body.classes,
            targets=[t.model_dump() for t in body.targets], minutes=body.minutes, max_calls=body.max_calls,
            agent=agent.agent,
        )
    except grants.RefusedError as exc:
        raise GatewayError(
            status_code=403 if exc.code == "grant_not_offered" else 422, code=exc.code, message=str(exc),
            error_type="permission_error" if exc.code == "grant_not_offered" else "invalid_request_error",
        ) from exc
    grant = await grants.issue(
        dao.session, _owner(auth, agent, device_id, agent.conversation_id if bound else None), granted,
        shown_text_hash=body.shown_text_sha256,
    )
    now = await grants.server_now(dao.session)
    return JSONResponse(
        {
            "object": "agent.grant", **grants.as_dict(grant, now), "server_now": now.isoformat(),
            "narrowed": granted.cut,
        },
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/v1/agents/{agent}/grants", response_model=None)
async def list_grants(
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """The device's live grants for the agent: active or paused, unexpired, not spent."""
    auth, agent, device_id = _device(caller)
    found = await grants.live(dao.session, _owner(auth, agent, device_id))
    now = await grants.server_now(dao.session)
    return JSONResponse({
        "object": "list", "data": [grants.as_dict(g, now) for g in found], "server_now": now.isoformat(),
    })


@router.delete("/v1/agents/{agent}/grants/{grant_id}", response_model=None)
async def revoke_agent_grant(
    grant_id: str,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """End one of the person's grants for this agent, made on any of their devices (revoking only narrows)."""
    auth, agent = caller
    grant = await grants.revoke(
        dao.session,
        _grant_id(grant_id),
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        agent_id=agent.agent.id,
    )
    return await _answer(dao, grant)


@router.delete("/v1/grants/{grant_id}", response_model=None)
async def revoke_grant(
    grant_id: str,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """End one of the person's grants, signed in anywhere (the console's Settings, another device)."""
    grant = await grants.revoke(
        dao.session, _grant_id(grant_id), tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    return await _answer(dao, grant)


@router.post("/v1/agents/{agent}/grants/{grant_id}/pause", response_model=None)
async def pause_grant(
    grant_id: str,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """Pause an active grant of this device (its screen locked, the person away); the clock runs on."""
    auth, agent, device_id = _device(caller)
    grant = await grants.pause(dao.session, _grant_id(grant_id), _owner(auth, agent, device_id))
    return await _answer(dao, grant)


@router.post("/v1/agents/{agent}/grants/{grant_id}/resume", response_model=None)
async def resume_grant(
    grant_id: str,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """Resume a paused grant of this device, if it has not ended meanwhile."""
    auth, agent, device_id = _device(caller)
    grant = await grants.resume(dao.session, _grant_id(grant_id), _owner(auth, agent, device_id))
    return await _answer(dao, grant)


class GrantExtension(BaseModel):
    """What the person added on the card the agent showed to extend a running grant: only the difference."""

    model_config = ConfigDict(extra="forbid")

    #: Moves the end this much later, at most to the grant's longest span from when it was issued.
    minutes: int = Field(default=0, ge=0, le=1440)
    #: Places a stretch of work gains, for the classes it already covers.
    targets: list[GrantTarget] = Field(default_factory=list, max_length=20)
    shown_text_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@router.post("/v1/agents/{agent}/grants/{grant_id}/extend", response_model=None)
async def extend_grant(
    grant_id: str,
    body: GrantExtension,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """Extend a running grant of this device in place; ``narrowed`` says when the longest span cut the time.

    Owner, 2026-11-07: the grant's whole span stays within the longest grant from when it was issued; past
    that a new grant needs the full card again. Each extension is recorded with what the card showed.
    """
    auth, agent, device_id = _device(caller)
    if not body.minutes and not body.targets:
        raise GatewayError(
            status_code=422, code="grant_scope_invalid", message="Say what the extension adds.",
        )
    try:
        grant, cut = await grants.extend(
            dao.session, _grant_id(grant_id), _owner(auth, agent, device_id), minutes=body.minutes,
            targets=[t.model_dump() for t in body.targets],
            longest_minutes=agent.agent.max_grant_minutes or grants.DEFAULT_LONGEST_MINUTES,
            shown_text_hash=body.shown_text_sha256,
        )
    except grants.RefusedError as exc:
        raise GatewayError(status_code=422, code=exc.code, message=str(exc)) from exc
    if grant is None:
        raise _not_found()
    now = await grants.server_now(dao.session)
    return JSONResponse({
        "object": "agent.grant", **grants.as_dict(grant, now), "server_now": now.isoformat(), "narrowed": cut,
    })
