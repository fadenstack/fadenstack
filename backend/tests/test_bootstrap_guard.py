"""The first administrator goes to whoever holds the install's bootstrap token, and only one is made.

``POST /api/bootstrap`` asked for nothing and counted the accounts without a lock: whoever reached the server
first after ``faden deploy`` opened ports 80 and 443 became its superuser, and two requests at once could both.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from faden_backend.db.models.users import User
from faden_backend.settings import settings
from faden_backend.web.api.bootstrap.views import BOOTSTRAP_TOKEN_HEADER, BootstrapRequest, create_first_admin

TOKEN = "the-install-bootstrap-token-0123456789"  # noqa: S105


async def _no_accounts(db: AsyncSession) -> None:
    if await db.scalar(select(func.count()).select_from(User)):
        pytest.skip("bootstrap only runs on an install without accounts")


async def _accounts(db: AsyncSession, email: str) -> int:
    return await db.scalar(select(func.count()).select_from(User).where(User.email == email)) or 0  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_without_a_configured_token_bootstrap_is_refused_and_says_how_to_set_one(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _no_accounts(dbsession)
    monkeypatch.setattr(settings, "bootstrap_token", "")
    email = f"first-{uuid.uuid4().hex[:8]}@example.com"

    r = await client.post("/api/bootstrap", json={"email": email}, headers={BOOTSTRAP_TOKEN_HEADER: "anything"})

    assert r.status_code == 403, r.text
    assert "FADEN_BACKEND_BOOTSTRAP_TOKEN" in r.json()["detail"]
    assert await _accounts(dbsession, email) == 0


@pytest.mark.anyio
@pytest.mark.parametrize("sent", [None, "", "wrong", TOKEN[:-1], TOKEN + "x"])
async def test_a_missing_or_wrong_token_is_refused(
    sent: str | None, client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _no_accounts(dbsession)
    monkeypatch.setattr(settings, "bootstrap_token", TOKEN)
    email = f"first-{uuid.uuid4().hex[:8]}@example.com"
    headers = {} if sent is None else {BOOTSTRAP_TOKEN_HEADER: sent}

    r = await client.post("/api/bootstrap", json={"email": email}, headers=headers)

    assert r.status_code == 403, r.text
    assert await _accounts(dbsession, email) == 0
    assert (await client.get("/api/bootstrap/status")).json() == {"needs_bootstrap": True}


@pytest.mark.anyio
async def test_the_right_token_makes_the_first_administrator_once(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _no_accounts(dbsession)
    monkeypatch.setattr(settings, "bootstrap_token", TOKEN)
    email = f"first-{uuid.uuid4().hex[:8]}@example.com"
    assert (await client.get("/api/bootstrap/status")).json() == {"needs_bootstrap": True}

    r = await client.post("/api/bootstrap", json={"email": email}, headers={BOOTSTRAP_TOKEN_HEADER: TOKEN})

    assert r.status_code == 201, r.text
    assert r.json()["email"] == email
    admin = await dbsession.scalar(select(User).where(User.email == email))  # type: ignore[arg-type]
    assert admin is not None
    assert admin.is_superuser
    assert (await client.get("/api/bootstrap/status")).json() == {"needs_bootstrap": False}
    again = await client.post(
        "/api/bootstrap", json={"email": f"second-{email}"}, headers={BOOTSTRAP_TOKEN_HEADER: TOKEN},
    )
    assert again.status_code == 409, again.text


@pytest.mark.anyio
async def test_two_bootstraps_at_once_make_one_administrator(_engine: AsyncEngine, dbsession: AsyncSession) -> None:
    await _no_accounts(dbsession)
    maker = async_sessionmaker(_engine, expire_on_commit=False)
    emails = [f"race-{n}-{uuid.uuid4().hex[:8]}@example.com" for n in (1, 2)]
    first, second = maker(), maker()
    try:
        await create_first_admin(first, BootstrapRequest(email=emails[0], password="first-admin-pw"))  # noqa: S106
        racing = asyncio.create_task(
            create_first_admin(second, BootstrapRequest(email=emails[1], password="second-admin-pw")),  # noqa: S106
        )
        await asyncio.sleep(0.5)
        assert not racing.done(), "the second bootstrap counted before the first one finished"

        await first.commit()
        with pytest.raises(HTTPException) as refused:
            await asyncio.wait_for(racing, timeout=10)
        assert refused.value.status_code == 409
    finally:
        await second.rollback()
        await first.rollback()
        await first.close()
        await second.close()
        async with maker() as cleanup:
            await cleanup.execute(delete(User).where(User.email.in_(emails)))  # type: ignore[attr-defined]
            await cleanup.commit()
