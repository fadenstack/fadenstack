"""The admin chat sessions list shows chats by id, and never reads their titles.

A chat's title is the start of its user's first message: theirs, as the
messages are, and encrypted with them. It used to be listed in plain text
wherever the gateway had no key, which was every install (2026-10-02).
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
    """Enough of an AsyncSession for the sessions list, keeping what it was asked."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.asked: list[str] = []

    async def execute(self, statement: Any, *_args: Any, **_kwargs: Any) -> list[SimpleNamespace]:
        self.asked.append(str(statement))
        return [SimpleNamespace(_mapping=row) for row in self._rows]


def _row() -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    return {
        "id": uuid.uuid4(), "tenant_id": "t", "user_id": "u", "project_id": None,
        "status": "active", "created_at": now, "updated_at": now,
    }


async def test_chats_are_listed_by_id_without_their_titles(fastapi_app: FastAPI, client: AsyncClient) -> None:
    gateway = _Gateway([_row(), _row()])

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
    assert len(rows) == 2 and all(uuid.UUID(r["id"]) for r in rows)
    assert all("title" not in r for r in rows)
    assert "title" not in gateway.asked[0]


async def test_projects_and_files_are_listed_without_their_names(fastapi_app: FastAPI, client: AsyncClient) -> None:
    """What people call their projects and files is theirs too (sealed in the gateway since 18 October 2026)."""
    gateway = _Gateway([_row()])

    async def gateway_session() -> Any:
        yield gateway

    fastapi_app.dependency_overrides[chat_admin._get_gateway_session] = gateway_session  # noqa: SLF001
    fastapi_app.dependency_overrides[current_active_user] = lambda: User(
        id=uuid.uuid4(), email="admin@example.com", hashed_password="x",  # noqa: S106
        is_active=True, is_verified=True, is_superuser=True,
    )

    assert (await client.get("/api/admin/chat/projects")).status_code == 200
    assert (await client.get("/api/admin/chat/attachments")).status_code == 200

    projects, attachments = gateway.asked[0], gateway.asked[1]
    assert "name" not in projects.replace("model_alias", "") and "description" not in projects
    assert "filename" not in attachments
