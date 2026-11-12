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
from fastapi import Path as FastAPIPath
from fastapi.responses import JSONResponse
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
    AgentPairing,
    AgentPairingStatus,
    AgentStatus,
    AgentVersion,
    PrincipalType,
)
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import approval_config
from faden_backend.services.agents import access, app_review, app_templates, pairing, tokens
from faden_backend.services.extensions import registry
from faden_backend.services.principals import PrincipalOption, find_principals
from faden_backend.services.usage import config as usage_config
from faden_backend.services.usage.measures import Measures
from faden_backend.services.usage.suppression import Cell, hide
from faden_backend.web.api.agents.schema import (
    AcceptAppsRequest,
    AccessDTO,
    AgentDTO,
    AgentTemplateDTO,
    AgentTokensDTO,
    AgentVersionDTO,
    AgentVersionFields,
    AgentVersionInput,
    AppSeenDTO,
    AppsReviewDTO,
    ClientEventDTO,
    CreateAgentRequest,
    GrantDTO,
    IssueTokenRequest,
    MemberDTO,
    PairingAgentDTO,
    PairingAgentsEntryDTO,
    PairingClient,
    PairingCodeRequest,
    PairingLookupDTO,
    PairingStartDTO,
    PairingTokenRequest,
    RefreshTokenRequest,
    ReplaceGrantsRequest,
    ReplaceMembersRequest,
    RevokeTokenRequest,
    SignedInUserDTO,
    SignInDTO,
    SignInLastsDTO,
    StartPairingRequest,
    ToolReviewDTO,
    UpdateAgentRequest,
    UsageDTO,
)
from faden_backend.web.origin import public_origin

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


async def _may_inspect(dao: AgentsDAO, rbac: RbacDAO, agent: Agent, user: User) -> bool:
    """Whether the user may see the agent's details: ``agents:read``, managing it, or a seeing member role."""
    return (
        await _has(rbac, user, "read")
        or await _can_manage(dao, rbac, agent, user)
        or await access.inspects(dao, agent, user)
    )


async def _inspected_agent(dao: AgentsDAO, rbac: RbacDAO, ref: str, user: User) -> Agent:
    """An agent whose details the user may see (``agents:read``, managing it, or e.g. auditing it)."""
    agent = await _agent_or_404(dao, ref)
    if not await _may_inspect(dao, rbac, agent, user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not maintain this agent.")
    return agent


async def _visible_agent(dao: AgentsDAO, rbac: RbacDAO, ref: str, user: User) -> Agent:
    agent = await _agent_or_404(dao, ref)
    if await _may_inspect(dao, rbac, agent, user):
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
        unenforced=access.unenforced(agent, published) if can_manage else [],
        unenforced_restrictions=access.unenforced_restrictions(agent) if can_manage else [],
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


def _check_approvals(contract: list[dict[str, Any]]) -> None:
    """Refuse ``approval: never`` on a tool that is not ``read`` or ``write`` (sdk/38 §4.3, D7).

    ``never`` lets the author spare the person a question the chat's mode would ask. For a destructive or
    unclassified tool that question is the person's to answer, so such a version is not served. Checked at
    publish, not when a draft is saved: an admin may reclassify a tool on the Apps tab first.
    """
    wrong = [
        str(t.get("name")) for t in contract
        if t.get("approval") == "never" and t.get("risk") not in ("read", "write")
    ]
    if wrong:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "'Never ask' (approval: never) is only for read and write tools. Change the class or the "
                "approval of: " + ", ".join(wrong)
            ),
        )


async def _check_bypass(session: Any, approvals: dict[str, Any] | None) -> None:
    """Refuse a version whose highest level is Bypass while the installation does not allow it (sdk/38 §5).

    Checked at publish, as ``approval: never``: a draft may hold it while an admin decides. Turning Bypass off
    later leaves such versions published; the gateway then narrows them to Auto (sdk/09 L-57).
    """
    if (approvals or {}).get("max_level") != "bypass":
        return
    if not await approval_config.bypass_allowed(session):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Bypass is not allowed in this installation, so no agent may go up to it. An administrator "
                "turns it on in Settings, Approvals (Root Mode); or choose Auto as the highest level."
            ),
        )


def _version_fields(fields: AgentVersionFields) -> dict[str, Any]:
    _check_extensions(fields.extensions)
    data = fields.model_dump()
    data["host_tool_contract"] = [tool.model_dump(exclude_none=True) for tool in fields.host_tool_contract]
    data["approvals"] = fields.approvals.model_dump(exclude_none=True)
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
    severity: str = "normal",
) -> None:
    await audit.log(
        action=action,
        target_type="agent",
        target_id=target_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=severity,
        metadata_json=json.dumps(metadata, default=str) if metadata else None,
    )


# -- agents -------------------------------------------------------------------


@router.get("", response_model=list[AgentDTO], name="list_agents")
async def list_agents(
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
) -> list[AgentDTO]:
    """Every agent for readers and managers; otherwise those the user can use, maintains or e.g. audits."""
    see_all = await _has(rbac, user, "read") or await _has(rbac, user, "manage")
    if see_all:
        agents = await dao.list_all(TENANT)
    else:
        agents = await dao.list_for_user(TENANT, user.id, registry.member_roles)
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
    """Users and teams to grant an agent to, make its members or check the access of.

    For those who manage agents or are members of one (maintainers; e.g. auditors, for the access check).
    ``q`` searches e-mail addresses and group names; ``ids`` (``user:<id>,group:<id>``) names them. Those who
    grant nothing (auditors) name a person by their whole address and a team by its whole name: they check
    access one person at a time and need no list of everyone (GDPR Art. 5(1)(c), 25(2)).
    """
    rights = [await _has(rbac, user, action) for action in ("read", "create", "manage")]
    grants = any(rights) or await dao.maintains_any(user.id)
    if not grants and not await dao.member_of_any(user.id, registry.member_roles):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not manage agents.")
    return await find_principals(dao.session, q=q, ids=ids, exact=not grants)


@router.get("/templates", response_model=list[AgentTemplateDTO], name="agent_templates")
async def agent_templates(
    user: Annotated[User, Depends(current_active_user)],
    rbac: RbacDAO = Depends(),
) -> list[dict[str, Any]]:
    """Agents for Fadenstack's own apps (Chrome, Office), ready to create from, as the apps ship them."""
    if not (await _has(rbac, user, "create") or await _has(rbac, user, "manage")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied: agents:create")
    return app_templates.templates()


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
    payload: AgentVersionInput,
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
    _check_approvals(version.host_tool_contract or [])
    await _check_bypass(dao.session, version.approvals)
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
    # A role nothing adds any more may stay where it is (it gives nothing); it is not given anew.
    kept = {m.user_id: m.role for m in await dao.members(agent.id)}
    bad_roles = {
        m.role
        for m in payload.members
        if m.role not in registry.member_roles and kept.get(m.user_id) != m.role
    }
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
    audit: AuditDAO = Depends(),
) -> AccessDTO:
    """Whether a user may use the agent, and why.

    Each check is recorded, who checked whom (GDPR Art. 5(2): a look at someone's access is accountable).
    """
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    target = await dao.session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    decision = await access.decide(dao, agent, target)
    await _audit(
        audit, user, "agent.access.check", str(agent.id),
        {
            "checked": str(target.id), "email": target.email, "allowed": decision.allowed,
            "code": decision.code,
        },
    )
    return AccessDTO(allowed=decision.allowed, code=decision.code, detail=decision.detail)


@router.delete("/{agent_ref}/restrictions/{name}", response_model=AgentDTO, name="lift_agent_restriction")
async def lift_restriction(
    agent_ref: str,
    name: Annotated[str, FastAPIPath(max_length=64)],
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    rbac: RbacDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> AgentDTO:
    """Lift a restriction nothing installed enforces: everyone granted can use the agent again.

    Refused while an extension enforces it (it is changed there). The extension may set it again once back.
    """
    agent = await _managed_agent(dao, rbac, agent_ref, user)
    if name not in (agent.restricted_by or []):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The agent has no such restriction")
    if name in registry.agent_restrictions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This restriction is enforced; change it where it is kept.",
        )
    await access.set_restricted(dao.session, agent.id, name, on=False)
    await _audit(audit, user, "agent.restriction.lift", str(agent.id), {"restriction": name}, severity="high")
    await dao.session.refresh(agent)
    return await _dto(dao, agent, can_manage=True)


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
    """Requests, tokens, users and conversations of the agent -- no content, and nobody named.

    The figures show only when at least k distinct people used the agent in the range, and each day only with
    k people (with complementary suppression, ``services/usage/suppression.py``): an agent of three people
    shows what each of them did (M04, M05). An agent only the caller used shows them their own figures.
    """
    agent = await _inspected_agent(dao, rbac, agent_ref, user)
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Gateway database not available")
    k = (await usage_config.read(dao.session)).k
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
        day_rows = (
            await gw.execute(
                text("""
                SELECT date_trunc('day', created_at) AS day, user_id, COUNT(*) AS requests,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens
                FROM llm_gateway_request_log
                WHERE agent_id = :agent AND created_at >= :since
                GROUP BY 1, 2 ORDER BY 1
                """),
                params,
            )
        ).mappings().all()
    people = {str(row["user_id"]) for row in day_rows}
    own_only = people <= {str(user.id)}
    if len(people) < k and not own_only:
        return UsageDTO(suppressed=True, min_group_size=k, by_day=[])
    per_day: dict[Any, tuple[Measures, set[str]]] = {}
    for row in day_rows:
        measures, users = per_day.setdefault(row["day"].date(), (Measures(), set()))
        measures.add({"requests": row["requests"], "input_tokens": row["total_tokens"]})
        users.add(str(row["user_id"]))
    hidden: set[Any] = set()
    if not own_only:
        cells = [Cell(key=day, measures=m, floor=len(users)) for day, (m, users) in per_day.items()]

        async def together(group: list[Cell]) -> int:
            return len(set().union(*(per_day[cell.key][1] for cell in group)))

        hidden, _ = await hide(cells, k, together)
    counts = {name: int(value or 0) for name, value in totals.items()}
    return UsageDTO(
        requests=counts["requests"],
        errors=counts["errors"],
        prompt_tokens=counts["prompt_tokens"],
        completion_tokens=counts["completion_tokens"],
        total_tokens=counts["total_tokens"],
        users=counts["users"],
        conversations=counts["conversations"],
        min_group_size=k,
        by_day=[
            {"day": day.isoformat(), "requests": None, "total_tokens": None, "suppressed": True}
            if day in hidden
            else {
                "day": day.isoformat(), "requests": measures.requests,
                "total_tokens": measures.counts["input_tokens"], "suppressed": False,
            }
            for day, (measures, _) in sorted(per_day.items())
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
    """What the agent's SDKs reported (02 §9), newest first, for its maintainers; nobody else named.

    An event of another person keeps no ``user_id`` (the usage of named people is seen only through
    ``/api/admin/usage/people``).
    """
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
    own = str(user.id)
    return [
        ClientEventDTO(**{**row, "user_id": row["user_id"] if str(row["user_id"]) == own else None})
        for row in rows
    ]


# -- what the apps bring ---------------------------------------------------------------


async def _manifests(request: Request, agent_id: uuid.UUID) -> list[app_review.ManifestRow]:
    """The manifests the gateway kept for the agent, newest first; none while its table is not there yet."""
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Gateway database not available")
    since = datetime.now(UTC) - timedelta(days=app_review.CURRENT_DAYS)
    async with factory() as gw:
        try:
            found = await gw.execute(text(app_review.MANIFEST_SQL), {"agent": str(agent_id), "since": since})
        except ProgrammingError:  # a gateway not upgraded yet
            return []
        return [app_review.manifest_row(row) for row in found.mappings().all()]


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
    rows = await _manifests(request, agent.id)
    now = datetime.now(UTC)
    latest = await dao.latest_version(agent.id)
    review = app_review.review(rows, latest, now=now)
    published = await dao.version_by_id(agent.published_version_id)
    live = review if published is None or latest is None or published.id == latest.id else (
        app_review.review(rows, published, now=now)
    )
    # Who reported an app last is named only when it is the caller: nobody else is (as in agent_usage).
    own = str(user.id)

    def seen(app: Any) -> AppSeenDTO:
        mine = app.last_user_id == own
        return AppSeenDTO(**{
            **vars(app), "last_user_id": app.last_user_id if mine else None,
            "last_user_label": user.email if mine else None,
        })

    return AppsReviewDTO(
        compared_with=review.compared_with,
        unrestricted=review.unrestricted,
        apps=[seen(app) for app in review.apps],
        tools=[ToolReviewDTO(**vars(tool)) for tool in review.tools],
        ui=review.ui,
        pending=review.pending,
        published_version=published.version if published else None,
        live_pending=live.pending,
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
        user=SignedInUserDTO(id=issued.user.id, email=issued.user.email),
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


@tokens_router.post("/revoke", status_code=status.HTTP_204_NO_CONTENT, name="revoke_agent_tokens")
async def revoke_agent_tokens(
    payload: RevokeTokenRequest,
    dao: AgentsDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> Response:
    """The app ends its own sign-in with its refresh token (signing out on the device).

    Always 204, whether or not the token matched: the answer tells nothing about tokens. A match ends the
    whole sign-in, every token of its family, as revoking it in *My sign-ins* does.
    """
    row = await dao.refresh_token_by_hash(tokens.hash_token(payload.refresh_token))
    if row is not None and await dao.revoke_family(row.family_id, datetime.now(UTC)):
        await audit.log(
            action="agent_token.revoke",
            target_type="agent",
            target_id=str(row.agent_id),
            result=AuditResult.ALLOW,
            actor_id=row.user_id,
            metadata_json=json.dumps({"device": str(row.family_id), "by": "app"}),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)


# -- pairing (RFC 8628) ------------------------------------------------------------------
#
# An app is signed in by a person who approves its code in the console (services/agents/pairing.py). Every
# answer is ``Cache-Control: no-store``. The app's routes (start, token) need no sign-in; the console's
# (lookup, approve, deny) need a console user, and the CSRF check covers their POSTs as every other.
# Neither code ever goes into an address: the user code travels in request bodies and in the fragment of the
# page link, which browsers never send, so no access log has it (ASVS 5.0 14.2.1). The audit trail (V16) gets
# agent_pairing.start / .approve / .deny and agent_token.issue, by the pairing's id, never a code.

_NO_STORE = {"Cache-Control": "no-store"}
#: The one answer for an agent that is unknown, retired or not granted: which of them is nobody's business.
_NOT_AVAILABLE = "This agent is not available to you."
#: The console page that approves a pairing.
PAIRING_PAGE = "/pair"


def _refuse(status_code: int, code: str, message: str) -> HTTPException:
    """An error in the backend's shape, not cached."""
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}, headers=_NO_STORE,
    )


def _too_many(wait: int, message: str) -> HTTPException:
    """429 with the seconds to wait (``Retry-After``)."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "too_many_attempts", "message": message, "retry_after": wait},
        headers={**_NO_STORE, "Retry-After": str(wait)},
    )


def _caller(request: Request) -> str | None:
    """The caller's address as uvicorn gives it: behind nginx, the client's (``services/agents/pairing``)."""
    return pairing.address(request.client.host if request.client else None)


@tokens_router.post(
    "/pairings",
    response_model=PairingStartDTO,
    status_code=status.HTTP_201_CREATED,
    name="start_agent_pairing",
)
async def start_agent_pairing(
    payload: StartPairingRequest,
    request: Request,
    response: Response,
    dao: AgentsDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> PairingStartDTO:
    """An app asks to be signed in to an agent: a code to show, and one to poll with (RFC 8628 §3.1, §3.2).

    No sign-in. The answer is the same whatever the agent: whether it exists, and whether the person may use
    it, is decided at the approval. 10 per address in 10 minutes; beyond, 429 with ``Retry-After``.
    """
    now = datetime.now(UTC)
    ip = _caller(request)
    wait = await pairing.start_wait(dao.session, ip, now)
    if wait:
        # A limit reached is a security event (ASVS 5.0 16.3.3): the address and the wait, never a code.
        await audit.log(
            action="agent_pairing.start_limited",
            target_type="agent_pairing",
            target_id="-",
            result=AuditResult.DENY,
            severity="warning",
            metadata_json=json.dumps({"ip": ip, "retry_after": wait}),
        )
        raise _too_many(wait, "Too many sign-in requests from this address; try again later.")
    started = await pairing.start(
        dao.session,
        secret=await _secret(dao.session),
        tenant_id=TENANT,
        agent_refs=payload.agent_refs(),
        device_label=payload.device_label,
        client_name=payload.client.name,
        client_version=payload.client.version,
        client_os=payload.client.os,
        ip=ip,
        now=now,
    )
    await audit.log(
        action="agent_pairing.start",
        target_type="agent_pairing",
        target_id=str(started.pairing.id),
        result=AuditResult.ALLOW,
        metadata_json=json.dumps({"agent": payload.agent, "agents": payload.agent_refs(), "ip": ip}),
    )
    response.headers["Cache-Control"] = "no-store"
    page = f"{public_origin(request)}{PAIRING_PAGE}"
    user_code = pairing.shown(started.user_code)
    return PairingStartDTO(
        pairing_code=started.pairing_code,
        user_code=user_code,
        verification_uri=page,
        verification_uri_complete=f"{page}#code={user_code}",
        expires_in=pairing.LIFETIME_S,
        interval=pairing.INTERVAL_S,
        agents=payload.agent_refs(),
    )


@tokens_router.post(
    "/pairings/token",
    response_model=AgentTokensDTO,
    status_code=status.HTTP_201_CREATED,
    name="agent_pairing_token",
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "RFC 8628 §3.5: ``{error, error_description}`` with ``authorization_pending``, "
            "``slow_down`` (and the new ``interval``), ``access_denied``, ``expired_token`` or "
            "``invalid_grant``.",
        },
    },
)
async def agent_pairing_token(
    payload: PairingTokenRequest,
    response: Response,
    dao: AgentsDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> Any:
    """The app polls with its pairing code: 400 with an RFC 8628 error until approved, then once its tokens.

    The tokens are a new sign-in of the approving person on this device (the pairing's device label), with
    ``user`` saying who that is. A pairing for several agents gives each approved agent's tokens once, to the
    request that names it (``agent``; the first when omitted); an agent not listed, refused or collected already
    answers ``invalid_grant``. The pairing is consumed when every approved agent is collected, or ten minutes
    after the approval.
    """
    try:
        paired = await pairing.poll(
            dao,
            secret=await _secret(dao.session),
            pairing_code=payload.pairing_code,
            agent=payload.agent,
            now=datetime.now(UTC),
        )
    except pairing.PairingError as exc:
        # The poll's time, a raised interval or a noted expiry must stick, although the answer is an error.
        await dao.session.commit()
        body: dict[str, Any] = {"error": exc.code, "error_description": exc.description}
        if exc.interval is not None:
            body["interval"] = exc.interval
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=body, headers=_NO_STORE)
    issued = paired.issued
    await _audit(
        audit, issued.user, "agent_token.issue", str(issued.agent.id),
        {"device": str(issued.device_id), "pairing": str(paired.pairing.id)},
    )
    response.headers["Cache-Control"] = "no-store"
    return _tokens_dto(issued)


async def _pending_pairing(
    code: str, request: Request, user: User, dao: AgentsDAO, audit: AuditDAO,
) -> AgentPairing:
    """The pending pairing *code* names, for the console, after the brute-force limits (RFC 8628 §5.1).

    429 while the person or their address is over a limit; 404 for a wrong or expired code, which counts
    against both; 409 once it was decided. The account itself is never locked (ASVS 5.0 6.1.1): only these
    routes wait.
    """
    now = datetime.now(UTC)
    ip = _caller(request)
    wait = await pairing.failure_wait(dao.session, user.id, ip, now)
    if wait:
        # A wrong code and a limit reached are security events (ASVS 5.0 16.3.3): who, from where, never the code.
        await audit.log(
            action="agent_pairing.limited",
            target_type="agent_pairing",
            target_id="-",
            result=AuditResult.DENY,
            actor_id=user.id,
            severity="warning",
            metadata_json=json.dumps({"ip": ip, "retry_after": wait}),
        )
        raise _too_many(wait, "Too many wrong codes; try again later.")
    row = await pairing.find(dao.session, secret=await _secret(dao.session), entered=code, now=now)
    if row is None:
        await pairing.record_failure(dao.session, user.id, ip, now)
        # The count, and an expiry noted, must stick although the answer is an error.
        await dao.session.commit()
        await audit.log(
            action="agent_pairing.wrong_code",
            target_type="agent_pairing",
            target_id="-",
            result=AuditResult.DENY,
            actor_id=user.id,
            metadata_json=json.dumps({"ip": ip}),
        )
        raise _refuse(
            status.HTTP_404_NOT_FOUND,
            "unknown_code",
            "No sign-in waits for this code. Check it, or get a new one in the app.",
        )
    if row.status != AgentPairingStatus.PENDING:
        raise _refuse(
            status.HTTP_409_CONFLICT, "already_decided", "This sign-in was already approved or denied.",
        )
    return row


def _pairing_record(row: AgentPairing, **more: Any) -> str:
    """What the audit trail keeps about a pairing: never a code."""
    return json.dumps(
        {
            "pairing": str(row.id),
            "device_label": row.device_label,
            "requester_ip": str(row.requester_ip) if row.requester_ip is not None else None,
            **more,
        },
    )


@tokens_router.post("/pairings/lookup", response_model=PairingLookupDTO, name="lookup_agent_pairing")
async def lookup_agent_pairing(
    payload: PairingCodeRequest,
    request: Request,
    response: Response,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> PairingLookupDTO:
    """What asks to be signed in with this code, for the person to check before approving (ASVS 5.0 10.7.1).

    A POST, so the code stays out of addresses and logs. ``available`` is false, and the agent's name null,
    when the person may not use the agent -- unknown, retired and not granted alike.
    """
    row = await _pending_pairing(payload.code, request, user, dao, audit)
    named = await pairing.members(dao.session, row)
    listed = await pairing.resolve_all(dao, row, user, named)
    entries = [
        PairingAgentsEntryDTO(
            slug=one.agent.slug if one.agent is not None and one.available else one.ref,
            name=one.agent.name if one.agent is not None and one.available else None,
            available=one.available,
        )
        for one in listed
    ]
    first_usable = next((one.agent for one in listed if one.available and one.agent is not None), None)
    life = (
        await tokens.lifetimes(dao, first_usable)
        if first_usable is not None
        else registry.token_lifetimes.lifetimes({})
    )
    response.headers["Cache-Control"] = "no-store"
    day = 86400
    return PairingLookupDTO(
        agent=PairingAgentDTO(slug=entries[0].slug, name=entries[0].name),
        available=entries[0].available,
        agents=entries,
        device_label=row.device_label,
        client=PairingClient(name=row.client_name, version=row.client_version, os=row.client_os),
        requester_ip=str(row.requester_ip) if row.requester_ip is not None else None,
        same_network=pairing.same_network(row.requester_ip, _caller(request)),
        created_at=row.created_at,
        expires_at=row.expires_at,
        sign_in_lasts=SignInLastsDTO(
            idle_days=round(life.idle_s / day, 2), max_days=round(life.refresh_s / day, 2),
        ),
    )


@tokens_router.post(
    "/pairings/approve", status_code=status.HTTP_204_NO_CONTENT, name="approve_agent_pairing",
)
async def approve_agent_pairing(
    payload: PairingCodeRequest,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> Response:
    """The person approves the pairing: the app's next poll gets their tokens for the agent.

    ``access.decide`` for each agent the pairing names. One approval covers every one the person may use (ASVS
    5.0 10.7.1, 10.7.2); the others are refused for this pairing and never issued. When there is none, the same
    403 whether unknown, retired or not granted; the pairing stays open (they may deny it, or it expires). The
    audit entry names all agents and the approved ones, never a code.
    """
    row = await _pending_pairing(payload.code, request, user, dao, audit)
    named = await pairing.members(dao.session, row)
    listed = await pairing.resolve_all(dao, row, user, named)
    if not any(one.available for one in listed):
        await audit.log(
            action="agent_pairing.approve",
            target_type="agent_pairing",
            target_id=str(row.id),
            result=AuditResult.DENY,
            actor_id=user.id,
            metadata_json=_pairing_record(
                row,
                agent=row.agent_ref,
                agents=[one.ref for one in listed],
                approved=[],
                reason=listed[0].decision.code,
            ),
        )
        raise _refuse(status.HTTP_403_FORBIDDEN, "agent_not_available", _NOT_AVAILABLE)
    approved = await pairing.approve(dao.session, row, user, datetime.now(UTC), listed, named)
    first = approved[0].agent
    assert first is not None
    await audit.log(
        action="agent_pairing.approve",
        target_type="agent_pairing",
        target_id=str(row.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        metadata_json=_pairing_record(
            row,
            agent=first.slug,
            agent_id=str(first.id),
            agents=[one.ref for one in listed],
            approved=[one.agent.slug for one in approved if one.agent is not None],
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)


@tokens_router.post("/pairings/deny", status_code=status.HTTP_204_NO_CONTENT, name="deny_agent_pairing")
async def deny_agent_pairing(
    payload: PairingCodeRequest,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    dao: AgentsDAO = Depends(),
    audit: AuditDAO = Depends(),
) -> Response:
    """The person denies the pairing: the app's next poll answers ``access_denied``."""
    row = await _pending_pairing(payload.code, request, user, dao, audit)
    await pairing.deny(dao.session, row, user, datetime.now(UTC))
    await audit.log(
        action="agent_pairing.deny",
        target_type="agent_pairing",
        target_id=str(row.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        metadata_json=_pairing_record(row, agent=row.agent_ref),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=_NO_STORE)
