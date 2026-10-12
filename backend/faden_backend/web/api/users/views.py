from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from faden_backend.db.models.users import (
    UserRead,  # type: ignore
    UserUpdate,  # type: ignore
    api_users,  # type: ignore
    auth_cookie,  # type: ignore
    auth_jwt,  # type: ignore
    get_jwt_strategy,  # type: ignore
    login_lifetime_seconds,
)
from faden_backend.settings import settings

router = APIRouter()

# No register router: accounts are made by an administrator (POST /api/admin/users/, the console's Users
# page), by bootstrap for the first one, or by a sign-in provider set to create them. With it mounted at
# POST /api/auth/register, anyone who reached the server could make an account with the default role.

router.include_router(
    api_users.get_reset_password_router(),
    prefix="/auth",
    tags=["auth"],
)

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
