"""GET /api/admin/notices: what the registered sources have to say, for superusers (services/extensions.py)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette import status

from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.extensions import AdminNotice, registry

pytestmark = pytest.mark.anyio


def _signed_in(app: FastAPI, *, superuser: bool) -> None:
    user = User(id=uuid.uuid4(), email="admin@example.com", hashed_password="x",  # noqa: S106
                is_active=True, is_verified=True, is_superuser=superuser)
    app.dependency_overrides[current_active_user] = lambda: user


async def _licence(session: Any) -> list[AdminNotice]:
    assert session is not None  # a source gets the request's session
    return [AdminNotice("licence_expiring", "warning", {"days": 12})]


async def _seats(_session: Any) -> list[AdminNotice]:
    return [AdminNotice("seats_over", "info", {"used": 12, "seats": 10}),
            AdminNotice("licence_expired", "error")]


async def _broken(_session: Any) -> list[AdminNotice]:
    msg = "the source is down"
    raise RuntimeError(msg)


async def test_core_has_none(fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "admin_notices", [])
    _signed_in(fastapi_app, superuser=True)
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.status_code == status.HTTP_200_OK
    assert r.json() == []


async def test_every_source_errors_first(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "admin_notices", [_licence, _seats])
    _signed_in(fastapi_app, superuser=True)
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.json() == [
        {"code": "licence_expired", "severity": "error", "params": {}},
        {"code": "licence_expiring", "severity": "warning", "params": {"days": 12}},
        {"code": "seats_over", "severity": "info", "params": {"used": 12, "seats": 10}},
    ]


async def test_a_broken_source_hides_nothing_else(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(registry, "admin_notices", [_broken, _licence])
    _signed_in(fastapi_app, superuser=True)
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.status_code == status.HTTP_200_OK
    assert [n["code"] for n in r.json()] == ["licence_expiring"]
    assert "An admin notice source failed" in caplog.text


async def test_only_superusers_read_them(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "admin_notices", [_licence])
    _signed_in(fastapi_app, superuser=False)
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.status_code == status.HTTP_403_FORBIDDEN


async def test_nobody_without_signing_in(fastapi_app: FastAPI, client: AsyncClient) -> None:
    r = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert r.status_code == status.HTTP_401_UNAUTHORIZED
