"""API tokens: each has a row, so its owner or an administrator can revoke it; console sign-ins expire."""

from __future__ import annotations

import uuid
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.api_tokens import API_TOKEN_TYPE, ApiToken
from faden_backend.db.models.users import User, current_active_user, get_jwt_strategy, login_lifetime_seconds
from faden_backend.settings import settings


class _As:
    def __init__(self) -> None:
        self.user: User | None = None

    def __call__(self) -> User:
        assert self.user is not None
        return self.user


async def _user(db: AsyncSession, *, admin: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:10]}@example.com",
        hashed_password="x",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    return user


@pytest.fixture
def signed_in(fastapi_app: FastAPI) -> _As:
    who = _As()
    fastapi_app.dependency_overrides[current_active_user] = who
    return who


@pytest.fixture
def secret() -> str:
    if not settings.users_secret:
        object.__setattr__(settings, "users_secret", "test-api-token-secret-0123456789abcdef")
    return settings.users_secret


async def _make(client: AsyncClient, **body: Any) -> dict[str, Any]:
    r = await client.post("/api/admin/users/me/api-token", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.anyio
async def test_a_token_has_a_row_and_says_it_is_an_api_token(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession)
    made = await _make(client, name="Ticket script", expires_in=3600)
    claims = jwt.decode(made["token"], secret, algorithms=["HS256"])
    assert claims["typ"] == API_TOKEN_TYPE
    assert claims["jti"] == made["id"]
    assert claims["sub"] == str(signed_in.user.id)
    assert claims["exp"] - claims["iat"] == 3600
    row = await dbsession.get(ApiToken, uuid.UUID(made["id"]))
    assert row is not None and row.name == "Ticket script" and row.revoked_at is None
    assert row.expires_at is not None


@pytest.mark.anyio
async def test_the_owner_lists_and_revokes_their_tokens(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    signed_in.user = await _user(dbsession)
    first = await _make(client, name="first")
    second = await _make(client)
    listed = (await client.get("/api/admin/users/me/api-tokens")).json()
    assert {t["id"] for t in listed} == {first["id"], second["id"]}
    assert all("token" not in t for t in listed)

    r = await client.delete(f"/api/admin/users/me/api-tokens/{first['id']}")
    assert r.status_code == 204
    listed = {t["id"]: t for t in (await client.get("/api/admin/users/me/api-tokens")).json()}
    assert listed[first["id"]]["revoked_at"] is not None
    assert listed[second["id"]]["revoked_at"] is None


@pytest.mark.anyio
async def test_nobody_revokes_someone_elses_token_but_an_administrator(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    owner = await _user(dbsession)
    signed_in.user = owner
    made = await _make(client, name="owner's")

    signed_in.user = await _user(dbsession)
    r = await client.delete(f"/api/admin/users/me/api-tokens/{made['id']}")
    assert r.status_code == 404
    r = await client.delete(f"/api/admin/users/{owner.id}/api-tokens/{made['id']}")
    assert r.status_code == 403

    signed_in.user = await _user(dbsession, admin=True)
    listed = (await client.get(f"/api/admin/users/{owner.id}/api-tokens")).json()
    assert [t["id"] for t in listed] == [made["id"]]
    r = await client.delete(f"/api/admin/users/{owner.id}/api-tokens/{made['id']}")
    assert r.status_code == 204
    row = await dbsession.get(ApiToken, uuid.UUID(made["id"]))
    await dbsession.refresh(row)
    assert row.revoked_at is not None


@pytest.mark.anyio
async def test_a_lifetime_must_be_positive(signed_in: _As, client: AsyncClient, dbsession: AsyncSession, secret: str) -> None:
    signed_in.user = await _user(dbsession)
    r = await client.post("/api/admin/users/me/api-token", json={"expires_in": 0})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_a_sign_in_from_before_sign_ins_expired_is_refused(
    client: AsyncClient, dbsession: AsyncSession, secret: str,
) -> None:
    user = await _user(dbsession)
    current = await get_jwt_strategy().write_token(user)
    old = jwt.encode({"sub": str(user.id), "aud": ["fastapi-users:auth"]}, secret, algorithm="HS256")
    r = await client.get("/api/users/me", headers={"Authorization": f"Bearer {current}"})
    assert r.status_code == 200, r.text
    r = await client.get("/api/users/me", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_a_console_sign_in_expires_after_the_setting(dbsession: AsyncSession, secret: str) -> None:
    user = await _user(dbsession)
    saved = settings.users_login_lifetime_s
    try:
        object.__setattr__(settings, "users_login_lifetime_s", 7 * 24 * 3600)
        claims = jwt.decode(
            await get_jwt_strategy().write_token(user), secret, algorithms=["HS256"], audience="fastapi-users:auth",
        )
        assert "exp" in claims
        object.__setattr__(settings, "users_login_lifetime_s", 0)
        assert login_lifetime_seconds() is None
        claims = jwt.decode(
            await get_jwt_strategy().write_token(user), secret, algorithms=["HS256"], audience="fastapi-users:auth",
        )
        assert "exp" not in claims
    finally:
        object.__setattr__(settings, "users_login_lifetime_s", saved)
