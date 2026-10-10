"""Bootstrap endpoint — one-shot first-admin creation.

Available *only* when zero users exist in the database, and only to a caller
that sends the install's bootstrap token (``FADEN_BACKEND_BOOTSTRAP_TOKEN``,
which ``faden deploy`` writes into ``.env``) in the ``X-Faden-Bootstrap-Token``
header. After the first admin is created the endpoints return 409 Conflict
for all subsequent calls, making them self-disabling.

Without that token the first administrator went to whoever asked first:
deploy opens ports 80 and 443 before it bootstraps. (It is not the hour-long
sign-in bootstrap hands back, which the CLI finishes setting up with.)
"""

from __future__ import annotations

import hmac
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User
from faden_backend.services.password_policy import MAX_LENGTH, MIN_LENGTH, password_problem
from faden_backend.settings import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])

#: How long the setup token lasts: the CLI finishes setting up with it, and never needs it again.
BOOTSTRAP_TOKEN_TTL_S = 3600

#: The header the install's bootstrap token comes in.
BOOTSTRAP_TOKEN_HEADER = "X-Faden-Bootstrap-Token"  # noqa: S105 - a header name

#: The transaction lock two bootstraps at once queue on (``pg_advisory_xact_lock``): the second one
#: counts after the first has committed, and finds its administrator.
BOOTSTRAP_LOCK_KEY = 0x6661_6465_6E62_7374  # "fadenbst"

_REFUSED_UNCONFIGURED = (
    "Bootstrap is refused: this server has no bootstrap token. Set FADEN_BACKEND_BOOTSTRAP_TOKEN in the "
    "install's .env to a long random value (for example the output of `openssl rand -base64 48`), "
    "restart the backend (`faden up`), then run `faden deploy` again, which sends it."
)
_REFUSED_WRONG = f"Bootstrap is refused: the {BOOTSTRAP_TOKEN_HEADER} header is missing or wrong."


# ── Schemas ────────────────────────────────────────────────────────


class BootstrapStatusResponse(BaseModel):
    """Whether the system needs initial admin setup."""

    needs_bootstrap: bool


class BootstrapRequest(BaseModel):
    """Payload for first-admin creation."""

    email: str = Field("admin@localhost", min_length=3, max_length=320)
    password: str | None = Field(
        default=None,
        description="Admin password.  Auto-generated if omitted.",
        min_length=MIN_LENGTH,
        max_length=MAX_LENGTH,
    )
    generate_api_token: bool = Field(
        default=True,
        description="Generate an LLM gateway API token for the new admin.",
    )
    tenant_id: str = Field(
        default="default",
        min_length=1,
        max_length=128,
    )


class BootstrapResponse(BaseModel):
    """Credentials returned exactly once after bootstrap."""

    email: str
    password: str
    api_token: str | None = None


# ── Helpers ────────────────────────────────────────────────────────


def _generate_password(length: int = 20) -> str:
    """Generate a URL-safe random password."""
    return secrets.token_urlsafe(length)


async def _user_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(User))
    return result.scalar_one()


def check_bootstrap_token(sent: str | None) -> None:
    """Refuse a bootstrap without the install's bootstrap token; compared in constant time."""
    expected = settings.bootstrap_token.strip()
    if not expected:
        log.warning("Bootstrap refused: FADEN_BACKEND_BOOTSTRAP_TOKEN is not set.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_REFUSED_UNCONFIGURED)
    if not hmac.compare_digest((sent or "").strip().encode("utf-8"), expected.encode("utf-8")):
        log.warning("Bootstrap refused: wrong or missing bootstrap token.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_REFUSED_WRONG)


async def create_first_admin(session: AsyncSession, payload: BootstrapRequest) -> tuple[User, str]:
    """Create the first administrator, or 409 when there is one; ``(user, password)``.

    Counting and creating run under a transaction lock: two requests at once
    both counted no users, and both made a superuser.
    """
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    # The rule every new password meets (services/password_policy.py); a generated one always does.
    if payload.password:
        problem = password_problem(payload.password, payload.email)
        if problem:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BOOTSTRAP_LOCK_KEY})
    if await _user_count(session) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="System is already bootstrapped. Admin users exist.",
        )

    raw_password = payload.password or _generate_password()
    admin_user = User(
        id=uuid.uuid4(),
        email=payload.email,
        hashed_password=PasswordHelper().hash(raw_password),
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    session.add(admin_user)
    await session.flush()

    # ── Assign the built-in admin role ────────────────────────
    from faden_backend.db.dao.rbac_dao import RbacDAO  # noqa: PLC0415

    rbac_dao = RbacDAO(session)
    admin_role = await rbac_dao.get_role_by_name("admin")
    if admin_role:
        await rbac_dao.assign_role(admin_user.id, admin_role.id)
        await session.flush()
    return admin_user, raw_password


# ── Endpoints ──────────────────────────────────────────────────────


@router.get(
    "/status",
    response_model=BootstrapStatusResponse,
    name="bootstrap_status",
)
async def bootstrap_status(
    session: AsyncSession = Depends(get_db_session),
) -> BootstrapStatusResponse:
    """Check whether the system needs initial admin setup."""
    count = await _user_count(session)
    return BootstrapStatusResponse(needs_bootstrap=count == 0)


@router.post(
    "",
    response_model=BootstrapResponse,
    status_code=status.HTTP_201_CREATED,
    name="bootstrap",
)
async def bootstrap(
    payload: BootstrapRequest,
    session: AsyncSession = Depends(get_db_session),
    bootstrap_token: str | None = Header(default=None, alias=BOOTSTRAP_TOKEN_HEADER),
) -> BootstrapResponse:
    """Create the first admin user and optionally generate an API token.

    This endpoint is **only** available when the ``user`` table is empty,
    and to a caller with the install's bootstrap token in the
    ``X-Faden-Bootstrap-Token`` header. Once the first admin is created, all
    subsequent calls return ``409 Conflict``.
    """
    # ── Guard: the install's bootstrap token, then zero users ─
    check_bootstrap_token(bootstrap_token)
    admin_user, raw_password = await create_first_admin(session, payload)

    # ── Optional: generate API gateway token ──────────────────
    api_token: str | None = None
    if payload.generate_api_token:
        import time  # noqa: PLC0415

        import jwt as pyjwt  # noqa: PLC0415

        from faden_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415

        # Resolve the JWT signing secret (same logic as the token endpoint).
        # Use the secret WITHOUT stripping — JWTStrategy uses it as-is.
        secret = settings.users_secret if settings.users_secret else ""
        if not secret and settings.settings_master_key:
            row = await session.execute(
                text("SELECT ciphertext FROM system_setting_secret WHERE key = :k"),
                {"k": "faden_backend.users_secret"},
            )
            ciphertext = row.scalar_one_or_none()
            if ciphertext:
                try:
                    secret = SettingsCrypto(settings.settings_master_key).decrypt(ciphertext).strip()
                except Exception:
                    secret = ""

        if secret:
            now = int(time.time())
            # A sign-in for the CLI to finish setting up with (an enrollment token for the local machine),
            # good for an hour. It had no end: a superuser's sign-in for good, which nothing could revoke.
            claims = {
                "sub": str(admin_user.id),
                "aud": "fastapi-users:auth",
                "tenant_id": payload.tenant_id,
                "email": admin_user.email,
                "iat": now,
                "exp": now + BOOTSTRAP_TOKEN_TTL_S,
                "jti": uuid.uuid4().hex,
            }
            api_token = pyjwt.encode(claims, secret, algorithm="HS256")
        else:
            log.warning(
                "JWT secret not available during bootstrap — "
                "API token not generated. Generate one manually via the admin UI."
            )

    log.info("System bootstrapped: admin user '%s' created.", payload.email)

    return BootstrapResponse(
        email=payload.email,
        password=raw_password,
        api_token=api_token,
    )
