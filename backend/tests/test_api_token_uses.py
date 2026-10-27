"""Every use of an API token: its owner and administrators see who uses it, when, from where and how.

The gateway writes the uses (``api_token_uses``); the backend shows them and deletes them after the retention
period (GDPR Art. 5(1)(e)).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.api_tokens import ApiToken, ApiTokenUse
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import api_token_uses


async def _person(db: AsyncSession, *, admin: bool = False) -> User:
    user = User(
        id=uuid.uuid4(), email=f"t-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    return user


async def _token(db: AsyncSession, owner: User, name: str, *uses_days_ago: float) -> ApiToken:
    token = ApiToken(id=uuid.uuid4(), user_id=owner.id, name=name)
    db.add(token)
    await db.flush()
    now = datetime.now(UTC)
    for index, days in enumerate(uses_days_ago):
        db.add(ApiTokenUse(
            token_id=token.id, user_id=owner.id, at=now - timedelta(days=days), address=f"10.0.0.{index + 1}",
            client="faden-sdk/0.2", route="POST /v1/chat/completions",
        ))
    await db.flush()
    return token


@pytest.mark.anyio
async def test_the_overview_names_owner_and_last_use(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    admin, owner = await _person(dbsession, admin=True), await _person(dbsession)
    used = await _token(dbsession, owner, "nightly export", 40, 2, 1)
    idle = await _token(dbsession, owner, "old script")
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin

    r = await client.get("/api/admin/users/api-tokens")
    assert r.status_code == status.HTTP_200_OK, r.text
    found = {row["id"]: row for row in r.json()}
    assert found[str(used.id)]["email"] == owner.email
    assert found[str(used.id)]["last_used_address"] == "10.0.0.3", "the newest use"
    assert found[str(used.id)]["last_used_route"] == "POST /v1/chat/completions"
    assert found[str(used.id)]["recent_uses"] == 2, "the one 40 days ago is not recent"
    assert found[str(idle.id)]["last_used_at"] is None

    uses = (await client.get(f"/api/admin/users/api-tokens/{used.id}/uses")).json()
    assert [use["address"] for use in uses] == ["10.0.0.3", "10.0.0.2", "10.0.0.1"], "newest first"

    mine = (await client.get(f"/api/admin/users/{owner.id}/api-tokens")).json()
    assert {row["id"]: row["recent_uses"] for row in mine} == {str(used.id): 2, str(idle.id): 0}


@pytest.mark.anyio
async def test_people_see_their_own_tokens_uses_only(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession,
) -> None:
    owner, other = await _person(dbsession), await _person(dbsession)
    token = await _token(dbsession, owner, "mine", 1)
    fastapi_app.dependency_overrides[current_active_user] = lambda: owner
    assert len((await client.get(f"/api/admin/users/me/api-tokens/{token.id}/uses")).json()) == 1
    assert (await client.get("/api/admin/users/me/api-tokens")).json()[0]["last_used_address"] == "10.0.0.1"
    assert (await client.get("/api/admin/users/api-tokens")).status_code == status.HTTP_403_FORBIDDEN

    fastapi_app.dependency_overrides[current_active_user] = lambda: other
    r = await client.get(f"/api/admin/users/me/api-tokens/{token.id}/uses")
    assert r.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_uses_past_the_retention_period_are_deleted(dbsession: AsyncSession) -> None:
    owner = await _person(dbsession)
    token = await _token(dbsession, owner, "kept", 100, 10)
    assert await api_token_uses.delete_older_than(dbsession, 0) == 0, "0 keeps them for good"
    assert await api_token_uses.delete_older_than(dbsession, 90) >= 1
    left = await dbsession.scalar(select(func.count()).where(ApiTokenUse.token_id == token.id))
    assert left == 1
