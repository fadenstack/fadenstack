"""One-time links an administrator creates so that a person can set a new password, without mail.

For a server that sends no mail, or a person who cannot get it: an administrator, in Root Mode and with a
reason, creates a link for one account and hands it over through a channel only that person reads. After the
OWASP Forgot Password Cheat Sheet, as authentik's recovery links and Keycloak's admin-initiated actions:

- a token of 256 random bits from the CSPRNG; only its SHA-256 is kept, so the table holds no usable link;
- it works once, until it expires (an hour, or a day for a person reached later);
- a new link for the account cancels the earlier ones; a password change, a suspension or ended sign-ins
  since the link was made void it (``User.sessions_valid_after``);
- it travels in the address's fragment (``/reset-password#link=``), which browsers never send to a server, so
  it stays out of access logs and Referer headers;
- using it sets the password under the password rule and ends every sign-in of the account. It signs nobody
  in: the person signs in afterwards as usual. A password the rule refuses leaves the link usable.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta

from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.password_reset import PasswordResetLink
from faden_backend.db.models.users import User, end_sign_ins
from faden_backend.services.password_policy import password_problem

#: How long a link may work, by the hours an administrator picks.
LIFETIMES = {1: timedelta(hours=1), 24: timedelta(hours=24)}
#: Links that ended longer ago than this are deleted.
KEEP_ENDED = timedelta(days=1)


class LinkExpiredError(Exception):
    """The link is used, expired, cancelled, or its account can no longer use it."""


class PasswordRejectedError(Exception):
    """The new password breaks the password rule; the message says how. The link stays usable."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create(
    session: AsyncSession, user: User, admin: User, *, valid_hours: int, reason: str,
) -> tuple[str, datetime]:
    """A new link for *user*, cancelling earlier ones; the token (shown once) and when it expires."""
    now = datetime.now(UTC)
    expires_at = now + LIFETIMES[valid_hours]
    unused = (PasswordResetLink.user_id == user.id, PasswordResetLink.used_at.is_(None))
    cancelled = await session.execute(delete(PasswordResetLink).where(*unused))
    await session.execute(delete(PasswordResetLink).where(PasswordResetLink.expires_at < now - KEEP_ENDED))
    token = secrets.token_urlsafe(32)
    session.add(PasswordResetLink(
        user_id=user.id, created_by=admin.id, token_hash=_hash(token), created_at=now, expires_at=expires_at,
    ))
    await session.flush()
    await AuditDAO(session).log(
        action="user.password.reset_link",
        target_type="user",
        target_id=str(user.id),
        result=AuditResult.ALLOW,
        actor_id=admin.id,
        severity="high",
        metadata_json=json.dumps({
            "email": user.email, "reason": reason, "valid_hours": valid_hours,
            "expires_at": expires_at.isoformat(), "earlier_links_cancelled": cancelled.rowcount or 0,
        }),
    )
    return token, expires_at


async def _open(
    session: AsyncSession, token: str, *, lock: bool = False,
) -> tuple[PasswordResetLink, User] | None:
    query = select(PasswordResetLink).where(
        PasswordResetLink.token_hash == _hash(token),
        PasswordResetLink.used_at.is_(None),
        PasswordResetLink.expires_at > datetime.now(UTC),
    )
    link = await session.scalar(query.with_for_update() if lock else query)
    user = await session.get(User, link.user_id) if link is not None else None
    if link is None or user is None or not user.is_active:
        return None
    if user.sessions_valid_after is not None and user.sessions_valid_after > link.created_at:
        return None  # the password changed, or the account was suspended, since the link was made
    return link, user


async def pending(session: AsyncSession, token: str) -> tuple[str, datetime] | None:
    """The account's address and the link's expiry, while *token* can set a password; changes nothing."""
    found = await _open(session, token)
    return (found[1].email, found[0].expires_at) if found is not None else None


async def use(session: AsyncSession, token: str, password: str) -> User:
    """Set *password* on the account *token* names, once; every sign-in of the account ends.

    Raises :class:`LinkExpiredError`, or :class:`PasswordRejectedError` (the link stays usable).
    """
    found = await _open(session, token, lock=True)
    if found is None:
        raise LinkExpiredError
    link, user = found
    problem = password_problem(password, user.email)
    if problem:
        raise PasswordRejectedError(problem)
    link.used_at = datetime.now(UTC)
    user.hashed_password = PasswordHelper().hash(password)
    end_sign_ins(user)
    await session.execute(delete(PasswordResetLink).where(
        PasswordResetLink.user_id == user.id, PasswordResetLink.used_at.is_(None),
    ))
    await session.flush()
    await AuditDAO(session).log(
        action="user.password.reset_by_link",
        target_type="user",
        target_id=str(user.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="high",
        metadata_json=json.dumps({"email": user.email, "link_created_by": str(link.created_by)}),
    )
    return user
