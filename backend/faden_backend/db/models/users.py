# type: ignore
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import jwt
from fastapi import Depends, HTTPException, Request, Response
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
from sqlalchemy import Boolean, DateTime, ForeignKey, String, delete, false, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    # Console sign-ins made before this moment are over: set when the password changes or is reset, when an
    # administrator suspends the account or takes a role away. ``None``: no sign-in was ever ended this way.
    sessions_valid_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class RevokedSignIn(Base):
    """A console sign-in that was ended on purpose (a logout), by its ``jti``.

    A JWT cannot be taken back, so the id of each ended one is kept until the token would have expired anyway;
    after that the signature check refuses it, and the next logout deletes the row.
    """

    __tablename__ = "revoked_sign_ins"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ``None``: the token never expires (``users.login_lifetime_s`` 0), so the row stays.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserRead(schemas.BaseUser[uuid.UUID]):
    """Represents a read command for a user."""

    email: str
    model_config = ConfigDict(from_attributes=True)


class UserCreate(schemas.BaseUserCreate):
    """Represents a create command for a user."""


class UserUpdate(schemas.BaseUserUpdate):
    """Represents an update command for a user.

    ``current_password`` is how a person proves it is them before they change their own email or password
    (``UserManager.update``); it is never written to the account.
    """

    current_password: str | None = None

    def create_update_dict(self) -> dict[str, Any]:
        """The changes a person may make to their own account."""
        changes = super().create_update_dict()
        changes.pop("current_password", None)
        return changes

    def create_update_dict_superuser(self) -> dict[str, Any]:
        """The changes an administrator may make to any account."""
        changes = super().create_update_dict_superuser()
        changes.pop("current_password", None)
        return changes


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """Manages a user session and its tokens."""

    reset_password_token_secret = settings.users_secret
    verification_token_secret = settings.users_secret

    async def validate_password(self, password: str, user: UserCreate | User) -> None:
        """The password rule (services/password_policy.py), for a reset and a change through /api/users/me."""
        problem = password_problem(password, getattr(user, "email", None))
        if problem:
            raise InvalidPasswordException(reason=problem)

    async def update(
        self,
        user_update: UserUpdate,
        user: User,
        safe: bool = False,
        request: Request | None = None,
    ) -> User:
        """A change to an account; a person changing their own email or password (*safe*) proves who they are.

        Without the current password, whoever holds a session (a left-open browser, a stolen cookie) could move
        the account to an address they control, or lock its owner out. An administrator's edit (not *safe*) is
        theirs to make and needs none. An email change marks the address unverified (``_update``).
        """
        if safe:
            sent = user_update.model_fields_set
            changes_email = "email" in sent and user_update.email != user.email
            changes_password = "password" in sent and user_update.password is not None
            if changes_email or changes_password:
                if not user_update.current_password:
                    raise HTTPException(
                        status_code=400,
                        detail="Your current password is required to change your email or password.",
                    )
                verified, _ = self.password_helper.verify_and_update(
                    user_update.current_password, user.hashed_password,
                )
                if not verified:
                    raise HTTPException(status_code=400, detail="Current password is incorrect.")
        return await super().update(user_update, user, safe=safe, request=request)

    async def _update(self, user: User, update_dict: dict[str, Any]) -> User:
        """Apply the changes; one that should end the account's sign-ins does (see :func:`end_sign_ins`)."""
        if (
            update_dict.get("password") is not None
            or update_dict.get("is_active") is False
            or update_dict.get("is_superuser") is False
        ):
            update_dict = {**update_dict, "sessions_valid_after": datetime.now(UTC)}
        return await super()._update(user, update_dict)

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
    """Whether a console token was made since sign-ins expire and can end: it carries ``iat`` and ``jti``.

    Sign-ins from before had no end and no ``iat``, and later ones no ``jti`` to end one by (a logout);
    they are refused, and the person signs in again. The CLI's bootstrap token has both.
    """
    return "iat" in claims and bool(claims.get("jti"))


def sign_in_ended_for_all(claims: dict[str, Any], sessions_valid_after: datetime | None) -> bool:
    """Whether the account's sign-ins were ended (``end_sign_ins``) after this one was made.

    ``iat`` has whole seconds: a sign-in made in the second the others were ended still stands.
    """
    if sessions_valid_after is None:
        return False
    try:
        return int(claims["iat"]) < int(sessions_valid_after.timestamp())
    except (KeyError, TypeError, ValueError):
        return True


def end_sign_ins(user: User) -> None:
    """End all of *user*'s console sign-ins (browser cookies, apps signed in with a bearer token) from now on.

    Called for a password change, a suspension and a role taken away; ``UserManager._update`` does it for the
    stock routes. API tokens are separate keys with their own revoke, and agent tokens their own refresh.
    """
    user.sessions_valid_after = datetime.now(UTC)


async def is_sign_in_revoked(session: AsyncSession, jti: str) -> bool:
    """Whether the sign-in with *jti* was ended by a logout. One read of the table's primary key."""
    return (await session.scalar(select(RevokedSignIn.jti).where(RevokedSignIn.jti == jti))) is not None


class SignInStrategy(JWTStrategy):
    """Console sign-ins: a JWT with ``iat`` and ``jti``, and ``exp`` after the configured lifetime.

    Every request is checked against the account (``sessions_valid_after``, read with the user) and, when a
    session is at hand, against the logouts (``revoked_sign_ins``). That is one more primary-key read per
    request and no cache here, so a logout holds on every worker at once. The gateway caches its answers
    for 30 s instead (it reads the backend database over a pool of its own).
    """

    def __init__(self, *args: Any, session: AsyncSession | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._session = session

    async def write_token(self, user: Any) -> str:
        """A sign-in for *user*, with ``iat`` and a ``jti`` to end it by."""
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "iat": int(time.time()),
            "jti": uuid.uuid4().hex,
        }
        return generate_jwt(data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm)

    async def read_token(self, token: str | None, user_manager: Any) -> Any:
        """The signed-in user, or ``None``.

        Refused: a sign-in from before sign-ins expired (no ``iat``) or could end (no ``jti``), one that
        was ended with the account's others, and one that was logged out. An API token (``typ`` ``api``)
        has its own row and revoke, and stays out of both.
        """
        if token is None:
            return None
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return None
        if not is_current_sign_in(claims):
            return None
        user = await super().read_token(token, user_manager)
        if user is None or claims.get("typ") == "api":
            return user
        if sign_in_ended_for_all(claims, user.sessions_valid_after):
            return None
        if self._session is not None and await is_sign_in_revoked(self._session, str(claims["jti"])):
            return None
        return user

    async def destroy_token(self, token: str, user: Any) -> None:
        """Log out: keep this sign-in's ``jti`` until it would have expired anyway."""
        if self._session is None:
            return
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return
        jti = claims.get("jti")
        if not jti or claims.get("typ") == "api":
            return
        exp = claims.get("exp")
        now = datetime.now(UTC)
        await self._session.execute(delete(RevokedSignIn).where(RevokedSignIn.expires_at < now))
        await self._session.execute(
            pg_insert(RevokedSignIn)
            .values(jti=str(jti), user_id=user.id, expires_at=datetime.fromtimestamp(int(exp), UTC) if exp else None)
            .on_conflict_do_nothing(),
        )


def get_jwt_strategy() -> JWTStrategy:
    """
    Return a JWTStrategy in order to instantiate it dynamically.

    Read on every sign-in, so a changed lifetime applies to the next one. This one has no database
    session: it makes tokens and reads them without the logout list; requests get
    :func:`get_request_strategy`.

    :returns: instance of JWTStrategy with provided settings.
    """
    return SignInStrategy(
        secret=settings.users_secret,
        lifetime_seconds=login_lifetime_seconds(),
    )


def get_request_strategy(session: AsyncSession = Depends(get_db_session)) -> JWTStrategy:
    """The strategy for a request: :func:`get_jwt_strategy`, with the request's session for the logouts."""
    return SignInStrategy(
        secret=settings.users_secret,
        lifetime_seconds=login_lifetime_seconds(),
        session=session,
    )


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")
auth_jwt = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_request_strategy,
)
cookie_transport = CookieTransport(
    cookie_name="fapiauth",
    # The cookie lasts as long as the token in it; a changed lifetime applies after a restart.
    cookie_max_age=login_lifetime_seconds(),
    cookie_secure=settings.cookie_secure,
)
auth_cookie = AuthenticationBackend(name="cookie", transport=cookie_transport, get_strategy=get_request_strategy)

# The first that yields a user wins: a token the caller sends on purpose before the browser's ambient cookie.
# Cookie first, an app in a browser where someone is signed in to the console (an extension, a web add-in) acted
# as that person instead of the one who signed in to the app (found on .61, 4 October 2026).
backends = [
    auth_jwt,
    auth_cookie,
]

def set_sign_in_cookie(response: Response, token: str) -> None:
    """Put *token* in the console's sign-in cookie, as the login route does."""
    response.set_cookie(
        cookie_transport.cookie_name,
        token,
        max_age=cookie_transport.cookie_max_age,
        path="/",
        secure=cookie_transport.cookie_secure,
        httponly=True,
        samesite="lax",
    )


api_users = FastAPIUsers[User, uuid.UUID](get_user_manager, backends)

current_active_user = api_users.current_user(active=True)
