"""Admin endpoints for Chat & Sessions management.

Queries the gateway database (``faden_gateway``) via the secondary engine
set up in ``app.state.llm_graph_trace_session_factory``, using raw SQL
to avoid coupling with the gateway's ORM models.

All read endpoints gracefully return empty data when the gateway has
not yet run its chat migrations (``chat_project`` table missing).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.settings import settings
from faden_backend.db.models.users import User
from faden_backend.web.api.admin.dependencies import require_superuser

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_missing_table(exc: ProgrammingError) -> bool:
    """Return True if the error is an UndefinedTableError."""
    return "UndefinedTableError" in str(exc) or "does not exist" in str(exc)


async def _get_gateway_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session against the gateway (faden_gateway) database."""
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Gateway database not available")
    async with factory() as session:
        yield session


# ── Projects ──────────────────────────────────────────────────────


async def _erase(scope: str, target: str, not_found: str) -> None:
    """Erase through the gateway, which alone reaches the files; 404 when nothing was there."""
    from faden_backend.services.chat.gateway_client import EraseFailed, shared_client  # noqa: PLC0415

    try:
        erased = await shared_client().erase(scope, target, secret=settings.users_secret)
    except EraseFailed as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None
    counts = {"session": "chats", "project": "projects", "attachment": "files"}
    if not erased.get(counts[scope]) and not (scope == "attachment" and erased):
        raise HTTPException(status_code=404, detail=not_found)


@router.get("/projects")
async def list_projects(
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> dict[str, Any]:
    try:
        result = await gw.execute(
            text(
                # Not its name or description: what people call their projects is theirs, as their chats
                # are (sealed in the gateway; features/admin-guardrails.md, D-D).
                "SELECT id, tenant_id, user_id, "
                "model_alias, created_at, updated_at "
                "FROM chat_project ORDER BY created_at DESC"
            ),
        )
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            return {"data": []}
        raise
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        for k in ("id",):
            row[k] = str(row[k])
        for k in ("created_at", "updated_at"):
            if row.get(k):
                row[k] = row[k].isoformat()
    return {"data": rows}


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)],
) -> None:
    """Erase it for good through the gateway: its files go with its rows (recorded in the audit trail)."""
    await _erase("project", str(project_id), "Project not found")


# ── Sessions ──────────────────────────────────────────────────────


@router.get("/sessions")
async def list_sessions(
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    # Listed by id: a chat's title is the start of its user's first message,
    # theirs as the messages are, and is not read here at all.
    q = (
        "SELECT id, tenant_id, user_id, project_id, status, "
        "created_at, updated_at FROM chat_session"
    )
    params: dict[str, Any] = {}
    if project_id:
        q += " WHERE project_id = :pid"
        params["pid"] = project_id
    q += " ORDER BY created_at DESC"

    try:
        result = await gw.execute(text(q), params)
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            return {"data": []}
        raise
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        for k in ("id", "project_id"):
            if row.get(k):
                row[k] = str(row[k])
        for k in ("created_at", "updated_at"):
            if row.get(k):
                row[k] = row[k].isoformat()
    return {"data": rows}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)],
) -> None:
    """Erase it for good through the gateway: its files go with its rows (recorded in the audit trail)."""
    await _erase("session", str(session_id), "Session not found")


# ── Attachments ───────────────────────────────────────────────────


@router.get("/attachments")
async def list_attachments(
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
    session_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    q = (
        # Not the file name: it is the user's own (sealed in the gateway).
        "SELECT id, tenant_id, user_id, session_id, project_id, "
        "content_type, size_bytes, extraction_status, "
        "scope, page_count, truncated, created_at "
        "FROM chat_attachment"
    )
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if session_id:
        clauses.append("session_id = :sid")
        params["sid"] = session_id
    if project_id:
        clauses.append("project_id = :pid")
        params["pid"] = project_id
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY created_at DESC"

    try:
        result = await gw.execute(text(q), params)
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            return {"data": []}
        raise
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        for k in ("id", "session_id", "project_id", "message_id"):
            if row.get(k):
                row[k] = str(row[k])
        for k in ("created_at",):
            if row.get(k):
                row[k] = row[k].isoformat()
    return {"data": rows}


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)],
) -> None:
    """Erase it for good through the gateway: its files go with its rows (recorded in the audit trail)."""
    await _erase("attachment", str(attachment_id), "Attachment not found")


# ── Stats ─────────────────────────────────────────────────────────


@router.get("/stats")
async def chat_stats(
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> dict[str, Any]:
    empty = {
        "total_projects": 0,
        "total_sessions": 0,
        "total_attachments": 0,
        "total_attachment_bytes": 0,
    }
    try:
        projects = await gw.execute(text("SELECT count(*) FROM chat_project"))
        sessions = await gw.execute(text("SELECT count(*) FROM chat_session"))
        attachments = await gw.execute(
            text("SELECT count(*), coalesce(sum(size_bytes), 0) FROM chat_attachment"),
        )
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            return empty
        raise
    att_row = attachments.one()
    return {
        "total_projects": projects.scalar() or 0,
        "total_sessions": sessions.scalar() or 0,
        "total_attachments": att_row[0] or 0,
        "total_attachment_bytes": att_row[1] or 0,
    }
