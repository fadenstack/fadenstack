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
from faden_backend.web.api.bootstrap.views import BOOTSTRAP_TOKEN_TTL_S


@pytest.mark.anyio
async def test_the_setup_token_lasts_an_hour(client: AsyncClient, dbsession: AsyncSession) -> None:
    if await dbsession.scalar(select(func.count()).select_from(User)):
        pytest.skip("bootstrap only runs on an install without accounts")
    r = await client.post("/api/bootstrap", json={"email": "first@example.com", "password": "first-admin-pw"})
    assert r.status_code in (200, 201), r.text
    claims = jwt.decode(
        r.json()["api_token"], settings.users_secret, algorithms=["HS256"], audience="fastapi-users:auth",
    )
    assert claims["exp"] - claims["iat"] == BOOTSTRAP_TOKEN_TTL_S == 3600
