"""The skills API: one set of routes for everyone.

What a caller may do follows from who they are (see ``auth``) and the rules
in ``services.access``; administrators are callers with the ``admin``
capability, not a separate API. The backend serves it to the browser at
``/api/skills``.
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Literal, TypeVar

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from faden_skills.db.models.skill import SkillModel
from faden_skills.db.session import get_db_session
from faden_skills.services import bundle as fmt
from faden_skills.services.access import Access, Principal
from faden_skills.services.dao import (
    VIEW_ALL,
    VIEWS,
    GrantSpec,
    SkillDao,
    VersionContent,
)
from faden_skills.services.errors import ConflictError, ForbiddenError
from faden_skills.settings import settings
from faden_skills.web.api.auth import get_principal
from faden_skills.web.api.skills.schemas import (
    CompletionsResponse,
    CreateAssignmentRequest,
    CreateSkillRequest,
    FileInfo,
    GrantResponse,
    GrantsRequest,
    NewVersionRequest,
    PublishRequest,
    SaveVersionResponse,
    SkillAssignmentResponse,
    SkillDetailResponse,
    SkillListResponse,
    SkillSummaryResponse,
    UpdateSkillRequest,
    VersionDetailResponse,
    VersionSummaryResponse,
    VisibilityRequest,
)

router = APIRouter()

_T = TypeVar("_T")


# ── Listing and creating ──────────────────────────────────────────────


@router.get("", response_model=SkillListResponse)
async def list_skills(
    view: str = Query(VIEW_ALL, pattern=f"^({'|'.join(VIEWS)})$"),
    q: str | None = Query(None, max_length=200),
    status: str | None = None,
    visibility: str | None = None,
    usable: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillListResponse:
    """The skills the caller can see.

    ``view``: ``mine`` (they own), ``shared`` (shared with them), ``org``
    (the whole tenant's), ``all``. ``usable``: only the ones a chat can be
    given -- what the chat's skill picker lists.
    """
    rows, total = await SkillDao(session).list_skills(
        principal,
        view=view,
        search=q,
        status=status,
        visibility=visibility,
        usable=usable,
        offset=offset,
        limit=limit,
    )
    return SkillListResponse(
        items=[_summary(skill, access) for skill, access in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=SkillDetailResponse, status_code=201)
async def create_skill(
    body: CreateSkillRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Create a skill from its fields, or from a SKILL.md in ``content``."""
    parsed = (
        fmt.from_markdown(body.content, default_name=body.slug or body.name)
        if body.content
        else None
    )
    slug = body.slug or (parsed.name if parsed else None)
    title = body.name or (parsed.catalog.get("title") if parsed else None) or ""
    if not title:
        title = fmt.title_from_name(slug) if slug else ""
    if not title and not slug:
        raise ConflictError("A skill needs a name.")
    content = VersionContent(
        description=_pick(body.description, parsed.description if parsed else None, ""),
        body_markdown=_pick(body.body_markdown, parsed.body if parsed else None, ""),
        frontmatter=_pick(body.frontmatter, parsed.frontmatter if parsed else None, {}),
        manifest=_pick(body.manifest, parsed.manifest if parsed else None, {}),
        files=body.files or {},
    )
    catalog = parsed.catalog if parsed else {}
    dao = SkillDao(session)
    skill = await dao.create_skill(
        principal,
        title=title,
        slug=slug,
        content=content,
        organization=body.owner == "organization",
        visibility=body.visibility,
        activation=body.activation or catalog.get("activation") or "auto",
        priority=_pick(body.priority, catalog.get("priority"), 50),
        tags=_pick(body.tags, catalog.get("tags"), []),
        enabled=body.enabled,
        publish=body.publish,
        change_note=body.change_note,
    )
    access = (await dao.get_skill(skill.id, principal))[1]
    return await _detail(dao, skill, access)


@router.post("/import", response_model=SkillDetailResponse, status_code=201)
async def import_skill(
    file: UploadFile = File(...),
    owner: Literal["me", "organization"] = Form("me"),
    visibility: Literal["private", "tenant", "global"] = Form("private"),
    publish: bool = Form(False),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Create a skill from a SKILL.md (``.md``) or a zipped skill folder (``.zip``)."""
    filename = file.filename or ""
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise fmt.BundleError(
            f"The file is larger than {settings.max_upload_bytes} bytes.",
        )
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    stem = PurePosixPath(filename.replace("\\", "/")).stem
    if suffix == ".zip" or data[:4] == b"PK\x03\x04":
        parsed = fmt.from_zip(data)
    elif suffix in {".md", ".markdown", ""}:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise fmt.BundleError("The file is not UTF-8 text.") from exc
        # "SKILL.md" says nothing about the skill; any other file name does.
        default = None if stem.upper() == "SKILL" else stem
        parsed = fmt.from_markdown(text, default_name=default)
    else:
        raise fmt.BundleError(
            "Import a SKILL.md (.md) or a zipped skill folder (.zip).",
        )

    dao = SkillDao(session)
    skill = await dao.create_skill(
        principal,
        title=parsed.catalog.get("title") or fmt.title_from_name(parsed.name),
        slug=parsed.name,
        content=VersionContent(
            description=parsed.description,
            body_markdown=parsed.body,
            frontmatter=parsed.frontmatter,
            manifest=parsed.manifest,
            files=parsed.files,
        ),
        organization=owner == "organization",
        visibility=visibility,
        activation=parsed.catalog.get("activation", "auto"),
        priority=parsed.catalog.get("priority", 50),
        tags=parsed.catalog.get("tags", []),
        publish=publish,
        change_note=f"Imported from {filename}" if filename else "Imported",
    )
    access = (await dao.get_skill(skill.id, principal))[1]
    return await _detail(dao, skill, access)


@router.get("/completions", response_model=CompletionsResponse)
async def get_completions(
    _principal: Principal = Depends(get_principal),
) -> CompletionsResponse:
    """What the skill editor offers as you type.

    It was declared after ``/{skill_id}``, which took "completions" for an id
    and answered 422: the editor never got it.
    """
    return CompletionsResponse(
        frontmatter_keys=[
            {"key": "name", "description": "Skill name: lowercase, digits, hyphens"},
            {"key": "description", "description": "What it does and when to use it"},
            {"key": "license", "description": "License name or bundled file"},
            {"key": "compatibility", "description": "Environment requirements"},
            {"key": "metadata", "description": "Extra string key/values"},
            {"key": "allowed-tools", "description": "Tools other agents pre-approve"},
        ],
        extension_keys=[
            {"key": "allowed_tools", "description": "Only these tools in the chat"},
            {"key": "preferred_tools", "description": "Tools to prioritize"},
            {"key": "forbidden_tools", "description": "Tools removed from the chat"},
            {"key": "knowledge_sources", "description": "RAG collection IDs"},
            {"key": "trigger_rules", "description": "keywords / file_types"},
        ],
        visibility_values=["private", "tenant", "global"],
        activation_values=["always", "auto", "manual"],
        status_values=["draft", "published", "archived"],
        section_templates=[
            {"name": "## Goal", "description": "Define the skill's objective"},
            {"name": "## Procedure", "description": "Step-by-step instructions"},
            {"name": "## Output format", "description": "Expected output structure"},
            {"name": "## Do not", "description": "Explicit constraints"},
            {"name": "## Escalation", "description": "When to escalate or stop"},
            {"name": "## Examples", "description": "Few-shot examples"},
        ],
        file_types=["pdf", "xlsx", "docx", "csv", "json", "md", "txt", "html", "pptx"],
    )


# ── One skill ─────────────────────────────────────────────────────────


@router.get("/{skill_id}", response_model=SkillDetailResponse)
async def get_skill(
    skill_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """A skill and the content of the version the caller works on."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    return await _detail(dao, skill, access)


@router.patch("/{skill_id}", response_model=SkillDetailResponse)
async def update_skill(
    skill_id: uuid.UUID,
    body: UpdateSkillRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Change a skill's title, tags, activation, priority or enabled flag."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    skill = await dao.update_catalog(
        skill,
        access,
        principal,
        **body.model_dump(exclude_unset=True),
    )
    return await _detail(dao, skill, access)


@router.delete("/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a skill and all its versions."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    await dao.delete_skill(skill, access)


@router.post("/{skill_id}/versions", response_model=SaveVersionResponse)
async def save_version(
    skill_id: uuid.UUID,
    body: NewVersionRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SaveVersionResponse:
    """Save a new version (and, with ``publish``, serve it to chats)."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    content = VersionContent(
        description=body.description,
        body_markdown=body.body_markdown,
        frontmatter=body.frontmatter,
        manifest=body.manifest,
        files=body.files,
    )
    if body.content is not None:
        parsed = fmt.from_markdown(body.content, default_name=skill.slug)
        content.description = _pick(body.description, parsed.description, "")
        content.body_markdown = _pick(body.body_markdown, parsed.body, "")
        content.frontmatter = _pick(body.frontmatter, parsed.frontmatter, {})
        if body.manifest is None and parsed.manifest:
            content.manifest = parsed.manifest
    version, created = await dao.add_version(
        skill,
        access,
        principal,
        content,
        base_version=body.base_version,
        publish=body.publish,
        change_note=body.change_note,
    )
    return SaveVersionResponse(
        version=VersionSummaryResponse.model_validate(version),
        created=created,
        skill=await _detail(dao, skill, access),
    )


@router.get("/{skill_id}/versions", response_model=list[VersionSummaryResponse])
async def list_versions(
    skill_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[VersionSummaryResponse]:
    """A skill's history, newest first (for its editors)."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    _need_editor(access)
    return [
        VersionSummaryResponse.model_validate(v)
        for v in await dao.list_versions(skill.id)
    ]


@router.get("/{skill_id}/versions/{version}", response_model=VersionDetailResponse)
async def get_version(
    skill_id: uuid.UUID,
    version: int,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> VersionDetailResponse:
    """One version with its content."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    if access < Access.WRITE and version != skill.published_version:
        _need_editor(access)
    found = await dao.require_version(skill.id, version)
    return VersionDetailResponse(
        version=found.version,
        description=found.description,
        content_sha256=found.content_sha256,
        change_note=found.change_note,
        created_by=found.created_by,
        created_at=found.created_at,
        body_markdown=found.body_markdown,
        frontmatter=found.frontmatter or {},
        manifest=found.manifest or {},
        files=[FileInfo(**f) for f in fmt.describe_files(found.files or {})],
    )


@router.get("/{skill_id}/diff", response_class=PlainTextResponse)
async def diff_versions(
    skill_id: uuid.UUID,
    from_version: int = Query(..., alias="from", ge=1),
    to_version: int = Query(..., alias="to", ge=1),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> PlainTextResponse:
    """A unified diff between two versions."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    _need_editor(access)
    text = await dao.diff(skill, from_version, to_version)
    return PlainTextResponse(text, media_type="text/x-diff; charset=utf-8")


@router.post("/{skill_id}/publish", response_model=SkillDetailResponse)
async def publish_skill(
    skill_id: uuid.UUID,
    body: PublishRequest | None = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Serve a version to chats: the latest, or an older one to roll back."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    skill = await dao.publish(skill, access, principal, body.version if body else None)
    return await _detail(dao, skill, access)


@router.post("/{skill_id}/archive", response_model=SkillDetailResponse)
async def archive_skill(
    skill_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Stop serving a skill to chats."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    skill = await dao.archive(skill, access)
    return await _detail(dao, skill, access)


@router.post("/{skill_id}/restore", response_model=SkillDetailResponse)
async def restore_skill(
    skill_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Bring an archived skill back."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    skill = await dao.restore(skill, access, principal)
    return await _detail(dao, skill, access)


@router.put("/{skill_id}/visibility", response_model=SkillDetailResponse)
async def set_visibility(
    skill_id: uuid.UUID,
    body: VisibilityRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillDetailResponse:
    """Change who sees a skill."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    skill = await dao.set_visibility(skill, access, principal, body.visibility)
    return await _detail(dao, skill, access)


@router.get("/{skill_id}/grants", response_model=list[GrantResponse])
async def list_grants(
    skill_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[GrantResponse]:
    """Who a skill is shared with."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    if access < Access.OWNER:
        _need_owner()
    grants = (await dao.grants_for([skill.id])).get(skill.id, [])
    return [GrantResponse.model_validate(g) for g in grants]


@router.put("/{skill_id}/grants", response_model=list[GrantResponse])
async def replace_grants(
    skill_id: uuid.UUID,
    body: GrantsRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[GrantResponse]:
    """Share a skill with exactly these people and groups."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    grants = await dao.replace_grants(
        skill,
        access,
        principal,
        [GrantSpec(g.principal_type, g.principal_id, g.access) for g in body.grants],
    )
    return [GrantResponse.model_validate(g) for g in grants]


@router.get("/{skill_id}/export")
async def export_skill(
    skill_id: uuid.UUID,
    format: Literal["zip", "md"] = "zip",
    version: int | None = Query(None, ge=1),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """A version as a zipped skill folder, or as its SKILL.md alone.

    Editors get the latest version unless they name one; readers get the
    published one.
    """
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    number = version or _working_version(skill, access)
    if access < Access.WRITE and number != skill.published_version:
        _need_editor(access)
    found = await dao.require_version(skill.id, number)
    exported = dao.bundle_of(skill, found)
    if format == "md":
        return Response(
            content=fmt.to_markdown(exported),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{skill.slug}.md"',
            },
        )
    return Response(
        content=fmt.to_zip(exported),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{skill.slug}.zip"',
        },
    )


# ── Assignments (administrators) ──────────────────────────────────────


@router.get(
    "/{skill_id}/assignments",
    response_model=list[SkillAssignmentResponse],
)
async def list_assignments(
    skill_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> list[SkillAssignmentResponse]:
    """All assignments for a skill."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    if access < Access.ADMIN:
        _need_admin()
    return [
        SkillAssignmentResponse.model_validate(a)
        for a in await dao.list_assignments(skill)
    ]


@router.post(
    "/{skill_id}/assignments",
    response_model=SkillAssignmentResponse,
    status_code=201,
)
async def create_assignment(
    skill_id: uuid.UUID,
    body: CreateAssignmentRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> SkillAssignmentResponse:
    """Create a new skill assignment."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    assignment = await dao.create_assignment(
        skill,
        access,
        target_type=body.target_type,
        target_id=body.target_id,
        enabled=body.enabled,
        priority_override=body.priority_override,
    )
    return SkillAssignmentResponse.model_validate(assignment)


@router.delete("/{skill_id}/assignments/{assignment_id}", status_code=204)
async def delete_assignment(
    skill_id: uuid.UUID,
    assignment_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove a skill assignment."""
    dao = SkillDao(session)
    skill, access = await dao.get_skill(skill_id, principal)
    await dao.delete_assignment(skill, access, assignment_id)


# ── Helpers ───────────────────────────────────────────────────────────


def _pick(*values: _T | None) -> _T:
    """The first value that is not ``None`` (the last one is the default)."""
    for value in values:
        if value is not None:
            return value
    return values[-1]  # type: ignore[return-value]


def _working_version(skill: SkillModel, access: Access) -> int:
    """The version the caller sees: editors the latest, readers the published."""
    if access >= Access.WRITE or skill.published_version is None:
        return skill.latest_version
    return skill.published_version


def _need_editor(access: Access) -> None:
    if access < Access.WRITE:
        raise ForbiddenError("Only the skill's editors see its unpublished versions.")


def _need_owner() -> None:
    raise ForbiddenError("Only the skill's owner sees who it is shared with.")


def _need_admin() -> None:
    raise ForbiddenError("Only administrators manage assignments.")


def _summary(skill: SkillModel, access: Access) -> SkillSummaryResponse:
    return SkillSummaryResponse(
        id=skill.id,
        tenant_id=skill.tenant_id,
        name=skill.name,
        slug=skill.slug,
        description=skill.description,
        owner_id=skill.owner_id,
        visibility=skill.visibility,
        activation=skill.activation,
        status=skill.status,
        enabled=skill.enabled,
        priority=skill.priority,
        tags=list(skill.tags or []),
        latest_version=skill.latest_version,
        published_version=skill.published_version,
        created_by=skill.created_by,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
        access=access.label,  # type: ignore[arg-type]
    )


async def _detail(
    dao: SkillDao,
    skill: SkillModel,
    access: Access,
) -> SkillDetailResponse:
    number = _working_version(skill, access)
    version = await dao.require_version(skill.id, number)
    grants = None
    if access >= Access.OWNER:
        grants = [
            GrantResponse.model_validate(g)
            for g in (await dao.grants_for([skill.id])).get(skill.id, [])
        ]
    uses = (await dao.usage([skill.id])).get(skill.id, 0)
    return SkillDetailResponse(
        **_summary(skill, access).model_dump(),
        version=version.version,
        body_markdown=version.body_markdown,
        frontmatter=version.frontmatter or {},
        manifest=version.manifest or {},
        files=[FileInfo(**f) for f in fmt.describe_files(version.files or {})],
        content_sha256=version.content_sha256,
        grants=grants,
        uses_30d=uses,
    )
