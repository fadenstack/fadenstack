import contextlib
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi_users import exceptions
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import (
    UserManager,
    UserRead,  # type: ignore
    UserUpdate,  # type: ignore
    api_users,  # type: ignore
    auth_cookie,  # type: ignore
    auth_jwt,  # type: ignore
    get_jwt_strategy,  # type: ignore
    get_user_manager,
    login_lifetime_seconds,
)
from faden_backend.services import password_reset_links
from faden_backend.services.extensions import mail_available
from faden_backend.settings import settings
from faden_backend.web.api import about

router = APIRouter()

# No register router: accounts are made by an administrator (POST /api/admin/users/, the console's Users
# page), by bootstrap for the first one, or by a sign-in provider set to create them. With it mounted at
# POST /api/auth/register, anyone who reached the server could make an account with the default role.

reset_router = api_users.get_reset_password_router()
# Not the stock forgot-password: it checks the address as an email address on the internet, which refuses
# special-use domains (.local, .test, .internal and others) that accounts are made with (a company on
# firma.local), so such people could never reset their password. This one takes the address as accounts are
# made with it, and answers the same whether or not an account has it.
reset_router.routes[:] = [r for r in reset_router.routes if getattr(r, "name", "") != "reset:forgot_password"]


@reset_router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED, name="reset:forgot_password")
async def forgot_password(
    request: Request,
    email: str = Body(..., embed=True, min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$"),
    user_manager: UserManager = Depends(get_user_manager),
) -> None:
    """Mail a reset link to the account with *email*, if there is one; 202 either way."""
    try:
        user = await user_manager.get_by_email(email)
    except exceptions.UserNotExists:
        return
    with contextlib.suppress(exceptions.UserInactive):
        await user_manager.forgot_password(user, request)


router.include_router(reset_router, prefix="/auth", tags=["auth"])

router.include_router(
    api_users.get_verify_router(UserRead),
    prefix="/auth",
    tags=["auth"],
)

users_router = api_users.get_users_router(UserRead, UserUpdate)
# Not the stock PATCH and DELETE of another account (/users/{id}): they can deactivate, demote or delete any
# account, the administrator's own and the last superuser's included, and cannot tell who is asking. The
# Users page uses /api/admin/users/*, which guards both; there is no second way to do the same.
users_router.routes[:] = [
    route
    for route in users_router.routes
    if not (getattr(route, "path", "") == "/{id}" and getattr(route, "methods", set()) & {"PATCH", "DELETE"})
]
router.include_router(users_router, prefix="/users", tags=["users"])
router.include_router(api_users.get_auth_router(auth_jwt), prefix="/auth/jwt", tags=["auth"])
router.include_router(api_users.get_auth_router(auth_cookie), prefix="/auth/cookie", tags=["auth"])


class SignInOptions(BaseModel):
    """What the sign-in page may offer; no account is named."""

    #: A forgotten password can be reset by mail (a mail sender is on); else an administrator sets a new one.
    password_reset: bool
    #: The page links back to fadenstack.com (as ``GET /api/about`` says once signed in).
    vendor_links: bool = True


@router.get("/auth/options", response_model=SignInOptions, tags=["auth"])
async def sign_in_options() -> SignInOptions:
    """What the sign-in page may offer, before anyone signs in."""
    return SignInOptions(password_reset=mail_available(), vendor_links=about.vendor_links())


class ResetLinkToken(BaseModel):
    """The token from a password reset link an administrator made (``services/password_reset_links.py``)."""

    token: str = Field(min_length=16, max_length=128)


class ResetLinkPreview(BaseModel):
    """Whose password the link sets, and until when it works."""

    email: str
    expires_at: datetime


class ResetLinkUse(ResetLinkToken):
    """The token and the new password."""

    password: str = Field(min_length=1, max_length=1024)


@router.post("/auth/reset-link/preview", response_model=ResetLinkPreview, tags=["auth"])
async def reset_link_preview(
    payload: ResetLinkToken, session: AsyncSession = Depends(get_db_session),
) -> ResetLinkPreview:
    """Whose password the link sets; 404 ``expired`` once it no longer works. Changes nothing.

    The token goes in the body: the page reads it from the address's fragment, which never reaches a server.
    """
    found = await password_reset_links.pending(session, payload.token)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="expired")
    return ResetLinkPreview(email=found[0], expires_at=found[1])


@router.post("/auth/reset-link/use", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
async def reset_link_use(payload: ResetLinkUse, session: AsyncSession = Depends(get_db_session)) -> None:
    """Set the new password, once; every sign-in of the account ends and nobody is signed in.

    400 ``expired`` when the link no longer works; 422 with the password rule's words when the password breaks
    it (the link stays usable).
    """
    try:
        await password_reset_links.use(session, payload.token, payload.password)
    except password_reset_links.LinkExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expired") from exc
    except password_reset_links.PasswordRejectedError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.post("/auth/dev-login", tags=["auth"])
async def dev_login(request: Request) -> Response:
    """One-click dev login — sets a cookie token for admin@localhost.

    Only available when ENVIRONMENT=dev.
    """
    if settings.environment != "dev":
        return JSONResponse({"detail": "Not available"}, status_code=404)

    from sqlalchemy import select  # noqa: PLC0415

    from faden_backend.db.models.users import User  # noqa: PLC0415

    async with request.app.state.db_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == "admin@localhost"),  # type: ignore[arg-type]
        )
        user = result.scalars().first()
        if user is None:
            return JSONResponse(
                {"detail": "Dev user not seeded. Restart the backend."},
                status_code=500,
            )

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

    response = JSONResponse(
        {"token": token, "email": user.email},
    )
    # Set both cookie (for browser) and return token (for programmatic use)
    response.set_cookie(
        key="fapiauth",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=login_lifetime_seconds(),
        path="/",
    )
    return response
