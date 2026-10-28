"""Observability names nobody: no ranking of people, no person in the request list, its detail or the CSV export,
whoever asks and however person-level reporting is set (M02, M11). The Enterprise stubs are gone.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from tests.usage_fixtures import gateway, person, request_log, set_usage, sign_in

__all__ = ["gateway"]

pytestmark = pytest.mark.anyio()

AT = datetime.now(UTC) - timedelta(minutes=5)


@pytest.fixture
async def seen(fastapi_app: FastAPI, gateway: AsyncSession) -> Any:
    """A superuser who is a usage auditor, with person-level reporting on: still nobody is named here."""
    await set_usage(gateway, person_reporting=True)
    user = await person(gateway, f"ua-{uuid.uuid4().hex[:6]}@example.com", roles=["usage_auditor"], superuser=True)
    sign_in(fastapi_app, user)
    for n, who in enumerate(("person-a", "person-b", str(user.id))):
        await request_log(gateway, who, AT, request_id=f"r-{n}", trace_id=f"t-{n}")
    return user


async def test_the_summary_ranks_nobody(client: AsyncClient, seen: Any) -> None:
    body = (await client.get("/api/admin/observability/summary")).json()
    assert "top_users" not in body and body["total_requests"] == 3


async def test_the_request_list_names_nobody_and_filters_by_nobody(client: AsyncClient, seen: Any) -> None:
    body = (await client.get("/api/admin/observability/requests", params={"reason": "Checking the bill"})).json()
    assert body["total"] == 3 and {item["user_id"] for item in body["items"]} == {None}
    filtered = (await client.get("/api/admin/observability/requests", params={"user_id": "person-a"})).json()
    assert filtered["total"] == 3, "there is no filter by person"


async def test_the_detail_and_the_trace_name_nobody(client: AsyncClient, seen: Any) -> None:
    detail = await client.get("/api/admin/observability/requests/r-0")
    assert detail.status_code == status.HTTP_200_OK and detail.json()["user_id"] is None
    own = await client.get("/api/admin/observability/requests/by-trace/t-2")  # the caller's own
    assert own.status_code == status.HTTP_200_OK and own.json()["user_id"] is None


async def test_the_csv_export_names_nobody(client: AsyncClient, seen: Any) -> None:
    response = await client.get("/api/admin/observability/export.csv")
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 3 and {row["user_id"] for row in rows} == {""}
    assert "person-a" not in response.text


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "budgets"), ("GET", "forecast"), ("POST", "alerts"), ("GET", "chargeback"),
])
async def test_the_enterprise_stubs_are_gone(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, method: str, path: str,
) -> None:
    sign_in(fastapi_app, await person(dbsession, f"a-{uuid.uuid4().hex[:6]}@example.com", superuser=True))
    response = await client.request(method, f"/api/admin/observability/{path}")
    assert response.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_405_METHOD_NOT_ALLOWED)
