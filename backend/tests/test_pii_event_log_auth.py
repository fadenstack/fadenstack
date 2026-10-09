# ruff: noqa: S105
"""``POST /api/admin/pii/events/log`` takes the PII service's internal token, nothing else."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.settings import settings

BODY = {"operation": "scan", "pii_detected": True, "entities_found": 2}


def _url(app: FastAPI) -> str:
    return app.url_path_for("pii_event_log")


@pytest.mark.anyio
async def test_without_a_token_the_call_is_refused(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "internal_token", "right-token")
    resp = await client.post(_url(fastapi_app), json=BODY)
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_a_wrong_token_is_refused(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "internal_token", "right-token")
    resp = await client.post(_url(fastapi_app), json=BODY, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_nothing_configured_refuses_everything(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "internal_token", "")
    monkeypatch.setattr(settings, "environment", "production")
    for headers in ({}, {"Authorization": "Bearer "}):
        resp = await client.post(_url(fastapi_app), json=BODY, headers=headers)
        assert resp.status_code == 503


@pytest.mark.anyio
async def test_the_right_token_logs_the_event(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from faden_backend.db.dependencies import get_db_session  # noqa: PLC0415

    fastapi_app.dependency_overrides[get_db_session] = lambda: dbsession
    monkeypatch.setattr(settings, "internal_token", "right-token")
    resp = await client.post(_url(fastapi_app), json=BODY, headers={"Authorization": "Bearer right-token"})
    assert resp.status_code == 200
    assert "id" in resp.json()


@pytest.mark.anyio
async def test_dev_environment_falls_back_to_the_dev_token(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from faden_backend.db.dependencies import get_db_session  # noqa: PLC0415

    fastapi_app.dependency_overrides[get_db_session] = lambda: dbsession
    monkeypatch.setattr(settings, "internal_token", "")
    monkeypatch.setattr(settings, "environment", "dev")
    resp = await client.post(_url(fastapi_app), json=BODY, headers={"Authorization": "Bearer dev-internal-token"})
    assert resp.status_code == 200
