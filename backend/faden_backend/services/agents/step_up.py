"""The second sign-in before Bypass is switched on (sdk/41; OWASP ASVS 5.0 7.5.3), shaped as a pairing (RFC 8628).

Switching approvals off for a chat is a highly sensitive operation, so the person signs in again first -- in the
browser, as when the app was paired, so the app never sees a password and the organisation's sign-in (with its
MFA) applies:

1. Only the Bypass card's Allow starts it: the app sends its agent token to ``POST /api/agent-tokens/step-ups``
   and gets the app's code (kept by the app), a code for the link, and the link (``/step-up#code=...``).
2. The app opens the link in the system browser. The page binds the step-up to that browser (an httpOnly cookie)
   and demands a fresh sign-in, even with a console sign-in open there: the account's password again, a sign-in
   provider forced to authenticate anew (OIDC ``prompt=login`` and ``max_age=0`` with ``auth_time`` checked, SAML
   ``ForceAuthn`` with ``AuthnInstant`` checked), or the directory password again. Whoever signs in must be the
   person of the app's agent token; nobody else can approve their Bypass.
3. The page says what is approved -- the agent, the device, "switch approvals off in a chat", the longest time --
   with Approve and Deny.
4. The app polls with its code and, once approved, gets the proof once: a JWT under a key derived only for it
   (never valid as a sign-in), naming tenant, person, agent and device, the time of the sign-in by this server's
   clock and how it was made. The gateway takes it for 5 minutes after the sign-in (``faden_gateway.services.
   gateway.step_up``), for every Bypass that person switches on in that app meanwhile (parallel chats).

Codes (ASVS 5.0 V6.5), as pairing's: the app's code is 32 bytes from the CSPRNG, kept as its SHA-256; the link's
code is 8 characters of ``BCDFGHJKLMNPQRSTVWXZ`` (34.5 bits), kept as an HMAC under a key derived from the backend's
secret. Both live 10 minutes and serve once. Neither, nor the proof, is ever logged or written to the audit trail;
the step-up's id is.

Limits (RFC 8628 §5.1, ASVS 6.1.1, 6.3.1): starting, 10 per device and 20 per address in 10 minutes; a wrong or
expired code on the page, 20 per address; a wrong sign-in, 5 per person and 20 per address, and a step-up with 5
wrong sign-ins is denied. Over a limit the answer is 429 with the seconds to wait; the account is never locked.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import jwt
from sqlalchemy import cast, delete, select, update
from sqlalchemy.dialects.postgresql import INET

from faden_backend.db.dao.agents_dao import AgentsDAO
from faden_backend.db.models.agents import Agent, AgentRefreshToken
from faden_backend.db.models.step_up import BypassStepUp, BypassStepUpFailure, StepUpStatus
from faden_backend.db.models.users import User, sign_in_ended_for_all
from faden_backend.services.agents import tokens
from faden_backend.services.agents.access import decide

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import InstrumentedAttribute
    from sqlalchemy.sql.elements import ColumnElement

#: How long a step-up works (ASVS 6.5.5), and how old the sign-in may be when the gateway takes the proof
#: (owner, 2026-11-09: 5 minutes; the gateway's ``MAX_AGE_S``).
LIFETIME_S = 600
FRESH_S = 300
#: A provider's word on when it authenticated the person may be this far off this server's clock.
SKEW_S = 60
#: Polling (RFC 8628 §3.5): the interval to start with, and what ``slow_down`` adds.
INTERVAL_S = 5
SLOW_DOWN_S = 5
POLL_GRACE_S = 1.0
#: Step-ups are deleted a day after they were made; the grant keeps how and when the person signed in.
KEEP_S = 24 * 3600
#: The window of every limit, and the limits in it.
WINDOW_S = 600
STARTS_PER_DEVICE = 10
STARTS_PER_ADDRESS = 20
FAILURES_PER_USER = 5
FAILURES_PER_ADDRESS = 20
SIGN_INS_PER_STEP_UP = 5

USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
USER_CODE_LENGTH = 8
_USER_CODE_KEY_LABEL = b"fadenstack bypass step-up: user code v1"
#: The proof (the gateway checks the same values).
PROOF_TYPE = "bypass_step_up"
PROOF_AUDIENCE = "faden-gateway:bypass"
PROOF_KEY_LABEL = b"fadenstack bypass step-up proof v1"
#: How the person signed in again.
METHODS = ("password", "oidc", "saml", "ldap")
#: The page that shows a step-up, and the browser's cookie for it.
PAGE = "/step-up"
COOKIE = "faden_step_up"
COOKIE_PATH = "/api/auth/step-up"


class StepUpError(Exception):
    """A request about a step-up that is refused: an HTTP status, a code and words (never a secret)."""

    def __init__(self, status_code: int, code: str, message: str, *, interval: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.interval = interval


# -- codes and keys ------------------------------------------------------------------------


def _derived(secret: str, label: bytes) -> bytes:
    """A key for one purpose: an HMAC of *label* under the backend's secret, never the secret itself."""
    return hmac.new(secret.encode(), label, hashlib.sha256).digest()


def new_user_code() -> str:
    """A fresh code for the link: 8 characters, no dash (``shown`` adds it)."""
    return "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def shown(user_code: str) -> str:
    """The code as people read it: ``XXXX-XXXX``."""
    half = USER_CODE_LENGTH // 2
    return f"{user_code[:half]}-{user_code[half:]}"


def normalise(entered: str) -> str | None:
    """The code from the link as stored: upper case, no dashes or spaces; ``None`` if it cannot be one."""
    code = "".join(entered.split()).replace("-", "").upper()
    if len(code) != USER_CODE_LENGTH or any(ch not in USER_CODE_ALPHABET for ch in code):
        return None
    return code


def user_code_hmac(secret: str, user_code: str) -> str:
    """The HMAC-SHA-256 the link's (normalised) code is stored and found by."""
    return hmac.new(_derived(secret, _USER_CODE_KEY_LABEL), user_code.encode(), hashlib.sha256).hexdigest()


def digest(value: str) -> str:
    """The SHA-256 the app's code and the browser's cookie are stored as (256 random bits each)."""
    return hashlib.sha256(value.encode()).hexdigest()


def address(host: str | None) -> str | None:
    """The request's client address as an IP address; an IPv4-mapped IPv6 address as IPv4."""
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def _network(ip: str) -> str:
    parsed = ipaddress.ip_address(ip)
    return str(ipaddress.ip_network(f"{parsed}/{32 if parsed.version == 4 else 64}", strict=False))


def _in_network(column: InstrumentedAttribute[Any], ip: str | None) -> ColumnElement[bool]:
    if ip is None:
        return column.is_(None)
    return column.op("<<=")(cast(_network(ip), INET))


# -- the app ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Caller:
    """The app asking: its agent token's person, agent and sign-in on the device."""

    user: User
    agent: Agent
    device_id: uuid.UUID
    device_label: str | None


def _refused() -> StepUpError:
    return StepUpError(401, "invalid_token", "The app's sign-in is not valid; sign in to the agent again.")


async def caller(session: AsyncSession, secret: str, authorization: str | None) -> Caller:
    """The app behind *authorization* (``Bearer <agent token>``), checked as the gateway checks it.

    The token must be an agent token for one agent and device (``typ`` ``agent``, ``aud`` ``agent:<id>``,
    ``did``), unexpired; its person active, their sign-ins not ended since, its sign-in on the device not
    revoked, and the agent still theirs to use.
    """
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _refused()
    try:
        claims = jwt.decode(
            token.strip(), secret, algorithms=["HS256"],
            options={"verify_aud": False, "require": ["exp", "sub", "aud", "typ", "did"]},
        )
    except jwt.PyJWTError:
        raise _refused() from None
    aud = claims.get("aud")
    if claims.get("typ") != tokens.TOKEN_TYPE or not isinstance(aud, str) or not aud.startswith("agent:"):
        raise _refused()
    try:
        user_id, agent_id, device_id = uuid.UUID(str(claims["sub"])), uuid.UUID(aud[6:]), uuid.UUID(str(claims["did"]))
    except ValueError:
        raise _refused() from None
    user = await session.get(User, user_id)
    agent = await session.get(Agent, agent_id)
    family = await session.get(AgentRefreshToken, device_id)
    if (
        user is None or not user.is_active or sign_in_ended_for_all(claims, user.sessions_valid_after)
        or agent is None or agent.tenant_id != str(claims.get("tenant_id", agent.tenant_id))
        or family is None or family.revoked_at is not None or family.user_id != user.id or family.agent_id != agent.id
    ):
        raise _refused()
    if not (await decide(AgentsDAO(session), agent, user)).allowed:
        raise StepUpError(403, "agent_not_available", "The agent is not available to you.")
    return Caller(user=user, agent=agent, device_id=device_id, device_label=family.device_label)


async def _wait(
    session: AsyncSession, column: InstrumentedAttribute[datetime], where: list[ColumnElement[bool]], limit: int,
    now: datetime,
) -> int:
    """Seconds until fewer than *limit* rows match *where* within the window; 0 when that is so now."""
    nth = await session.scalar(
        select(column).where(*where, column > now - timedelta(seconds=WINDOW_S))
        .order_by(column.desc()).offset(limit - 1).limit(1),
    )
    if nth is None:
        return 0
    return max(1, math.ceil((nth + timedelta(seconds=WINDOW_S) - now).total_seconds()))


async def start_wait(session: AsyncSession, device_id: uuid.UUID, ip: str | None, now: datetime) -> int:
    """Seconds until this device (and its address) may start another step-up; 0 when it may now."""
    at = BypassStepUp.created_at
    by_device = await _wait(session, at, [BypassStepUp.device_id == device_id], STARTS_PER_DEVICE, now)
    by_address = await _wait(session, at, [_in_network(BypassStepUp.requester_ip, ip)], STARTS_PER_ADDRESS, now)
    return max(by_device, by_address)


@dataclass(frozen=True)
class Started:
    """A new step-up, with the two codes in the clear: handed to the app once and never kept."""

    row: BypassStepUp
    step_up_code: str
    user_code: str


async def start(
    session: AsyncSession, *, secret: str, who: Caller, longest_minutes: int | None, client_version: str | None,
    ip: str | None, now: datetime,
) -> Started:
    """A new step-up for the app *who*; older ones of a day ago go, pending ones past their time expire."""
    await session.execute(delete(BypassStepUp).where(BypassStepUp.created_at < now - timedelta(seconds=KEEP_S)))
    await session.execute(
        update(BypassStepUp)
        .where(
            BypassStepUp.status.in_((StepUpStatus.PENDING, StepUpStatus.SIGNED_IN, StepUpStatus.APPROVED)),
            BypassStepUp.expires_at <= now,
        )
        .values(status=StepUpStatus.EXPIRED),
    )
    for _ in range(5):
        user_code = new_user_code()
        code_hmac = user_code_hmac(secret, user_code)
        taken = await session.scalar(
            select(BypassStepUp.id).where(
                BypassStepUp.user_code_hmac == code_hmac, BypassStepUp.expires_at > now,
            ).limit(1),
        )
        if taken is None:
            break
    else:  # pragma: no cover - 5 clashes in a row among 20^8 codes
        msg = "Could not allocate a step-up code."
        raise RuntimeError(msg)
    step_up_code = secrets.token_urlsafe(32)
    row = BypassStepUp(
        id=uuid.uuid4(), tenant_id=who.agent.tenant_id, user_id=who.user.id, agent_id=who.agent.id,
        device_id=who.device_id, device_label=who.device_label, client_version=client_version,
        longest_minutes=longest_minutes, step_up_code_hash=digest(step_up_code), user_code_hmac=code_hmac,
        status=StepUpStatus.PENDING, requester_ip=ip, created_at=now,
        expires_at=now + timedelta(seconds=LIFETIME_S), interval_s=INTERVAL_S, failed_sign_ins=0,
    )
    session.add(row)
    await session.flush()
    return Started(row=row, step_up_code=step_up_code, user_code=user_code)


@dataclass(frozen=True)
class Proof:
    """What the app gets once approved: the proof (never kept here), and how and when the person signed in."""

    token: str
    expires_in: int
    signed_in_at: datetime
    method: str


def proof(secret: str, row: BypassStepUp) -> str:
    """The proof of *row*'s sign-in, for the gateway: valid :data:`FRESH_S` seconds from the sign-in."""
    assert row.signed_in_at is not None and row.method is not None  # noqa: S101 - only approved ones get here
    signed_in = int(row.signed_in_at.timestamp())
    claims: dict[str, Any] = {
        "typ": PROOF_TYPE, "aud": PROOF_AUDIENCE, "jti": str(row.id), "ten": row.tenant_id, "usr": str(row.user_id),
        "agt": str(row.agent_id), "did": str(row.device_id), "method": row.method, "auth_time": signed_in,
        "iat": int(time.time()), "exp": signed_in + FRESH_S,
    }
    if row.provider:
        claims["prv"] = row.provider
    if row.idp_auth_time is not None:
        claims["idp_auth_time"] = int(row.idp_auth_time.timestamp())
    if row.longest_minutes:
        claims["max_minutes"] = row.longest_minutes
    return jwt.encode(claims, _derived(secret, PROOF_KEY_LABEL), algorithm="HS256")


async def poll(session: AsyncSession, *, secret: str, who: Caller, step_up_code: str, now: datetime) -> Proof:
    """The app asks whether the person approved; :class:`StepUpError` with an RFC 8628 code until then.

    Only the app's own sign-in polls its step-up: another device, person or agent gets ``invalid_grant`` as for
    a code that does not exist (ASVS 5.0 8.2.2).
    """
    row = await session.scalar(
        select(BypassStepUp).where(BypassStepUp.step_up_code_hash == digest(step_up_code)).with_for_update(),
    )
    if row is None or (row.user_id, row.agent_id, row.device_id) != (who.user.id, who.agent.id, who.device_id):
        raise StepUpError(400, "invalid_grant", "The step-up code is not valid.")
    if row.status == StepUpStatus.CONSUMED:
        raise StepUpError(400, "invalid_grant", "The step-up was used already; start a new one.")
    if row.status == StepUpStatus.DENIED:
        raise StepUpError(400, "access_denied", "The sign-in was denied.")
    if row.status == StepUpStatus.EXPIRED or now >= row.expires_at:
        row.status = StepUpStatus.EXPIRED
        raise StepUpError(400, "expired_token", "The step-up took too long; start a new one.")
    since = (now - row.last_polled_at).total_seconds() if row.last_polled_at is not None else None
    row.last_polled_at = now
    if since is not None and since < row.interval_s - POLL_GRACE_S:
        row.interval_s = min(row.interval_s + SLOW_DOWN_S, LIFETIME_S)
        raise StepUpError(
            400, "slow_down", f"Polled too often; wait {row.interval_s} s between polls.", interval=row.interval_s,
        )
    if row.status != StepUpStatus.APPROVED:
        raise StepUpError(400, "authorization_pending", "Waiting for the sign-in in the browser.")
    assert row.signed_in_at is not None and row.method is not None  # noqa: S101 - approve() sets both
    left = int((row.signed_in_at + timedelta(seconds=FRESH_S) - now).total_seconds())
    if left <= 0:
        row.status = StepUpStatus.EXPIRED
        raise StepUpError(400, "expired_token", "The sign-in is older than 5 minutes; start a new one.")
    row.status = StepUpStatus.CONSUMED
    await session.flush()
    return Proof(token=proof(secret, row), expires_in=left, signed_in_at=row.signed_in_at, method=row.method)


# -- the browser ------------------------------------------------------------------------------


async def failure_wait(session: AsyncSession, user_id: uuid.UUID | None, ip: str | None, now: datetime) -> int:
    """Seconds until this address (and the person, once known) may try again; 0 when they may now."""
    at = BypassStepUpFailure.failed_at
    waits = [await _wait(session, at, [_in_network(BypassStepUpFailure.ip, ip)], FAILURES_PER_ADDRESS, now)]
    if user_id is not None:
        waits.append(await _wait(session, at, [BypassStepUpFailure.user_id == user_id], FAILURES_PER_USER, now))
    return max(waits)


async def record_failure(session: AsyncSession, user_id: uuid.UUID | None, ip: str | None, now: datetime) -> None:
    """Count a wrong code or a wrong sign-in against the address (and the person); older counts go."""
    await session.execute(
        delete(BypassStepUpFailure).where(BypassStepUpFailure.failed_at <= now - timedelta(seconds=WINDOW_S)),
    )
    session.add(BypassStepUpFailure(user_id=user_id, ip=ip, failed_at=now))
    await session.flush()


async def find(session: AsyncSession, *, secret: str, entered: str, now: datetime) -> BypassStepUp | None:
    """The waiting step-up the link's code names, locked; ``None`` for a wrong, used or expired code."""
    code = normalise(entered)
    if code is None:
        return None
    row = await session.scalar(
        select(BypassStepUp).where(BypassStepUp.user_code_hmac == user_code_hmac(secret, code))
        .order_by(BypassStepUp.created_at.desc()).limit(1).with_for_update(),
    )
    if row is None or row.status not in (StepUpStatus.PENDING, StepUpStatus.SIGNED_IN):
        return None
    if now >= row.expires_at:
        row.status = StepUpStatus.EXPIRED
        await session.flush()
        return None
    return row


def bind_browser(row: BypassStepUp) -> str:
    """Bind *row* to the browser that opened the link: the cookie's value, kept here as its hash.

    Opened again (another browser, or the same after a reload) before anyone signed in, the newest opening
    holds; once signed in, only that browser may decide (:func:`by_browser`).
    """
    token = secrets.token_urlsafe(32)
    row.browser_hash = digest(token)
    return token


async def by_browser(session: AsyncSession, cookie: str | None, now: datetime) -> BypassStepUp | None:
    """The live step-up this browser opened, locked; ``None`` without one."""
    if not cookie:
        return None
    row = await session.scalar(
        select(BypassStepUp).where(BypassStepUp.browser_hash == digest(cookie)).with_for_update(),
    )
    if row is None or row.status not in (StepUpStatus.PENDING, StepUpStatus.SIGNED_IN) or now >= row.expires_at:
        return None
    return row


def fresh_from_provider(authenticated_at: datetime | None, asked_at: datetime, now: datetime) -> bool:
    """Whether a provider's word on when it authenticated the person shows a fresh sign-in.

    It must say (OIDC ``auth_time``, SAML ``AuthnInstant``), and the time must lie after the fresh sign-in was
    asked for and not in the future, both within :data:`SKEW_S` of this clock (ASVS 5.0 6.8.4).
    """
    if authenticated_at is None or authenticated_at.tzinfo is None:
        return False
    return asked_at - timedelta(seconds=SKEW_S) <= authenticated_at <= now + timedelta(seconds=SKEW_S)


def signed_in(
    row: BypassStepUp, user: User, *, method: str, provider: str | None, idp_auth_time: datetime | None,
    now: datetime,
) -> bool:
    """The person signed in again; ``False`` when it was not the person of the app's sign-in.

    *now* (this server's clock) is the sign-in's time for the proof. A sign-in as someone else counts as a wrong
    one (:func:`wrong_sign_in`); the page cannot tell anyone whose step-up it is.
    """
    if user.id != row.user_id or not user.is_active or method not in METHODS:
        return False
    row.status = StepUpStatus.SIGNED_IN
    row.signed_in_at, row.method, row.provider, row.idp_auth_time = now, method, provider, idp_auth_time
    return True


def wrong_sign_in(row: BypassStepUp, now: datetime) -> bool:
    """Count a wrong sign-in on *row*; at :data:`SIGN_INS_PER_STEP_UP` it is denied (``True``)."""
    row.failed_sign_ins += 1
    if row.failed_sign_ins >= SIGN_INS_PER_STEP_UP:
        row.status, row.decided_at = StepUpStatus.DENIED, now
        return True
    return False


def approve(row: BypassStepUp, now: datetime) -> None:
    """The person approves: the app's next poll gets the proof. Only after a sign-in at most 5 minutes old."""
    if row.status != StepUpStatus.SIGNED_IN or row.signed_in_at is None:
        raise StepUpError(409, "sign_in_first", "Sign in again first.")
    if (now - row.signed_in_at).total_seconds() >= FRESH_S:
        row.status, row.signed_in_at, row.method, row.provider, row.idp_auth_time = (
            StepUpStatus.PENDING, None, None, None, None,
        )
        raise StepUpError(409, "sign_in_too_old", "The sign-in is older than 5 minutes; sign in again.")
    row.status, row.decided_at = StepUpStatus.APPROVED, now


def deny(row: BypassStepUp, now: datetime) -> None:
    """The person denies: the app's next poll answers ``access_denied``."""
    row.status, row.decided_at = StepUpStatus.DENIED, now


def now_utc() -> datetime:
    """This server's clock."""
    return datetime.now(UTC)
