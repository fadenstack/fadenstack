"""Signing in with an outside identity provider: the browser's side (``services/sign_in``).

- ``GET  /api/auth/sso/providers``: who people may sign in with (the sign-in page).
- ``GET  /api/auth/sso/{key}/start``: off to the provider, with a short binding cookie.
- ``GET  /api/auth/sso/{key}/callback``: the provider's answer as a query (OIDC).
- ``POST /api/auth/sso/{key}/acs``: the provider's answer as a posted form (SAML).
- ``POST /api/auth/sso/{key}/password``: a directory account's name and password (LDAP).
- ``GET  /api/auth/sso/{key}/metadata``: what the provider is set up with (SAML service-provider metadata).

The binding cookie ties an answer to the browser that began the sign-in, so an answer someone else started
cannot sign this browser in. It holds a hash of the sign-in's transaction, signed, for ten minutes. An answer
posted across sites (SAML) brings it back only as ``SameSite=None``, which browsers allow over HTTPS alone;
such providers therefore need HTTPS, except in development.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import SignInStrategy, cookie_transport, login_lifetime_seconds
from faden_backend.services.sign_in import accounts, module
from faden_backend.services.sign_in.contract import (
    KEY_PATTERN,
    ExternalIdentity,
    ProviderKind,
    ProviderPublic,
    Refusal,
)
from faden_backend.settings import settings
from faden_backend.web.origin import public_origin

log = logging.getLogger(__name__)

router = APIRouter()

BINDING_COOKIE = "faden_sso"
BINDING_PATH = "/api/auth/sso"
BINDING_TTL_S = 600
_BINDING_AUDIENCE = "faden-sso-binding"
_DEV_ENVIRONMENTS = {"dev", "pytest"}

Key = Annotated[str, Path(pattern=KEY_PATTERN)]


def _hash(transaction: str) -> str:
    return hashlib.sha256(transaction.encode()).hexdigest()


def _https(request: Request) -> bool:
    return public_origin(request).startswith("https://")


def _refusal_named(detail: object, otherwise: Refusal) -> Refusal:
    """The refusal a module's error names in its ``detail``, if it names one."""
    try:
        return Refusal(str(detail))
    except ValueError:
        return otherwise


def _to_login(refusal: Refusal) -> RedirectResponse:
    """Back to the sign-in page, which says why in the person's language."""
    response = RedirectResponse(f"/login?error={refusal.value}", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(BINDING_COOKIE, path=BINDING_PATH)
    return response


async def _provider(key: str) -> ProviderPublic:
    found = await module.provider(key)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such sign-in provider")
    return found


def _return_url(request: Request, provider: ProviderPublic) -> str:
    step = "acs" if provider.posts_back else "callback"
    return f"{public_origin(request)}{BINDING_PATH}/{provider.key}/{step}"


def _bound(request: Request, key: str, transaction: str) -> bool:
    cookie = request.cookies.get(BINDING_COOKIE)
    if not cookie:
        return False
    try:
        claims = jwt.decode(cookie, settings.users_secret, algorithms=["HS256"], audience=_BINDING_AUDIENCE)
    except jwt.PyJWTError:
        return False
    return claims.get("key") == key and claims.get("tx") == _hash(transaction)


async def _issue(
    request: Request, response: Response, signed: accounts.SignedIn, identity: ExternalIdentity,
) -> None:
    """A console sign-in for *signed*'s account, for the shorter of the backend's and the provider's limit."""
    lifetime = login_lifetime_seconds()
    if identity.session_lifetime_s is not None:
        limit = identity.session_lifetime_s
        lifetime = min(lifetime, limit) if lifetime else limit
    strategy = SignInStrategy(secret=settings.users_secret, lifetime_seconds=lifetime)
    token = await strategy.write_token(signed.user)
    response.set_cookie(
        key=cookie_transport.cookie_name,
        value=token,
        max_age=lifetime,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_https(request),
    )


@router.get("/providers", response_model=list[ProviderPublic], name="sso_providers")
async def providers() -> list[ProviderPublic]:
    """The providers people may sign in with; none when no sign-in module is on."""
    return await module.public_providers()


@router.get("/{key}/start", name="sso_start")
async def start(key: Key, request: Request) -> RedirectResponse:
    """Off to the provider; the answer comes back to ``callback`` or ``acs``."""
    provider = await _provider(key)
    if provider.kind is not ProviderKind.REDIRECT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This provider signs in on the page",
        )
    https = _https(request)
    if provider.posts_back and not https and settings.environment not in _DEV_ENVIRONMENTS:
        return _to_login(Refusal.NEEDS_HTTPS)
    try:
        begun = await module.start(key, _return_url(request, provider))
    except module.ModuleRefusedError as exc:
        log.warning("Sign-in with %s could not start: %s", key, exc)
        return _to_login(_refusal_named(exc.detail, Refusal.PROVIDER_ERROR))
    except module.ModuleUnavailableError as exc:
        log.warning("Sign-in with %s could not start: %s", key, exc)
        return _to_login(Refusal.PROVIDER_UNAVAILABLE)
    response = RedirectResponse(begun.redirect_url, status_code=status.HTTP_302_FOUND)
    expires = int(time.time()) + BINDING_TTL_S
    binding = jwt.encode(
        {"aud": _BINDING_AUDIENCE, "key": key, "tx": _hash(begun.transaction), "exp": expires},
        settings.users_secret,
        algorithm="HS256",
    )
    cross_site = provider.posts_back and https
    response.set_cookie(
        BINDING_COOKIE, binding, max_age=BINDING_TTL_S, path=BINDING_PATH, httponly=True,
        samesite="none" if cross_site else "lax", secure=https,
    )
    return response


def _answer_is_ours(request: Request, provider: ProviderPublic, transaction: str | None) -> bool:
    """Whether this browser began the sign-in the answer belongs to."""
    if not transaction:
        return False
    if _bound(request, provider.key, transaction):
        return True
    # Over plain HTTP a posted answer never brings the cookie back. That is allowed in development only
    # (``start`` refuses it elsewhere), where the module's own check of the request ID is what remains.
    return provider.posts_back and not _https(request) and settings.environment in _DEV_ENVIRONMENTS


async def _identity(
    request: Request, provider: ProviderPublic, transaction: str, params: dict[str, str],
) -> ExternalIdentity | Refusal:
    """The module's reading of the provider's answer."""
    try:
        answer = await module.finish(provider.key, transaction, _return_url(request, provider), params)
    except module.ModuleRefusedError as exc:
        log.warning("Sign-in with %s refused by the module: %s", provider.key, exc)
        return Refusal.PROVIDER_ERROR
    except module.ModuleUnavailableError as exc:
        log.warning("Sign-in with %s could not finish: %s", provider.key, exc)
        return Refusal.PROVIDER_UNAVAILABLE
    if answer.outcome == "refused" or answer.identity is None:
        return answer.refusal or Refusal.PROVIDER_ERROR
    return answer.identity


async def _finish(
    request: Request,
    session: AsyncSession,
    provider: ProviderPublic,
    transaction: str | None,
    params: dict[str, str],
) -> RedirectResponse:
    if transaction is None or not _answer_is_ours(request, provider, transaction):
        return _to_login(Refusal.SIGN_IN_EXPIRED)
    identity = await _identity(request, provider, transaction, params)
    if isinstance(identity, Refusal):
        return _to_login(identity)
    signed = await accounts.sign_in(session, identity)
    if isinstance(signed, Refusal):
        await session.commit()  # the refusal's audit entry
        return _to_login(signed)
    response = RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    await _issue(request, response, signed, identity)
    response.delete_cookie(BINDING_COOKIE, path=BINDING_PATH)
    return response


@router.get("/{key}/callback", name="sso_callback")
async def callback(
    key: Key, request: Request, session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """The provider's answer as a query string (OIDC: ``code`` and ``state``, or ``error``)."""
    provider = await _provider(key)
    params = dict(request.query_params)
    return await _finish(request, session, provider, params.get("state"), params)


@router.post("/{key}/acs", name="sso_acs")
async def acs(
    key: Key, request: Request, session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """The provider's answer as a posted form (SAML: ``SAMLResponse`` and ``RelayState``)."""
    provider = await _provider(key)
    if not provider.posts_back:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This provider answers to callback")
    form = await request.form()
    params = {name: value for name, value in form.items() if isinstance(value, str)}
    return await _finish(request, session, provider, params.get("RelayState"), params)


class PasswordSignIn(BaseModel):
    """A directory account's name and password."""

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/{key}/password", status_code=status.HTTP_204_NO_CONTENT, name="sso_password")
async def password(
    key: Key, payload: PasswordSignIn, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Sign in with a directory account; a refusal is a 400 with the reason as ``detail``."""
    provider = await _provider(key)
    if provider.kind is not ProviderKind.PASSWORD:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This provider signs in elsewhere")
    try:
        answer = await module.password(key, payload.username, payload.password)
    except module.ModuleRefusedError as exc:
        log.warning("Sign-in with %s refused by the module: %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=Refusal.PROVIDER_ERROR.value,
        ) from exc
    except module.ModuleUnavailableError as exc:
        log.warning("Sign-in with %s could not be checked: %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=Refusal.PROVIDER_UNAVAILABLE.value,
        ) from exc
    if answer.outcome == "refused" or answer.identity is None:
        refusal = answer.refusal or Refusal.WRONG_CREDENTIALS
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=refusal.value)
    signed = await accounts.sign_in(session, answer.identity)
    if isinstance(signed, Refusal):
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=signed.value)
    await _issue(request, response, signed, answer.identity)


@router.get("/{key}/metadata", name="sso_metadata")
async def metadata(key: Key) -> Response:
    """The provider's service-provider metadata (SAML), for its administrator to set it up with."""
    await _provider(key)
    try:
        upstream = await module.metadata(key)
    except module.ModuleRefusedError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except module.ModuleUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sign-in module unavailable",
        ) from exc
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "application/samlmetadata+xml"),
    )
