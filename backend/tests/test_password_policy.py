"""One rule for every new password: at least 8 characters, and not the account's e-mail.

Bootstrap took 6 characters, an administrator's new user and a password change 6, and a reset or a change through
``/api/users/me`` any length at all: ``UserManager`` never checked one.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi_users import InvalidPasswordException
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.users import UserCreate, UserManager
from faden_backend.services.password_policy import MAX_LENGTH, MIN_LENGTH, password_problem
from faden_backend.settings import settings
from faden_backend.web.api.bootstrap.views import BOOTSTRAP_TOKEN_HEADER
from tests.test_api_tokens import _As, _user, signed_in  # noqa: F401 - fixture used by name


def test_the_rule() -> None:
    assert MIN_LENGTH == 8
    assert password_problem("x" * (MIN_LENGTH - 1)) is not None
    assert password_problem("x" * MIN_LENGTH) is None
    assert password_problem("x" * (MAX_LENGTH + 1)) is not None
    assert password_problem("Someone@Example.com", "someone@example.com") is not None
    assert password_problem("a-long-password-1", "someone@example.com") is None


@pytest.mark.anyio
async def test_a_reset_or_change_through_fastapi_users_meets_it() -> None:
    manager = UserManager(user_db=None)  # type: ignore[arg-type]
    user = UserCreate(email="someone@example.com", password="whatever")
    with pytest.raises(InvalidPasswordException):
        await manager.validate_password("short", user)
    with pytest.raises(InvalidPasswordException):
        await manager.validate_password("someone@example.com", user)
    await manager.validate_password("a-long-password-1", user)


@pytest.mark.anyio
async def test_bootstrap_refuses_a_short_password(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bootstrap_token", "the-install-bootstrap-token")
    r = await client.post(
        "/api/bootstrap",
        json={"email": "first@example.com", "password": "six-ch"},
        headers={BOOTSTRAP_TOKEN_HEADER: "the-install-bootstrap-token"},
    )
    assert r.status_code == 422, r.text


@pytest.mark.anyio
async def test_bootstrap_refuses_the_email_as_password(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "bootstrap_token", "the-install-bootstrap-token")
    r = await client.post(
        "/api/bootstrap",
        json={"email": "first@example.com", "password": "first@example.com"},
        headers={BOOTSTRAP_TOKEN_HEADER: "the-install-bootstrap-token"},
    )
    assert r.status_code == 422, r.text
    assert "e-mail" in r.text


@pytest.mark.anyio
async def test_an_administrator_cannot_give_a_new_user_a_short_password(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    await RbacDAO(dbsession).seed_defaults()
    signed_in.user = await _user(dbsession, admin=True)
    email = f"new-colleague-{uuid.uuid4().hex[:8]}@example.com"

    short = await client.post("/api/admin/users/", json={"email": email, "password": "seven-c"})
    own_email = await client.post("/api/admin/users/", json={"email": email, "password": email})
    fine = await client.post("/api/admin/users/", json={"email": email, "password": "eight-ch"})

    assert short.status_code == 422, short.text
    assert own_email.status_code == 422, own_email.text
    assert fine.status_code == 201, fine.text


@pytest.mark.anyio
async def test_a_password_change_meets_it(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    user = await _user(dbsession)
    user.hashed_password = PasswordHelper().hash("the-old-password")
    await dbsession.flush()
    signed_in.user = user

    short = await client.post(
        "/api/admin/users/me/change-password",
        json={"current_password": "the-old-password", "new_password": "short"},
    )
    as_email = await client.post(
        "/api/admin/users/me/change-password",
        json={"current_password": "the-old-password", "new_password": user.email},
    )

    assert short.status_code == 422, short.text
    assert as_email.status_code == 422, as_email.text
