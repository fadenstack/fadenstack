from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from faden_gateway.services.gateway.agents import AgentLookupError
from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.jwt_secret import load_jwt_secret_from_backend_db
from faden_gateway.settings import settings


@dataclass(slots=True, frozen=True)
class AuthContext:
    """Authenticated request context extracted from JWT."""

    user_id: str
    tenant_id: str
    raw_claims: dict[str, Any]
    #: The bearer token the caller sent, for calls made on their behalf.
    token: str | None = None
    #: An agent's knowledge collections: its knowledge searches stay within them
    #: (``None``: whatever the person may read).
    knowledge: tuple[str, ...] | None = None


# auto_error=False so we can return our own structured error response
# instead of FastAPI's default 403.
_bearer_scheme = HTTPBearer(auto_error=False)
_JWT_SECRET_REFRESH_LOCK = asyncio.Lock()
_JWT_SECRET_REFRESH_COOLDOWN_SEC = 15.0
_last_jwt_secret_refresh_attempt_monotonic = 0.0


async def _refresh_jwt_secret_if_needed() -> None:
    """Best-effort lazy secret refresh to recover from startup races."""
    global _last_jwt_secret_refresh_attempt_monotonic
    if settings.jwt_secret:
        return

    now = time.monotonic()
    if now - _last_jwt_secret_refresh_attempt_monotonic < _JWT_SECRET_REFRESH_COOLDOWN_SEC:
        return

    async with _JWT_SECRET_REFRESH_LOCK:
        if settings.jwt_secret:
            return
        now = time.monotonic()
        if now - _last_jwt_secret_refresh_attempt_monotonic < _JWT_SECRET_REFRESH_COOLDOWN_SEC:
            return
        _last_jwt_secret_refresh_attempt_monotonic = now
        await load_jwt_secret_from_backend_db()


#: The ``typ`` claim of agent tokens: bound to one agent (``aud`` is
#: ``agent:<id>``), accepted on that agent's routes only.
AGENT_TOKEN_TYPE = "agent"  # noqa: S105 - a claim value, not a secret


#: The ``typ`` claim of API tokens. Each has a row in the backend (its ``jti``
#: is the row's id), and revoking the row ends the token.
API_TOKEN_TYPE = "api"  # noqa: S105 - a claim value, not a secret


#: The header the backend sends a caller's right to weaken a chat's PII policy in.
PII_WEAKEN_HEADER = "X-PII-Allow-Weaken"
#: The ``typ`` of the grant it carries: a short JWT the backend signs with the shared
#: secret after its own check (``pii.session:weaken``, or a superuser).
PII_WEAKEN_TOKEN_TYPE = "pii_weaken"  # noqa: S105 - a claim value, not a secret


def pii_weaken_granted(value: str | None, auth: AuthContext, session_id: str = "") -> bool:
    """Whether *value* (the ``X-PII-Allow-Weaken`` header) is the backend's grant for this caller and chat.

    Anything else -- the old ``true``, the caller's own token, a grant for
    another user or chat, an expired one -- is no right: a caller who reaches
    the gateway directly cannot claim it. *session_id* is ``""`` before a chat
    exists (``/v1/pii-defaults``).
    """
    token = (value or "").strip()
    if not token or not settings.jwt_secret:
        return False
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False, "require": ["exp", "sub", "typ"]},
        )
    except jwt.PyJWTError:
        return False
    return (
        claims.get("typ") == PII_WEAKEN_TOKEN_TYPE
        and str(claims.get("sub")) == auth.user_id
        and str(claims.get("sid", "")) == session_id
    )


def verify_token(token: str, *, check_audience: bool = True) -> dict[str, Any]:
    """Verify JWT token and return claims.

    Without *check_audience* the caller checks ``aud`` itself: the agent
    routes accept agent tokens, whose audience is their agent.
    """
    if not settings.jwt_secret:
        raise GatewayError(
            status_code=500,
            message="JWT secret is not configured.",
            error_type="server_error",
            code="jwt_not_configured",
        )
    try:
        decode_opts: dict[str, Any] = {
            "algorithms": [settings.jwt_algorithm],
        }
        if settings.jwt_audience and check_audience:
            decode_opts["audience"] = settings.jwt_audience
        else:
            # When no audience is configured, skip audience verification so
            # tokens with an "aud" claim (e.g. fastapi-users) are accepted.
            decode_opts["options"] = {"verify_aud": False}
        if settings.jwt_issuer:
            decode_opts["issuer"] = settings.jwt_issuer
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            **decode_opts,
        )
    except jwt.PyJWTError as exc:
        raise GatewayError(
            status_code=401,
            message="Invalid JWT token.",
            code="invalid_token",
        ) from exc
    if claims.get("typ") == PII_WEAKEN_TOKEN_TYPE:
        # A weaken grant is a claim about one chat, never a sign-in: without a
        # configured audience nothing else would tell it from a user's token.
        raise GatewayError(
            status_code=401,
            message="Invalid JWT token.",
            code="invalid_token",
        )
    return claims


def get_auth_context_from_claims(claims: dict[str, Any], token: str | None = None) -> AuthContext:
    """Extract user and tenant identifiers from verified claims."""
    user_id = str(claims.get("sub", "")).strip()
    if not user_id:
        raise GatewayError(
            status_code=401,
            message="JWT token does not contain subject (sub).",
            code="invalid_token_sub",
        )
    tenant_id = str(claims.get("tenant_id", "")).strip()
    if not tenant_id:
        # Default to "default" tenant for single-tenant deployments and
        # fastapi-users tokens that don't carry a tenant_id claim.
        tenant_id = "default"
    return AuthContext(user_id=user_id, tenant_id=tenant_id, raw_claims=claims, token=token)


#: The audience of console sign-ins (fastapi-users).
SIGN_IN_AUDIENCE = "fastapi-users:auth"


def _is_outdated_sign_in(claims: dict[str, Any]) -> bool:
    """A console sign-in from before sign-ins expired: no ``iat``; the CLI's bootstrap token has one."""
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    return SIGN_IN_AUDIENCE in audiences and "iat" not in claims


def _is_unrecorded_api_token(claims: dict[str, Any]) -> bool:
    """An API token from before API tokens had rows: a ``jti`` but no ``typ`` and no ``aud``."""
    return claims.get("typ") is None and bool(claims.get("jti")) and "aud" not in claims


async def check_caller(app: Any, claims: dict[str, Any]) -> str | None:
    """Refuses a revoked or unrecorded API token, and a user who was deactivated or deleted.

    Returns an API token's tenant as its row names it, ``None`` for other tokens. Agent tokens
    are checked on their own routes. Without the backend database (development, tests) only the
    claims are checked.
    """
    if _is_outdated_sign_in(claims):
        raise GatewayError(
            status_code=401,
            message="This sign-in is from before sign-ins expired. Sign in again.",
            code="sign_in_outdated",
        )
    if _is_unrecorded_api_token(claims):
        raise GatewayError(
            status_code=401,
            message=(
                "This API token was made before API tokens could be revoked. Create a new one in your profile."
            ),
            code="api_token_outdated",
        )
    directory = getattr(app.state, "agent_directory", None)
    if directory is None:
        return None
    user_id = str(claims.get("sub", "")).strip()
    tenant_id: str | None = None
    try:
        if claims.get("typ") == API_TOKEN_TYPE and directory.checks_api_tokens:
            record = await directory.api_token(str(claims.get("jti", "")))
            if record is None or record.revoked or record.user_id != user_id:
                raise GatewayError(
                    status_code=401,
                    message="This API token was revoked.",
                    code="api_token_revoked",
                )
            if record.expired:
                raise GatewayError(
                    status_code=401,
                    message="This API token has expired. Create a new one in your profile.",
                    code="api_token_expired",
                )
            tenant_id = record.tenant_id
        user = await directory.user(user_id)
    except AgentLookupError as exc:
        raise GatewayError(
            status_code=503,
            message="Your access could not be checked; try again.",
            error_type="server_error",
            code="access_check_failed",
        ) from exc
    if user is None or not user.is_active:
        raise GatewayError(
            status_code=403,
            message="The user is deactivated or unknown.",
            error_type="permission_error",
            code="user_inactive",
        )
    return tenant_id


async def caller_context(app: Any, claims: dict[str, Any], token: str | None = None) -> AuthContext:
    """Who the caller is, once :func:`check_caller` lets them in.

    An API token's tenant is the one its row names; its claim is only what the token was made with.
    """
    auth = get_auth_context_from_claims(claims, token)
    tenant_id = await check_caller(app, claims)
    return replace(auth, tenant_id=tenant_id) if tenant_id is not None else auth


async def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthContext:
    """FastAPI dependency that validates JWT bearer token.

    Using HTTPBearer ensures Swagger UI shows the Authorize button and
    correctly includes the token in the Authorization: Bearer <token> header.
    """
    token = credentials.credentials if credentials else None
    if not token:
        raise GatewayError(
            status_code=401,
            message="Missing Authorization header.",
            code="missing_authorization",
        )
    await _refresh_jwt_secret_if_needed()
    claims = verify_token(token)
    if claims.get("typ") == AGENT_TOKEN_TYPE:
        # An agent token carries the agent's rules only on the agent's routes;
        # anywhere else it would be a way around them.
        raise GatewayError(
            status_code=403,
            message="Agent tokens are accepted only on their agent's routes (/v1/agents/{agent}/...).",
            error_type="permission_error",
            code="agent_token_not_allowed",
        )
    return await caller_context(request.app, claims, token)


async def get_agent_route_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> tuple[dict[str, Any], str]:
    """Verified claims and token for an agent route; the route checks ``aud``.

    Agent tokens name their agent in ``aud``; any other token keeps the
    configured audience check.
    """
    token = credentials.credentials if credentials else None
    if not token:
        raise GatewayError(
            status_code=401,
            message="Missing Authorization header.",
            code="missing_authorization",
        )
    await _refresh_jwt_secret_if_needed()
    claims = verify_token(token, check_audience=False)
    if claims.get("typ") != AGENT_TOKEN_TYPE and settings.jwt_audience:
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if settings.jwt_audience not in audiences:
            raise GatewayError(status_code=401, message="Invalid JWT token.", code="invalid_token")
    return claims, token
