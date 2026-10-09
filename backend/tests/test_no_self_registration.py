"""Accounts are made by an administrator, never by whoever reaches the server.

fastapi-users' register router was mounted at ``POST /api/auth/register``: anyone could make an account with the
default role (chat on the GPUs and the paid providers, every user's e-mail, the MCP tools). Administrators make
accounts in the console (``POST /api/admin/users/``), and those still get the default role.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.rbac_dao import RbacDAO
from faden_backend.db.models.users import User
from tests.test_api_tokens import _As, _user, signed_in  # noqa: F401 - fixture used by name


def _paths(app: FastAPI) -> set[str]:
    return set(app.openapi()["paths"])


@pytest.mark.anyio
async def test_nobody_registers_themselves(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    paths = _paths(fastapi_app)
    assert "/api/auth/jwt/login" in paths
    assert "/api/auth/register" not in paths
    email = f"walk-in-{uuid.uuid4().hex[:8]}@example.com"

    r = await client.post("/api/auth/register", json={"email": email, "password": "a-walk-in-password"})

    assert r.status_code in (404, 405), r.text
    assert await dbsession.scalar(select(User).where(User.email == email)) is None  # type: ignore[arg-type]


def test_signing_in_password_reset_and_verification_stay(fastapi_app: FastAPI) -> None:
    paths = _paths(fastapi_app)
    for path in (
        "/api/auth/jwt/login",
        "/api/auth/cookie/login",
        "/api/auth/forgot-password",
        "/api/auth/reset-password",
        "/api/auth/request-verify-token",
        "/api/auth/verify",
    ):
        assert path in paths, path


@pytest.mark.anyio
async def test_an_administrator_still_creates_accounts_with_the_default_role(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    signed_in.user = await _user(dbsession, admin=True)
    email = f"new-colleague-{uuid.uuid4().hex[:8]}@example.com"

    r = await client.post("/api/admin/users/", json={"email": email, "password": "a-long-password-1"})

    assert r.status_code == 201, r.text
    assert [role["name"] for role in r.json()["roles"]] == ["default_user"]
    roles = await rbac.get_user_roles(uuid.UUID(r.json()["id"]))
    assert [role.name for role in roles] == ["default_user"]


@pytest.mark.anyio
async def test_only_an_administrator_creates_accounts(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    signed_in.user = await _user(dbsession)
    email = f"by-a-user-{uuid.uuid4().hex[:8]}@example.com"

    r = await client.post("/api/admin/users/", json={"email": email, "password": "a-long-password-1"})

    assert r.status_code == 403, r.text
    assert await dbsession.scalar(select(User).where(User.email == email)) is None  # type: ignore[arg-type]
