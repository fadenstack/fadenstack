# type: ignore
import logging
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import jwt
from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, InvalidPasswordException, UUIDIDMixin, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from fastapi_users.jwt import generate_jwt
from pydantic import ConfigDict
from sqlalchemy import Boolean, DateTime, ForeignKey, false
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base
from faden_backend.db.dependencies import get_db_session
from faden_backend.services.password_policy import password_problem
from faden_backend.settings import settings

log = logging.getLogger(__name__)


class User(SQLAlchemyBaseUserTableUUID, Base):
    """Represents a user entity."""

    # The group this user's LLM usage is attributed to -- for usage reports,
    # and for the budgets the enterprise edition builds on them. Group
    # membership stays RBAC; this is the one answer to "whose usage is this".
    usage_group_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Developer mode: set by an admin, never by the user. Agents let such a
    # user attach any local MCP server on their own machine; it never widens
    # what the server allows. Until ``developer_mode_until`` when set.
    developer_mode: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    developer_mode_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class UserRead(schemas.BaseUser[uuid.UUID]):
    """Represents a read command for a user."""

    email: str
    model_config = ConfigDict(from_attributes=True)


class UserCreate(schemas.BaseUserCreate):
    """Represents a create command for a user."""


class UserUpdate(schemas.BaseUserUpdate):
    """Represents an update command for a user."""


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """Manages a user session and its tokens."""

    reset_password_token_secret = settings.users_secret
    verification_token_secret = settings.users_secret

    async def validate_password(self, password: str, user: UserCreate | User) -> None:
        """The password rule (services/password_policy.py), for a reset and a change through /api/users/me."""
        problem = password_problem(password, getattr(user, "email", None))
        if problem:
            raise InvalidPasswordException(reason=problem)

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        """Assign default RBAC role to newly registered non-superusers.

        Runs for every account made through ``UserManager.create``, such as one a sign-in module's
        OAuth callback creates. There is no self-registration route; the admin API and bootstrap
        create their accounts and give them their roles themselves.
        """
        if user.is_superuser:
            return

        session = getattr(self.user_db, "session", None)
        if not isinstance(session, AsyncSession):
            log.warning("on_after_register skipped: SQLAlchemy session unavailable")
            return

        try:
            from faden_backend.db.dao.rbac_dao import RbacDAO  # noqa: PLC0415

            rbac_dao = RbacDAO(session)
            default_role = await rbac_dao.get_role_by_name("default_user")
            if default_role is None:
                log.warning("on_after_register skipped: default_user role not found")
                return
            await rbac_dao.assign_role(user.id, default_role.id)
            await session.flush()
        except Exception:
            log.exception("Failed to assign default_user role after registration")

    async def on_after_forgot_password(
        self,
        user: User,
        token: str,
        request: Request | None = None,
    ) -> None:
        """Enqueue password-reset email delivery via notification outbox."""
        try:
            from faden_backend.services.notifications import NotificationService  # noqa: PLC0415
        except Exception:
            log.exception("Failed to import NotificationService in forgot-password hook.")
            return

        session = getattr(self.user_db, "session", None)
        if not isinstance(session, AsyncSession):
            log.warning("Forgot-password hook skipped: SQLAlchemy session is unavailable.")
            return

        try:
            base = settings.mailer_frontend_base_url.rstrip("/")
            query = urlencode({"token": token})
            reset_url = f"{base}/reset-password?{query}"
            request_id = str(uuid.uuid4())
            if request is not None:
                request_id = (
                    request.headers.get("x-request-id")
                    or request.headers.get("x-correlation-id")
                    or request_id
                )
            to_name = user.email.split("@", 1)[0] if user.email else None
            service = NotificationService(session)
            await service.enqueue_password_reset(
                to_email=user.email,
                to_name=to_name,
                reset_url=reset_url,
                request_id=request_id,
            )
        except Exception:
            # Keep forgot-password response enumeration-safe even if enqueue fails.
            log.exception("Failed to enqueue forgot-password notification.")


async def get_user_db(
    session: AsyncSession = Depends(get_db_session),
) -> SQLAlchemyUserDatabase:
    """
    Yield a SQLAlchemyUserDatabase instance.

    :param session: asynchronous SQLAlchemy session.
    :yields: instance of SQLAlchemyUserDatabase.
    """
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> UserManager:
    """
    Yield a UserManager instance.

    :param user_db: SQLAlchemy user db instance
    :yields: an instance of UserManager.
    """
    yield UserManager(user_db)


def login_lifetime_seconds() -> int | None:
    """How long a sign-in lasts (``users.login_lifetime_s``); ``None``: it never expires."""
    lifetime = settings.users_login_lifetime_s
    return lifetime if lifetime > 0 else None


def is_current_sign_in(claims: dict[str, Any]) -> bool:
    """Whether a console token was made since sign-ins expire: it carries ``iat``.

    Sign-ins from before had no end and no ``iat``; they are refused, and the
    person signs in again. The CLI's bootstrap token has an ``iat`` too.
    """
    return "iat" in claims


class SignInStrategy(JWTStrategy):
    """Console sign-ins: a JWT with ``iat``, and ``exp`` after the configured lifetime."""

    async def write_token(self, user: Any) -> str:
        """A sign-in for *user*, with ``iat``."""
        data = {"sub": str(user.id), "aud": self.token_audience, "iat": int(time.time())}
        return generate_jwt(data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm)

    async def read_token(self, token: str | None, user_manager: Any) -> Any:
        """The signed-in user, or ``None``; a sign-in without ``iat`` is from before and refused."""
        if token is None:
            return None
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return None
        if not is_current_sign_in(claims):
            return None
        return await super().read_token(token, user_manager)


def get_jwt_strategy() -> JWTStrategy:
    """
    Return a JWTStrategy in order to instantiate it dynamically.

    Read on every sign-in, so a changed lifetime applies to the next one.

    :returns: instance of JWTStrategy with provided settings.
    """
    return SignInStrategy(
        secret=settings.users_secret,
        lifetime_seconds=login_lifetime_seconds(),
    )


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")
auth_jwt = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)
cookie_transport = CookieTransport(
    cookie_name="fapiauth",
    # The cookie lasts as long as the token in it; a changed lifetime applies after a restart.
    cookie_max_age=login_lifetime_seconds(),
    cookie_secure=settings.cookie_secure,
)
auth_cookie = AuthenticationBackend(name="cookie", transport=cookie_transport, get_strategy=get_jwt_strategy)

# The first that yields a user wins: a token the caller sends on purpose before the browser's ambient cookie.
# Cookie first, an app in a browser where someone is signed in to the console (an extension, a web add-in) acted
# as that person instead of the one who signed in to the app (found on .61, 4 October 2026).
backends = [
    auth_jwt,
    auth_cookie,
]

api_users = FastAPIUsers[User, uuid.UUID](get_user_manager, backends)

current_active_user = api_users.current_user(active=True)
