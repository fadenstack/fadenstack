"""``/api/public/provenance``: the public check page's API, without an account.

Offered while the page is on (system setting ``provenance.public_check_enabled``, on by default)
and the provenance module is on; otherwise every route here answers 404, as an unknown address does.

- ``GET  /api/public/provenance``          whom to ask for a text check (``provenance.public_check_contact``)
- ``POST /api/public/provenance/check``    check a picture or a file: 10 checks a minute per address, up to
  20 MB; texts (pasted, or a file of a ``text/*`` type) are refused with 403 ``text_not_public``: they are
  checked for people with an account
- ``GET  /api/public/provenance/anchors``  the signing chains and CAs, to check a signed result offline

Nothing submitted is kept or logged.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dependencies import get_db_session
from faden_backend.services.provenance import client as engine
from faden_backend.services.provenance.checks import (
    CHECK_BODY_DOC,
    PUBLIC_ANCHORS,
    PUBLIC_CHECKS,
    PublicCheck,
    client_key,
    public_check_settings,
    read_check_body,
    refuse,
)
from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values
from faden_backend.settings import settings

router = APIRouter(prefix="/public/provenance", tags=["provenance-public"])


async def _offered(session: AsyncSession) -> PublicCheck:
    """The page's settings when it is offered; 404 when it is not."""
    await refresh_runtime_values(session, prefix="faden_gateway.provenance_")
    page = await public_check_settings(session)
    if not (settings.provenance_enabled and page.enabled):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return page


@router.get("", name="public_check_info")
async def public_check_info(session: Annotated[AsyncSession, Depends(get_db_session)]) -> dict[str, Any]:
    """What the public page shows besides the form: whom to ask for a text check, the largest file."""
    page = await _offered(session)
    return {"contact": page.contact, "max_bytes": engine.MAX_CONTENT_BYTES}


@router.post("/check", name="public_check", openapi_extra=CHECK_BODY_DOC)
async def public_check(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Check a picture or a file (base64) for marks; the engine's result, signed when ``signed`` is true."""
    page = await _offered(session)
    PUBLIC_CHECKS.enforce(client_key(request))
    body = await read_check_body(request)
    if body.is_text:
        contact = f" Ask: {page.contact}" if page.contact else ""
        raise refuse(
            status.HTTP_403_FORBIDDEN,
            "text_not_public",
            f"Texts are checked only for people with an account here.{contact}",
            contact=page.contact,
        )
    try:
        return await engine.verify(body.engine_body())
    except engine.EngineError as exc:
        raise refuse(exc.status_code, exc.code, exc.message) from None


@router.get("/anchors", name="public_check_anchors")
async def public_check_anchors(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Every signing chain this installation used and the CAs they end in, as the engine gives them."""
    await _offered(session)
    PUBLIC_ANCHORS.enforce(client_key(request))
    try:
        return await engine.anchors()
    except engine.EngineError as exc:
        raise refuse(exc.status_code, exc.code, exc.message) from None
