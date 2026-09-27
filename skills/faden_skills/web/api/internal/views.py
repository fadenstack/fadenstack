"""Internal API: what the gateway asks per chat request, and cleanup calls."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from faden_skills.db.models.skill import SkillModel, SkillVisibility
from faden_skills.db.session import get_db_session
from faden_skills.services import bundle as fmt
from faden_skills.services.access import Principal
from faden_skills.services.dao import SkillDao
from faden_skills.services.errors import NotFoundError
from faden_skills.services.resolve import RequestContext
from faden_skills.web.api.auth import verify_service_token

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
    #: How many characters of the skills' files to send along, for a model
    #: that cannot call ``read_skill_file``; 0 sends only the list.
    inline_files_chars: int = Field(default=0, ge=0, le=200_000)


class SkillFileRef(BaseModel):
    """A file in a resolved skill's folder."""

    path: str
    size: int
    kind: str
    executable: bool
    #: The file's text, when the request asked for files inline and it fit.
    text: str | None = None


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
    #: The folder's other files (references/, assets/, ...): the model reads
    #: them when the instructions call for it.
    files: list[SkillFileRef] = Field(default_factory=list)


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


class SkillFileResponse(BaseModel):
    """One file of a skill version, for the model."""

    path: str
    size: int
    kind: str
    #: ``None`` for a binary file, which cannot be given to a model as text.
    text: str | None


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
    budget = [body.inline_files_chars]
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
                files=_file_refs(r.version.files or {}, budget),
            )
            for r in resolved
        ],
    )


def _file_refs(
    files: dict[str, dict[str, str]], budget: list[int],
) -> list[SkillFileRef]:
    """A version's files, with the text of those that fit in *budget*.

    *budget* is shared by every skill of the request (a one-item list, so it
    goes down as files are taken). Scripts are listed, not inlined.
    """
    refs: list[SkillFileRef] = []
    for info in fmt.describe_files(files):
        text = None
        entry = files[info["path"]]
        if (
            "text" in entry
            and not info["executable"]
            and len(entry["text"]) <= budget[0]
        ):
            text = entry["text"]
            budget[0] -= len(text)
        refs.append(SkillFileRef(**info, text=text))
    return refs


@router.get(
    "/skills/{skill_id}/versions/{version}/file",
    response_model=SkillFileResponse,
)
async def read_skill_file(
    skill_id: uuid.UUID,
    version: int,
    tenant_id: str = Query(...),
    path: str = Query(..., min_length=1, max_length=512),
    session: AsyncSession = Depends(get_db_session),
) -> SkillFileResponse:
    """One file of a skill's published version, for a chat that was given it.

    The gateway asks only for files of the skills it resolved for the
    request, so the user could see them; this checks the tenant and that the
    version is the published one -- the one a chat can have been given.
    """
    skill = await session.get(SkillModel, skill_id)
    visible = skill is not None and (
        skill.tenant_id == tenant_id or skill.visibility == SkillVisibility.GLOBAL.value
    )
    if not visible or skill is None or skill.published_version != version:
        raise NotFoundError("Skill not found.")
    found = await SkillDao(session).require_version(skill.id, version)
    entry = (found.files or {}).get(path)
    if entry is None:
        raise NotFoundError(f"The skill has no file {path}.")
    raw = fmt.file_bytes(entry)
    return SkillFileResponse(
        path=path,
        size=len(raw),
        kind="text" if "text" in entry else "binary",
        text=entry.get("text"),
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
