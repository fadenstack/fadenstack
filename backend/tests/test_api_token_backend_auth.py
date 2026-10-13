"""The backend never accepts an API token: it carries no fastapi-users audience, so the sign-in decode fails.

Only the gateway accepts one, and it checks the token's row there. A revoked or expired token is therefore
refused here by construction; these tests guard that, should a token ever be given the console audience.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.api_tokens import ApiToken
from faden_backend.db.models.users import current_active_user, get_jwt_strategy
from tests.test_api_tokens import _As, _make, _user, secret, signed_in  # noqa: F401 - fixtures used by name


async def _me(client: AsyncClient, token: str) -> int:
    return (await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})).status_code


async def _mint(
    signed_in: _As, fastapi_app: FastAPI, client: AsyncClient, **body: Any,  # noqa: F811
) -> dict[str, Any]:
    made = await _make(client, **body)
    fastapi_app.dependency_overrides.pop(current_active_user, None)
    return made


@pytest.mark.anyio
async def test_an_api_token_does_not_sign_in_to_the_backend(
    signed_in: _As, fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811
) -> None:
    signed_in.user = await _user(dbsession)
    made = await _mint(signed_in, fastapi_app, client)
    assert await _me(client, made["token"]) == 401


@pytest.mark.anyio
async def test_a_revoked_api_token_is_refused(
    signed_in: _As, fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811
) -> None:
    signed_in.user = await _user(dbsession)
    made = await _make(client)
    assert (await client.delete(f"/api/admin/users/me/api-tokens/{made['id']}")).status_code == 204
    fastapi_app.dependency_overrides.pop(current_active_user, None)
    assert await _me(client, made["token"]) == 401


@pytest.mark.anyio
async def test_an_expired_api_token_is_refused(
    signed_in: _As, fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811
) -> None:
    signed_in.user = await _user(dbsession)
    made = await _make(client)
    row = await dbsession.get(ApiToken, made["id"])
    row.expires_at = datetime.now(UTC) - timedelta(seconds=5)
    await dbsession.flush()
    fastapi_app.dependency_overrides.pop(current_active_user, None)
    assert await _me(client, made["token"]) == 401


@pytest.mark.anyio
async def test_a_console_sign_in_is_unaffected(
    client: AsyncClient, dbsession: AsyncSession, secret: str,  # noqa: F811
) -> None:
    user = await _user(dbsession)
    assert await _me(client, await get_jwt_strategy().write_token(user)) == 200
