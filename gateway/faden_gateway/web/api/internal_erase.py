"""Erase chat data at the backend's word: an administrator's delete in the console, an account deleted.

The backend cannot reach the attachment files (only the gateway mounts them), so it asks here. Its request
carries a grant only the backend can sign -- a short JWT with the users' secret, ``typ`` ``chat_erase``,
naming what to erase -- so a caller who reaches the gateway directly cannot erase anything.
"""

from __future__ import annotations

import uuid
from typing import Literal

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dependencies import get_db_session
from faden_gateway.services.gateway import erasure
from faden_gateway.settings import settings

router = APIRouter()

#: The header with the grant, and the grant's ``typ``.
ERASE_GRANT_HEADER = "X-Faden-Erase-Grant"
ERASE_TOKEN_TYPE = "chat_erase"  # noqa: S105 - a claim value, not a secret


class EraseRequest(BaseModel):
    """What to erase: one chat, project or file, or everything of a person."""

    scope: Literal["session", "project", "attachment", "user"]
    target: str


def _granted(token: str | None, body: EraseRequest) -> bool:
    if not token or not settings.jwt_secret:
        return False
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False, "require": ["exp", "typ", "scope", "target"]},
        )
    except jwt.PyJWTError:
        return False
    return (
        claims.get("typ") == ERASE_TOKEN_TYPE
        and claims.get("scope") == body.scope
        and str(claims.get("target")) == body.target
    )


@router.post("/internal/chat-erase", include_in_schema=False)
async def erase(
    body: EraseRequest,
    request: Request,
    grant: str | None = Header(default=None, alias=ERASE_GRANT_HEADER),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
    """Erase what the grant names, files first; returns what went."""
    if not _granted(grant, body):
        raise HTTPException(status_code=403, detail="No erase grant for this.")
    file_store = getattr(request.app.state, "chat_file_store", None)
    if body.scope == "user":
        erased = await erasure.erase_user(session, file_store, body.target)
    else:
        try:
            target = uuid.UUID(body.target)
        except ValueError:
            raise HTTPException(status_code=422, detail="Not an id.") from None
        run = {
            "session": erasure.erase_session,
            "project": erasure.erase_project,
            "attachment": erasure.erase_attachment,
        }[body.scope]
        erased = await run(session, file_store, target)
    await session.commit()
    return {
        "chats": erased.chats, "projects": erased.projects, "files": erased.files,
        "memory_facts": erased.memory_facts,
        # An account's usage: its request-log rows and person-level usage days.
        "request_logs": erased.request_logs, "usage_days": erased.usage_days,
    }
