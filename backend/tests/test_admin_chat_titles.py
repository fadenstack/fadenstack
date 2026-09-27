"""The admin chat sessions list shows no ciphertext for an encrypted title.

The gateway encrypts chat titles like the messages; the backend has no key,
so an encrypted title is listed as encrypted rather than as its token.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.db.models.users import User, current_active_user
from faden_backend.web.api.admin.chat import views as chat_admin

pytestmark = pytest.mark.anyio()


class _Gateway:
    """Enough of an AsyncSession for the sessions list."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def execute(self, *_args: Any, **_kwargs: Any) -> list[SimpleNamespace]:
        return [SimpleNamespace(_mapping=row) for row in self._rows]


def _row(title: str | None) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    return {
        "id": uuid.uuid4(), "tenant_id": "t", "user_id": "u", "project_id": None,
        "title": title, "status": "active", "created_at": now, "updated_at": now,
    }


async def test_an_encrypted_title_is_listed_as_encrypted(fastapi_app: FastAPI, client: AsyncClient) -> None:
    gateway = _Gateway([_row("Launch plan"), _row("gAAAAABm-ciphertext-of-a-title"), _row(None)])

    async def gateway_session() -> Any:
        yield gateway

    fastapi_app.dependency_overrides[chat_admin._get_gateway_session] = gateway_session  # noqa: SLF001
    fastapi_app.dependency_overrides[current_active_user] = lambda: User(
        id=uuid.uuid4(), email="admin@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=True,
    )

    resp = await client.get("/api/admin/chat/sessions")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert [(r["title"], r.get("title_encrypted", False)) for r in rows] == [
        ("Launch plan", False),
        (None, True),
        (None, False),
    ]
