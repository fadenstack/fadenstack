"""The ways a person signs in: their password, and the outside sign-ins linked to their account.

- A person sees their linked sign-ins and unlinks one; an administrator does so for anyone. Each unlink is
  audited, with who did it.
- **Never the last way in**: a link goes only while another link, or a password that signs in, remains. A
  password signs in when someone knows it (``User.password_set``) and no password check refuses the address
  (``password_check_refusal``: an SSO-only domain, say), superusers excepted; a check that fails counts as a
  refusal. Who has no other way in gets a password first (a reset link).
- **Linking while signed in**: the person signs in with the provider from their profile, and that identity is
  linked to the account they are signed in to. An identity linked to another account, or that the provider's
  provisioning keeps for another account, stays theirs; with ``provisioned_only``, only an account the
  provider provisions is linked.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.users import User, password_check_refusal
from faden_backend.services import provisioning
from faden_backend.services.sign_in import accounts

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from faden_backend.services.sign_in.contract import ExternalIdentity

#: Why an identity was not linked to the signed-in account (``not_provisioned`` is the sign-in's refusal).
LinkRefusal = Literal["already_linked", "not_provisioned"]


@dataclass(frozen=True)
class Linked:
    """One linked sign-in, as its owner and administrators see it (never the provider's tokens)."""

    id: uuid.UUID
    provider: str
    #: The address the provider gave at the last sign-in.
    account_email: str | None
    last_sign_in_at: datetime | None


class LastWayError(Exception):
    """The link is the account's last way in."""


async def links(session: AsyncSession, user_id: uuid.UUID) -> list[Linked]:
    """*user_id*'s linked sign-ins, by provider."""
    rows = await session.scalars(
        select(OAuthAccount).where(OAuthAccount.user_id == user_id).order_by(OAuthAccount.oauth_name),
    )
    return [Linked(r.id, r.oauth_name, r.account_email, r.last_sign_in_at) for r in rows]


async def password_signs_in(user: User) -> bool:
    """Whether *user*'s password is a way in: someone knows it, and no password check refuses it."""
    if not user.password_set:
        return False
    return user.is_superuser or await password_check_refusal(user.email) is None


async def unlink(session: AsyncSession, user: User, link_id: uuid.UUID, *, actor: User) -> bool:
    """Unlink *link_id* from *user*'s account; ``False`` when it is not theirs.

    ``LastWayError`` when it is their last way in.
    """
    found = await session.scalar(
        select(OAuthAccount).where(OAuthAccount.id == link_id, OAuthAccount.user_id == user.id),
    )
    if found is None:
        return False
    others = await session.scalar(
        select(func.count()).select_from(OAuthAccount).where(
            OAuthAccount.user_id == user.id, OAuthAccount.id != link_id,
        ),
    )
    if not others and not await password_signs_in(user):
        raise LastWayError
    provider, subject = found.oauth_name, found.account_id
    await session.delete(found)
    await session.flush()
    await AuditDAO(session).log(
        action="auth.identity.unlink", target_type="user", target_id=str(user.id), result=AuditResult.ALLOW,
        actor_id=actor.id, severity="high",
        metadata_json=json.dumps({"provider": provider, "subject": subject, "by_owner": actor.id == user.id}),
    )
    return True


async def link_signed_in(session: AsyncSession, user: User, identity: ExternalIdentity) -> LinkRefusal | None:
    """Link *identity* to *user*, who is signed in and has just signed in with it too; or why not."""
    found = await accounts.find_link(session, identity.provider, identity.subject)
    if found is not None:
        return None if found.user_id == user.id else "already_linked"
    source = provisioning.source_of(identity.provider)
    kept = await provisioning.find_person(session, source, external_id=identity.subject)
    if kept is not None and kept.user.id != user.id:
        return "already_linked"
    if identity.email and identity.email_trusted and identity.email.lower() != user.email.lower():
        owner = await session.scalar(select(User).where(func.lower(User.email) == identity.email.lower()))
        # The provider's provisioning keeps that address for its own account.
        if owner is not None and await provisioning.find_person(session, source, user_id=owner.id):
            return "already_linked"
    kept_here = await provisioning.find_person(session, source, user_id=user.id)
    if identity.provisioned_only and not user.is_superuser and kept_here is None:
        return "not_provisioned"
    await accounts.link(session, user, identity, via="profile")
    return None
