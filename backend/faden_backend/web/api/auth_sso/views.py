"""Signing in with an outside identity provider: the browser's side (``services/sign_in``).

- ``GET  /api/auth/sso/providers``: who people may sign in with (the sign-in page).
- ``GET  /api/auth/sso/{key}/start``: off to the provider, with a short binding cookie.
- ``GET  /api/auth/sso/{key}/callback``: the provider's answer as a query (OIDC).
- ``POST /api/auth/sso/{key}/acs``: the provider's answer as a posted form (SAML).
- ``POST /api/auth/sso/{key}/password``: a directory account's name and password (LDAP).
- ``GET  /api/auth/sso/{key}/metadata``: what the provider is set up with (SAML service-provider metadata).
- ``GET  /api/auth/sso/link``, ``POST /link/code``, ``/link/with-password``, ``/link/with-code``: a sign-in
  that met an existing account waits, in this browser only (the ``faden_link`` cookie), until the person
  proves the account is theirs, with its password or a code sent to its address
  (``services/sign_in/link_requests.py``). Not ``/link/password``: ``/{key}/password`` would take it.
- ``POST /api/auth/sso/{key}/link-start``, ``POST /{key}/link-password``: a signed-in person links a provider
  to their own account from My Profile (``services/sign_in/linked.py``). The start is a POST, so no other
  site's page can begin it (the Origin check), and the binding cookie names the account: the answer links
  to that account and signs nobody in. A SAML answer is posted across sites and brings no sign-in cookie,
  so the binding, signed and tied to this one transaction, is what names the account; it is void once the
  account's sign-ins were ended.
- ``POST /api/auth/sso/sign-out``: sign out here, then at the provider the sign-in came through, when it asks
  for that (SAML Single Logout): the answer says where the browser goes.
- ``GET|POST /api/auth/sso/{key}/sls``: a provider's logout message (SAML): its sign-out of someone, which
  ends their sign-ins here, or its answer to ours.

The binding cookie ties an answer to the browser that began the sign-in, so an answer someone else started
cannot sign this browser in. It holds a hash of the sign-in's transaction, signed, for ten minutes. An answer
posted across sites (SAML) brings it back only as ``SameSite=None``, which browsers allow over HTTPS alone;
such providers therefore need HTTPS, except in development.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Annotated, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.users import (
    SignInStrategy,
    User,
    cookie_transport,
    current_active_user,
    end_sign_ins,
    login_lifetime_seconds,
    sign_in_ended_for_all,
)
from faden_backend.services.sign_in import accounts, link_requests, linked, module
from faden_backend.services.sign_in.contract import (
    KEY_PATTERN,
    ExternalIdentity,
    LogoutMessage,
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


#: Where a link begun on My Profile may come back to: the console page, or the chat dialog (people who only
#: chat have no console). Only these, so the answer never sends the browser anywhere else.
PROFILES = ("/admin/profile", "/chat")


def _to_profile(back: str, *, linked_key: str | None = None, refused: str | None = None) -> RedirectResponse:
    """Back to My Profile, which says whether the provider was linked, or why not."""
    query = f"linked={linked_key}" if linked_key else f"link_refused={refused}"
    where = back if back in PROFILES else PROFILES[0]
    response = RedirectResponse(f"{where}?{query}", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(BINDING_COOKIE, path=BINDING_PATH)
    return response


async def _offered(key: str) -> ProviderPublic | Refusal:
    """The provider with *key*, or why nobody can sign in with it now.

    ``not_offered`` covers a provider that was removed or switched off, and every provider while the module
    lists none (its licence); a browser that began on an older page is told so on the sign-in page.
    """
    try:
        found = await module.provider(key)
    except module.ModuleUnavailableError as exc:
        log.warning("Sign-in with %s: the module cannot say which providers it offers: %s", key, exc)
        return Refusal.PROVIDER_UNAVAILABLE
    return found or Refusal.NOT_OFFERED


async def _wait_for_proof(session: AsyncSession, refusal: Refusal, identity: ExternalIdentity) -> str | None:
    """For a sign-in that met an existing account, the token of the request that waits for the proof."""
    if refusal is not Refusal.LINK_NEEDS_CONFIRMATION:
        return None
    return await link_requests.begin(session, identity)


def _set_link_cookie(request: Request, response: Response, token: str) -> None:
    """This browser, and no other, gives the proof: httpOnly, for the link routes only, until it expires."""
    response.set_cookie(
        link_requests.COOKIE, token, max_age=int(link_requests.TTL.total_seconds()),
        path=link_requests.COOKIE_PATH, httponly=True, samesite="lax", secure=_https(request),
    )


def _return_url(request: Request, provider: ProviderPublic) -> str:
    step = "acs" if provider.posts_back else "callback"
    return f"{public_origin(request)}{BINDING_PATH}/{provider.key}/{step}"


def _binding(request: Request, key: str, transaction: str) -> dict | None:
    """This browser's binding for *transaction* at *key*, if it has one."""
    cookie = request.cookies.get(BINDING_COOKIE)
    if not cookie:
        return None
    try:
        claims = jwt.decode(cookie, settings.users_secret, algorithms=["HS256"], audience=_BINDING_AUDIENCE)
    except jwt.PyJWTError:
        return None
    return claims if claims.get("key") == key and claims.get("tx") == _hash(transaction) else None


async def _issue(
    request: Request, response: Response, signed: accounts.SignedIn, identity: ExternalIdentity,
) -> None:
    """A console sign-in for *signed*'s account, for the shorter of the backend's and the provider's limit."""
    lifetime = login_lifetime_seconds()
    if identity.session_lifetime_s is not None:
        limit = identity.session_lifetime_s
        lifetime = min(lifetime, limit) if lifetime else limit
    strategy = SignInStrategy(secret=settings.users_secret, lifetime_seconds=lifetime)
    token = await strategy.write_token(signed.user, provider=identity.provider)
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


async def _begin(
    request: Request, key: str, *, link: User | None = None, back: str = PROFILES[0],
) -> tuple[ProviderPublic, str, str] | Refusal:
    """The provider, where to send the browser and the binding cookie for a sign-in; or why it cannot start.

    With *link*, the answer is to be linked to that account rather than sign anyone in.
    """
    provider = await _offered(key)
    if isinstance(provider, Refusal):
        return provider
    if provider.kind is not ProviderKind.REDIRECT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="This provider signs in on the page",
        )
    if provider.posts_back and not _https(request) and settings.environment not in _DEV_ENVIRONMENTS:
        return Refusal.NEEDS_HTTPS
    try:
        begun = await module.start(key, _return_url(request, provider))
    except module.ModuleRefusedError as exc:
        log.warning("Sign-in with %s could not start: %s", key, exc)
        return _refusal_named(exc.detail, Refusal.PROVIDER_ERROR)
    except module.ModuleUnavailableError as exc:
        log.warning("Sign-in with %s could not start: %s", key, exc)
        return Refusal.PROVIDER_UNAVAILABLE
    now = int(time.time())
    claims: dict[str, object] = {
        "aud": _BINDING_AUDIENCE, "key": key, "tx": _hash(begun.transaction), "iat": now,
        "exp": now + BINDING_TTL_S,
    }
    if link is not None:
        claims["link"], claims["back"] = str(link.id), back
    binding = jwt.encode(claims, settings.users_secret, algorithm="HS256")
    return provider, begun.redirect_url, binding


def _set_binding(request: Request, response: Response, provider: ProviderPublic, binding: str) -> None:
    https = _https(request)
    response.set_cookie(
        BINDING_COOKIE, binding, max_age=BINDING_TTL_S, path=BINDING_PATH, httponly=True,
        samesite="none" if provider.posts_back and https else "lax", secure=https,
    )


@router.get("/{key}/start", name="sso_start")
async def start(key: Key, request: Request) -> RedirectResponse:
    """Off to the provider; the answer comes back to ``callback`` or ``acs``."""
    begun = await _begin(request, key)
    if isinstance(begun, Refusal):
        return _to_login(begun)
    provider, redirect_url, binding = begun
    response = RedirectResponse(redirect_url, status_code=status.HTTP_302_FOUND)
    _set_binding(request, response, provider, binding)
    return response


class LinkStart(BaseModel):
    """Where to send the browser to sign in with the provider that is to be linked."""

    redirect_url: str


class LinkFrom(BaseModel):
    """Where My Profile is open: the console's page, or the chat's dialog."""

    back: Literal["/admin/profile", "/chat"] = "/admin/profile"


@router.post("/{key}/link-start", response_model=LinkStart, name="sso_link_start")
async def link_start(
    key: Key, request: Request, response: Response, user: Annotated[User, Depends(current_active_user)],
    payload: LinkFrom | None = None,
) -> LinkStart:
    """Begin linking *key* to the signed-in account; the answer comes back to My Profile, where it was open.

    Refused with 404 ``not_offered`` / 503 and the reason as ``detail``, as the sign-in page's are named.
    """
    begun = await _begin(request, key, link=user, back=(payload or LinkFrom()).back)
    if isinstance(begun, Refusal):
        gone = begun is Refusal.NOT_OFFERED
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if gone else status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=begun.value,
        )
    provider, redirect_url, binding = begun
    _set_binding(request, response, provider, binding)
    return LinkStart(redirect_url=redirect_url)


def _answer_is_ours(request: Request, provider: ProviderPublic, transaction: str | None) -> dict | None:
    """This browser's binding for the sign-in the answer belongs to; ``None`` when it did not begin it."""
    if not transaction:
        return None
    claims = _binding(request, provider.key, transaction)
    if claims is not None:
        return claims
    # Over plain HTTP a posted answer never brings the cookie back. That is allowed in development only
    # (``start`` refuses it elsewhere), where the module's own check of the request ID is what remains; a
    # link needs the binding, so none is made this way.
    if provider.posts_back and not _https(request) and settings.environment in _DEV_ENVIRONMENTS:
        return {}
    return None


async def _identity(
    request: Request, session: AsyncSession, provider: ProviderPublic, transaction: str,
    params: dict[str, str],
) -> ExternalIdentity | Refusal:
    """The module's reading of the provider's answer; a refused one is recorded (ASVS 5.0 V16.3.1)."""
    try:
        answer = await module.finish(provider.key, transaction, _return_url(request, provider), params)
    except module.ModuleRefusedError as exc:
        log.warning("Sign-in with %s refused by the module: %s", provider.key, exc)
        return Refusal.PROVIDER_ERROR
    except module.ModuleUnavailableError as exc:
        log.warning("Sign-in with %s could not finish: %s", provider.key, exc)
        return Refusal.PROVIDER_UNAVAILABLE
    if answer.outcome == "refused" or answer.identity is None:
        refusal = await accounts.refuse_answer(
            session, provider.key, answer.refusal or Refusal.PROVIDER_ERROR, answer.detail,
        )
        await session.commit()  # the refusal's audit entry
        return refusal
    return answer.identity


async def _finish(
    request: Request,
    session: AsyncSession,
    provider: ProviderPublic,
    transaction: str | None,
    params: dict[str, str],
) -> RedirectResponse:
    claims = _answer_is_ours(request, provider, transaction)
    if transaction is None or claims is None:
        return _to_login(Refusal.SIGN_IN_EXPIRED)
    identity = await _identity(request, session, provider, transaction, params)
    if claims.get("link"):
        return await _finish_link(session, claims, identity, provider.key)
    if isinstance(identity, Refusal):
        return _to_login(identity)
    signed = await accounts.sign_in(session, identity)
    if isinstance(signed, Refusal):
        token = await _wait_for_proof(session, signed, identity)
        await session.commit()  # the refusal's audit entry, and the waiting sign-in
        response = _to_login(signed)
        if token:
            _set_link_cookie(request, response, token)
        return response
    # The sign-in page sends whoever is signed in on to where they belong (the chat, or the console); "/" is
    # the landing page, which would ask them to sign in again.
    response = RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    await _issue(request, response, signed, identity)
    response.delete_cookie(BINDING_COOKIE, path=BINDING_PATH)
    return response


async def _linking(session: AsyncSession, claims: dict) -> User | None:
    """The account a link's binding names, while it is active and its sign-ins were not ended since."""
    try:
        user = await session.get(User, uuid.UUID(str(claims["link"])))
    except (KeyError, ValueError):
        return None
    if user is None or not user.is_active or sign_in_ended_for_all(claims, user.sessions_valid_after):
        return None
    return user


async def _finish_link(
    session: AsyncSession, claims: dict, identity: ExternalIdentity | Refusal, key: str,
) -> RedirectResponse:
    """A link begun on My Profile: the identity joins the binding's account, or My Profile says why not."""
    back = str(claims.get("back", ""))
    user = await _linking(session, claims)
    if user is None:
        return _to_profile(back, refused=Refusal.SIGN_IN_EXPIRED.value)
    if isinstance(identity, Refusal):
        return _to_profile(back, refused=identity.value)
    refused = await linked.link_signed_in(session, user, identity)
    await session.commit()
    return _to_profile(back, refused=refused) if refused else _to_profile(back, linked_key=key)


@router.get("/{key}/callback", name="sso_callback")
async def callback(
    key: Key, request: Request, session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """The provider's answer as a query string (OIDC: ``code`` and ``state``, or ``error``)."""
    provider = await _offered(key)
    if isinstance(provider, Refusal):
        return _to_login(provider)
    params = dict(request.query_params)
    return await _finish(request, session, provider, params.get("state"), params)


@router.post("/{key}/acs", name="sso_acs")
async def acs(
    key: Key, request: Request, session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """The provider's answer as a posted form (SAML: ``SAMLResponse`` and ``RelayState``)."""
    provider = await _offered(key)
    if isinstance(provider, Refusal):
        return _to_login(provider)
    if not provider.posts_back:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This provider answers to callback")
    form = await request.form()
    params = {name: value for name, value in form.items() if isinstance(value, str)}
    return await _finish(request, session, provider, params.get("RelayState"), params)


class PasswordSignIn(BaseModel):
    """A directory account's name and password."""

    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


@router.post(
    "/{key}/password", status_code=status.HTTP_204_NO_CONTENT, name="sso_password", response_model=None,
)
async def password(
    key: Key, payload: PasswordSignIn, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> Response | None:
    """Sign in with a directory account; a refusal is a 400 with the reason as ``detail``.

    One that met an existing account (``link_needs_confirmation``) also sets this browser's link cookie.
    """
    provider = await _offered(key)
    if isinstance(provider, Refusal):
        gone = provider is Refusal.NOT_OFFERED
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if gone else status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=provider.value,
        )
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
        token = await _wait_for_proof(session, signed, answer.identity)
        await session.commit()
        if not token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=signed.value)
        refused = JSONResponse({"detail": signed.value}, status_code=status.HTTP_400_BAD_REQUEST)
        _set_link_cookie(request, refused, token)
        return refused
    await _issue(request, response, signed, answer.identity)
    return None


@router.post("/{key}/link-password", status_code=status.HTTP_204_NO_CONTENT, name="sso_link_password_of")
async def link_password(
    key: Key, payload: PasswordSignIn, user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Link a directory account to the signed-in account: its name and password prove it.

    A refusal is a 400 (404, 503) with the reason as ``detail``, as ``/{key}/password``'s.
    """
    provider = await _offered(key)
    if isinstance(provider, Refusal):
        gone = provider is Refusal.NOT_OFFERED
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if gone else status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=provider.value,
        )
    if provider.kind is not ProviderKind.PASSWORD:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This provider signs in elsewhere")
    try:
        answer = await module.password(key, payload.username, payload.password)
    except module.ModuleRefusedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=Refusal.PROVIDER_ERROR.value,
        ) from exc
    except module.ModuleUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=Refusal.PROVIDER_UNAVAILABLE.value,
        ) from exc
    if answer.outcome == "refused" or answer.identity is None:
        refusal = answer.refusal or Refusal.WRONG_CREDENTIALS
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=refusal.value)
    refused = await linked.link_signed_in(session, user, answer.identity)
    if refused:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=refused)


@router.get("/{key}/metadata", name="sso_metadata")
async def metadata(key: Key) -> Response:
    """The provider's service-provider metadata (SAML), for its administrator to set it up with."""
    offered = await _offered(key)
    if offered is Refusal.NOT_OFFERED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such sign-in provider")
    if isinstance(offered, Refusal):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sign-in module unavailable",
        )
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


class LinkWaiting(BaseModel):
    """A sign-in that met an existing account, waiting in this browser for the proof that it is theirs."""

    provider_name: str
    account_email: str
    expires_at: datetime
    #: A code can be sent to the account's address.
    mail: bool
    code_sent: bool


class LinkPassword(BaseModel):
    """The existing account's password."""

    password: str = Field(min_length=1, max_length=1024)


class LinkCode(BaseModel):
    """The code sent to the account's address; spaces and dashes are ignored."""

    code: str = Field(min_length=1, max_length=32)


class CodeSent(BaseModel):
    """A code is on its way to the account's address."""

    sent: bool = True


#: Why no code was sent: the answer's status and ``detail``.
_NO_CODE = {
    "expired": (status.HTTP_404_NOT_FOUND, Refusal.SIGN_IN_EXPIRED.value),
    "no_mail": (status.HTTP_404_NOT_FOUND, "no_mail"),
    "too_soon": (status.HTTP_429_TOO_MANY_REQUESTS, "too_soon"),
    "too_many": (status.HTTP_429_TOO_MANY_REQUESTS, "too_many"),
}


def _link_token(request: Request) -> str | None:
    return request.cookies.get(link_requests.COOKIE)


@router.get("/link", response_model=LinkWaiting, name="sso_link")
async def link_waiting(request: Request, session: AsyncSession = Depends(get_db_session)) -> LinkWaiting:
    """What this browser's sign-in waits for; 404 ``sign_in_expired`` when nothing waits. Changes nothing."""
    found = await link_requests.waiting(session, _link_token(request))
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=Refusal.SIGN_IN_EXPIRED.value)
    return LinkWaiting(
        provider_name=found.provider_name, account_email=found.account_email, expires_at=found.expires_at,
        mail=found.mail, code_sent=found.code_sent,
    )


@router.post(
    "/link/code", status_code=status.HTTP_202_ACCEPTED, response_model=CodeSent, name="sso_link_code",
)
async def link_send_code(request: Request, session: AsyncSession = Depends(get_db_session)) -> CodeSent:
    """Send a code to the account's address, replacing one sent before (404 / 429 with why not)."""
    result = await link_requests.send_code(session, _link_token(request))
    await session.commit()
    if result != "sent":
        code, detail = _NO_CODE[result]
        raise HTTPException(status_code=code, detail=detail)
    return CodeSent()


@router.post("/link/with-password", status_code=status.HTTP_204_NO_CONTENT, name="sso_link_password")
async def link_with_password(
    payload: LinkPassword, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """The account's password proves it: linked, and signed in. A refusal is a 400 with why as ``detail``."""
    linked = await link_requests.prove(session, _link_token(request), password=payload.password)
    await _signed_in_by_link(request, response, session, linked)


@router.post("/link/with-code", status_code=status.HTTP_204_NO_CONTENT, name="sso_link_confirm")
async def link_with_code(
    payload: LinkCode, request: Request, response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """The code sent to the account's address proves it: linked, and signed in. As ``/link/with-password``."""
    code = "".join(ch for ch in payload.code if ch not in " -")
    linked = await link_requests.prove(session, _link_token(request), code=code)
    await _signed_in_by_link(request, response, session, linked)


async def _signed_in_by_link(
    request: Request, response: Response, session: AsyncSession, linked: link_requests.Linked | Refusal,
) -> None:
    if isinstance(linked, Refusal):
        await session.commit()  # the try counts, and its audit entry stays
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=linked.value)
    await _issue(request, response, linked.signed, linked.identity)
    response.delete_cookie(link_requests.COOKIE, path=link_requests.COOKIE_PATH)


# ── Single Logout (SAML) ────────────────────────────────────────────────────────────────────────────────────

#: The most a logout message may be: the module reads at most this much of a query, a form a little more.
LOGOUT_MAX_BYTES = 1_048_576


class SignedOut(BaseModel):
    """Signed out here; *redirect_url*: where the browser goes to be signed out at the provider too."""

    redirect_url: str | None = None


@router.post("/sign-out", response_model=SignedOut, name="sso_sign_out")
async def sign_out(
    request: Request,
    response: Response,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> SignedOut:
    """Sign out here, then, for a sign-in that came through a provider asking for it, at the provider too.

    Only on the person's own sign-out, never on expiry (SAML2Int SDP-SP35). This sign-in ends first and for
    good, whatever the provider says (SDP-SP34): its ``jti`` is kept as logged out and the cookie goes. The
    browser then goes to the provider's logout with a top-level navigation (SDP-SP27); the provider sends it
    back to ``/{key}/sls``.
    """
    token = request.cookies.get(cookie_transport.cookie_name) or ""
    claims: dict[str, object] = {}
    if token:
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            claims = {}
        strategy = SignInStrategy(
            secret=settings.users_secret, lifetime_seconds=login_lifetime_seconds(), session=session,
        )
        await strategy.destroy_token(token, user)
    await session.commit()
    response.delete_cookie(cookie_transport.cookie_name, path="/")
    provider = str(claims.get("sso") or "")
    if not provider or not module.enabled():
        return SignedOut()
    subject = await session.scalar(select(OAuthAccount.account_id).where(
        OAuthAccount.user_id == user.id, OAuthAccount.oauth_name == provider,
    ))
    if subject is None:
        return SignedOut()
    try:
        started = await module.sign_out_start(provider, subject)
    except (module.ModuleUnavailableError, module.ModuleRefusedError) as exc:
        log.warning("Signing out at %s could not start: %s", provider, exc)
        return SignedOut()
    if started.redirect_url is None and started.detail:
        log.info("No sign-out at %s for this sign-in: %s", provider, started.detail)
    return SignedOut(redirect_url=started.redirect_url)


@router.get("/{key}/sls", name="sso_sls_redirect")
async def sls_redirect(
    key: Key, request: Request, session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """A logout message over HTTP-Redirect, passed on octet for octet (its signature covers the query)."""
    query = request.scope.get("query_string", b"").decode("latin-1")
    if len(query) > LOGOUT_MAX_BYTES:
        return _to_login(Refusal.PROVIDER_ERROR)
    return await _logout(session, key, LogoutMessage(
        provider=key, return_url=_sls_url(request, key), binding="redirect", query=query,
    ))


@router.post("/{key}/sls", name="sso_sls_post")
async def sls_post(
    key: Key, request: Request, session: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    """A logout message over HTTP-POST."""
    if int(request.headers.get("content-length") or 0) > LOGOUT_MAX_BYTES:
        return _to_login(Refusal.PROVIDER_ERROR)
    form = await request.form()
    params = {name: value for name, value in form.items() if isinstance(value, str)}
    return await _logout(session, key, LogoutMessage(
        provider=key, return_url=_sls_url(request, key), binding="post", params=params,
    ))


def _sls_url(request: Request, key: str) -> str:
    """The logout service's address; the module refuses a message unless it is the one the provider knows."""
    return f"{public_origin(request)}/api/auth/sso/{key}/sls"


async def _logout(session: AsyncSession, key: str, message: LogoutMessage) -> RedirectResponse:
    """What a provider's logout message comes to: sign-ins ended, or this server's own sign-out completed.

    Reached across sites without a sign-in or a binding cookie, like ``/acs``, and passed on whether or not
    the provider is offered for signing in: a logout only ends sign-ins.
    """
    if not module.enabled():
        return _to_login(Refusal.NOT_OFFERED)
    try:
        answer = await module.logout_message(message)
    except (module.ModuleUnavailableError, module.ModuleRefusedError) as exc:
        log.warning("Logout message from %s not read: %s", key, exc)
        return _to_login(Refusal.PROVIDER_UNAVAILABLE)
    audit = AuditDAO(session)
    if answer.outcome == "end_sign_ins":
        # Every sign-in of each person, whatever session the provider named (ASVS 5.0 V7.4.1).
        for subject in answer.subjects:
            account = await session.scalar(select(OAuthAccount).where(
                OAuthAccount.oauth_name == key, OAuthAccount.account_id == subject,
            ))
            user = await session.get(User, account.user_id) if account is not None else None
            if user is None:
                continue
            end_sign_ins(user)
            await audit.log(
                action="auth.sign_out.provider", target_type="user", target_id=str(user.id),
                result=AuditResult.ALLOW, source="system",
                metadata_json=json.dumps({"provider": key, "email": user.email}),
            )
        await session.commit()
        # Only now the provider hears it worked (its signed answer, in redirect_url).
        return RedirectResponse(answer.redirect_url or "/login", status_code=status.HTTP_302_FOUND)
    if answer.outcome == "signed_out":
        return RedirectResponse("/login?signed_out=provider", status_code=status.HTTP_302_FOUND)
    if answer.outcome == "not_signed_out":
        log.info("%s did not confirm the sign-out: %s", key, answer.detail)
        return RedirectResponse("/login?signed_out=here_only", status_code=status.HTTP_302_FOUND)
    log.warning("Logout message from %s refused: %s", key, answer.detail)
    await audit.log(
        action="auth.sign_out.refused", target_type="sign_in_provider", target_id=key,
        result=AuditResult.DENY, source="system", severity="high",
        metadata_json=json.dumps({"provider": key, "detail": answer.detail}),
    )
    await session.commit()
    return _to_login(Refusal.PROVIDER_ERROR)
