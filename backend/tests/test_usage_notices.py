"""The first purge of old request records is announced to administrators while it is still ahead (M09)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.services.extensions import registry
from tests.usage_fixtures import gateway, person, set_usage, sign_in

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()


async def _notices(client: AsyncClient, fastapi_app: FastAPI) -> list[dict[str, object]]:
    response = await client.get(fastapi_app.url_path_for("admin_notices"))
    assert response.status_code == 200, response.text
    return [n for n in response.json() if n["code"] == "usage_purge_pending"]


async def test_the_purge_ahead_is_announced_with_its_date_and_the_days_kept(
    client: AsyncClient, fastapi_app: FastAPI, gateway: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(registry, "admin_notices", [])
    sign_in(fastapi_app, await person(gateway, f"a-{uuid.uuid4().hex[:6]}@example.com", superuser=True))
    assert await _notices(client, fastapi_app) == [], "no backfill yet: no date to announce"

    purge_from = datetime(2030, 3, 4, 23, 30, tzinfo=UTC)
    await gateway.execute(text("INSERT INTO usage_rollup_state (purge_from) VALUES (:at)"), {"at": purge_from})
    await set_usage(gateway, request_log_retention_days=120, time_zone="Europe/Berlin")
    assert await _notices(client, fastapi_app) == [
        {"code": "usage_purge_pending", "severity": "warning", "params": {"date": "2030-03-05", "days": 120}},
    ]

    await gateway.execute(text("UPDATE usage_rollup_state SET purge_from = :at"),
                          {"at": datetime.now(UTC) - timedelta(minutes=1)})
    assert await _notices(client, fastapi_app) == [], "the purge may run: nothing to announce"
