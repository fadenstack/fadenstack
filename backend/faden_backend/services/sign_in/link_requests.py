"""An outside identity that meets an existing account is linked once the person proves the account is theirs.

``accounts`` never links an identity to an account it found by address: the provider's word that an
address is someone's is not the account owner's, and a new way into an account needs the account itself
(ASVS 5.0 7.5.1: full re-authentication before what an account signs in with changes). So the sign-in
waits, for ``TTL``, bound to the browser it happened in: an httpOnly cookie holds a token of which only the
SHA-256 is kept. In that browser the person proves the account is theirs:

- with the account's password (always), or
- with a code sent to the account's address, while this server can send mail (``mail_available``).

Then the identity is linked and the person signed in, as the provider would have signed them in. Since the
proof is given in the browser that began, a mail alone links nothing: whoever starts a sign-in with an
identity that carries someone else's address cannot have that someone confirm it from their mailbox.

Limits (ASVS 5.0 V6.3 against guessing, V6.6 for codes sent out of band): ``MAX_TRIES`` wrong passwords or
codes use the request up; at most ``MAX_CODES`` codes, one per ``CODE_EVERY``, each replacing the last; a
code has 8 digits, is kept only as an HMAC under the backend's secret, and works until the request expires.
A wrong code reads as a wrong password, and the routes share the sign-in's rate limit.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi_users.password import PasswordHelper
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.oauth import IdentityLinkRequest
from faden_backend.db.models.users import User
from faden_backend.services.extensions import mail_available
from faden_backend.services.sign_in import accounts
from faden_backend.services.sign_in.contract import ExternalIdentity, Refusal
from faden_backend.settings import settings

log = logging.getLogger(__name__)
#: ASVS 5.0 6.5.5: out-of-band requests live at most 10 minutes.
TTL = timedelta(minutes=10)
MAX_TRIES = 5
MAX_CODES = 3
CODE_EVERY = timedelta(seconds=60)
#: What the browser keeps, and where it sends it back.
COOKIE = "faden_link"
COOKIE_PATH = "/api/auth/sso/link"
#: Requests that ended longer ago than this are deleted (they hold the identity's address).
KEEP_ENDED = timedelta(days=1)

CodeResult = Literal["sent", "too_soon", "too_many", "no_mail", "expired"]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _code_hash(request: IdentityLinkRequest, code: str) -> str:
    message = f"{request.id}:{code}".encode()
    return hmac.new(settings.users_secret.encode(), message, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Waiting:
    """What the sign-in page shows while the sign-in waits for the proof."""

    provider_name: str
    account_email: str
    expires_at: datetime
    #: A code can be sent to the account's address (mail is on).
    mail: bool
    code_sent: bool


async def begin(session: AsyncSession, identity: ExternalIdentity) -> str | None:
    """The sign-in *identity* made waits for the proof; the token for this browser's cookie.

    ``None`` when no account has the identity's address (nothing to link to).
    """
    if not identity.email:
        return None
    user = await session.scalar(select(User).where(func.lower(User.email) == identity.email.lower()))
    if user is None:
        return None
    now = datetime.now(UTC)
    ended = IdentityLinkRequest.expires_at < now - KEEP_ENDED
    await session.execute(delete(IdentityLinkRequest).where(ended))
    token = secrets.token_urlsafe(32)
    session.add(IdentityLinkRequest(
        user_id=user.id, provider=identity.provider, provider_name=identity.provider_name,
        subject=identity.subject, identity_email=identity.email, sent_to=user.email,
        token_hash=_hash(token), expires_at=now + TTL, identity_json=identity.model_dump(mode="json"),
    ))
    await session.flush()
    await accounts.audit(session, "auth.identity.link_requested", user, identity)
    return token


async def _open(
    session: AsyncSession, token: str | None, *, lock: bool = False,
) -> IdentityLinkRequest | None:
    """The request this browser's *token* names, while the proof can still come."""
    if not token:
        return None
    query = select(IdentityLinkRequest).where(
        IdentityLinkRequest.token_hash == _hash(token),
        IdentityLinkRequest.used_at.is_(None),
        IdentityLinkRequest.expires_at > datetime.now(UTC),
        IdentityLinkRequest.attempts < MAX_TRIES,
    )
    return await session.scalar(query.with_for_update() if lock else query)


async def waiting(session: AsyncSession, token: str | None) -> Waiting | None:
    """What this browser's sign-in waits for, if it still waits."""
    request = await _open(session, token)
    user = await session.get(User, request.user_id) if request is not None else None
    if request is None or user is None:
        return None
    return Waiting(
        provider_name=request.provider_name, account_email=user.email, expires_at=request.expires_at,
        mail=mail_available(), code_sent=request.code_hash is not None,
    )


async def send_code(session: AsyncSession, token: str | None) -> CodeResult:
    """Send a code to the account's address, replacing any sent before; or why not."""
    from faden_backend.services.notifications import NotificationService  # noqa: PLC0415
    from faden_backend.services.notifications.language import recipient_language  # noqa: PLC0415

    request = await _open(session, token, lock=True)
    user = await session.get(User, request.user_id) if request is not None else None
    if request is None or user is None:
        return "expired"
    now = datetime.now(UTC)
    held_back = _held_back(request, now)
    if held_back is not None:
        return held_back
    code = f"{secrets.randbelow(10**8):08d}"
    queued = await NotificationService(session).enqueue_link_code(
        to_email=user.email,
        to_name=user.email.split("@", 1)[0],
        provider_name=request.provider_name,
        identity_email=request.identity_email,
        code=code,
        expires_at=request.expires_at,
        language=await recipient_language(session, user.id),
    )
    if not queued:
        return "no_mail"
    request.code_hash = _code_hash(request, code)
    request.codes_sent += 1
    request.code_sent_at = now
    identity = ExternalIdentity.model_validate(request.identity_json)
    await accounts.audit(session, "auth.identity.link_code_sent", user, identity, sent_to=user.email)
    return "sent"


def _held_back(request: IdentityLinkRequest, now: datetime) -> CodeResult | None:
    """Why no code goes out now, if one does not."""
    if not mail_available():
        return "no_mail"
    if request.codes_sent >= MAX_CODES:
        return "too_many"
    if request.code_sent_at is not None and now - request.code_sent_at < CODE_EVERY:
        return "too_soon"
    return None


@dataclass(frozen=True)
class Linked:
    """The identity is linked: the person signs in with it."""

    signed: accounts.SignedIn
    identity: ExternalIdentity


async def prove(
    session: AsyncSession, token: str | None, *, password: str | None = None, code: str | None = None,
) -> Linked | Refusal:
    """Link the waiting identity if *password* is the account's or *code* the one sent; or why not.

    A wrong one counts against ``MAX_TRIES`` and reads as wrong credentials, whichever it was.
    """
    request = await _open(session, token, lock=True)
    if request is None:
        return Refusal.SIGN_IN_EXPIRED
    identity = ExternalIdentity.model_validate(request.identity_json)
    user = await session.get(User, request.user_id)
    if user is None:
        request.used_at = datetime.now(UTC)
        return await accounts.refuse(session, Refusal.NOT_ALLOWED, identity)
    if not _proved(request, user, password=password, code=code):
        request.attempts += 1
        if request.attempts >= MAX_TRIES:
            request.used_at = datetime.now(UTC)
        await accounts.audit(
            session, "auth.identity.link_refused", user, identity, result="deny",
            reason="wrong_proof", by="password" if password is not None else "code", tries=request.attempts,
        )
        return Refusal.WRONG_CREDENTIALS
    request.used_at = datetime.now(UTC)
    refusal = await _account_refusal(session, request, user, identity)
    if refusal is not None:
        return await accounts.refuse(session, refusal, identity, user)
    if await accounts.find_link(session, identity.provider, identity.subject) is None:
        await accounts.link(session, user, identity, via="password" if password is not None else "email_code")
    signed = await accounts.sign_in(session, identity)
    return signed if isinstance(signed, Refusal) else Linked(signed=signed, identity=identity)


async def _account_refusal(
    session: AsyncSession, request: IdentityLinkRequest, user: User, identity: ExternalIdentity,
) -> Refusal | None:
    """Why the proven account still cannot take the identity, if it cannot."""
    if not user.is_active:
        return Refusal.ACCOUNT_DISABLED
    if not user.is_verified:
        return Refusal.ACCOUNT_NOT_VERIFIED
    if user.email.lower() != request.sent_to.lower():
        # The account's address changed since the sign-in met it: it may be another person's account now.
        return Refusal.NOT_ALLOWED
    linked = await accounts.find_link(session, identity.provider, identity.subject)
    if linked is not None and linked.user_id != user.id:
        return Refusal.NOT_ALLOWED
    return None


def _proved(request: IdentityLinkRequest, user: User, *, password: str | None, code: str | None) -> bool:
    if password is not None:
        verified, updated = PasswordHelper().verify_and_update(password, user.hashed_password)
        if verified and updated is not None:
            user.hashed_password = updated
        return verified
    if code is None or request.code_hash is None:
        return False
    return hmac.compare_digest(request.code_hash, _code_hash(request, code))
