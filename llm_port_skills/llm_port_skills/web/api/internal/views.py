"""Internal API: what the gateway asks per chat request, and cleanup calls."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_skills.db.session import get_db_session
from llm_port_skills.services.access import Principal
from llm_port_skills.services.dao import SkillDao
from llm_port_skills.services.resolve import RequestContext
from llm_port_skills.web.api.auth import verify_service_token

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(verify_service_token)])


# ── Schemas ───────────────────────────────────────────────────────────


class SkillResolveRequest(BaseModel):
    """Request to resolve matching skills for a gateway request."""

    tenant_id: str
    user_id: str | None = None
    #: The user's groups: a skill shared with one of them is theirs to use.
    group_ids: list[str] = Field(default_factory=list)
    #: Skills asked for by name (slug or id), e.g. from the chat's picker.
    requested: list[str] = Field(default_factory=list, max_length=20)
    assistant_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    user_query: str | None = None
    file_types: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    max_skills: int = Field(default=3, ge=0, le=10)


class ResolvedSkill(BaseModel):
    """A resolved skill ready for injection."""

    skill_id: str
    name: str
    slug: str
    version: int
    content_sha256: str
    body_markdown: str
    priority: int
    score: int
    #: ``explicit``, ``always``, ``trigger`` or ``assignment``.
    matched_by: str
    allowed_tools: list[str] | None = None
    preferred_tools: list[str] | None = None
    forbidden_tools: list[str] | None = None
    knowledge_sources: list[str] | None = None


class SkillResolveResponse(BaseModel):
    """Response containing resolved skills."""

    skills: list[ResolvedSkill]


class SkillUsageRequest(BaseModel):
    """Record skill usage telemetry."""

    tenant_id: str
    skill_id: str
    version: int
    session_id: str | None = None
    user_id: str | None = None
    matched_by: str | None = None


class PrincipalRemovedResponse(BaseModel):
    """How many grants a removed user or group had."""

    removed: int


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/skills/resolve", response_model=SkillResolveResponse)
async def resolve_skills(
    body: SkillResolveRequest,
    session: AsyncSession = Depends(get_db_session),
) -> SkillResolveResponse:
    """The skills a chat request gets, from those its user can see.

    The user's id was sent but not used: every chat was resolved against
    every published skill in the tenant, so a skill meant for one person
    reached everyone whose message matched its keywords.
    """
    principal = Principal(
        tenant_id=body.tenant_id,
        user_id=body.user_id,
        group_ids=frozenset(body.group_ids),
    )
    ctx = RequestContext(
        user_id=body.user_id,
        requested=body.requested,
        user_query=body.user_query,
        file_types=body.file_types,
        tags=body.tags,
        assistant_id=body.assistant_id,
        workspace_id=body.workspace_id,
        project_id=body.project_id,
    )
    resolved = await SkillDao(session).resolve(
        principal,
        ctx,
        max_results=body.max_skills,
    )
    return SkillResolveResponse(
        skills=[
            ResolvedSkill(
                skill_id=str(r.skill.id),
                name=r.skill.name,
                slug=r.skill.slug,
                version=r.version.version,
                content_sha256=r.version.content_sha256,
                body_markdown=r.version.body_markdown,
                priority=r.skill.priority,
                score=r.match.score,
                matched_by=r.match.matched_by,
                allowed_tools=r.version.manifest.get("allowed_tools"),
                preferred_tools=r.version.manifest.get("preferred_tools"),
                forbidden_tools=r.version.manifest.get("forbidden_tools"),
                knowledge_sources=r.version.manifest.get("knowledge_sources"),
            )
            for r in resolved
        ],
    )


@router.post("/skills/usage", status_code=204)
async def record_usage(
    body: SkillUsageRequest,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Count one use of a skill version (fire-and-forget from the gateway).

    It was only ever logged, so nobody could see which skills were used.
    """
    logger.info(
        "Skill usage: tenant=%s skill=%s v%d session=%s user=%s match=%s",
        body.tenant_id,
        body.skill_id,
        body.version,
        body.session_id,
        body.user_id,
        body.matched_by,
    )
    try:
        skill_id = uuid.UUID(body.skill_id)
    except ValueError:
        return
    try:
        async with session.begin_nested():
            await SkillDao(session).record_usage(skill_id, body.version)
    except IntegrityError:
        # The skill was deleted between the chat and this call.
        logger.debug("Usage for unknown skill %s ignored", body.skill_id)


@router.delete(
    "/principals/{principal_type}/{principal_id}",
    response_model=PrincipalRemovedResponse,
)
async def remove_principal(
    principal_type: str,
    principal_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> PrincipalRemovedResponse:
    """Drop the grants of a user or group the backend has deleted."""
    if principal_type not in {"user", "group"}:
        return PrincipalRemovedResponse(removed=0)
    removed = await SkillDao(session).remove_principal(principal_type, principal_id)
    return PrincipalRemovedResponse(removed=removed)
