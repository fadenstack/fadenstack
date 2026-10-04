"""``/api/agents``: agents, their versions, grants and maintainers; ``/api/agent-tokens``.

RBAC decides what a user may do with agents in general:

- ``agents:read``    see every agent of the tenant: versions, access, activity
- ``agents:create``  create agents (the creator maintains them)
- ``agents:manage``  manage every agent (superusers always do)
- ``agents:delete``  delete retired agents for good

Maintainers manage their own agents -- versions, grants, maintainers, retiring
them -- without ``agents:manage``; deleting one takes ``agents:delete``. Anyone
may list the agents they can use, and sign in to one of them
(``POST /api/agent-tokens``) when a grant allows it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.agents_dao import AgentsDAO
from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.agents import (
    Agent,
    AgentMemberRole,
    AgentStatus,
    AgentVersion,
    PrincipalType,
)
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.agents import access, app_review, tokens
from faden_backend.services.extensions import registry
from faden_backend.services.principals import PrincipalOption, find_principals
from faden_backend.web.api.agents.schema import (
    AcceptAppsRequest,
    AccessDTO,
    AgentDTO,
    AgentTokensDTO,
    AgentVersionDTO,
    AgentVersionFields,
    AppSeenDTO,
    AppsReviewDTO,
    ClientEventDTO,
    CreateAgentRequest,
    GrantDTO,
    IssueTokenRequest,
    MemberDTO,
    RefreshTokenRequest,
    ReplaceGrantsRequest,
    ReplaceMembersRequest,
    SignInDTO,
    ToolReviewDTO,
    UpdateAgentRequest,
    UsageDTO,
)

router = APIRouter(prefix="/agents", tags=["agents"])
tokens_router = APIRouter(prefix="/agent-tokens", tags=["agents"])

RESOURCE = "agents"
#: Agents live in the tenant of the token; the backend serves one tenant.
TENANT = "default"


# -- helpers -----------------------------------------------------------------


async def _has(rbac: RbacDAO, user: User, action: str) -> bool:
    return user.is_superuser or await rbac.has_permission(user.id, RESOURCE, action)


async def _can_manage(dao: AgentsDAO, rbac: RbacDAO, agent: Agent, user: User) -> bool:
    return await _has(rbac, user, "manage") or await access.is_maintainer(dao, agent, user)


async def _agent_or_404(dao: AgentsDAO, ref: str) -> Agent:
    agent = await dao.get(TENANT, ref)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


async def _managed_agent(dao: AgentsDAO, rbac: RbacDAO, ref: str, user: User) -> Agent:
    agent = await _agent_or_404(dao, ref)
    if not await _can_manage(dao, rbac, agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not maintain this agent.")
    return agent


async def _inspected_agent(dao: AgentsDAO, rbac: RbacDAO, ref: str, user: User) -> Agent:
    """An agent whose details the user may see (``agents:read``) or manages."""
    agent = await _agent_or_404(dao, ref)
    if not (await _has(rbac, user, "read") or await _can_manage(dao, rbac, agent, user)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not maintain this agent.")
    return agent


async def _visible_agent(dao: AgentsDAO, rbac: RbacDAO, ref: str, user: User) -> Agent:
    agent = await _agent_or_404(dao, ref)
    if await _has(rbac, user, "read") or await _can_manage(dao, rbac, agent, user):
        return agent
    if (await access.decide(dao, agent, user)).allowed:
        return agent
    # Not told apart from a missing agent: its existence is not the user's business.
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")


async def _dto(dao: AgentsDAO, agent: Agent, *, can_manage: bool) -> AgentDTO:
    published = await dao.version_by_id(agent.published_version_id)
    latest = await dao.latest_version(agent.id)
    return AgentDTO(
        id=agent.id,
        tenant_id=agent.tenant_id,
        slug=agent.slug,
        name=agent.name,
        description=agent.description,
        status=agent.status,
        published_version=published.version if published else None,
        latest_version=latest.version if latest else None,
        can_manage=can_manage,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _check_extensions(extensions: dict[str, Any]) -> None:
    """Refuse settings no installed extension enforces, and invalid ones."""
    missing = access.unenforced_extensions(extensions)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="These settings need Fadenstack Enterprise: " + ", ".join(missing),
        )
    for key, value in extensions.items():
        try:
            registry.version_extensions[key](value)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid setting {key}: {exc}",
            ) from exc


def _version_fields(fields: AgentVersionFields) -> dict[str, Any]:
    _check_extensions(fields.extensions)
    data = fields.model_dump()
    data["host_tool_contract"] = [tool.model_dump(exclude_none=True) for tool in fields.host_tool_contract]
    names = [tool["name"] for tool in data["host_tool_contract"]]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Host tool names must be unique.")
    return data


async def _audit(
    audit: AuditDAO,
    user: User,
    action: str,
    target_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    await audit.log(
        action=action,
        target_type="agent",
        target_id=target_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        metadata_json=json.dumps(metadata, default=str) if metadata else None,
    )


# -- agents -------------------------------------------------------------------


@router.get("", response_model=list[AgentDTO], name="list_agents")
async def list_agents(
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> list[AgentDTO]:
    """Every agent for readers and managers; otherwise those the user can use or maintains."""
    see_all = await _has(rbac, user, "read") or await _has(rbac, user, "manage")
    agents = await dao.list_all(TENANT) if see_all else await dao.list_for_user(TENANT, user.id)
    manage_all = await _has(rbac, user, "manage")
    return [
        await _dto(dao, a, can_manage=manage_all or await access.is_maintainer(dao, a, user)) for a in agents
    ]


@router.post("", response_model=AgentDTO, status_code=status.HTTP_201_CREATED, name="create_agent")
async def create_agent(
    payload: CreateAgentRequest,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> AgentDTO:
    """A new agent, as a draft with its first version; the creator maintains it."""
    if not await _has(rbac, user, "create"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied: agents:create")
    if await dao.slug_taken(TENANT, payload.slug):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"An agent '{payload.slug}' exists.")
    fields = _version_fields(payload.version)
    agent = await dao.add(
        Agent(
            tenant_id=TENANT,
            slug=payload.slug,
            name=payload.name,
            description=payload.description,
            status=AgentStatus.DRAFT,
            created_by=user.id,
        ),
    )
    await dao.add_version(agent.id, {**fields, "created_by": user.id})
    await dao.add_member(agent.id, user.id, AgentMemberRole.MAINTAINER.value)
    await _audit(audit, user, "agent.create", str(agent.id), {"slug": agent.slug})
    await dao.session.refresh(agent)
    return await _dto(dao, agent, can_manage=True)


@router.get("/principals", response_model=list[PrincipalOption], name="find_agent_principals")
async def list_principals(
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    q: Annotated[str | None, Query(min_length=2, max_length=100)] = None,
    ids: Annotated[str | None, Query(max_length=4000)] = None,
) -> list[PrincipalOption]:
    """Users and teams to grant an agent to or make its maintainers, for those who manage agents.

    ``q`` searches e-mail addresses and group names; ``ids`` (``user:<id>,group:<id>``) names them.
    """
    rights = [await _has(rbac, user, action) for action in ("read", "create", "manage")]
    if not any(rights) and not await dao.maintains_any(user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not manage agents.")
    return await find_principals(dao.session, q=q, ids=ids)


@router.get("/{agent_ref}", response_model=AgentDTO, name="get_agent")
async def get_agent(
    agent_ref: str,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> AgentDTO:
    """One agent the user can see."""
    agent = await _visible_agent(dao, rbac, agent_ref, user)
    return await _dto(dao, agent, can_manage=await _can_manage(dao, rbac, agent, user))


@router.patch("/{agent_ref}", response_model=AgentDTO, name="update_agent")
async def update_agent(
    agent_ref: str,
    payload: UpdateAgentRequest,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> AgentDTO:
    """Rename or re-describe an agent."""
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    if payload.name is not None:
        agent.name = payload.name
    if payload.description is not None:
        agent.description = payload.description
    await dao.session.flush()
    await dao.session.refresh(agent)
    return await _dto(dao, agent, can_manage=True)


@router.post("/{agent_ref}/retire", response_model=AgentDTO, name="retire_agent")
async def retire_agent(
    agent_ref: str,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> AgentDTO:
    """Retire the agent: nobody can use it or sign in to it; it can be restored."""
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    agent.status = AgentStatus.RETIRED
    await dao.session.flush()
    await _audit(audit, user, "agent.retire", str(agent.id))
    await dao.session.refresh(agent)
    return await _dto(dao, agent, can_manage=True)


@router.delete("/{agent_ref}", status_code=status.HTTP_204_NO_CONTENT, name="delete_agent")
async def delete_agent(
    agent_ref: str,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> None:
    """Delete a retired agent for good: its versions, access and sign-ins.

    A role's ``agents:delete``, not maintaining the agent, allows it. Retired first, so a live agent is never
    deleted by mistake -- and a client from before this route, when ``DELETE`` retired, cannot delete one.
    What the agent did stays: the gateway's request log and decisions, and the SDKs' events, keep its id.
    """
    if not await _has(rbac, user, "delete"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied: agents:delete")
    agent = await _agent_or_404(dao, agent_ref)
    if agent.status != AgentStatus.RETIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Retire the agent first (POST /api/agents/{agent.slug}/retire); "
            "only a retired agent can be deleted.",
        )
    # By name: once deleted, the id resolves to nothing.
    record = {"slug": agent.slug, "name": agent.name, "versions": len(await dao.versions(agent.id))}
    await _audit(audit, user, "agent.delete", str(agent.id), record)
    await dao.remove(agent)


@router.post("/{agent_ref}/restore", response_model=AgentDTO, name="restore_agent")
async def restore_agent(
    agent_ref: str,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> AgentDTO:
    """Bring a retired agent back, as it was."""
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    agent.status = AgentStatus.PUBLISHED if agent.published_version_id else AgentStatus.DRAFT
    await dao.session.flush()
    await _audit(audit, user, "agent.restore", str(agent.id))
    await dao.session.refresh(agent)
    return await _dto(dao, agent, can_manage=True)


# -- versions -----------------------------------------------------------------


@router.get("/{agent_ref}/versions", response_model=list[AgentVersionDTO], name="list_agent_versions")
async def list_versions(
    agent_ref: str,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> list[AgentVersion]:
    """Every version of the agent, newest first."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    return await dao.versions(agent.id)


@router.post(
    "/{agent_ref}/versions",
    response_model=AgentVersionDTO,
    status_code=status.HTTP_201_CREATED,
    name="create_agent_version",
)
async def create_version(
    agent_ref: str,
    payload: AgentVersionFields,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> AgentVersion:
    """A new draft version. Versions are never changed; a change is a new version."""
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    return await dao.add_version(agent.id, {**_version_fields(payload), "created_by": user.id})


@router.get("/{agent_ref}/versions/{number}", response_model=AgentVersionDTO, name="get_agent_version")
async def get_version(
    agent_ref: str,
    number: int,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> AgentVersion:
    """One version of the agent."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    version = await dao.version(agent.id, number)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return version


@router.post(
    "/{agent_ref}/versions/{number}/publish",
    response_model=AgentDTO,
    name="publish_agent_version",
)
async def publish_version(
    agent_ref: str,
    number: int,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> AgentDTO:
    """Serve this version to the agent's users. An older one rolls back."""
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    version = await dao.version(agent.id, number)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    await _publish(dao, audit, user, agent, version)
    await dao.session.refresh(agent)
    return await _dto(dao, agent, can_manage=True)


async def _publish(dao: AgentsDAO, audit: AuditDAO, user: User, agent: Agent, version: AgentVersion) -> None:
    """Serve *version* to the agent's users."""
    if agent.status == AgentStatus.RETIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Restore the agent before publishing.",
        )
    _check_extensions(version.extensions)
    if version.published_at is None:
        version.published_at = datetime.now(UTC)
    agent.published_version_id = version.id
    agent.status = AgentStatus.PUBLISHED
    await dao.session.flush()
    await _audit(audit, user, "agent.publish", str(agent.id), {"version": version.version})


# -- grants, members, access ----------------------------------------------------


async def _grant_dtos(dao: AgentsDAO, agent: Agent) -> list[GrantDTO]:
    grants = await dao.grants(agent.id)
    groups = await dao.existing_groups(
        {g.principal_id for g in grants if g.principal_type == PrincipalType.GROUP},
    )
    users = await dao.existing_users(
        {g.principal_id for g in grants if g.principal_type == PrincipalType.USER},
    )
    names = groups | users
    return [
        GrantDTO(
            principal_type=g.principal_type, principal_id=g.principal_id, label=names.get(g.principal_id),
        )
        for g in grants
    ]


async def _member_dtos(dao: AgentsDAO, members: list[Any]) -> list[MemberDTO]:
    emails = await dao.existing_users({m.user_id for m in members})
    return [MemberDTO(user_id=m.user_id, role=m.role, label=emails.get(m.user_id)) for m in members]


@router.get("/{agent_ref}/grants", response_model=list[GrantDTO], name="list_agent_grants")
async def list_grants(
    agent_ref: str,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> list[GrantDTO]:
    """The users and teams allowed to use the agent."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    return await _grant_dtos(dao, agent)


@router.put("/{agent_ref}/grants", response_model=list[GrantDTO], name="replace_agent_grants")
async def replace_grants(
    agent_ref: str,
    payload: ReplaceGrantsRequest,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> list[GrantDTO]:
    """The users and teams allowed to use the agent; replaces the list."""
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    user_ids = {g.principal_id for g in payload.grants if g.principal_type == PrincipalType.USER}
    group_ids = {g.principal_id for g in payload.grants if g.principal_type == PrincipalType.GROUP}
    unknown = (user_ids - (await dao.existing_users(user_ids)).keys()) | (
        group_ids - (await dao.existing_groups(group_ids)).keys()
    )
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown users or groups: " + ", ".join(sorted(map(str, unknown))),
        )
    await dao.replace_grants(agent.id, [(g.principal_type, g.principal_id) for g in payload.grants], user.id)
    await _audit(
        audit, user, "agent.grants", str(agent.id), {"grants": [g.model_dump() for g in payload.grants]},
    )
    return await _grant_dtos(dao, agent)


@router.get("/{agent_ref}/members", response_model=list[MemberDTO], name="list_agent_members")
async def list_members(
    agent_ref: str,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> list[MemberDTO]:
    """The agent's maintainers."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    return await _member_dtos(dao, await dao.members(agent.id))


@router.put("/{agent_ref}/members", response_model=list[MemberDTO], name="replace_agent_members")
async def replace_members(
    agent_ref: str,
    payload: ReplaceMembersRequest,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> list[MemberDTO]:
    """The agent's maintainers (and, with Enterprise, auditors); replaces the list."""
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    bad_roles = {m.role for m in payload.members} - registry.member_roles
    if bad_roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown roles: " + ", ".join(sorted(bad_roles)),
        )
    ids = {m.user_id for m in payload.members}
    unknown = ids - (await dao.existing_users(ids)).keys()
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown users: " + ", ".join(sorted(map(str, unknown))),
        )
    rows = await dao.replace_members(agent.id, {m.user_id: m.role for m in payload.members})
    await _audit(
        audit, user, "agent.members", str(agent.id), {"members": [m.model_dump() for m in payload.members]},
    )
    return await _member_dtos(dao, rows)


@router.get("/{agent_ref}/access/{user_id}", response_model=AccessDTO, name="explain_agent_access")
async def explain_access(
    agent_ref: str,
    user_id: uuid.UUID,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> AccessDTO:
    """Whether a user may use the agent, and why."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    target = await dao.session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    decision = await access.decide(dao, agent, target)
    return AccessDTO(allowed=decision.allowed, code=decision.code, detail=decision.detail)


# -- usage and sign-ins -------------------------------------------------------------


@router.get("/{agent_ref}/usage", response_model=UsageDTO, name="agent_usage")
async def agent_usage(
    agent_ref: str,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    days: int = 30,
) -> UsageDTO:
    """Requests, tokens, users and conversations of the agent -- no content."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Gateway database not available")
    since = datetime.now(UTC) - timedelta(days=max(1, min(days, 90)))
    params = {"agent": str(agent.id), "since": since}
    async with factory() as gw:
        totals = (
            await gw.execute(
                text("""
                SELECT COUNT(*) AS requests,
                       COALESCE(SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END), 0) AS errors,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       COUNT(DISTINCT user_id) AS users,
                       COUNT(DISTINCT conversation_id) AS conversations
                FROM llm_gateway_request_log
                WHERE agent_id = :agent AND created_at >= :since
                """),
                params,
            )
        ).mappings().one()
        days_rows = (
            await gw.execute(
                text("""
                SELECT date_trunc('day', created_at) AS day, COUNT(*) AS requests,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM llm_gateway_request_log
                WHERE agent_id = :agent AND created_at >= :since
                GROUP BY 1 ORDER BY 1
                """),
                params,
            )
        ).mappings().all()
    return UsageDTO(
        **{k: int(v or 0) for k, v in totals.items()},
        by_day=[
            {
                "day": row["day"].date().isoformat(),
                "requests": row["requests"],
                "total_tokens": row["total_tokens"],
            }
            for row in days_rows
        ],
    )


@router.get("/{agent_ref}/events", response_model=list[ClientEventDTO], name="agent_client_events")
async def agent_client_events(
    agent_ref: str,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    type: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[ClientEventDTO]:
    """What the agent's SDKs reported (02 §9), newest first, for its maintainers."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Gateway database not available")
    async with factory() as gw:
        rows = (
            await gw.execute(
                text("""
                SELECT id, type, user_id, device_id, conversation_id, data, occurred_at, created_at
                FROM agent_client_event
                WHERE agent_id = :agent
                  AND (CAST(:type AS TEXT) IS NULL OR type = :type)
                  AND (CAST(:since AS TIMESTAMPTZ) IS NULL OR created_at >= :since)
                ORDER BY created_at DESC LIMIT :limit
                """),
                {"agent": str(agent.id), "type": type, "since": since, "limit": max(1, min(limit, 500))},
            )
        ).mappings().all()
    return [ClientEventDTO(**row) for row in rows]


# -- what the apps bring ---------------------------------------------------------------


async def _manifests(request: Request, agent_id: uuid.UUID) -> list[app_review.ManifestRow]:
    """The manifests the gateway kept for the agent, newest first; none while its table is not there yet."""
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Gateway database not available")
    since = datetime.now(UTC) - timedelta(days=app_review.CURRENT_DAYS)
    async with factory() as gw:
        try:
            rows = (
                await gw.execute(
                    text("""
                    SELECT source, app_name, app_version, manifest, first_seen_at, last_seen_at, reports,
                           last_user_id
                    FROM agent_client_manifest
                    WHERE agent_id = :agent AND last_seen_at >= :since
                    ORDER BY last_seen_at DESC LIMIT 500
                    """),
                    {"agent": str(agent_id), "since": since},
                )
            ).mappings().all()
        except ProgrammingError:  # a gateway not upgraded yet
            return []
    return [
        app_review.ManifestRow(
            source=row["source"],
            app_name=row["app_name"],
            app_version=row["app_version"],
            manifest=json.loads(row["manifest"]) if isinstance(row["manifest"], str) else row["manifest"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            reports=row["reports"],
            last_user_id=row["last_user_id"],
        )
        for row in rows
    ]


async def _review(
    request: Request, dao: AgentsDAO, agent: Agent,
) -> tuple[app_review.Review, AgentVersion | None]:
    """What the apps bring, against the agent's latest version; and that version."""
    latest = await dao.latest_version(agent.id)
    return app_review.review(await _manifests(request, agent.id), latest, now=datetime.now(UTC)), latest


@router.get("/{agent_ref}/apps", response_model=AppsReviewDTO, name="agent_apps")
async def agent_apps(
    agent_ref: str,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> AppsReviewDTO:
    """The apps that run the agent and what they bring, against its latest version (``app_review``)."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    review, _ = await _review(request, dao, agent)
    labels: dict[str, str | None] = {}
    for app in review.apps:
        if app.last_user_id not in labels:
            try:
                found = await dao.session.get(User, uuid.UUID(app.last_user_id))
            except ValueError:
                found = None
            labels[app.last_user_id] = found.email if found else None
    return AppsReviewDTO(
        compared_with=review.compared_with,
        unrestricted=review.unrestricted,
        apps=[AppSeenDTO(**vars(app), last_user_label=labels.get(app.last_user_id)) for app in review.apps],
        tools=[ToolReviewDTO(**vars(tool)) for tool in review.tools],
        ui=review.ui,
        pending=review.pending,
    )


@router.post(
    "/{agent_ref}/apps/accept",
    response_model=AgentVersionDTO,
    status_code=status.HTTP_201_CREATED,
    name="accept_agent_apps",
)
async def accept_agent_apps(
    agent_ref: str,
    payload: AcceptAppsRequest,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> AgentVersion:
    """A new version with what the apps bring, as the admin chose it -- published at once if asked."""
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    review, latest = await _review(request, dao, agent)
    base = AgentVersionFields.model_validate(latest, from_attributes=True) if latest else AgentVersionFields()
    try:
        fields = app_review.accept(
            base.model_dump(mode="json"),
            review,
            tools=[(t.name, t.risk) for t in payload.tools],
            remove=payload.remove,
            take_ui=payload.ui,
        )
    except app_review.AcceptError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    version = await dao.add_version(
        agent.id, {**_version_fields(AgentVersionFields.model_validate(fields)), "created_by": user.id},
    )
    await _audit(audit, user, "agent.apps_accept", str(agent.id), {
        "version": version.version,
        "tools": {t.name: t.risk for t in payload.tools},
        "removed": payload.remove,
        "ui": payload.ui,
    })
    if payload.publish:
        await _publish(dao, audit, user, agent, version)
    return version


def _sign_in(row: Any) -> SignInDTO:
    return SignInDTO(
        device_id=row.family_id,
        agent_id=row.agent_id,
        user_id=row.user_id,
        device_label=row.device_label,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        idle_expires_at=row.idle_expires_at,
    )


@router.get("/{agent_ref}/sign-ins", response_model=list[SignInDTO], name="list_agent_sign_ins")
async def list_agent_sign_ins(
    agent_ref: str,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> list[SignInDTO]:
    """The devices signed in to the agent, for its maintainers."""
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    return [_sign_in(r) for r in await dao.active_families(now=datetime.now(UTC), agent_id=agent.id)]


# -- tokens --------------------------------------------------------------------------


async def _secret(session: AsyncSession) -> str:
    from faden_backend.web.api.admin.users.views import _resolve_users_secret  # noqa: PLC0415

    secret = await _resolve_users_secret(session)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT secret is not configured. Set 'Backend Users JWT Secret' in System Settings.",
        )
    return secret


def _tokens_dto(issued: tokens.IssuedTokens) -> AgentTokensDTO:
    return AgentTokensDTO(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        refresh_token=issued.refresh_token,
        refresh_expires_in=issued.refresh_expires_in,
        device_id=issued.device_id,
        agent_id=issued.agent.id,
        agent=issued.agent.slug,
    )


def _token_error(exc: tokens.AgentTokenError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


@tokens_router.post(
    "",
    response_model=AgentTokensDTO,
    status_code=status.HTTP_201_CREATED,
    name="issue_agent_tokens",
)
async def issue_agent_tokens(
    payload: IssueTokenRequest,
    response: Response,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> AgentTokensDTO:
    """Sign the user in to an agent on one device: an access and a refresh token."""
    agent = await dao.get(TENANT, payload.agent)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    try:
        issued = await tokens.issue(
            dao, secret=await _secret(dao.session), user=user, agent=agent, device_label=payload.device_label,
        )
    except tokens.AgentTokenError as exc:
        raise _token_error(exc) from exc
    await _audit(audit, user, "agent_token.issue", str(agent.id), {"device": str(issued.device_id)})
    response.headers["Cache-Control"] = "no-store"
    return _tokens_dto(issued)


@tokens_router.post("/refresh", response_model=AgentTokensDTO, name="refresh_agent_tokens")
async def refresh_agent_tokens(
    payload: RefreshTokenRequest,
    response: Response,
    dao: AgentsDAO = Depends(),
) -> AgentTokensDTO:
    """New tokens for a refresh token; the refresh token is replaced."""
    try:
        issued = await tokens.refresh(
            dao, secret=await _secret(dao.session), refresh_token=payload.refresh_token,
        )
    except tokens.AgentTokenError as exc:
        # The revocation a reuse or a lost grant causes must stick.
        await dao.session.commit()
        raise _token_error(exc) from exc
    response.headers["Cache-Control"] = "no-store"
    return _tokens_dto(issued)


@tokens_router.get("/mine", response_model=list[SignInDTO], name="list_my_agent_sign_ins")
async def list_my_sign_ins(
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
) -> list[SignInDTO]:
    """The user's own sign-ins to agents."""
    return [_sign_in(r) for r in await dao.active_families(now=datetime.now(UTC), user_id=user.id)]


@tokens_router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT, name="revoke_agent_sign_in")
async def revoke_sign_in(
    device_id: uuid.UUID,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> Response:
    """End a sign-in: the user's own, or any for a maintainer of the agent."""
    row = await dao.family(device_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sign-in not found")
    if row.user_id != user.id:
        agent = await dao.session.get(Agent, row.agent_id)
        if agent is None or not await _can_manage(dao, rbac, agent, user):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sign-in not found")
    await dao.revoke_family(device_id, datetime.now(UTC))
    await _audit(audit, user, "agent_token.revoke", str(row.agent_id), {"device": str(device_id)})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
