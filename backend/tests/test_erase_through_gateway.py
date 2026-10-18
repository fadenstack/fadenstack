"""An administrator's delete and an account's delete erase through the gateway, which alone reaches the files."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.notifications import NotificationOutbox
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.chat.gateway_client import EraseFailed, GatewayChatClient
from faden_backend.services.notifications.retention import prune_sent_notifications

pytestmark = pytest.mark.anyio


async def _person(db: AsyncSession, *, admin: bool = False) -> User:
    user = User(
        id=uuid.uuid4(), email=f"p-{uuid.uuid4().hex[:6]}@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=admin,
    )
    db.add(user)
    await db.flush()
    return user


async def test_the_admin_chat_browser_erases_through_the_gateway(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, _no_gateway_erase: list,
) -> None:
    admin = await _person(dbsession, admin=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    chat, project, attachment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for path in (f"sessions/{chat}", f"projects/{project}", f"attachments/{attachment}"):
        r = await client.delete(f"/api/admin/chat/{path}")
        assert r.status_code == 204, r.text
    assert _no_gateway_erase == [("session", str(chat)), ("project", str(project)), ("attachment", str(attachment))]


async def test_an_account_is_deleted_with_its_chat_data_and_not_without_it(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession,
    _no_gateway_erase: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await _person(dbsession, admin=True)
    await _person(dbsession, admin=True)  # not the last administrator
    target = await _person(dbsession)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin

    async def down(self: object, scope: str, target_id: str, *, secret: str) -> dict:
        raise EraseFailed("The gateway could not be reached.")

    with monkeypatch.context() as patched:
        patched.setattr(GatewayChatClient, "erase", down)
        r = await client.delete(f"/api/admin/users/{target.id}")
        assert r.status_code == 502
        assert "was not deleted" in r.json()["detail"]
    assert await dbsession.get(User, target.id) is not None

    r = await client.delete(f"/api/admin/users/{target.id}")
    assert r.status_code == 204, r.text
    assert ("user", str(target.id)) in _no_gateway_erase


async def test_sent_notifications_go_after_a_week(dbsession: AsyncSession) -> None:
    now = datetime.now(UTC)
    for sent_days_ago, status in ((10, "sent"), (2, "sent"), (10, "pending")):
        dbsession.add(NotificationOutbox(
            kind="password_reset", payload_json={"reset_url": "https://x/reset?token=t"}, status=status,
            sent_at=now - timedelta(days=sent_days_ago) if status == "sent" else None,
        ))
    await dbsession.flush()
    assert await prune_sent_notifications(dbsession, now) == 1
    left = (await dbsession.execute(select(NotificationOutbox.status))).scalars().all()
    assert sorted(left) == ["pending", "sent"]
