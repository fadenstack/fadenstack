"""Who a request to the skills service acts for.

The backend calls with the shared service token and says who the user is in
headers: their id, their groups, and what RBAC lets them do with skills.
Those headers are believed only next to the service token -- anyone else who
reaches the service would otherwise be whoever they claimed.

Every call used to come in as user "service" of tenant "default": the
service could not tell users apart, so it stored no owner and resolved the
same skills for everyone.
"""

from __future__ import annotations

import hmac
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from faden_skills.services.access import KNOWN_CAPS, Principal
from faden_skills.settings import settings

HEADER_USER = "x-faden-user"
HEADER_GROUPS = "x-faden-groups"
HEADER_CAPS = "x-faden-caps"
HEADER_TENANT = "x-faden-tenant"

_bearer_scheme = HTTPBearer(auto_error=False)


def _verify_token(token: str) -> dict[str, Any]:
    if not settings.jwt_secret:
        raise HTTPException(status_code=401, detail="Invalid token.")
    try:
        decode_opts: dict[str, Any] = {
            "algorithms": [settings.jwt_algorithm],
        }
        if settings.jwt_audience:
            decode_opts["audience"] = settings.jwt_audience
        else:
            decode_opts["options"] = {"verify_aud": False}
        if settings.jwt_issuer:
            decode_opts["issuer"] = settings.jwt_issuer
        return jwt.decode(token, settings.jwt_secret, **decode_opts)  # type: ignore[no-any-return]
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid JWT token.") from exc


def _split(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def principal_from_headers(request: Request) -> Principal:
    """The user the trusted caller acts for, as its headers say."""
    return Principal(
        tenant_id=(request.headers.get(HEADER_TENANT) or "default").strip()
        or "default",
        user_id=(request.headers.get(HEADER_USER) or "").strip() or None,
        group_ids=_split(request.headers.get(HEADER_GROUPS)),
        caps=_split(request.headers.get(HEADER_CAPS)) & KNOWN_CAPS,
    )


def _is_service_token(token: str | None) -> bool:
    return bool(
        token
        and settings.service_token
        and hmac.compare_digest(token.encode(), settings.service_token.encode()),
    )


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> Principal:
    """FastAPI dependency: the ``Principal`` a request acts for.

    - Service token: the user the headers name, with their capabilities.
    - A user's own JWT (when a JWT secret is configured): that user, with no
      capabilities -- they see what is visible to them and nothing more,
      since only the backend knows their RBAC.
    - No service token configured (development): the headers, as the
      internal API also trusts every caller then.
    """
    token = credentials.credentials if credentials else None
    if _is_service_token(token):
        return principal_from_headers(request)
    if not settings.service_token and not token:
        return principal_from_headers(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")

    claims = _verify_token(token)
    user_id = str(claims.get("sub", "")).strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="JWT missing subject.")
    tenant_id = str(claims.get("tenant_id", "default")).strip() or "default"
    return Principal(tenant_id=tenant_id, user_id=user_id)


async def verify_service_token(request: Request) -> None:
    """FastAPI dependency — validates internal service bearer token."""
    if not settings.service_token:
        return  # No service token configured => open (dev mode)
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing service token.")
    if not _is_service_token(auth_header[7:]):
        raise HTTPException(status_code=403, detail="Invalid service token.")
