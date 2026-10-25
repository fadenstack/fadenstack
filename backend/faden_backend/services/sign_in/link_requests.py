"""An outside identity that meets an existing account waits for the account's owner to confirm it.

``accounts`` never links an identity to an account it found by address: the provider's word that an
address is someone's is not the account owner's. When the registry's ``link_confirmation`` is this
module's ``ask_the_owner``, the owner gets a link at the account's address instead; following it and
confirming links the identity, and the next sign-in with the provider lands in that account.

The link (OWASP Forgot Password Cheat Sheet; ASVS 5.0 6.5.1 and 6.5.3 ask the same of out-of-band codes):

- a token of 256 random bits from the CSPRNG; only its SHA-256 is kept, so the table gives nobody a
  usable link;
- it works once (the update that uses it is the check) and for ``TTL``;
- it works only while the account's address is still the one it went to, and the account is active;
- following it shows what would be linked; only an explicit confirmation (POST) links, so a mail
  scanner that opens links uses nothing up.

Another sign-in attempt within ``RESEND_AFTER`` sends no further mail.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.oauth import IdentityLinkRequest
from faden_backend.db.models.users import User
from faden_backend.services.sign_in import accounts
from faden_backend.services.sign_in.contract import ExternalIdentity, Refusal
from faden_backend.settings import settings

log = logging.getLogger(__name__)
TTL = timedelta(minutes=30)
RESEND_AFTER = timedelta(minutes=5)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def confirm_url(token: str) -> str:
    """Where the link in the mail goes: the console's sign-in page, which asks before confirming.

    The token is in the fragment, which browsers never send: not to the server, not as a Referer of the
    page's own requests, so it stays out of every access log. The base is the configured console address,
    never the request's Host header (OWASP Forgot Password Cheat Sheet).
    """
    return f"{settings.mailer_frontend_base_url.rstrip('/')}/login#{urlencode({'link': token})}"


async def ask_the_owner(session: AsyncSession, user: User, identity: ExternalIdentity) -> Refusal:
    """Send *user* a link to confirm *identity* is theirs; the sign-in waits for it."""
    from faden_backend.services.notifications import NotificationService  # noqa: PLC0415

    now = datetime.now(UTC)
    recent = await session.scalar(
        select(IdentityLinkRequest.id).where(
            IdentityLinkRequest.user_id == user.id,
            IdentityLinkRequest.provider == identity.provider,
            IdentityLinkRequest.subject == identity.subject,
            IdentityLinkRequest.used_at.is_(None),
            IdentityLinkRequest.created_at > now - RESEND_AFTER,
        ),
    )
    if recent is not None:
        return Refusal.LINK_CONFIRMATION_SENT
    token = secrets.token_urlsafe(32)
    session.add(IdentityLinkRequest(
        user_id=user.id, provider=identity.provider, provider_name=identity.provider_name,
        subject=identity.subject, identity_email=identity.email, sent_to=user.email,
        token_hash=_hash(token), expires_at=now + TTL,
    ))
    await NotificationService(session).enqueue_link_confirmation(
        to_email=user.email,
        to_name=user.email.split("@", 1)[0],
        provider_name=identity.provider_name,
        identity_email=identity.email,
        confirm_url=confirm_url(token),
        expires_minutes=int(TTL.total_seconds() // 60),
    )
    await accounts.audit(session, "auth.identity.link_requested", user, identity, sent_to=user.email)
    return Refusal.LINK_CONFIRMATION_SENT


async def pending(session: AsyncSession, token: str) -> IdentityLinkRequest | None:
    """The request *token* names, while it can still be confirmed."""
    return await session.scalar(
        select(IdentityLinkRequest).where(
            IdentityLinkRequest.token_hash == _hash(token),
            IdentityLinkRequest.used_at.is_(None),
            IdentityLinkRequest.expires_at > datetime.now(UTC),
        ),
    )


async def confirm(session: AsyncSession, token: str) -> User | Refusal:
    """Link the identity *token*'s request names to its account, once; or why not."""
    used = await session.scalar(
        update(IdentityLinkRequest)
        .where(
            IdentityLinkRequest.token_hash == _hash(token),
            IdentityLinkRequest.used_at.is_(None),
            IdentityLinkRequest.expires_at > datetime.now(UTC),
        )
        .values(used_at=datetime.now(UTC))
        .returning(IdentityLinkRequest),
    )
    if used is None:
        return Refusal.SIGN_IN_EXPIRED
    identity = ExternalIdentity(
        provider=used.provider, provider_name=used.provider_name, subject=used.subject,
        email=used.identity_email,
    )
    user = await session.get(User, used.user_id)
    if user is None or not user.is_active:
        return await accounts.refuse(session, Refusal.ACCOUNT_DISABLED, identity, user)
    if user.email.lower() != used.sent_to.lower():
        # The account's address changed since: the link proves nothing about the new one.
        return await accounts.refuse(session, Refusal.NOT_ALLOWED, identity, user)
    linked = await accounts.find_link(session, used.provider, used.subject)
    if linked is not None:
        if linked.user_id == user.id:
            return user
        return await accounts.refuse(session, Refusal.NOT_ALLOWED, identity, user)
    await accounts.link(session, user, identity, via="email_confirmation")
    return user
