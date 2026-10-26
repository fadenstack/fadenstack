"""One-time password reset links an administrator makes (services/password_reset_links.py).

After the OWASP Forgot Password Cheat Sheet: random and long, kept only as a hash, single use, short-lived, for
one account; made in Root Mode with a reason, like a right; using it ends the account's sign-ins and signs
nobody in.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.containers import AuditEvent
from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.db.models.password_reset import PasswordResetLink
from faden_backend.db.models.users import User, current_active_user, end_sign_ins
from faden_backend.web.api.admin.dependencies import get_root_mode_active
from tests.conftest import RecordingMailSender
from tests.test_account_hardening import PASSWORD, _person

pytestmark = pytest.mark.anyio

REASON = "Alex lost the password and sends no mail"
NEW = "A-new-password-for-2026!"


@pytest.fixture
async def people(fastapi_app: FastAPI, dbsession: AsyncSession) -> tuple[User, User]:
    admin = await _person(dbsession, admin=True)
    target = await _person(dbsession)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: True
    return admin, target


async def _link(client: AsyncClient, target: User, **body: object) -> dict:
    r = await client.post(f"/api/admin/users/{target.id}/password-reset-link", json={"reason": REASON, **body})
    assert r.status_code == status.HTTP_201_CREATED, r.text
    return r.json()


async def _event(db: AsyncSession, action: str, target: User) -> AuditEvent:
    return (await db.execute(select(AuditEvent).where(
        AuditEvent.action == action, AuditEvent.target_id == str(target.id),
    ))).scalar_one()


async def _signs_in(client: AsyncClient, email: str, password: str) -> int:
    return (await client.post("/api/auth/jwt/login", data={"username": email, "password": password})).status_code


async def _preview(client: AsyncClient, token: str) -> int:
    return (await client.post("/api/auth/reset-link/preview", json={"token": token})).status_code


async def test_a_link_needs_root_mode_and_a_reason(
    client: AsyncClient, fastapi_app: FastAPI, people: tuple[User, User],
) -> None:
    _, target = people
    path = f"/api/admin/users/{target.id}/password-reset-link"
    assert (await client.post(path, json={"reason": "x"})).status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert (await client.post(path, json={"reason": REASON, "valid_hours": 5})).status_code == 422
    fastapi_app.dependency_overrides[get_root_mode_active] = lambda: False
    no_root = await client.post(path, json={"reason": REASON})
    assert no_root.status_code == status.HTTP_403_FORBIDDEN and "Root Mode" in no_root.json()["detail"]


async def test_the_link_is_shown_once_kept_as_a_hash_and_recorded(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    admin, target = people
    before = datetime.now(UTC)
    made = await _link(client, target)
    token = made["token"]
    assert len(token) >= 43, "256 random bits"
    expires = datetime.fromisoformat(made["expires_at"])
    assert before + timedelta(minutes=59) < expires <= datetime.now(UTC) + timedelta(hours=1)
    row = (await dbsession.execute(select(PasswordResetLink).where(PasswordResetLink.user_id == target.id))).scalar_one()
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest() and token not in row.token_hash
    assert row.created_by == admin.id
    event = await _event(dbsession, "user.password.reset_link", target)
    meta = json.loads(event.metadata_json or "{}")
    assert event.severity == "high" and event.actor_id == admin.id
    assert meta["reason"] == REASON and meta["valid_hours"] == 1 and meta["email"] == target.email
    day = await _link(client, target, valid_hours=24)
    assert datetime.fromisoformat(day["expires_at"]) > datetime.now(UTC) + timedelta(hours=23)


async def test_not_for_oneself_nor_a_suspended_account(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    admin, target = people
    own = await client.post(f"/api/admin/users/{admin.id}/password-reset-link", json={"reason": REASON})
    assert own.status_code == status.HTTP_409_CONFLICT and "Manage Profile" in own.json()["detail"]
    target.is_active = False
    await dbsession.flush()
    suspended = await client.post(f"/api/admin/users/{target.id}/password-reset-link", json={"reason": REASON})
    assert suspended.status_code == status.HTTP_409_CONFLICT


async def test_the_link_sets_a_new_password_once_and_signs_nobody_in(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    _, target = people
    token = (await _link(client, target))["token"]
    seen = await client.post("/api/auth/reset-link/preview", json={"token": token})
    assert seen.status_code == 200 and seen.json()["email"] == target.email
    used = await client.post("/api/auth/reset-link/use", json={"token": token, "password": NEW})
    assert used.status_code == status.HTTP_204_NO_CONTENT, used.text
    assert not used.cookies, "nobody is signed in: the person signs in as usual"
    await dbsession.refresh(target)
    assert target.sessions_valid_after is not None, "every sign-in of the account ends"
    assert await _signs_in(client, target.email, NEW) == 200
    assert await _signs_in(client, target.email, PASSWORD) == 400
    again = await client.post("/api/auth/reset-link/use", json={"token": token, "password": "Another-one-2026!"})
    assert again.status_code == 400 and again.json()["detail"] == "expired", "once"
    assert await _preview(client, token) == 404
    event = await _event(dbsession, "user.password.reset_by_link", target)
    assert event.actor_id == target.id and event.severity == "high"


async def test_a_password_the_rule_refuses_keeps_the_link(
    client: AsyncClient, people: tuple[User, User],
) -> None:
    _, target = people
    token = (await _link(client, target))["token"]
    short = await client.post("/api/auth/reset-link/use", json={"token": token, "password": "short"})
    assert short.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT and short.json()["detail"]
    assert (await client.post("/api/auth/reset-link/use", json={"token": token, "password": NEW})).status_code == 204


async def test_a_new_link_cancels_the_earlier_one(client: AsyncClient, people: tuple[User, User]) -> None:
    _, target = people
    first = (await _link(client, target))["token"]
    second = (await _link(client, target))["token"]
    assert await _preview(client, first) == 404
    assert await _preview(client, second) == 200


async def test_a_password_change_or_a_suspension_since_voids_the_link(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    _, target = people
    token = (await _link(client, target))["token"]
    end_sign_ins(target)  # as a password change, by any way, does
    await dbsession.flush()
    assert await _preview(client, token) == 404
    fresh = (await _link(client, target))["token"]
    target.is_active = False
    await dbsession.flush()
    assert await _preview(client, fresh) == 404
    used = await client.post("/api/auth/reset-link/use", json={"token": fresh, "password": NEW})
    assert used.status_code == 400


async def test_an_expired_or_made_up_link(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    _, target = people
    token = (await _link(client, target))["token"]
    await dbsession.execute(update(PasswordResetLink).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert await _preview(client, token) == 404
    late = await client.post("/api/auth/reset-link/use", json={"token": token, "password": NEW})
    assert late.status_code == 400 and late.json()["detail"] == "expired"
    assert await _preview(client, "x" * 43) == 404
    assert await _preview(client, "short") == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_links_that_ended_a_day_ago_are_deleted(
    client: AsyncClient, dbsession: AsyncSession, people: tuple[User, User],
) -> None:
    _, target = people
    other = await _person(dbsession)
    old = (await _link(client, other))["token"]
    await dbsession.execute(update(PasswordResetLink).where(PasswordResetLink.user_id == other.id).values(
        expires_at=datetime.now(UTC) - timedelta(days=2),
    ))
    await _link(client, target)
    left = (await dbsession.execute(select(PasswordResetLink.token_hash))).scalars().all()
    assert hashlib.sha256(old.encode()).hexdigest() not in left


async def test_the_mailed_reset_link_carries_its_token_in_the_fragment(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender,
) -> None:
    """Never in the query: the page's own requests would send it on as their Referer, into access logs."""
    user = await _person(dbsession)
    assert (await client.post("/api/auth/forgot-password", json={"email": user.email})).status_code == 202
    mail = (await dbsession.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == "password_reset", NotificationOutbox.payload_json["to_email"].astext == user.email,
    ))).scalar_one()
    url = mail.payload_json["reset_url"]
    assert "/reset-password#token=" in url and "?" not in url, url


async def test_the_mailed_reset_link_starts_where_mail_says_not_where_the_request_says(
    client: AsyncClient, dbsession: AsyncSession, mail_on: RecordingMailSender,
) -> None:
    """A forged Host must not send the link to someone else's site (OWASP: password reset poisoning)."""
    user = await _person(dbsession)
    r = await client.post(
        "/api/auth/forgot-password", json={"email": user.email}, headers={"Host": "evil.example"},
    )
    assert r.status_code == 202
    mail = (await dbsession.execute(select(NotificationOutbox).where(
        NotificationOutbox.kind == "password_reset", NotificationOutbox.payload_json["to_email"].astext == user.email,
    ))).scalar_one()
    assert mail.payload_json["reset_url"].startswith(f"{mail_on.base}/reset-password#token=")
