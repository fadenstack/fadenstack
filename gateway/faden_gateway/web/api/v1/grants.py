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
  never past the longest grant from when it was issued; never a Bypass grant (409 ``bypass_not_extendable``).
- ``POST /v1/agents/{agent}/grants/{id}/report``: at the end of a turn under Bypass, the calls the device ran
  under the grant; the gateway compares them with its own records and flags any difference (sdk/09 L-61).

A ``bypass`` grant (sdk/40 A3) is made only where Bypass may be chosen -- the installation's switch, the
agent's highest level, no lower ceiling (L-57) -- for one conversation. Its row is the record the device waits
for before it relies on Bypass (L-59).

The grant limits an extension registers (``registry.grant_limits``) narrow the longest grant at issue and at
extend, and set what a grant's covered calls leave behind; one that fails makes and extends no grant (503
``grant_limits_unavailable``), so each call asks.

A grant is the device's: only the agent's route with a device sign-in (the agent token's ``did``) makes,
lists, pauses and resumes one. The gateway checks and counts every covered call itself
(``services/gateway/grants.py``); what the device holds is a copy.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.services.gateway import approval_settings, grants
from faden_gateway.services.gateway.agents import AgentRequest
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.web.api.v1.agents import (
    CONVERSATION_HEADER,
    agent_request,
    bypass_offered,
    grant_limits,
    tool_mode_ceiling,
)

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
    #: The person's own words on why (Bypass, optional; sdk/09 L-59).
    reason: str | None = Field(default=None, max_length=500)
    #: The client as it names itself, e.g. ``Fadenstack.Agents/0.3.0`` (L-59).
    client_version: str | None = Field(default=None, max_length=64)


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


async def _limits(
    request: Request, auth: AuthContext, agent: AgentRequest, kind: str | None,
) -> grants.Limits:
    """The registered grant limits for this grant; one that fails makes none (ASVS 5.0 V8.3.1: fail closed)."""
    try:
        return await grant_limits(request, auth, agent.agent, kind)
    except grants.LimitsUnavailableError as exc:
        raise GatewayError(
            status_code=503, error_type="server_error", code="grant_limits_unavailable",
            message="Grants cannot be checked against their limits right now, so none is made; each call asks.",
        ) from exc


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
    request: Request,
    body: GrantRequest,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """Store what the person allowed, within the agent's limits; ``narrowed`` names what was cut."""
    auth, agent, device_id = _device(caller)
    bound = body.kind in ("work", "bypass") or body.conversation
    if bound and not agent.conversation_id:
        raise GatewayError(
            status_code=422, code="grant_conversation_required", param=CONVERSATION_HEADER,
            message=f"This grant belongs to one conversation: send {CONVERSATION_HEADER}.",
        )
    found = await approval_settings.current(dao.session.bind)
    limits = await _limits(request, auth, agent, body.kind)
    allowed = body.kind == "bypass" and bypass_offered(
        agent.agent, await tool_mode_ceiling(request, auth, agent.agent), found,
    )
    try:
        granted = grants.limit(
            kind=body.kind, tools=body.tools, classes=body.classes,
            targets=[t.model_dump() for t in body.targets], minutes=body.minutes, max_calls=body.max_calls,
            agent=agent.agent, tenant_longest=limits.longest(found.longest_grant_minutes), bypass_allowed=allowed,
        )
    except grants.RefusedError as exc:
        refused = exc.code in ("grant_not_offered", "bypass_not_allowed")
        raise GatewayError(
            status_code=403 if refused else 422, code=exc.code, message=str(exc),
            error_type="permission_error" if refused else "invalid_request_error",
        ) from exc
    grant = await grants.issue(
        dao.session, _owner(auth, agent, device_id, agent.conversation_id if bound else None), granted,
        shown_text_hash=body.shown_text_sha256, client_version=body.client_version, reason=body.reason,
        evidence_level=limits.evidence_level,
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
    request: Request,
    grant_id: str,
    body: GrantExtension,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """Extend a running grant of this device in place; ``narrowed`` says when the longest span cut the time.

    Owner, 2026-11-07: the grant's whole span stays within the longest grant from when it was issued; past
    that a new grant needs the full card again. Each extension is recorded with what the card showed.
    A Bypass grant is never extended (owner, 2026-11-08; sdk/09 L-58): 409 ``bypass_not_extendable``, the
    grant as it was -- the request is well formed, the grant's kind is what refuses it. More time is a new
    Bypass, with its confirmation.
    """
    auth, agent, device_id = _device(caller)
    found = await approval_settings.current(dao.session.bind)
    if not body.minutes and not body.targets:
        raise GatewayError(
            status_code=422, code="grant_scope_invalid", message="Say what the extension adds.",
        )
    limits = await _limits(request, auth, agent, None)
    try:
        grant, cut = await grants.extend(
            dao.session, _grant_id(grant_id), _owner(auth, agent, device_id), minutes=body.minutes,
            targets=[t.model_dump() for t in body.targets],
            longest_minutes=grants.longest(agent.agent, limits.longest(found.longest_grant_minutes)),
            shown_text_hash=body.shown_text_sha256,
        )
    except grants.RefusedError as exc:
        conflict = exc.code == "bypass_not_extendable"
        raise GatewayError(status_code=409 if conflict else 422, code=exc.code, message=str(exc)) from exc
    if grant is None:
        raise _not_found()
    now = await grants.server_now(dao.session)
    return JSONResponse({
        "object": "agent.grant", **grants.as_dict(grant, now), "server_now": now.isoformat(), "narrowed": cut,
    })


class GrantReport(BaseModel):
    """The calls the device ran under its Bypass grant in the turn that just ended (sdk/09 L-61)."""

    model_config = ConfigDict(extra="forbid")

    calls: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=1000,
    )


@router.post("/v1/agents/{agent}/grants/{grant_id}/report", response_model=None)
async def report_grant(
    grant_id: str,
    body: GrantReport,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
    dao: Annotated[GatewayDAO, Depends()],
) -> JSONResponse:
    """Compare the device's calls with the gateway's records; how many matched and how many differ.

    The trail does not rely on the device: a difference is flagged for admins, whichever side it is on. Only
    the grant's device reports; the grant may have ended meanwhile.
    """
    auth, agent, device_id = _device(caller)
    found = await grants.report(dao.session, _grant_id(grant_id), _owner(auth, agent, device_id), body.calls)
    if found is None:
        raise _not_found()
    return JSONResponse({
        "object": "agent.grant_report", "matched": found.matched,
        "device_only": len(found.device_only), "gateway_only": len(found.gateway_only),
    })
