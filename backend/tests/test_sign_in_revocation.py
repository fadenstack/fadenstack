"""A console sign-in can be ended: by a logout, and for all of an account's by a password change, a reset,
a suspension or a role taken away. Before, a sign-in lived out its seven days whatever happened to the account.
"""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from fastapi import FastAPI
from fastapi_users.jwt import generate_jwt
from fastapi_users.password import PasswordHelper
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.users import RevokedSignIn, User, current_active_user, end_sign_ins, get_jwt_strategy
from faden_backend.settings import settings
from tests.test_api_tokens import _As, _user, signed_in  # noqa: F401 - fixture used by name

PASSWORD = "the-old-password"  # noqa: S105


@pytest.fixture(autouse=True)
def _secret() -> None:
    if not settings.users_secret:
        object.__setattr__(settings, "users_secret", "test-sign-in-secret-0123456789abcdef")


async def _person(db: AsyncSession) -> User:
    user = await _user(db)
    user.hashed_password = PasswordHelper().hash(PASSWORD)
    await db.flush()
    return user


async def _token(user: User) -> str:
    return await get_jwt_strategy().write_token(user)


async def _me(client: AsyncClient, token: str, *, as_cookie: bool = False) -> int:
    if as_cookie:
        return (await client.get("/api/users/me", cookies={"fapiauth": token})).status_code
    return (await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})).status_code


@pytest.mark.anyio
async def test_a_sign_in_carries_a_jti_of_its_own(dbsession: AsyncSession) -> None:
    user = await _person(dbsession)
    claims = [
        jwt.decode(await _token(user), settings.users_secret, algorithms=["HS256"], audience="fastapi-users:auth")
        for _ in range(2)
    ]
    assert claims[0]["jti"] and claims[0]["jti"] != claims[1]["jti"]


@pytest.mark.anyio
async def test_a_sign_in_without_a_jti_is_refused(client: AsyncClient, dbsession: AsyncSession) -> None:
    user = await _person(dbsession)
    old = jwt.encode(
        {"sub": str(user.id), "aud": ["fastapi-users:auth"], "iat": int(time.time())},
        settings.users_secret,
        algorithm="HS256",
    )
    assert await _me(client, old) == 401


@pytest.mark.anyio
async def test_logout_ends_that_sign_in_and_only_that_one(client: AsyncClient, dbsession: AsyncSession) -> None:
    user = await _person(dbsession)
    here, elsewhere = await _token(user), await _token(user)
    assert await _me(client, here, as_cookie=True) == 200

    r = await client.post("/api/auth/cookie/logout", cookies={"fapiauth": here})
    assert r.status_code == 204, r.text

    assert await _me(client, here, as_cookie=True) == 401
    assert await _me(client, here) == 401, "the same token as a bearer token is over too"
    assert await _me(client, elsewhere) == 200, "the person's other sign-in stands"
    rows = (await dbsession.execute(select(RevokedSignIn).where(RevokedSignIn.user_id == user.id))).scalars().all()
    assert len(rows) == 1 and rows[0].expires_at is not None


@pytest.mark.anyio
async def test_a_bearer_logout_ends_the_bearer_sign_in(client: AsyncClient, dbsession: AsyncSession) -> None:
    user = await _person(dbsession)
    token = await _token(user)
    r = await client.post("/api/auth/jwt/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (200, 204), r.text
    assert await _me(client, token) == 401


@pytest.mark.anyio
async def test_a_logout_row_of_an_expired_sign_in_goes_with_the_next_logout(
    client: AsyncClient, dbsession: AsyncSession,
) -> None:
    user = await _person(dbsession)
    await client.post("/api/auth/cookie/logout", cookies={"fapiauth": await _token(user)})
    row = (await dbsession.execute(select(RevokedSignIn).where(RevokedSignIn.user_id == user.id))).scalar_one()
    row.expires_at = row.expires_at.replace(year=2020)
    await dbsession.flush()
    await client.post("/api/auth/cookie/logout", cookies={"fapiauth": await _token(user)})
    rows = (await dbsession.execute(select(RevokedSignIn).where(RevokedSignIn.user_id == user.id))).scalars().all()
    assert [r.jti for r in rows] != [row.jti] and len(rows) == 1


@pytest.mark.anyio
async def test_a_password_change_ends_every_sign_in_and_renews_the_browsers(
    client: AsyncClient, dbsession: AsyncSession,
) -> None:
    user = await _person(dbsession)
    browser, app = await _token(user), await _token(user)
    # Sign-ins have whole-second ``iat``: leave the second the others were made in.
    time.sleep(1.1)

    r = await client.post(
        "/api/admin/users/me/change-password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
        cookies={"fapiauth": browser},
    )
    assert r.status_code == 204, r.text
    renewed = r.cookies.get("fapiauth")
    assert renewed and renewed != browser, "this browser stays signed in, with a new sign-in"

    assert await _me(client, browser, as_cookie=True) == 401
    assert await _me(client, app) == 401
    assert await _me(client, renewed, as_cookie=True) == 200


@pytest.mark.anyio
async def test_a_password_reset_ends_every_sign_in(client: AsyncClient, dbsession: AsyncSession) -> None:
    user = await _person(dbsession)
    token = await _token(user)
    time.sleep(1.1)
    reset = generate_jwt(
        {"sub": str(user.id), "password_fgpt": PasswordHelper().hash(user.hashed_password), "aud": "fastapi-users:reset"},
        settings.users_secret,
        600,
    )
    r = await client.post("/api/auth/reset-password", json={"token": reset, "password": "a-brand-new-password"})
    assert r.status_code == 200, r.text
    assert await _me(client, token) == 401


@pytest.mark.anyio
async def test_a_suspension_ends_the_sign_ins_for_good(
    signed_in: _As, fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    person = await _person(dbsession)
    token = await _token(person)
    time.sleep(1.1)
    signed_in.user = await _user(dbsession, admin=True)

    assert (await client.put(f"/api/admin/users/{person.id}/active", json={"active": False})).status_code == 200
    assert (await client.put(f"/api/admin/users/{person.id}/active", json={"active": True})).status_code == 200

    fastapi_app.dependency_overrides.pop(current_active_user, None)
    assert await _me(client, token) == 401, "reactivating does not bring the old sign-ins back"
    assert await _me(client, await _token(person)) == 200


@pytest.mark.anyio
async def test_taking_a_role_away_ends_the_sign_ins_but_adding_one_does_not(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    dao = RbacDAO(dbsession)
    await dao.seed_defaults()
    role = await dao.get_role_by_name("default_user")
    person = await _person(dbsession)
    await dao.assign_role(person.id, role.id)
    await dbsession.flush()
    signed_in.user = await _user(dbsession, admin=True)

    time.sleep(1.1)
    await client.put(f"/api/admin/users/{person.id}/roles", json={"role_ids": [str(role.id)]})
    await dbsession.refresh(person)
    assert person.sessions_valid_after is None, "the same roles: nothing ended"

    await client.put(f"/api/admin/users/{person.id}/roles", json={"role_ids": []})
    await dbsession.refresh(person)
    assert person.sessions_valid_after is not None


@pytest.mark.anyio
async def test_an_api_token_is_not_ended_with_the_sign_ins(client: AsyncClient, dbsession: AsyncSession) -> None:
    """It has a row of its own (and its own revoke); a password change is not a reason to break an app's key."""
    user = await _person(dbsession)
    claims = {
        "sub": str(user.id), "aud": ["fastapi-users:auth"], "iat": int(time.time()) - 60,
        "jti": uuid.uuid4().hex, "typ": "api",
    }
    token = jwt.encode(claims, settings.users_secret, algorithm="HS256")
    end_sign_ins(user)
    await dbsession.flush()
    assert await _me(client, token) == 200
