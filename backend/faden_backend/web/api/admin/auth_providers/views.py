"""Admin auth-provider endpoints and the external auth module's callback.

Provider management is forwarded to the ``faden_auth`` micro-service
(``settings.auth_service_url``).  OAuth ``authorize`` redirects to the
auth module, which handles the full IdP exchange and sends signed
``OAuthUserClaims`` back to the ``/external-callback`` endpoint here,
where we find-or-create the user and issue a JWT cookie.

When the auth module is **disabled**, the routes still exist but
return empty lists / 404 — keeping the API shape stable so the front-
end doesn't break.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.users import User, get_jwt_strategy, login_lifetime_seconds
from faden_backend.settings import settings
from faden_backend.web.api.admin.auth_providers.schema import (
    AuthProviderDTO,
    AuthProviderPublicDTO,
    CreateAuthProviderRequest,
    UpdateAuthProviderRequest,
)
from faden_backend.web.api.admin.dependencies import require_superuser
from faden_backend.services.tls import default_httpx_verify

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
#  Proxy helpers
# ─────────────────────────────────────────────────────────────────────────────

_AUTH_API_TIMEOUT = 15.0

_auth_proxy_client: httpx.AsyncClient | None = None


def _get_auth_client() -> httpx.AsyncClient:
    global _auth_proxy_client
    if _auth_proxy_client is None:
        _auth_proxy_client = httpx.AsyncClient(verify=default_httpx_verify(), timeout=_AUTH_API_TIMEOUT)
    return _auth_proxy_client


def _auth_base() -> str:
    """Return the base URL for the auth module's providers API."""
    return f"{settings.auth_service_url.rstrip('/')}/api/providers"


async def _proxy_get(path: str) -> httpx.Response:
    return await _get_auth_client().get(f"{_auth_base()}{path}")


async def _proxy_post(path: str, payload: dict) -> httpx.Response:
    return await _get_auth_client().post(f"{_auth_base()}{path}", json=payload)


async def _proxy_patch(path: str, payload: dict) -> httpx.Response:
    return await _get_auth_client().patch(f"{_auth_base()}{path}", json=payload)


async def _proxy_delete(path: str) -> httpx.Response:
    return await _get_auth_client().delete(f"{_auth_base()}{path}")


def _forward_or_raise(resp: httpx.Response):
    """Raise an HTTPException mirroring the upstream status on error."""
    if resp.status_code >= 400:  # noqa: PLR2004
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)


def _require_auth_module() -> None:
    """404 unless the external auth module is enabled."""
    if not settings.auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")


# ─────────────────────────────────────────────────────────────────────────────
#  Public endpoint: enabled providers (login page)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/public",
    response_model=list[AuthProviderPublicDTO],
    name="list_enabled_providers",
)
async def list_enabled_providers() -> list[AuthProviderPublicDTO]:
    """List enabled providers — unauthenticated (login page)."""
    if not settings.auth_enabled:
        return []
    resp = await _proxy_get("/public")
    _forward_or_raise(resp)
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
#  External callback — receives signed claims from the auth module
# ─────────────────────────────────────────────────────────────────────────────

# Declared before ``/{provider_id}``, which would otherwise take this path.
@router.get(
    "/external-callback",
    name="oauth_external_callback",
)
async def oauth_external_callback(
    token: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """Receive signed OAuthUserClaims from the auth module.

    The auth module performs the full OAuth exchange and then redirects
    the user's browser here with a short-lived JWT.  We verify the
    signature, find-or-create the user, and issue a session cookie.
    """
    _require_auth_module()

    # Verify the signed token
    try:
        claims = jwt.decode(
            token,
            settings.users_secret,
            algorithms=["HS256"],
            issuer="faden-auth",
        )
    except jwt.ExpiredSignatureError:
        logger.warning("External callback token expired")
        return RedirectResponse("/login?error=token_expired")
    except jwt.InvalidTokenError:
        logger.warning("External callback token invalid")
        return RedirectResponse("/login?error=invalid_token")

    return await _process_oauth_login(
        session=session,
        request=request,
        provider_name=claims["provider_name"],
        account_id=claims.get("account_id", ""),
        account_email=claims["account_email"],
        access_token=claims.get("access_token", ""),
        expires_at=claims.get("expires_at"),
        refresh_token=claims.get("refresh_token"),
        auto_register=claims.get("auto_register", True),
        default_role_ids=claims.get("default_role_ids", []),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Admin CRUD
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[AuthProviderDTO], name="list_auth_providers")
async def list_auth_providers(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> list[AuthProviderDTO]:
    """List all configured auth providers."""
    if not settings.auth_enabled:
        return []
    resp = await _proxy_get("/")
    _forward_or_raise(resp)
    return resp.json()


@router.get("/{provider_id}", response_model=AuthProviderDTO, name="get_auth_provider")
async def get_auth_provider(
    provider_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> AuthProviderDTO:
    """Get a single auth provider by ID."""
    _require_auth_module()
    resp = await _proxy_get(f"/{provider_id}")
    _forward_or_raise(resp)
    return resp.json()


@router.post(
    "/",
    response_model=AuthProviderDTO,
    status_code=status.HTTP_201_CREATED,
    name="create_auth_provider",
)
async def create_auth_provider(
    payload: CreateAuthProviderRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> AuthProviderDTO:
    """Create a new auth provider."""
    _require_auth_module()
    resp = await _proxy_post("/", payload.model_dump())
    _forward_or_raise(resp)
    return resp.json()


@router.patch("/{provider_id}", response_model=AuthProviderDTO, name="update_auth_provider")
async def update_auth_provider(
    provider_id: uuid.UUID,
    payload: UpdateAuthProviderRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> AuthProviderDTO:
    """Update an auth provider."""
    _require_auth_module()
    resp = await _proxy_patch(
        f"/{provider_id}",
        payload.model_dump(exclude_none=True),
    )
    _forward_or_raise(resp)
    return resp.json()


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="delete_auth_provider",
)
async def delete_auth_provider(
    provider_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> None:
    """Delete an auth provider."""
    _require_auth_module()
    resp = await _proxy_delete(f"/{provider_id}")
    _forward_or_raise(resp)


# ─────────────────────────────────────────────────────────────────────────────
#  OAuth flow
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{provider_id}/authorize",
    name="oauth_authorize",
)
async def oauth_authorize(provider_id: uuid.UUID) -> RedirectResponse:
    """Redirect the browser to the auth module, which handles the full OAuth exchange."""
    _require_auth_module()
    return RedirectResponse(_auth_base() + f"/{provider_id}/authorize")


# ─────────────────────────────────────────────────────────────────────────────
#  Login from the auth module's claims
# ─────────────────────────────────────────────────────────────────────────────


async def _process_oauth_login(
    *,
    session,
    request: Request,
    provider_name: str,
    account_id: str,
    account_email: str,
    access_token: str,
    expires_at: int | None,
    refresh_token: str | None,
    auto_register: bool,
    default_role_ids: list[str],
) -> RedirectResponse:
    """Find-or-create a user from OAuth claims and issue a JWT cookie."""
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

    from faden_backend.db.dao.rbac_dao import RbacDAO  # noqa: PLC0415

    # Find or create the user
    user_result = await session.execute(
        select(User).where(User.email == account_email),  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if user is not None:
        if not user.is_active:
            return RedirectResponse("/login?error=account_disabled")
        # An address is no proof of who owns the account: until its owner has verified it, whoever
        # holds that address at the provider could sign in as them (and a superuser's account is
        # one email away). A person this provider already signed in to this account stays in.
        linked = await session.scalar(
            select(OAuthAccount.id).where(
                OAuthAccount.user_id == user.id,
                OAuthAccount.oauth_name == provider_name,
                OAuthAccount.account_id == str(account_id),
            ).limit(1),
        )
        if not user.is_verified and linked is None:
            logger.warning("SSO sign-in refused: %s has an unverified local account", provider_name)
            return RedirectResponse("/login?error=account_not_verified")

    if user is None:
        if not auto_register:
            return RedirectResponse("/login?error=registration_disabled")

        from fastapi_users.password import PasswordHelper  # noqa: PLC0415

        user = User(
            id=uuid.uuid4(),
            email=account_email,
            hashed_password=PasswordHelper().hash(uuid.uuid4().hex),
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.flush()

        # Assign default roles
        if default_role_ids:
            rbac_dao = RbacDAO(session)
            role_ids = [uuid.UUID(rid) for rid in default_role_ids if rid]
            if role_ids:
                await rbac_dao.set_user_roles(user.id, role_ids)
                await session.flush()
        else:
            rbac_dao = RbacDAO(session)
            default_role = await rbac_dao.get_role_by_name("default_user")
            if default_role is not None:
                await rbac_dao.assign_role(user.id, default_role.id)
                await session.flush()

    # Upsert OAuth account record
    await session.execute(
        pg_insert(OAuthAccount)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            oauth_name=provider_name,
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=refresh_token,
            account_id=str(account_id),
            account_email=account_email,
        )
        .on_conflict_do_update(
            index_elements=[OAuthAccount.id],
            set_={
                "access_token": access_token,
                "expires_at": expires_at,
                "refresh_token": refresh_token,
                "account_email": account_email,
            },
        ),
    )
    await session.flush()

    # Issue a JWT cookie and redirect to the admin dashboard
    strategy = get_jwt_strategy()
    jwt_token = await strategy.write_token(user)

    response = RedirectResponse("/admin/dashboard", status_code=302)
    response.set_cookie(
        key="fapiauth",
        value=jwt_token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=login_lifetime_seconds(),
        path="/",
    )
    return response
