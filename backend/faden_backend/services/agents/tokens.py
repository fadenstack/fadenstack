"""Agent tokens: a short access token for one agent, a refresh token per device.

The access token is a JWT the gateway verifies with the shared secret, like
the API tokens of ``/me/api-token``, but bound to one agent (``aud`` is
``agent:<id>``) and marked ``typ: agent`` so the plain gateway routes refuse
it. It lives minutes; removing someone's access takes effect at their next
refresh at the latest.

The refresh token is 256 random bits, stored only as its SHA-256. Each
refresh replaces it; presenting a replaced one again means it was copied, and
ends that sign-in on every device holding it (the whole family).
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from faden_backend.db.dao.agents_dao import AgentsDAO
from faden_backend.db.models.agents import Agent, AgentRefreshToken
from faden_backend.db.models.users import User
from faden_backend.services.agents.access import decide
from faden_backend.services.extensions import Lifetimes, registry

TOKEN_TYPE = "agent"  # noqa: S105 - the "typ" claim, not a secret


class AgentTokenError(Exception):
    """A token request that cannot be granted, with an HTTP status and a code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IssuedTokens:
    """What a sign-in or a refresh hands out."""

    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    device_id: uuid.UUID
    agent: Agent
    #: Whom the tokens are for.
    user: User


def audience(agent_id: uuid.UUID) -> str:
    """The ``aud`` of an access token for *agent_id*."""
    return f"agent:{agent_id}"


def hash_token(token: str) -> str:
    """The SHA-256 a refresh token is stored as."""
    return hashlib.sha256(token.encode()).hexdigest()


def developer_mode(user: User, now: datetime) -> bool:
    """Whether the user's developer mode is on right now."""
    until = user.developer_mode_until
    return bool(user.developer_mode) and (until is None or until > now)


def _access_token(*, secret: str, user: User, agent: Agent, device_id: uuid.UUID, ttl: int) -> str:
    moment = time.time()
    now = int(moment)
    claims = {
        "sub": str(user.id),
        "tenant_id": agent.tenant_id,
        "aud": audience(agent.id),
        "typ": TOKEN_TYPE,
        "agent": agent.slug,
        "did": str(device_id),
        "dev": developer_mode(user, datetime.now(UTC)),
        "email": user.email,
        "jti": uuid.uuid4().hex,
        "iat": now,
        # To the millisecond, as console sign-ins: ends with the person's sign-ins made a moment later.
        "iat_ms": int(moment * 1000),
        "exp": now + ttl,
    }
    return jwt.encode(claims, secret, algorithm="HS256")


async def lifetimes(dao: AgentsDAO, agent: Agent) -> Lifetimes:
    """How long *agent*'s tokens live, after its published version's settings."""
    version = await dao.version_by_id(agent.published_version_id)
    return registry.token_lifetimes.lifetimes(version.extensions if version else {})


async def issue(
    dao: AgentsDAO,
    *,
    secret: str,
    user: User,
    agent: Agent,
    device_label: str | None,
) -> IssuedTokens:
    """A new sign-in of *user* to *agent* on one device."""
    decision = await decide(dao, agent, user)
    if not decision.allowed:
        raise AgentTokenError(403, decision.code, decision.detail)
    life = await lifetimes(dao, agent)
    now = datetime.now(UTC)
    refresh = secrets.token_urlsafe(32)
    family = uuid.uuid4()
    await dao.add_refresh_token(
        AgentRefreshToken(
            id=family,
            family_id=family,
            agent_id=agent.id,
            user_id=user.id,
            tenant_id=agent.tenant_id,
            token_hash=hash_token(refresh),
            device_label=device_label,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(seconds=life.refresh_s),
            idle_expires_at=now + timedelta(seconds=life.idle_s),
        ),
    )
    return IssuedTokens(
        access_token=_access_token(
            secret=secret, user=user, agent=agent, device_id=family, ttl=life.access_s,
        ),
        expires_in=life.access_s,
        refresh_token=refresh,
        refresh_expires_in=life.refresh_s,
        device_id=family,
        agent=agent,
        user=user,
    )


async def refresh(dao: AgentsDAO, *, secret: str, refresh_token: str) -> IssuedTokens:
    """New tokens for a refresh token, which is replaced.

    Access is checked again: a user removed from the agent's teams gets no
    new access token.
    """
    now = datetime.now(UTC)
    row = await dao.refresh_token_by_hash(hash_token(refresh_token))
    if row is None or row.revoked_at is not None:
        raise AgentTokenError(401, "invalid_refresh_token", "The refresh token is not valid.")
    if row.replaced_by is not None:
        # Used before: whoever holds a copy loses this sign-in too.
        await dao.revoke_family(row.family_id, now)
        raise AgentTokenError(
            401, "refresh_token_reused", "The refresh token was already used; sign in again.",
        )
    if row.expires_at <= now or row.idle_expires_at <= now:
        raise AgentTokenError(401, "refresh_token_expired", "The sign-in has expired; sign in again.")

    user = await dao.session.get(User, row.user_id)
    agent = await dao.session.get(Agent, row.agent_id)
    if user is None or agent is None:
        await dao.revoke_family(row.family_id, now)
        raise AgentTokenError(401, "invalid_refresh_token", "The refresh token is not valid.")
    first = await dao.session.get(AgentRefreshToken, row.family_id)
    started = first.created_at if first is not None else row.created_at
    if user.sessions_valid_after is not None and started < user.sessions_valid_after:
        # The person's sign-ins were ended since (a password change, a suspension, a change of rights): this
        # one too (ASVS 5.0 V7.4.1).
        await dao.revoke_family(row.family_id, now)
        raise AgentTokenError(401, "sign_in_ended", "This sign-in has ended; sign in again.")
    decision = await decide(dao, agent, user)
    if not decision.allowed:
        await dao.revoke_family(row.family_id, now)
        raise AgentTokenError(403, decision.code, decision.detail)

    life = await lifetimes(dao, agent)
    # The sign-in ends where it always would; only idleness is renewed. A
    # lifetime lowered since takes effect now: the end moves closer.
    expires_at = min(row.expires_at, started + timedelta(seconds=life.refresh_s))
    new_refresh = secrets.token_urlsafe(32)
    replacement = await dao.add_refresh_token(
        AgentRefreshToken(
            family_id=row.family_id,
            agent_id=row.agent_id,
            user_id=row.user_id,
            tenant_id=row.tenant_id,
            token_hash=hash_token(new_refresh),
            device_label=row.device_label,
            created_at=now,
            last_used_at=now,
            expires_at=expires_at,
            idle_expires_at=now + timedelta(seconds=life.idle_s),
        ),
    )
    row.replaced_by = replacement.id
    row.last_used_at = now
    await dao.session.flush()
    return IssuedTokens(
        access_token=_access_token(
            secret=secret, user=user, agent=agent, device_id=row.family_id, ttl=life.access_s,
        ),
        expires_in=life.access_s,
        refresh_token=new_refresh,
        refresh_expires_in=max(0, int((replacement.expires_at - now).total_seconds())),
        device_id=row.family_id,
        agent=agent,
        user=user,
    )
