"""Linked sign-ins (``services/sign_in/linked.py``): one's own on My Profile, anyone's for administrators.

- ``GET /api/admin/users/me/sign-ins``, ``DELETE /me/sign-ins/{link_id}``: one's own.
- ``GET /api/admin/users/{user_id}/sign-ins``, ``DELETE /{user_id}/sign-ins/{link_id}``: a superuser's view
  of anyone's, audited.

An unlink that would leave the account no way in is refused with 409 ``last_way``. Linking a provider while
signed in starts in the browser routes (``POST /api/auth/sso/{key}/link-start``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.sign_in import linked, module
from faden_backend.web.api.admin.dependencies import require_superuser

log = logging.getLogger(__name__)

router = APIRouter()


class LinkDTO(BaseModel):
    """A linked sign-in."""

    id: uuid.UUID
    provider: str
    #: The provider's name as the sign-in page shows it; ``None`` when it is offered no more.
    provider_name: str | None = None
    account_email: str | None = None
    last_sign_in_at: datetime | None = None


class SignInsDTO(BaseModel):
    """How an account signs in: its linked sign-ins, and whether its password is a way in."""

    links: list[LinkDTO]
    password: bool


async def _names() -> dict[str, str]:
    """The offered providers' names, by key; none when the module cannot say."""
    if not module.enabled():
        return {}
    try:
        return {p.key: p.name for p in await module.public_providers()}
    except (module.ModuleUnavailableError, module.ModuleRefusedError) as exc:
        log.warning("Cannot name the sign-in providers: %s", exc)
        return {}


async def _sign_ins(session: AsyncSession, user: User) -> SignInsDTO:
    names = await _names()
    return SignInsDTO(
        links=[
            LinkDTO(id=link.id, provider=link.provider, provider_name=names.get(link.provider),
                    account_email=link.account_email, last_sign_in_at=link.last_sign_in_at)
            for link in await linked.links(session, user.id)
        ],
        password=await linked.password_signs_in(user),
    )


async def _unlink(session: AsyncSession, user: User, link_id: uuid.UUID, actor: User) -> None:
    try:
        done = await linked.unlink(session, user, link_id, actor=actor)
    except linked.LastWayError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="last_way") from exc
    if not done:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such linked sign-in")


@router.get("/me/sign-ins", response_model=SignInsDTO, name="my_sign_ins")
async def my_sign_ins(
    user: Annotated[User, Depends(current_active_user)], session: AsyncSession = Depends(get_db_session),
) -> SignInsDTO:
    """How the current user signs in."""
    return await _sign_ins(session, user)


@router.delete("/me/sign-ins/{link_id}", status_code=status.HTTP_204_NO_CONTENT, name="unlink_my_sign_in")
async def unlink_my_sign_in(
    link_id: uuid.UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Unlink one of the current user's sign-ins (never the last way in)."""
    await _unlink(session, user, link_id, user)


async def _target(session: AsyncSession, user_id: uuid.UUID) -> User:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return target


@router.get("/{user_id}/sign-ins", response_model=SignInsDTO, name="user_sign_ins")
async def user_sign_ins(
    user_id: uuid.UUID,
    _admin: Annotated[User, Depends(require_superuser)],
    session: AsyncSession = Depends(get_db_session),
) -> SignInsDTO:
    """How a person signs in, for an administrator."""
    return await _sign_ins(session, await _target(session, user_id))


@router.delete(
    "/{user_id}/sign-ins/{link_id}", status_code=status.HTTP_204_NO_CONTENT, name="unlink_user_sign_in",
)
async def unlink_user_sign_in(
    user_id: uuid.UUID,
    link_id: uuid.UUID,
    admin: Annotated[User, Depends(require_superuser)],
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Unlink a person's sign-in, as an administrator (never their last way in), audited."""
    await _unlink(session, await _target(session, user_id), link_id, admin)
