"""CRUD endpoints for chat projects and sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette import status

from faden_gateway.db.dao.session_dao import SessionDAO
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.errors import GatewayError, error_response
from faden_gateway.services.gateway.session_schemas import (
    MessageDTO,
    ProjectCreateRequest,
    ProjectDTO,
    ProjectUpdateRequest,
    SessionCreateRequest,
    SessionDTO,
    SessionUpdateRequest,
    SummaryDTO,
    TokenUsageDTO,
)
from faden_gateway.settings import settings

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

_UNSET = object()

# ── Projects ──────────────────────────────────────────────────────


@router.get("/projects")
async def list_projects(
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    projects = await dao.list_projects(
        tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    data = [ProjectDTO.model_validate(p).model_dump(mode="json") for p in projects]
    return JSONResponse(status_code=200, content={"data": data})


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    # Enforce capacity limit
    capacity = getattr(request.app.state, "_resource_capacity", {})
    limit = capacity.get("projects")
    if limit is not None:
        count = await dao.count_projects(
            tenant_id=auth.tenant_id, user_id=auth.user_id,
        )
        if count >= limit:
            raise GatewayError(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                message=(
                    f"Project limit reached ({limit}). "
                    "Upgrade to Fadenstack Enterprise for unlimited projects."
                ),
                code="resource_limit_reached",
            )

    project = await dao.create_project(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        name=body.name,
        description=body.description,
        system_instructions=body.system_instructions,
        model_alias=body.model_alias,
        metadata_json=body.metadata_json,
    )
    data = ProjectDTO.model_validate(project).model_dump(mode="json")
    return JSONResponse(status_code=201, content=data)


@router.get("/projects/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    project = await dao.get_project(
        project_id=project_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    if not project:
        raise GatewayError(
            status_code=404,
            message="Project not found.",
            code="project_not_found",
        )
    data = ProjectDTO.model_validate(project).model_dump(mode="json")
    return JSONResponse(status_code=200, content=data)


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    fields = body.model_dump(exclude_unset=True)
    project = await dao.update_project(
        project_id=project_id,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        **fields,
    )
    if not project:
        raise GatewayError(
            status_code=404,
            message="Project not found.",
            code="project_not_found",
        )
    data = ProjectDTO.model_validate(project).model_dump(mode="json")
    return JSONResponse(status_code=200, content=data)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> None:
    # Clean up attachment files before DB cascade deletes records
    file_store = getattr(request.app.state, "chat_file_store", None)
    if file_store:
        attachments = await dao.list_attachments_for_project(project_id=project_id)
        for att in attachments:
            try:
                await file_store.delete(att.storage_key)
            except Exception:
                pass
    deleted = await dao.delete_project(
        project_id=project_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    if not deleted:
        raise GatewayError(
            status_code=404,
            message="Project not found.",
            code="project_not_found",
        )


# ── Sessions ──────────────────────────────────────────────────────


@router.get("")
async def list_sessions(
    project_id: uuid.UUID | None = None,
    session_status: str | None = None,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    from faden_gateway.db.models.gateway import SessionStatus  # noqa: PLC0415

    status_enum = None
    if session_status:
        try:
            status_enum = SessionStatus(session_status)
        except ValueError:
            pass

    sessions = await dao.list_sessions(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        project_id=project_id,
        status=status_enum,
    )
    # The chats a reply is being written in: the sidebar marks them, since
    # a reply goes on while the user is in another chat.
    alive_since = datetime.now(timezone.utc) - timedelta(seconds=settings.chat_turn_stale_sec)
    writing = set(await dao.running_turn_sessions(
        tenant_id=auth.tenant_id, user_id=auth.user_id, alive_since=alive_since,
    ))
    data = []
    for s in sessions:
        dto = SessionDTO.model_validate(s)
        dto.writing = s.id in writing
        data.append(dto.model_dump(mode="json"))
    return JSONResponse(status_code=200, content={"data": data})


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    # Validate project ownership if project_id is given
    if body.project_id:
        project = await dao.get_project(
            project_id=body.project_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
        )
        if not project:
            raise GatewayError(
                status_code=404,
                message="Project not found.",
                code="project_not_found",
            )

    sess = await dao.create_session(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        project_id=body.project_id,
        title=body.title,
        metadata_json=body.metadata_json,
    )
    data = SessionDTO.model_validate(sess).model_dump(mode="json")
    return JSONResponse(status_code=201, content=data)


@router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    sess = await dao.get_session(
        session_id=session_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    if not sess:
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            code="session_not_found",
        )
    data = SessionDTO.model_validate(sess).model_dump(mode="json")
    return JSONResponse(status_code=200, content=data)


@router.patch("/{session_id}")
async def update_session(
    session_id: uuid.UUID,
    body: SessionUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    from faden_gateway.db.models.gateway import SessionStatus  # noqa: PLC0415

    fields = body.model_dump(exclude_unset=True)
    # Moving a chat into a project: only one of the user's own. The move was
    # ignored before (the field was not accepted), so a chat moved in the
    # sidebar went back to "no project" on the next load.
    if fields.get("project_id") is not None:
        project = await dao.get_project(
            project_id=fields["project_id"], tenant_id=auth.tenant_id, user_id=auth.user_id,
        )
        if not project:
            raise GatewayError(
                status_code=404,
                message="Project not found.",
                code="project_not_found",
            )
    if "status" in fields and fields["status"] is not None:
        try:
            fields["status"] = SessionStatus(fields["status"])
        except ValueError:
            raise GatewayError(
                status_code=400,
                message=f"Invalid status: {fields['status']}",
                code="invalid_status",
            )
    sess = await dao.update_session(
        session_id=session_id,
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        **fields,
    )
    if not sess:
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            code="session_not_found",
        )
    data = SessionDTO.model_validate(sess).model_dump(mode="json")
    return JSONResponse(status_code=200, content=data)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> None:
    deleted = await dao.delete_session(
        session_id=session_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    if not deleted:
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            code="session_not_found",
        )


# ── Messages ──────────────────────────────────────────────────────


@router.get("/{session_id}/messages")
async def list_messages(
    session_id: uuid.UUID,
    limit: int = 100,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    # Verify session ownership
    sess = await dao.get_session(
        session_id=session_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    if not sess:
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            code="session_not_found",
        )

    # The chat opens on a session's LATEST `limit` messages, in chronological
    # order: get_recent_messages (newest N, returned oldest-first). This used
    # list_messages, which is oldest-first before the limit -- so a chat past
    # 100 messages reopened at its beginning with its newest turns missing.
    #
    # MERGE NOTE (branch dashboard-redesign, 2026-09-26): one fix only. If the
    # branch being merged fixes this too, keep this call to the existing
    # get_recent_messages -- do not add a second newest-first query here or
    # in list_messages (which stays oldest-first for the summarizer).
    messages = await dao.get_recent_messages(session_id=session_id, limit=limit)
    # A reply shows what its request used, as it did while it streamed.
    usage = await dao.message_usage(
        session_id=session_id,
        trace_ids=[m.trace_id for m in messages if m.role == "assistant" and m.trace_id],
    )
    # A blocked message shows what the user was told, in place of its text.
    notices = await dao.decision_notices([m.decision_id for m in messages if m.decision_id])
    data = []
    for m in messages:
        dto = MessageDTO.model_validate(m)
        if m.role == "assistant" and m.trace_id in usage:
            dto.usage = TokenUsageDTO(**usage[m.trace_id])
        if m.decision_id:
            dto.notice = notices.get(m.decision_id)
        data.append(dto.model_dump(mode="json"))
    return JSONResponse(status_code=200, content={"data": data})


# ── Summaries ─────────────────────────────────────────────────────


@router.get("/{session_id}/summary")
async def get_session_summary(
    session_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    sess = await dao.get_session(
        session_id=session_id, tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    if not sess:
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            code="session_not_found",
        )
    summary = await dao.get_latest_summary(session_id=session_id)
    if not summary:
        return JSONResponse(status_code=200, content={"data": None})
    data = SummaryDTO.model_validate(summary).model_dump(mode="json")
    return JSONResponse(status_code=200, content={"data": data})


# ── Capacity ──────────────────────────────────────────────────────


@router.get("/capacity")
async def get_capacity(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: SessionDAO = Depends(),
) -> JSONResponse:
    """Return project capacity for the current user."""
    capacity = getattr(request.app.state, "_resource_capacity", {})
    limit = capacity.get("projects")
    current = await dao.count_projects(
        tenant_id=auth.tenant_id, user_id=auth.user_id,
    )
    return JSONResponse(
        status_code=200,
        content={
            "projects": {
                "current": current,
                "limit": limit,
            },
        },
    )
