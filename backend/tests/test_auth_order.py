"""Which identity a request carries when it has two: the token the caller sends wins over the browser's cookie.

An app in a browser (the Chrome extension, a web add-in) signs its user in with a bearer token while the browser
may still hold someone's console session cookie for the same server. Found on .61 (4 October 2026): with the cookie
first, the extension created and signed in to an agent as the console's admin, not as its own user.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.users import User, get_jwt_strategy
from faden_backend.settings import settings


async def _user(db: AsyncSession, email: str) -> User:
    user = User(id=uuid.uuid4(), email=email, hashed_password="x", is_active=True, is_verified=True)  # noqa: S106
    db.add(user)
    await db.flush()
    return user


async def test_a_bearer_token_wins_over_the_browsers_cookie(client: AsyncClient, dbsession: AsyncSession) -> None:
    if not settings.users_secret:
        object.__setattr__(settings, "users_secret", "test-auth-order-secret-0123456789abcdef")
    console = await _user(dbsession, f"console-{uuid.uuid4().hex[:6]}@example.com")
    app_user = await _user(dbsession, f"app-{uuid.uuid4().hex[:6]}@example.com")
    strategy = get_jwt_strategy()
    console_token = await strategy.write_token(console)
    app_token = await strategy.write_token(app_user)

    both = await client.get(
        "/api/users/me", headers={"Authorization": f"Bearer {app_token}"}, cookies={"fapiauth": console_token},
    )
    assert both.status_code == 200, both.text
    assert both.json()["email"] == app_user.email

    # Each alone is still that one: the console keeps signing in with its cookie.
    cookie_only = await client.get("/api/users/me", cookies={"fapiauth": console_token})
    assert cookie_only.json()["email"] == console.email
