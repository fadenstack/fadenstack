"""Grafana is behind the console's login.

Its anonymous viewer may query any data source (``/grafana/api/ds/query``),
and with nothing in front of it, whoever reached the console's port read
every log in Loki and every metric in Prometheus -- tested on the release VM
with no credentials at all, 2026-09-30. nginx now asks this endpoint before
every ``/grafana/`` request (``shared/nginx/locations.conf``).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.users import User, current_active_user

pytestmark = pytest.mark.anyio()


async def test_no_session_is_turned_away(fastapi_app: FastAPI, client: AsyncClient) -> None:
    response = await client.get(fastapi_app.url_path_for("observability_grafana_access"))
    assert response.status_code == 401


async def test_a_session_that_may_read_observability_passes(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession,
) -> None:
    admin = User(email="grafana-admin@example.com", hashed_password="x", is_active=True, is_superuser=True, is_verified=True)
    dbsession.add(admin)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    try:
        response = await client.get(fastapi_app.url_path_for("observability_grafana_access"))
    finally:
        fastapi_app.dependency_overrides.pop(current_active_user, None)
    assert response.status_code == 204
