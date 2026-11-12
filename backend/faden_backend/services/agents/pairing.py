"""Signing an app in to an agent by pairing: the OAuth 2.0 Device Authorization Grant (RFC 8628), reshaped.

1. The app starts a pairing and shows the user code (``POST /api/agent-tokens/pairings``).
2. A person signed in to the console looks the code up, compares it with the app, and approves or denies it.
   They sign in there however the server lets them (password, SSO, MFA); the app never sees their credentials.
3. The app polls with its pairing code. Once approved it receives the agent tokens a sign-in issues
   (:func:`faden_backend.services.agents.tokens.issue`), for the approving person, once.

Codes (OWASP ASVS 5.0 V6.5):

- The pairing code is 32 bytes from the CSPRNG (6.5.3) and is the app's alone. It is kept as its SHA-256: at
  256 bits a plain hash is enough (6.5.2).
- The user code is 8 characters from ``BCDFGHJKLMNPQRSTVWXZ`` (RFC 8628 §6.1: no vowels, no digits, so no
  ``0/O`` or ``1/I`` mix-ups), shown as ``XXXX-XXXX``: 34.5 bits, above 6.5.4's 20. It is kept as an
  HMAC-SHA-256 under a key derived from the backend's secret, the one the agent tokens are signed with: 34
  bits under a plain hash fall to a search in seconds, so a copy of the database would give away the live
  codes; without the secret it does not. The key is derived (HMAC of a fixed label), not the secret itself,
  so a value made for one purpose is never valid for the other (the secret also signs JWTs).
- Both work for 10 minutes (6.5.5, 10.4.3) and once (6.5.1): approved, then tokens once, then consumed.

One pairing may name up to 8 agents (``agent_pairing_agent``, first = ``agent_pairings.agent_ref``). One approval
covers every listed agent the person may use (10.7.1, 10.7.2); an agent they may not use is refused for this
pairing and never issued. The app collects each approved agent's tokens once, by naming it; the pairing is
consumed when all are collected, or ``REDEEM_WINDOW_S`` after the approval. A pairing made before the table
existed has no rows and works as one agent, as before.
- They are bound to their pairing (6.6.2): the user code approves only the pairing it was made for, and only
  that pairing's code collects the tokens -- a sign-in of its own, made at that poll and never kept.
- Neither code is ever logged or written to the audit trail; the pairing's id is (V16).

Polling (RFC 8628 §3.5): at most once per ``interval_s`` (5 s); a poll that comes too early answers
``slow_down`` and adds 5 s. After the expiry it answers ``expired_token``, after a denial ``access_denied``.

Limits (RFC 8628 §5.1, ASVS 6.1.1, 6.6.3). They are counted in the database, so every backend worker counts
the same (the provenance check's limiter counts per process, which with four workers would allow four times
as many tries):

- starting a pairing: 10 per address in 10 minutes (the pairings themselves are counted);
- a wrong or expired user code in the console (lookup, approve, deny): 5 per person and 20 per address in 10
  minutes (``agent_pairing_failures``). Over a limit the answer is 429 with the seconds to wait. The person's
  account is never locked: only these pairing routes wait.

An address is the one uvicorn gives: behind nginx, the client's, as for the audit trail and every other limit
(nginx sets ``X-Forwarded-For`` to the address it sees, and the backend trusts it from the proxy). IPv6
addresses count by their /64, IPv4 addresses one by one.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import cast, delete, select, update
from sqlalchemy.dialects.postgresql import INET

from faden_backend.db.models.agents import (
    Agent,
    AgentPairing,
    AgentPairingAgent,
    AgentPairingFailure,
    AgentPairingStatus,
)
from faden_backend.db.models.users import User
from faden_backend.services.agents import tokens
from faden_backend.services.agents.access import AccessDecision, decide

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import InstrumentedAttribute
    from sqlalchemy.sql.elements import ColumnElement

    from faden_backend.db.dao.agents_dao import AgentsDAO

#: How long a pairing works (ASVS 6.5.5).
LIFETIME_S = 600
#: The polling interval to start with, and what ``slow_down`` adds (RFC 8628 §3.5).
INTERVAL_S = 5
SLOW_DOWN_S = 5
#: A poll this much early still counts as on time: timers and networks are not exact.
POLL_GRACE_S = 1.0
#: How long after the approval the app may collect the tokens of the agents approved.
REDEEM_WINDOW_S = 600
#: The most agents one pairing names.
MAX_AGENTS = 8
#: Pairings are deleted a day after they were made.
KEEP_S = 24 * 3600
#: The window of every limit, and the limits in it.
WINDOW_S = 600
STARTS_PER_ADDRESS = 10
FAILURES_PER_USER = 5
FAILURES_PER_ADDRESS = 20

USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
USER_CODE_LENGTH = 8
#: Derives the user-code key from the backend's secret.
_USER_CODE_KEY_LABEL = b"fadenstack agent pairing: user code v1"


class PairingError(Exception):
    """A poll that yields no tokens (yet), with its RFC 8628 §3.5 error code."""

    def __init__(self, code: str, description: str, *, interval: int | None = None) -> None:
        super().__init__(description)
        self.code = code
        self.description = description
        self.interval = interval


# -- codes ------------------------------------------------------------------------


def new_user_code() -> str:
    """A fresh user code, as stored: 8 characters, no dash."""
    return "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def shown(user_code: str) -> str:
    """The user code as people read it: ``XXXX-XXXX``."""
    half = USER_CODE_LENGTH // 2
    return f"{user_code[:half]}-{user_code[half:]}"


def normalise(entered: str) -> str | None:
    """The typed user code as stored: upper case, no dashes or spaces; ``None`` if it cannot be one."""
    code = "".join(entered.split()).replace("-", "").upper()
    if len(code) != USER_CODE_LENGTH or any(ch not in USER_CODE_ALPHABET for ch in code):
        return None
    return code


def user_code_hmac(secret: str, user_code: str) -> str:
    """The HMAC-SHA-256 a (normalised) user code is stored and found by."""
    key = hmac.new(secret.encode(), _USER_CODE_KEY_LABEL, hashlib.sha256).digest()
    return hmac.new(key, user_code.encode(), hashlib.sha256).hexdigest()


def pairing_code_hash(pairing_code: str) -> str:
    """The SHA-256 a pairing code is stored and found by (as a refresh token)."""
    return tokens.hash_token(pairing_code)


# -- addresses ----------------------------------------------------------------------


def address(host: str | None) -> str | None:
    """*host* (the request's client address) as an IP address; an IPv4-mapped IPv6 address as IPv4."""
    try:
        ip = ipaddress.ip_address(host or "")
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def network(ip: str) -> str:
    """The network an address counts as: the address itself for IPv4, its /64 for IPv6."""
    parsed = ipaddress.ip_address(ip)
    prefix = 32 if parsed.version == 4 else 64
    return str(ipaddress.ip_network(f"{parsed}/{prefix}", strict=False))


def same_network(one: Any, other: Any) -> bool:
    """Whether two addresses are in the same network (:func:`network`); unknown ones are not.

    The addresses may be strings or ``ipaddress`` objects (an ``INET`` column reads back as one).
    """
    first = address(None if one is None else str(one))
    second = address(None if other is None else str(other))
    if first is None or second is None:
        return False
    return network(first) == network(second)


def _in_network(column: InstrumentedAttribute[Any], ip: str | None) -> ColumnElement[bool]:
    if ip is None:
        return column.is_(None)
    return column.op("<<=")(cast(network(ip), INET))


# -- limits -------------------------------------------------------------------------


async def _wait(
    session: AsyncSession,
    column: InstrumentedAttribute[datetime],
    where: list[ColumnElement[bool]],
    limit: int,
    now: datetime,
) -> int:
    """Seconds until fewer than *limit* rows match *where* within the window; 0 when that is so now."""
    since = now - timedelta(seconds=WINDOW_S)
    nth = await session.scalar(
        select(column).where(*where, column > since).order_by(column.desc()).offset(limit - 1).limit(1),
    )
    if nth is None:
        return 0
    return max(1, math.ceil((nth + timedelta(seconds=WINDOW_S) - now).total_seconds()))


async def start_wait(session: AsyncSession, ip: str | None, now: datetime) -> int:
    """Seconds until *ip* may start another pairing; 0 when it may now."""
    same_address = _in_network(AgentPairing.requester_ip, ip)
    return await _wait(session, AgentPairing.created_at, [same_address], STARTS_PER_ADDRESS, now)


async def failure_wait(session: AsyncSession, user_id: uuid.UUID, ip: str | None, now: datetime) -> int:
    """Seconds until the person (and their address) may try a code again; 0 when they may now."""
    at = AgentPairingFailure.failed_at
    by_user = await _wait(session, at, [AgentPairingFailure.user_id == user_id], FAILURES_PER_USER, now)
    same_address = _in_network(AgentPairingFailure.ip, ip)
    by_address = await _wait(session, at, [same_address], FAILURES_PER_ADDRESS, now)
    return max(by_user, by_address)


async def record_failure(session: AsyncSession, user_id: uuid.UUID, ip: str | None, now: datetime) -> None:
    """Count a wrong or expired code against the person and the address; older counts go."""
    await session.execute(
        delete(AgentPairingFailure).where(AgentPairingFailure.failed_at <= now - timedelta(seconds=WINDOW_S)),
    )
    session.add(AgentPairingFailure(user_id=user_id, ip=ip, failed_at=now))
    await session.flush()


# -- the flow -----------------------------------------------------------------------


@dataclass(frozen=True)
class Started:
    """A new pairing, with the two codes in the clear: they are handed to the app once and never kept."""

    pairing: AgentPairing
    pairing_code: str
    user_code: str


async def start(
    session: AsyncSession,
    *,
    secret: str,
    tenant_id: str,
    agent_refs: list[str],
    device_label: str,
    client_name: str | None,
    client_version: str | None,
    client_os: str | None,
    ip: str | None,
    now: datetime,
) -> Started:
    """A new pairing for the agents the app named; whether they exist is not looked at here.

    *agent_refs* is the app's list, at most :data:`MAX_AGENTS`, without repeats; the first is the pairing's agent.
    """
    if not 1 <= len(agent_refs) <= MAX_AGENTS or len(set(agent_refs)) != len(agent_refs):
        raise ValueError("a pairing names 1 to 8 different agents")
    kept_since = now - timedelta(seconds=KEEP_S)
    await session.execute(delete(AgentPairing).where(AgentPairing.created_at < kept_since))
    await session.execute(
        update(AgentPairing)
        .where(AgentPairing.status == AgentPairingStatus.PENDING, AgentPairing.expires_at <= now)
        .values(status=AgentPairingStatus.EXPIRED),
    )
    # 20^8 codes: a clash with a live one is next to impossible; the partial unique index guards the rest.
    for _ in range(5):
        user_code = new_user_code()
        digest = user_code_hmac(secret, user_code)
        taken = await session.scalar(
            select(AgentPairing.id)
            .where(AgentPairing.user_code_hmac == digest, AgentPairing.status == AgentPairingStatus.PENDING)
            .limit(1),
        )
        if taken is None:
            break
    else:  # pragma: no cover - 5 clashes in a row
        raise RuntimeError("Could not allocate a pairing code.")
    pairing_code = secrets.token_urlsafe(32)
    row = AgentPairing(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_ref=agent_refs[0],
        pairing_code_hash=pairing_code_hash(pairing_code),
        user_code_hmac=digest,
        device_label=device_label,
        client_name=client_name,
        client_version=client_version,
        client_os=client_os,
        requester_ip=ip,
        status=AgentPairingStatus.PENDING,
        created_at=now,
        expires_at=now + timedelta(seconds=LIFETIME_S),
        interval_s=INTERVAL_S,
        failed_polls=0,
    )
    session.add(row)
    await session.flush()
    session.add_all(
        AgentPairingAgent(pairing_id=row.id, position=place, agent_ref=ref) for place, ref in enumerate(agent_refs)
    )
    await session.flush()
    return Started(pairing=row, pairing_code=pairing_code, user_code=user_code)


async def find(session: AsyncSession, *, secret: str, entered: str, now: datetime) -> AgentPairing | None:
    """The pairing a typed user code names, locked; ``None`` for a wrong or expired code."""
    code = normalise(entered)
    if code is None:
        return None
    row = await session.scalar(
        select(AgentPairing)
        .where(AgentPairing.user_code_hmac == user_code_hmac(secret, code))
        .order_by(AgentPairing.created_at.desc())
        .limit(1)
        .with_for_update(),
    )
    if row is None:
        return None
    if now >= row.expires_at:
        if row.status == AgentPairingStatus.PENDING:
            row.status = AgentPairingStatus.EXPIRED
            await session.flush()
            return None
        # An approved pairing lives on by its own window (the poll decides); anything else is over.
        if row.status != AgentPairingStatus.APPROVED:
            return None
    return row


async def members(session: AsyncSession, row: AgentPairing) -> list[AgentPairingAgent]:
    """The agents the pairing names, in the app's order; none for a pairing made before the table existed."""
    found = await session.scalars(
        select(AgentPairingAgent).where(AgentPairingAgent.pairing_id == row.id).order_by(AgentPairingAgent.position),
    )
    return list(found)


@dataclass(frozen=True)
class Resolved:
    """One named agent, and whether the person may use it."""

    ref: str
    agent: Agent | None
    decision: AccessDecision

    @property
    def available(self) -> bool:
        return self.agent is not None and self.decision.allowed


async def resolve_ref(dao: AgentsDAO, tenant_id: str, ref: str, user: User) -> Resolved:
    """The agent *ref* names, and whether *user* may use it (``access.decide``)."""
    agent = await dao.get(tenant_id, ref)
    if agent is None:
        return Resolved(ref, None, AccessDecision(False, "unknown_agent", "There is no such agent."))
    return Resolved(ref, agent, await decide(dao, agent, user))


async def resolve_all(
    dao: AgentsDAO, row: AgentPairing, user: User, named: list[AgentPairingAgent],
) -> list[Resolved]:
    """Every agent the pairing names (just its own when it has no rows), in order."""
    refs = [m.agent_ref for m in named] or [row.agent_ref]
    return [await resolve_ref(dao, row.tenant_id, ref, user) for ref in refs]


async def approve(
    session: AsyncSession,
    row: AgentPairing,
    user: User,
    now: datetime,
    resolved: list[Resolved],
    named: list[AgentPairingAgent],
) -> list[Resolved]:
    """*user* approves the pairing for every agent they may use: the app gets their tokens for those, once each.

    Returns the agents approved, which the caller has seen to be at least one. The others are refused for this
    pairing (``approved`` false): never issued, whatever the app asks.
    """
    approved = [r for r in resolved if r.available]
    for member, one in zip(named, resolved, strict=False):  # no rows: a pairing from before the table
        member.approved = one.available
        member.agent_id = one.agent.id if one.available and one.agent is not None else None
    first = approved[0].agent
    row.status = AgentPairingStatus.APPROVED
    row.agent_id = first.id if first is not None else None
    row.approved_by = user.id
    row.decided_at = now
    await session.flush()
    return approved


async def deny(session: AsyncSession, row: AgentPairing, user: User, now: datetime) -> None:
    """*user* denies the pairing: the app's next poll answers ``access_denied``."""
    row.status = AgentPairingStatus.DENIED
    row.approved_by = user.id
    row.decided_at = now
    await session.flush()


@dataclass(frozen=True)
class Paired:
    """What a successful poll hands out: the tokens, and the pairing they came from."""

    issued: tokens.IssuedTokens
    pairing: AgentPairing


async def poll(
    dao: AgentsDAO, *, secret: str, pairing_code: str, now: datetime, agent: str | None = None,
) -> Paired:
    """The app asks whether its pairing was approved; :class:`PairingError` until it gets the tokens.

    *agent* names the agent to collect (the first when omitted); each approved agent is collected once.
    """
    session = dao.session
    digest = pairing_code_hash(pairing_code)
    row = await session.scalar(
        select(AgentPairing).where(AgentPairing.pairing_code_hash == digest).with_for_update(),
    )
    if row is None:
        raise PairingError("invalid_grant", "The pairing code is not valid.")
    if row.status == AgentPairingStatus.CONSUMED:
        raise PairingError("invalid_grant", "The pairing was used already; start a new one.")
    if row.status == AgentPairingStatus.DENIED:
        raise PairingError("access_denied", "The sign-in was denied.")
    if row.status == AgentPairingStatus.EXPIRED:
        raise PairingError("expired_token", "The code expired; start a new pairing.")
    named = await members(session, row)
    if named and row.status == AgentPairingStatus.APPROVED and row.decided_at is not None:
        # After the approval the code's own expiry no longer counts: the approved agents may be collected for
        # REDEEM_WINDOW_S, then the pairing is over.
        if now >= row.decided_at + timedelta(seconds=REDEEM_WINDOW_S):
            row.status = AgentPairingStatus.CONSUMED
            raise PairingError("expired_token", "The approval is too old; start a new pairing.")
    elif now >= row.expires_at:
        row.status = AgentPairingStatus.EXPIRED
        raise PairingError("expired_token", "The code expired; start a new pairing.")
    # Collecting a further agent after the first is no polling: the interval counts while the app waits.
    collecting = row.status == AgentPairingStatus.APPROVED and any(m.redeemed_at is not None for m in named)
    since_last = (now - row.last_polled_at).total_seconds() if row.last_polled_at is not None else None
    if not collecting and since_last is not None and since_last < row.interval_s - POLL_GRACE_S:
        row.interval_s = min(row.interval_s + SLOW_DOWN_S, LIFETIME_S)
        row.failed_polls += 1
        row.last_polled_at = now
        raise PairingError(
            "slow_down", f"Polled too often; wait {row.interval_s} s between polls.", interval=row.interval_s,
        )
    row.last_polled_at = now
    if row.status == AgentPairingStatus.PENDING:
        raise PairingError("authorization_pending", "Waiting for the approval in the console.")

    user = await session.get(User, row.approved_by) if row.approved_by else None
    if not named:
        # Made before several agents were possible: one agent, as ever.
        chosen = await session.get(Agent, row.agent_id) if row.agent_id else None
        if user is None or chosen is None:
            row.status = AgentPairingStatus.DENIED
            raise PairingError("access_denied", "The sign-in was denied.")
        try:
            # decide() again: access lost since the approval gives no tokens.
            issued = await tokens.issue(dao, secret=secret, user=user, agent=chosen, device_label=row.device_label)
        except tokens.AgentTokenError as exc:
            row.status = AgentPairingStatus.DENIED
            raise PairingError("access_denied", "The agent is not available to the person who approved.") from exc
        row.status = AgentPairingStatus.CONSUMED
        await session.flush()
        return Paired(issued=issued, pairing=row)

    wanted = agent if agent is not None else named[0].agent_ref
    member = next((m for m in named if m.agent_ref == wanted), None)
    if member is None:
        raise PairingError("invalid_grant", "The pairing does not name this agent.")
    if member.approved is not True:
        raise PairingError("invalid_grant", "The sign-in was not approved for this agent.")
    if member.redeemed_at is not None:
        raise PairingError("invalid_grant", "The tokens of this agent were collected already.")
    chosen = await session.get(Agent, member.agent_id) if member.agent_id else None
    if user is None or chosen is None:
        member.approved = False
        _settle(row, named)
        raise PairingError("access_denied", "The sign-in was denied.")
    try:
        issued = await tokens.issue(dao, secret=secret, user=user, agent=chosen, device_label=row.device_label)
    except tokens.AgentTokenError as exc:
        member.approved = False
        _settle(row, named)
        raise PairingError("access_denied", "The agent is not available to the person who approved.") from exc
    member.redeemed_at = now
    _settle(row, named)
    await session.flush()
    return Paired(issued=issued, pairing=row)


def _settle(row: AgentPairing, named: list[AgentPairingAgent]) -> None:
    """Consumed once no approved agent is left to collect; denied if none was ever collected."""
    if any(m.approved is True and m.redeemed_at is None for m in named):
        return
    row.status = (
        AgentPairingStatus.CONSUMED if any(m.redeemed_at is not None for m in named) else AgentPairingStatus.DENIED
    )
