"""The setup token the first administrator gets lasts an hour.

The CLI finishes setting up with it (an enrollment token for the local machine) and never needs it again.
It had no end: a superuser's sign-in for good, which nothing could revoke short of changing the secret
every sign-in is made with.
"""

from __future__ import annotations

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.users import User
from faden_backend.settings import settings
from faden_backend.web.api.bootstrap.views import BOOTSTRAP_TOKEN_HEADER, BOOTSTRAP_TOKEN_TTL_S


@pytest.mark.anyio
async def test_the_setup_token_lasts_an_hour(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if await dbsession.scalar(select(func.count()).select_from(User)):
        pytest.skip("bootstrap only runs on an install without accounts")
    monkeypatch.setattr(settings, "bootstrap_token", "the-install-bootstrap-token")
    r = await client.post(
        "/api/bootstrap",
        json={"email": "first@example.com", "password": "first-admin-pw"},
        headers={BOOTSTRAP_TOKEN_HEADER: "the-install-bootstrap-token"},
    )
    assert r.status_code in (200, 201), r.text
    claims = jwt.decode(
        r.json()["api_token"], settings.users_secret, algorithms=["HS256"], audience="fastapi-users:auth",
    )
    assert claims["exp"] - claims["iat"] == BOOTSTRAP_TOKEN_TTL_S == 3600


@pytest.mark.anyio
async def test_the_setup_token_is_in_the_accounts_tenant_and_no_other_is_taken(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if await dbsession.scalar(select(func.count()).select_from(User)):
        pytest.skip("bootstrap only runs on an install without accounts")
    monkeypatch.setattr(settings, "bootstrap_token", "the-install-bootstrap-token")
    headers = {BOOTSTRAP_TOKEN_HEADER: "the-install-bootstrap-token"}
    body = {"email": "first@example.com", "password": "first-admin-pw"}
    r = await client.post("/api/bootstrap", json={**body, "tenant_id": "zzz-other"}, headers=headers)
    assert r.status_code == 422, r.text
    assert not await dbsession.scalar(select(func.count()).select_from(User)), "refused before the admin is made"
    r = await client.post("/api/bootstrap", json={**body, "tenant_id": "default"}, headers=headers)
    assert r.status_code in (200, 201), r.text
    claims = jwt.decode(
        r.json()["api_token"], settings.users_secret, algorithms=["HS256"], audience="fastapi-users:auth",
    )
    assert claims["tenant_id"] == "default"
