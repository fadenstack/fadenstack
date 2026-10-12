"""Service-token check for the PII service's own routes.

The service is not published on a host port and nginx does not route to it,
but every container on the compose network can reach it, and ``/detokenize``
turns tokens back into personal data. The gateway, the backend and MCP
present ``FADEN_PII_SERVICE_TOKEN`` (as ``Authorization: Bearer``).
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from faden_pii.settings import settings


async def verify_service_token(request: Request) -> None:
    """FastAPI dependency: refuse the call unless it carries the service token.

    Unlike the MCP and skills services, an empty token does not open the
    service: it refuses every call (503), so a missing secret can never
    leave the personal-data routes unprotected.
    """
    if not settings.service_token:
        raise HTTPException(
            status_code=503,
            detail="The PII service has no service token (FADEN_PII_SERVICE_TOKEN) and refuses all calls.",
        )
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing service token.")
    # Constant time, so the check does not tell a caller how much of the token it guessed.
    if not hmac.compare_digest(header[7:].encode(), settings.service_token.encode()):
        raise HTTPException(status_code=403, detail="Invalid service token.")
