"""The second sign-in before Bypass is switched on: the app's routes and the browser page's (sdk/41).

The app (its agent token as ``Authorization``):

- ``POST /api/agent-tokens/step-ups``: start one; the app's code, the link's code and the link.
- ``POST /api/agent-tokens/step-ups/token``: poll with the app's code; RFC 8628 errors until approved, then
  the proof once.

The page ``/step-up`` (``services/agents/step_up.py`` for the flow):

- ``POST /api/auth/step-up/begin``: the link's code (from the address's fragment) opens the step-up in this
  browser, which gets the ``faden_step_up`` cookie; what to sign in with.
- ``GET  /api/auth/step-up``: where this browser's step-up stands (after a sign-in provider sent it back).
- ``POST /api/auth/step-up/password``: the account's address and password again.
- ``POST /api/auth/step-up/directory/{key}``: a directory account's name and password again (LDAP).
- ``POST /api/auth/step-up/sso/{key}``: off to a sign-in provider that must authenticate anew; its answer comes
  back through ``/api/auth/sso/{key}/callback`` or ``/acs`` and on to the page.
- ``POST /api/auth/step-up/approve`` and ``/deny``.

The page's routes take the cookie, never a console sign-in: an open console session counts for nothing here. The
cookie is ``SameSite=Strict`` and the routes refuse a request from another site's page (its ``Origin``), as the
console's own CSRF check does for its cookie. Codes and the proof are never logged; the audit trail keeps the
step-up's id, the agent, the device and how the person signed in.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi_users import exceptions as user_exceptions
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.agents import Agent
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.step_up import BypassStepUp, StepUpStatus
from faden_backend.db.models.users import User, UserManager, get_user_manager
from faden_backend.services.agents import step_up
from faden_backend.services.sign_in import accounts, module
from faden_backend.services.sign_in.contract import KEY_PATTERN, ExternalIdentity, ProviderKind, ProviderPublic, Refusal
from faden_backend.web.origin import origin_is_this_site, public_origin

log = logging.getLogger(__name__)

app_router = APIRouter(prefix="/agent-tokens/step-ups", tags=["agents"])
page_router = APIRouter(prefix="/auth/step-up", tags=["auth"])

Key = Annotated[str, Path(pattern=KEY_PATTERN)]


async def _secret(session: AsyncSession) -> str:
    from faden_backend.web.api.agents.views import _secret as agents_secret  # noqa: PLC0415

    return await agents_secret(session)


def _ip(request: Request) -> str | None:
    """The caller's address as uvicorn gives it: behind nginx, the client's (as every other limit)."""
    return step_up.address(request.client.host if request.client else None)


def _refused(exc: step_up.StepUpError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def _too_many(wait: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"code": "too_many_tries", "message": f"Too many tries; wait {wait} s.", "retry_after": wait},
        headers={"Retry-After": str(wait)},
    )


async def _audit(
    audit: AuditDAO, action: str, row: BypassStepUp, *, actor: uuid.UUID | None = None,
    result: str = AuditResult.ALLOW, **more: Any,
) -> None:
    """What the audit trail keeps of a step-up: never a code or the proof."""
    await audit.log(
        action=action,
        target_type="bypass_step_up",
        target_id=str(row.id),
        result=result,
        actor_id=actor,
        severity="high",
        metadata_json=json.dumps(
            {"agent": str(row.agent_id), "device": str(row.device_id), **more}, default=str, ensure_ascii=False,
        ),
    )


# -- the app ---------------------------------------------------------------------------------------


class StartStepUp(BaseModel):
    """What the app says about the Bypass it asks the person to sign in for."""

    #: The longest a Bypass may run that the app's card offers; the page shows it, the proof carries it.
    longest_minutes: int | None = Field(default=None, ge=1, le=1440)
    client_version: str | None = Field(default=None, max_length=64)


class StepUpStarted(BaseModel):
    """The app's code (kept by the app, polled with), the link's code, and the link (RFC 8628 §3.2)."""

    step_up_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@app_router.post("", response_model=StepUpStarted, status_code=status.HTTP_201_CREATED, name="start_bypass_step_up")
async def start_step_up(
    request: Request,
    response: Response,
    payload: StartStepUp,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db_session),
    audit: AuditDAO = Depends(),
) -> StepUpStarted:
    """Start the second sign-in for Bypass: only the card's Allow does this, never the model (sdk/09 L-58)."""
    secret = await _secret(session)
    try:
        who = await step_up.caller(session, secret, authorization)
    except step_up.StepUpError as exc:
        raise _refused(exc) from exc
    now = step_up.now_utc()
    ip = _ip(request)
    wait = await step_up.start_wait(session, who.device_id, ip, now)
    if wait:
        raise _too_many(wait)
    started = await step_up.start(
        session, secret=secret, who=who, longest_minutes=payload.longest_minutes,
        client_version=payload.client_version, ip=ip, now=now,
    )
    await _audit(audit, "bypass_step_up.start", started.row, actor=who.user.id)
    response.headers["Cache-Control"] = "no-store"
    page = f"{public_origin(request)}{step_up.PAGE}"
    code = step_up.shown(started.user_code)
    return StepUpStarted(
        step_up_code=started.step_up_code, user_code=code, verification_uri=page,
        # In the fragment: it never reaches a server's log, nor a Referer.
        verification_uri_complete=f"{page}#code={code}", expires_in=step_up.LIFETIME_S, interval=step_up.INTERVAL_S,
    )


class StepUpPoll(BaseModel):
    step_up_code: str = Field(min_length=16, max_length=128)


@app_router.post("/token", name="bypass_step_up_token", response_model=None)
async def step_up_token(
    payload: StepUpPoll,
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db_session),
    audit: AuditDAO = Depends(),
) -> JSONResponse:
    """The app polls: 400 with an RFC 8628 ``error`` until the person approved, then the proof, once.

    ``proof`` goes to the gateway with the Bypass grant request (``x-faden-step-up``); ``expires_in`` is how long
    it is still good by this server's clock, and the app keeps it no longer, in memory only.
    """
    secret = await _secret(session)
    try:
        who = await step_up.caller(session, secret, authorization)
    except step_up.StepUpError as exc:
        raise _refused(exc) from exc
    try:
        found = await step_up.poll(
            session, secret=secret, who=who, step_up_code=payload.step_up_code, now=step_up.now_utc(),
        )
    except step_up.StepUpError as exc:
        await session.commit()  # the interval, the last poll, an expiry
        body: dict[str, Any] = {"error": exc.code, "error_description": exc.message}
        if exc.interval is not None:
            body["interval"] = exc.interval
        return JSONResponse(body, status_code=exc.status_code, headers={"Cache-Control": "no-store"})
    row = await session.scalar(select(BypassStepUp).where(BypassStepUp.step_up_code_hash == step_up.digest(
        payload.step_up_code,
    )))
    if row is not None:
        await _audit(audit, "bypass_step_up.proof", row, actor=who.user.id, method=found.method)
    return JSONResponse(
        {
            "proof": found.token, "expires_in": found.expires_in, "signed_in_at": found.signed_in_at.isoformat(),
            "method": found.method,
        },
        headers={"Cache-Control": "no-store"},
    )


# -- the page ----------------------------------------------------------------------------------------


def _same_site(request: Request) -> None:
    """Refuse a request from another site's page (CSRF), whatever cookie it carries."""
    claimed = request.headers.get("origin") or request.headers.get("referer")
    if claimed is not None and not origin_is_this_site(claimed, request.headers):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This request comes from another site.")


SameSite = Annotated[None, Depends(_same_site)]


class StepUpState(BaseModel):
    """Where this browser's step-up stands, and what the page shows."""

    state: Literal["sign_in", "decide", "approved", "denied", "expired"]
    #: What the person may sign in with again: the account's password, and the sign-in providers.
    password: bool = True
    providers: list[ProviderPublic] = Field(default_factory=list)
    #: Once signed in again: what they approve.
    agent: str | None = None
    device: str | None = None
    longest_minutes: int | None = None
    signed_in_as: str | None = None
    method: str | None = None
    #: Until when the approval may be given (5 minutes after the sign-in) or the step-up works.
    until: datetime | None = None


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        step_up.COOKIE, token, max_age=step_up.LIFETIME_S, path=step_up.COOKIE_PATH, httponly=True,
        samesite="strict", secure=public_origin(request).startswith("https://"),
    )


async def _state(session: AsyncSession, row: BypassStepUp | None) -> StepUpState:
    """What the page shows for *row*: sign in, decide, or how it ended."""
    if row is None:
        return StepUpState(state="expired", password=False)
    if row.status in (StepUpStatus.APPROVED, StepUpStatus.CONSUMED):
        return StepUpState(state="approved", password=False)
    if row.status == StepUpStatus.DENIED:
        return StepUpState(state="denied", password=False)
    if row.status == StepUpStatus.EXPIRED or step_up.now_utc() >= row.expires_at:
        return StepUpState(state="expired", password=False)
    if row.status == StepUpStatus.SIGNED_IN and row.signed_in_at is not None:
        agent = await session.get(Agent, row.agent_id)
        user = await session.get(User, row.user_id)
        return StepUpState(
            state="decide", password=False, agent=agent.name if agent else None, device=row.device_label,
            longest_minutes=row.longest_minutes, signed_in_as=user.email if user else None, method=row.method,
            until=min(row.expires_at, row.signed_in_at + timedelta(seconds=step_up.FRESH_S)),
        )
    return StepUpState(state="sign_in", providers=await module.public_providers(), until=row.expires_at)


async def _mine(request: Request, session: AsyncSession) -> BypassStepUp:
    row = await step_up.by_browser(session, request.cookies.get(step_up.COOKIE), step_up.now_utc())
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "expired", "message": "This sign-in has ended; start it again in the app."},
        )
    return row


class OpenStepUp(BaseModel):
    code: str = Field(min_length=8, max_length=16)


@page_router.post("/begin", response_model=StepUpState, name="begin_bypass_step_up")
async def begin(
    payload: OpenStepUp, request: Request, response: Response, _: SameSite,
    session: AsyncSession = Depends(get_db_session),
) -> StepUpState:
    """The link's code opens the step-up in this browser; 404 ``expired`` for a wrong, used or expired code."""
    now = step_up.now_utc()
    ip = _ip(request)
    wait = await step_up.failure_wait(session, None, ip, now)
    if wait:
        raise _too_many(wait)
    row = await step_up.find(session, secret=await _secret(session), entered=payload.code, now=now)
    if row is None:
        await step_up.record_failure(session, None, ip, now)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "expired", "message": "This sign-in has ended; start it again in the app."},
        )
    if row.status == StepUpStatus.SIGNED_IN:
        # Signed in already in another browser: only that one decides.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "opened_elsewhere", "message": "This sign-in continues in another browser."},
        )
    _set_cookie(request, response, step_up.bind_browser(row))
    response.headers["Cache-Control"] = "no-store"
    return await _state(session, row)


@page_router.get("", response_model=StepUpState, name="bypass_step_up_state")
async def current(
    request: Request, response: Response, session: AsyncSession = Depends(get_db_session),
) -> StepUpState:
    """Where this browser's step-up stands, ended ones too; ``expired`` without one."""
    cookie = request.cookies.get(step_up.COOKIE)
    row = None
    if cookie:
        row = await session.scalar(select(BypassStepUp).where(BypassStepUp.browser_hash == step_up.digest(cookie)))
    response.headers["Cache-Control"] = "no-store"
    return await _state(session, row)


async def _wrong(
    request: Request, session: AsyncSession, audit: AuditDAO, row: BypassStepUp, reason: str,
) -> HTTPException:
    """A wrong sign-in: counted, recorded, committed; the step-up is denied at the limit."""
    now = step_up.now_utc()
    await step_up.record_failure(session, row.user_id, _ip(request), now)
    denied = step_up.wrong_sign_in(row, now)
    await _audit(audit, "bypass_step_up.sign_in", row, result=AuditResult.DENY, reason=reason, denied=denied)
    await session.commit()
    if denied:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "denied", "message": "Too many wrong sign-ins: this step-up was denied."},
        )
    # The same words for a wrong password and for another person's account: the page names nobody.
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "wrong_credentials", "message": "These are not the sign-in details of the app's account."},
    )


async def _signed_in(
    session: AsyncSession, audit: AuditDAO, row: BypassStepUp, user: User, *, method: str, provider: str | None,
    idp_auth_time: datetime | None,
) -> bool:
    if not step_up.signed_in(
        row, user, method=method, provider=provider, idp_auth_time=idp_auth_time, now=step_up.now_utc(),
    ):
        return False
    await _audit(audit, "bypass_step_up.sign_in", row, actor=user.id, method=method, provider=provider)
    return True


async def _limited(request: Request, session: AsyncSession, row: BypassStepUp) -> None:
    wait = await step_up.failure_wait(session, row.user_id, _ip(request), step_up.now_utc())
    if wait:
        raise _too_many(wait)


class PasswordAgain(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


@page_router.post("/password", response_model=StepUpState, name="bypass_step_up_password")
async def password_again(
    payload: PasswordAgain, request: Request, _: SameSite,
    session: AsyncSession = Depends(get_db_session),
    audit: AuditDAO = Depends(),
    user_manager: UserManager = Depends(get_user_manager),
) -> StepUpState:
    """The account's address and password again, checked as at the sign-in page (its checks, its audit).

    400 ``wrong_credentials`` for a wrong password and for another person's account alike; 400
    ``use_sign_in_provider`` where the organisation wants its people to sign in with a provider.
    """
    row = await _mine(request, session)
    await _limited(request, session, row)
    try:
        user = await user_manager.authenticate(SimpleNamespace(username=payload.email, password=payload.password))
    except HTTPException as exc:
        if exc.detail == "LOGIN_USE_SIGN_IN_PROVIDER":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "use_sign_in_provider", "message": "Sign in with your organisation's provider."},
            ) from exc
        raise
    except user_exceptions.UserNotExists:
        user = None
    if user is None or not await _signed_in(session, audit, row, user, method="password", provider=None,
                                            idp_auth_time=None):
        raise await _wrong(request, session, audit, row, "password")
    await session.commit()
    return await _state(session, row)


class DirectoryAgain(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


@page_router.post("/directory/{key}", response_model=StepUpState, name="bypass_step_up_directory")
async def directory_again(
    key: Key, payload: DirectoryAgain, request: Request, _: SameSite,
    session: AsyncSession = Depends(get_db_session),
    audit: AuditDAO = Depends(),
) -> StepUpState:
    """A directory account's name and password again, checked by the sign-in module (a fresh bind, LDAP)."""
    row = await _mine(request, session)
    await _limited(request, session, row)
    provider = await _provider(key, ProviderKind.PASSWORD)
    try:
        answer = await module.password(provider.key, payload.username, payload.password)
    except (module.ModuleRefusedError, module.ModuleUnavailableError) as exc:
        log.warning("A step-up with %s could not be checked: %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": Refusal.PROVIDER_UNAVAILABLE.value, "message": "The directory cannot be reached."},
        ) from exc
    user = await _linked_user(session, answer.identity) if answer.outcome == "identity" else None
    if user is None or not await _signed_in(session, audit, row, user, method="ldap", provider=provider.key,
                                            idp_auth_time=None):
        raise await _wrong(request, session, audit, row, "directory")
    await session.commit()
    return await _state(session, row)


async def _provider(key: str, kind: ProviderKind) -> ProviderPublic:
    try:
        found = await module.provider(key)
    except module.ModuleUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": Refusal.PROVIDER_UNAVAILABLE.value, "message": "The sign-in provider cannot be reached."},
        ) from exc
    if found is None or found.kind is not kind:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": Refusal.NOT_OFFERED.value, "message": "This sign-in provider is not offered."},
        )
    return found


async def _linked_user(session: AsyncSession, identity: ExternalIdentity | None) -> User | None:
    """The account a provider's identity is linked to; never a new link or account during a step-up."""
    if identity is None:
        return None
    found = await accounts.find_link(session, identity.provider, identity.subject)
    return await session.get(User, found.user_id) if found is not None else None


class SsoAgain(BaseModel):
    redirect_url: str


@page_router.post("/sso/{key}", response_model=SsoAgain, name="bypass_step_up_sso")
async def sso_again(
    key: Key, request: Request, response: Response, _: SameSite,
    session: AsyncSession = Depends(get_db_session),
) -> SsoAgain:
    """Off to a sign-in provider that must authenticate the person anew (OIDC ``prompt=login`` and ``max_age=0``,
    SAML ``ForceAuthn``); its answer comes back to the provider's callback and then to the page.
    """
    from faden_backend.web.api.auth_sso.views import _begin, _set_binding  # noqa: PLC0415

    row = await _mine(request, session)
    await _limited(request, session, row)
    begun = await _begin(request, key, step_up_of=row)
    if isinstance(begun, Refusal):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND if begun is Refusal.NOT_OFFERED else status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": begun.value, "message": "This sign-in provider cannot be used now."},
        )
    provider, redirect_url, binding = begun
    await session.commit()  # the transaction the answer is found by
    _set_binding(request, response, provider, binding)
    return SsoAgain(redirect_url=redirect_url)


def _to_page(refused: str | None = None) -> RedirectResponse:
    from faden_backend.web.api.auth_sso.views import BINDING_COOKIE, BINDING_PATH  # noqa: PLC0415

    response = RedirectResponse(
        f"{step_up.PAGE}?refused={refused}" if refused else step_up.PAGE, status_code=status.HTTP_302_FOUND,
    )
    response.delete_cookie(BINDING_COOKIE, path=BINDING_PATH)
    return response


async def finish_sso(
    session: AsyncSession, claims: dict[str, Any], identity: ExternalIdentity | Refusal, provider: ProviderPublic,
    tx_hash: str,
) -> RedirectResponse | None:
    """A provider's answer to a step-up's sign-in (``auth_sso._finish``): signed in again, or the page says why.

    The binding names the step-up and the browser that began it; the answer must say when the provider
    authenticated the person, after the step-up asked (OIDC ``auth_time``, SAML ``AuthnInstant``; ASVS 5.0 6.8.4),
    and name an account linked to the provider that is the app's person. Nothing is linked or made here.
    Without a binding (*claims* empty: a SAML answer over plain HTTP in development) the step-up is the one that
    began the transaction *tx_hash*; ``None`` when none did, and the answer is an ordinary sign-in.
    """
    audit = AuditDAO(session)
    now = step_up.now_utc()
    if not claims:
        row = await session.scalar(
            select(BypassStepUp).where(BypassStepUp.sso_tx_hash == tx_hash).with_for_update(),
        )
        if row is None or row.sso_asked_at is None:
            return None
        asked_at, browser = row.sso_asked_at, row.browser_hash
    else:
        try:
            step_up_id = uuid.UUID(str(claims["step_up"]))
            asked_at = datetime.fromtimestamp(int(claims["iat"]), UTC)
        except (KeyError, ValueError, TypeError):
            return _to_page("expired")
        row = await session.scalar(select(BypassStepUp).where(BypassStepUp.id == step_up_id).with_for_update())
        browser = claims.get("sb")
    if (
        row is None or row.browser_hash is None or row.browser_hash != browser or row.sso_tx_hash != tx_hash
        or row.status not in (StepUpStatus.PENDING, StepUpStatus.SIGNED_IN) or now >= row.expires_at
    ):
        return _to_page("expired")
    if isinstance(identity, Refusal):
        return _to_page(identity.value)
    if not step_up.fresh_from_provider(identity.authenticated_at, asked_at, now):
        await _audit(audit, "bypass_step_up.sign_in", row, result=AuditResult.DENY, reason="not_fresh",
                     provider=provider.key)
        await session.commit()
        return _to_page("not_fresh")
    user = await _linked_user(session, identity)
    method = "saml" if provider.posts_back else "oidc"
    if user is None or not await _signed_in(session, audit, row, user, method=method, provider=provider.key,
                                            idp_auth_time=identity.authenticated_at):
        await step_up.record_failure(session, row.user_id, None, now)
        step_up.wrong_sign_in(row, now)
        await _audit(audit, "bypass_step_up.sign_in", row, result=AuditResult.DENY, reason="another_account",
                     provider=provider.key)
        await session.commit()
        return _to_page("wrong_account")
    await session.commit()
    return _to_page()


@page_router.post("/approve", response_model=StepUpState, name="approve_bypass_step_up")
async def approve(
    request: Request, _: SameSite,
    session: AsyncSession = Depends(get_db_session),
    audit: AuditDAO = Depends(),
) -> StepUpState:
    """The person approves, within 5 minutes of their sign-in: the app's next poll gets the proof."""
    row = await _mine(request, session)
    try:
        step_up.approve(row, step_up.now_utc())
    except step_up.StepUpError as exc:
        await session.commit()
        raise _refused(exc) from exc
    await _audit(audit, "bypass_step_up.approve", row, actor=row.user_id, method=row.method, provider=row.provider)
    await session.commit()
    return await _state(session, row)


@page_router.post("/deny", response_model=StepUpState, name="deny_bypass_step_up")
async def deny(
    request: Request, _: SameSite,
    session: AsyncSession = Depends(get_db_session),
    audit: AuditDAO = Depends(),
) -> StepUpState:
    """The person denies: the app's next poll answers ``access_denied`` and Bypass stays off."""
    row = await _mine(request, session)
    step_up.deny(row, step_up.now_utc())
    await _audit(audit, "bypass_step_up.deny", row, actor=row.user_id if row.signed_in_at else None)
    await session.commit()
    return await _state(session, row)
