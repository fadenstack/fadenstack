"""``/api/provenance``: checking content for marks, for everyone signed in with ``provenance:check``.

Every built-in role has the right. A text, a picture or a file goes to the engine's detector and its result
comes back as it is (``VerifyResult``): the verdict in plain words and what each layer found, signed when
asked for (to download, CoP 2.1.2). 30 checks a minute per person; content up to 20 MB. Nothing submitted is
kept or logged.

``/records/not-written``, with ``audit:read``: the records whose write an agent reported as failed. They are
kept (a write reported as failed may have gone through in part) and not counted as placed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User
from faden_backend.services.provenance import client as engine
from faden_backend.services.provenance.checks import CHECK_BODY_DOC, USER_CHECKS, read_check_body, refuse
from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values
from faden_backend.settings import settings
from faden_backend.web.api.rbac import require_permission

router = APIRouter(prefix="/provenance", tags=["provenance"])

PERMISSION = ("provenance", "check")


@router.get("/status", name="content_check_status")
async def content_check_status(
    _user: Annotated[User, Depends(require_permission(*PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Whether content can be checked here (the provenance module is on), and the largest content checked."""
    await refresh_runtime_values(session, prefix="faden_gateway.provenance_")
    return {"enabled": bool(settings.provenance_enabled), "max_bytes": engine.MAX_CONTENT_BYTES}


@router.post("/verify", name="content_check_verify", openapi_extra=CHECK_BODY_DOC)
async def content_check_verify(
    request: Request,
    user: Annotated[User, Depends(require_permission(*PERMISSION))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Check a text or a file (base64) for marks; the engine's result, signed when ``signed`` is true.

    503 ``module_off`` when the provenance module is switched off, 429 ``rate_limited`` after 30 checks in
    a minute, 413 ``too_large`` over 20 MB, 422 for a body that is not a check; the engine's own refusals
    keep its code (503 ``no_signing_key``, ``busy``, ``unavailable``; 413 ``too_large``; 422), an engine
    that refuses the service token is 502 ``refused``.
    """
    await refresh_runtime_values(session, prefix="faden_gateway.provenance_")
    if not settings.provenance_enabled:
        raise refuse(
            status.HTTP_503_SERVICE_UNAVAILABLE, "module_off", "The provenance module is switched off.",
        )
    USER_CHECKS.enforce(str(user.id))
    body = await read_check_body(request)
    try:
        return await engine.verify(body.engine_body())
    except engine.EngineError as exc:
        raise refuse(exc.status_code, exc.code, exc.message) from None


@router.get("/records/not-written", name="provenance_not_written")
async def provenance_not_written(
    _user: Annotated[User, Depends(require_permission("audit", "read"))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """The records whose write an agent reported as failed, newest first: to find out what went wrong.

    Each with its times, agent, model, media type, edit kind, the host error's code, and the tool and
    place of its last placement; never a hash, a signature or content. 503 ``module_off`` while the module is
    switched off; the engine's refusals keep their code.
    """
    await refresh_runtime_values(session, prefix="faden_gateway.provenance_")
    if not settings.provenance_enabled:
        raise refuse(
            status.HTTP_503_SERVICE_UNAVAILABLE, "module_off", "The provenance module is switched off.",
        )
    try:
        return await engine.not_written(since=since.isoformat() if since else None, limit=limit)
    except engine.EngineError as exc:
        raise refuse(exc.status_code, exc.code, exc.message) from None
