"""Sign-in provider settings, for administrators.

The sign-in module owns each protocol's settings (``services/sign_in/contract.py``); the backend checks that a
superuser asks and passes the request on unchanged, answer included. Signing in itself is
``web/api/auth_sso``. Without a sign-in module every route here answers 404.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette import status

from faden_backend.db.models.users import User
from faden_backend.services.sign_in import module
from faden_backend.web.api.admin.dependencies import require_superuser

log = logging.getLogger(__name__)

router = APIRouter()


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    name="auth_provider_settings",
)
async def provider_settings(
    path: str,
    request: Request,
    _admin: Annotated[User, Depends(require_superuser)],
) -> Response:
    """``/api/admin/auth-providers/<path>`` is the module's ``/api/v1/admin/providers/<path>``."""
    if not module.enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No sign-in module is switched on")
    payload = None
    body = await request.body()
    if body:
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="JSON expected",
            ) from exc
    target = "/api/v1/admin/providers" + (f"/{path}" if path else "")
    try:
        upstream = await module.request(
            request.method, target, json=payload, params=dict(request.query_params),
        )
    except module.ModuleRefusedError as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)
    except module.ModuleUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The sign-in module is unavailable",
        ) from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
