"""Cookie sign-ins and account changes: no cross-site posts, no unproven email changes, no locking out the admins."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi_users.password import PasswordHelper
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.users import User, get_jwt_strategy
from faden_backend.settings import settings
from tests.test_api_tokens import _As, _user, signed_in  # noqa: F401 - fixture used by name

PASSWORD = "the-old-password"  # noqa: S105


@pytest.fixture(autouse=True)
def _secret() -> None:
    if not settings.users_secret:
        object.__setattr__(settings, "users_secret", "test-hardening-secret-0123456789abcdef")


async def _person(db: AsyncSession, *, admin: bool = False) -> User:
    user = await _user(db, admin=admin)
    user.hashed_password = PasswordHelper().hash(PASSWORD)
    await db.flush()
    return user


async def _bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {await get_jwt_strategy().write_token(user)}"}


# ── CSRF ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_post_from_another_site_with_the_cookie_is_refused(client: AsyncClient, dbsession: AsyncSession) -> None:
    cookie = {"fapiauth": await get_jwt_strategy().write_token(await _person(dbsession))}
    for headers in (
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://test.evil.example"},
        {"Referer": "https://evil.example/page"},
    ):
        r = await client.post("/api/auth/cookie/logout", cookies=cookie, headers=headers)
        assert r.status_code == 403, (headers, r.text)
    # Nothing was done: the sign-in still works.
    assert (await client.get("/api/users/me", cookies=cookie)).status_code == 200


@pytest.mark.anyio
async def test_the_console_its_proxy_and_plain_clients_are_let_through(
    client: AsyncClient, dbsession: AsyncSession,
) -> None:
    person = await _person(dbsession)

    async def post(headers: dict[str, str]) -> int:
        cookie = {"fapiauth": await get_jwt_strategy().write_token(person)}
        return (await client.post("/api/auth/cookie/logout", cookies=cookie, headers=headers)).status_code

    assert await post({"Origin": "http://test"}) == 204, "same origin"
    assert await post({"Origin": "https://console.example", "Host": "console.example"}) == 204, "nginx passes Host on"
    assert await post({"Origin": "https://console.example", "X-Forwarded-Host": "console.example"}) == 204
    assert await post({"Referer": "http://test/admin/profile"}) == 204, "no Origin: the Referer says"
    assert await post({}) == 204, "neither: a script or a server, not a page"


@pytest.mark.anyio
async def test_a_bearer_token_is_not_checked_for_origin_and_a_read_always_passes(
    client: AsyncClient, dbsession: AsyncSession,
) -> None:
    person = await _person(dbsession)
    headers = {**await _bearer(person), "Origin": "chrome-extension://abcdef"}
    assert (await client.post("/api/auth/jwt/logout", headers=headers)).status_code in (200, 204)
    cookie = {"fapiauth": await get_jwt_strategy().write_token(person)}
    assert (await client.get("/api/users/me", cookies=cookie, headers={"Origin": "https://evil.example"})).status_code == 200


@pytest.mark.anyio
async def test_a_listed_origin_may_post(client: AsyncClient, dbsession: AsyncSession) -> None:
    person = await _person(dbsession)
    saved = settings.trusted_origins_raw
    try:
        object.__setattr__(settings, "trusted_origins_raw", "https://console.example, https://other.example")
        cookie = {"fapiauth": await get_jwt_strategy().write_token(person)}
        r = await client.post("/api/auth/cookie/logout", cookies=cookie, headers={"Origin": "https://other.example"})
        assert r.status_code == 204
    finally:
        object.__setattr__(settings, "trusted_origins_raw", saved)


@pytest.mark.anyio
async def test_the_chat_proxy_that_reads_json_whatever_the_type_needs_the_origin_too(
    client: AsyncClient, dbsession: AsyncSession,
) -> None:
    """``request.json()`` ignores Content-Type; the Origin rule covers it, so a form post as text/plain is stopped."""
    cookie = {"fapiauth": await get_jwt_strategy().write_token(await _person(dbsession))}
    r = await client.post(
        "/api/chat/completions",
        cookies=cookie,
        content='{"model":"x","messages":[]}',
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert r.status_code == 403


# ── Email and password ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_person_proves_themselves_to_change_their_email(client: AsyncClient, dbsession: AsyncSession) -> None:
    person = await _person(dbsession)
    headers = await _bearer(person)
    new = f"moved-{uuid.uuid4().hex[:8]}@example.com"

    r = await client.patch("/api/users/me", json={"email": new}, headers=headers)
    assert r.status_code == 400 and "current password" in r.text
    r = await client.patch("/api/users/me", json={"email": new, "current_password": "wrong-password"}, headers=headers)
    assert r.status_code == 400 and "incorrect" in r.text
    await dbsession.refresh(person)
    assert person.email != new

    r = await client.patch("/api/users/me", json={"email": new, "current_password": PASSWORD}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == new
    assert r.json()["is_verified"] is False, "the new address is not verified until its owner does"
    await dbsession.refresh(person)
    assert person.hashed_password != "x" and "current_password" not in vars(person)


@pytest.mark.anyio
async def test_a_person_proves_themselves_to_change_their_password_this_way_too(
    client: AsyncClient, dbsession: AsyncSession,
) -> None:
    person = await _person(dbsession)
    headers = await _bearer(person)
    r = await client.patch("/api/users/me", json={"password": "a-brand-new-password"}, headers=headers)
    assert r.status_code == 400
    r = await client.patch(
        "/api/users/me", json={"password": "a-brand-new-password", "current_password": PASSWORD}, headers=headers,
    )
    assert r.status_code == 200, r.text
    await dbsession.refresh(person)
    assert PasswordHelper().verify_and_update("a-brand-new-password", person.hashed_password)[0]
    assert person.sessions_valid_after is not None, "and it ends the sign-ins like the other route"


@pytest.mark.anyio
async def test_an_update_that_changes_neither_needs_no_password(client: AsyncClient, dbsession: AsyncSession) -> None:
    person = await _person(dbsession)
    r = await client.patch("/api/users/me", json={"email": person.email}, headers=await _bearer(person))
    assert r.status_code == 200, r.text
    assert r.json()["is_verified"] is True


@pytest.mark.anyio
async def test_the_stock_routes_that_edit_another_account_are_gone(fastapi_app: FastAPI) -> None:
    paths = fastapi_app.openapi()["paths"]
    assert set(paths["/api/users/{id}"]) == {"get"}
    assert {"get", "patch"} <= set(paths["/api/users/me"]), "a person's own account is still theirs to read and edit"


# ── Deleting and demoting administrators ────────────────────────────────


async def _only_active_superusers(db: AsyncSession, *keep: User) -> None:
    """Leave *keep* the only active superusers (the test database may hold others)."""
    for other in (await db.execute(select(User).where(User.is_superuser.is_(True)))).scalars().all():
        if other.id not in {k.id for k in keep}:
            other.is_active = False
    await db.flush()


@pytest.mark.anyio
async def test_nobody_deletes_themselves(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    admin = await _person(dbsession, admin=True)
    await _person(dbsession, admin=True)
    signed_in.user = admin
    r = await client.delete(f"/api/admin/users/{admin.id}")
    assert r.status_code == 409 and "yourself" in r.text
    assert await dbsession.get(User, admin.id) is not None


@pytest.mark.anyio
async def test_the_last_active_superuser_is_not_deleted(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    last = await _person(dbsession, admin=True)
    actor = await _person(dbsession, admin=True)
    signed_in.user = actor
    await _only_active_superusers(dbsession, last)
    actor.is_active = False  # the signed-in one is not counted: it is the one that is deleting
    await dbsession.flush()

    r = await client.delete(f"/api/admin/users/{last.id}")
    assert r.status_code == 409 and "last active superuser" in r.text
    assert await dbsession.get(User, last.id) is not None


@pytest.mark.anyio
async def test_another_account_is_deleted_and_so_is_a_superuser_when_others_remain(
    signed_in: _As, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811
) -> None:
    admin = await _person(dbsession, admin=True)
    second = await _person(dbsession, admin=True)
    ordinary = await _person(dbsession)
    signed_in.user = admin
    assert (await client.delete(f"/api/admin/users/{ordinary.id}")).status_code == 204
    assert (await client.delete(f"/api/admin/users/{second.id}")).status_code == 204
