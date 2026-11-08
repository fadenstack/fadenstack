"""A grant's evidence, decrypted, at the backend's word (sdk/09 L-64).

The places, arguments and results a grant's covered calls leave behind, and the reason the person gave, are
encrypted at rest under this gateway's key (``grant-evidence``), which the backend does not hold. The backend
asks here once it has checked that its caller may read them, and keeps the read in its audit trail. Its request
carries a grant only the backend can sign -- a short JWT with the users' secret, ``typ`` ``grant_evidence``,
naming what to read -- so a caller who reaches the gateway directly reads nothing. What it names:

- ``grant`` and ``part`` ``calls``: the grant's calls with their places, arguments, outcomes and summaries;
- ``grant`` and ``part`` ``reason``: only the reason the person gave;
- ``place``, ``since``, ``until`` (and maybe ``user``, ``agent``, ``device``): the covered calls whose place is
  that one or lies in it, in a window of at most 31 days.

Each read is logged here with whom the backend named.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dependencies import get_db_session
from faden_gateway.services.gateway import grants
from faden_gateway.settings import settings

router = APIRouter()
logger = logging.getLogger(__name__)

#: The header with the grant, and the grant's ``typ``.
EVIDENCE_GRANT_HEADER = "X-Faden-Evidence-Grant"
EVIDENCE_TOKEN_TYPE = "grant_evidence"  # noqa: S105 - a claim value, not a secret


def _claims(token: str | None) -> dict[str, Any] | None:
    if not token or not settings.jwt_secret:
        return None
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False, "require": ["exp", "typ", "sub", "tenant"]},
        )
    except jwt.PyJWTError:
        return None
    return claims if claims.get("typ") == EVIDENCE_TOKEN_TYPE else None


def _when(value: Any) -> datetime:
    try:
        found = datetime.fromisoformat(str(value))
    except ValueError:
        raise HTTPException(status_code=422, detail="Not a time.") from None
    if found.tzinfo is None:
        raise HTTPException(status_code=422, detail="A time needs its offset.")
    return found


@router.post("/internal/grant-evidence", include_in_schema=False)
async def grant_evidence(
    grant: str | None = Header(default=None, alias=EVIDENCE_GRANT_HEADER),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """What the grant names, decrypted: one grant's calls or reason, or the calls found by place."""
    claims = _claims(grant)
    if claims is None:
        raise HTTPException(status_code=403, detail="No evidence grant for this.")
    tenant, reader = str(claims["tenant"]), str(claims["sub"])
    if claims.get("grant"):
        try:
            grant_id = uuid.UUID(str(claims["grant"]))
        except ValueError:
            raise HTTPException(status_code=422, detail="Not an id.") from None
        found = await grants.evidence(session, grant_id, tenant)
        if found is None:
            raise HTTPException(status_code=404, detail="No such grant.")
        logger.info("Evidence of grant %s (%s) read for %s", grant_id, claims.get("part"), reader)
        if claims.get("part") == "reason":
            return {"grant_id": found["grant_id"], "reason": found["reason"]}
        if claims.get("part") != "calls":
            raise HTTPException(status_code=422, detail="Say which part.")
        return found
    if claims.get("place"):
        try:
            found_calls, cut = await grants.find_by_place(
                session, tenant_id=tenant, place=str(claims["place"]), since=_when(claims.get("since")),
                until=_when(claims.get("until")), user_id=claims.get("user"), agent_id=claims.get("agent"),
                device_id=claims.get("device"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        logger.info("Covered calls searched by place for %s: %d found", reader, len(found_calls))
        return {"data": found_calls, "truncated": cut}
    raise HTTPException(status_code=422, detail="Name a grant or a place.")
