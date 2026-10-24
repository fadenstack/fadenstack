"""An outside identity becomes a Fadenstack account.

The rules, whatever the protocol:

- A person is found again by provider and subject (``oauth_account``), never by e-mail alone.
- A new account needs an address the module vouches for, and the provider's leave to create it; it gets
  the provider's default roles, never superuser rights.
- An existing account the identity is not linked to yet is not taken over: the link waits for its owner
  (``registry.link_confirmation``) or for an administrator.
- With ``manage_groups``, the memberships the provider keeps follow its answer; those an administrator set
  stay as they are.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.groups import Group, UserGroup
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.rbac import Role
from faden_backend.db.models.users import User
from faden_backend.services.extensions import registry
from faden_backend.services.sign_in.contract import ExternalIdentity, Refusal

log = logging.getLogger(__name__)


@dataclass
class SignedIn:
    """The account an identity signed in to, and what changed on the way."""

    user: User
    created: bool = False
    groups_added: list[uuid.UUID] = field(default_factory=list)
    groups_removed: list[uuid.UUID] = field(default_factory=list)


def membership_source(provider: str) -> str:
    """How a membership kept by *provider* is marked (``user_groups.source``)."""
    return f"provider:{provider}"


async def _audit(
    session: AsyncSession,
    action: str,
    user: User | None,
    identity: ExternalIdentity,
    *,
    result: str = "allow",
    **metadata: object,
) -> None:
    from faden_backend.db.models.containers import AuditResult  # noqa: PLC0415
    from faden_backend.services.audit import chain  # noqa: PLC0415

    try:
        await chain.append(
            chain.NewEvent(
                action=action,
                target_type="user",
                target_id=str(user.id) if user else identity.subject,
                result=AuditResult.ALLOW if result == "allow" else AuditResult.DENY,
                actor_id=user.id if user else None,
                severity="normal" if action == "auth.sign_in" else "high",
                metadata_json=json.dumps({"provider": identity.provider, **metadata}, default=str),
                actor_email=user.email if user else None,
            ),
            request_session=session,
        )
    except Exception:
        log.exception("Could not record %s in the audit trail", action)


async def _refuse(
    session: AsyncSession, refusal: Refusal, identity: ExternalIdentity, user: User | None = None,
) -> Refusal:
    await _audit(session, "auth.sign_in.refused", user, identity, result="deny", reason=refusal.value)
    return refusal


async def find_link(session: AsyncSession, provider: str, subject: str) -> OAuthAccount | None:
    """The link of *subject* at *provider*, if there is one."""
    return await session.scalar(
        select(OAuthAccount).where(OAuthAccount.oauth_name == provider, OAuthAccount.account_id == subject),
    )


async def link(session: AsyncSession, user: User, identity: ExternalIdentity) -> OAuthAccount:
    """Link *identity* to *user*'s account (a confirmed link, or a new account)."""
    row = OAuthAccount(
        id=uuid.uuid4(),
        user_id=user.id,
        oauth_name=identity.provider,
        account_id=identity.subject,
        account_email=identity.email,
        access_token="",
        last_sign_in_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    await _audit(session, "auth.identity.link", user, identity, subject=identity.subject)
    return row


async def _new_account(session: AsyncSession, identity: ExternalIdentity, email: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=PasswordHelper().hash(secrets.token_urlsafe(32)),
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.flush()
    rbac = RbacDAO(session)
    role_ids: list[uuid.UUID] = []
    if identity.default_role_ids:
        found = await session.scalars(select(Role.id).where(Role.id.in_(identity.default_role_ids)))
        role_ids = list(found)
    if role_ids:
        await rbac.set_user_roles(user.id, role_ids)
    else:
        default = await rbac.get_role_by_name("default_user")
        if default is not None:
            await rbac.assign_role(user.id, default.id)
    await session.flush()
    await _audit(session, "user.create", user, identity, via="sign_in_provider")
    return user


async def _sync_groups(session: AsyncSession, user: User, identity: ExternalIdentity) -> tuple[list, list]:
    """The memberships *identity*'s provider keeps, made to match its answer."""
    source = membership_source(identity.provider)
    wanted: set[uuid.UUID] = set()
    if identity.group_ids:
        wanted = set(await session.scalars(select(Group.id).where(Group.id.in_(identity.group_ids))))
    rows = (await session.execute(
        select(UserGroup.group_id, UserGroup.source).where(UserGroup.user_id == user.id),
    )).all()
    kept_by_provider = {group_id for group_id, row_source in rows if row_source == source}
    member_of = {group_id for group_id, _ in rows}

    added = sorted(wanted - member_of)
    removed = sorted(kept_by_provider - wanted)
    if added:
        await session.execute(
            pg_insert(UserGroup)
            .values([{"user_id": user.id, "group_id": group_id, "source": source} for group_id in added])
            .on_conflict_do_nothing(),
        )
    if removed:
        await session.execute(
            delete(UserGroup).where(
                UserGroup.user_id == user.id, UserGroup.source == source, UserGroup.group_id.in_(removed),
            ),
        )
    await session.flush()
    return added, removed


async def _linked(session: AsyncSession, found: OAuthAccount, identity: ExternalIdentity) -> User | Refusal:
    """The account a known person is linked to, if they may still use it."""
    user = await session.get(User, found.user_id)
    if user is None:
        return await _refuse(session, Refusal.NOT_ALLOWED, identity)
    if not user.is_active:
        return await _refuse(session, Refusal.ACCOUNT_DISABLED, identity, user)
    found.last_sign_in_at = datetime.now(UTC)
    if identity.email:
        found.account_email = identity.email
    await _follow_email(session, user, identity)
    return user


async def _unlinked(session: AsyncSession, identity: ExternalIdentity) -> User | Refusal:
    """A new account for a person the provider has not signed in before, or why not."""
    if not identity.email:
        return await _refuse(session, Refusal.EMAIL_MISSING, identity)
    if not identity.email_trusted:
        return await _refuse(session, Refusal.EMAIL_NOT_TRUSTED, identity)
    existing = await session.scalar(select(User).where(func.lower(User.email) == identity.email.lower()))
    if existing is not None:
        return await _refuse(session, await _meets_account(session, existing, identity), identity, existing)
    if not identity.create_account:
        return await _refuse(session, Refusal.REGISTRATION_DISABLED, identity)
    user = await _new_account(session, identity, identity.email)
    await link(session, user, identity)
    return user


async def _meets_account(session: AsyncSession, existing: User, identity: ExternalIdentity) -> Refusal:
    """Why an identity does not simply sign in to an account with its address."""
    if not existing.is_active:
        return Refusal.ACCOUNT_DISABLED
    if not existing.is_verified:
        return Refusal.ACCOUNT_NOT_VERIFIED
    if registry.link_confirmation is not None:
        return Refusal(await registry.link_confirmation(session, existing, identity))
    return Refusal.LINK_NEEDS_CONFIRMATION


async def sign_in(session: AsyncSession, identity: ExternalIdentity) -> SignedIn | Refusal:
    """The account *identity* signs in to, or why it may not."""
    found = await find_link(session, identity.provider, identity.subject)
    user = await (_linked(session, found, identity) if found is not None else _unlinked(session, identity))
    if isinstance(user, Refusal):
        return user
    created = found is None

    added: list[uuid.UUID] = []
    removed: list[uuid.UUID] = []
    if identity.manage_groups:
        added, removed = await _sync_groups(session, user, identity)
    await session.flush()
    await _audit(
        session, "auth.sign_in", user, identity, method="provider", created=created,
        groups_added=added, groups_removed=removed,
    )
    return SignedIn(user=user, created=created, groups_added=added, groups_removed=removed)


async def _follow_email(session: AsyncSession, user: User, identity: ExternalIdentity) -> None:
    """The provider's address for a linked person, when it vouches for it and no other account has it."""
    if not identity.email or not identity.email_trusted or identity.email.lower() == user.email.lower():
        return
    taken = await session.scalar(
        select(User.id).where(func.lower(User.email) == identity.email.lower(), User.id != user.id),
    )
    if taken is not None:
        log.warning("Sign-in provider %s gave %s an address another account has; kept the old one",
                    identity.provider, user.id)
        return
    old = user.email
    user.email = identity.email
    await _audit(session, "user.account.update", user, identity, changed=["email"], previous=old)
