"""The records whose write an agent reported as failed: for auditors, from the engine, never with content.

An agent's SDK signs a record before the host writes the content; when the write then fails, the engine
keeps the record as not written (the text may still exist in part) and leaves it out of what was placed.
``GET /api/provenance/records/not-written`` lists them for whoever reads the audit (``audit:read``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.services.provenance import client as provenance_client
from faden_backend.settings import settings
from tests.test_content_check import SERVICE_TOKEN, _person, _sign_in

URL = "/api/provenance/records/not-written"
LISTED: dict[str, Any] = {"records": [{
    "record_id": "5a0e2b5c-7f3e-4a51-9a40-6a1c55e1d3f1", "created_at": "2026-10-19T08:10:00Z",
    "state_changed_at": "2026-10-19T08:10:02Z", "agent": "nextcloud-agent", "model": "qwen3.8-27b-fp8",
    "media_type": "text/markdown", "edit_kind": "rewritten", "failure": "FileBusyError",
    "tool": "write_file", "sink_kind": "nextcloud.file",
}]}

pytestmark = pytest.mark.anyio


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """The engine's listing, answered here through the real client."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=LISTED)

    real = provenance_client.not_written

    async def not_written(**kwargs: Any) -> dict[str, Any]:
        return await real(
            **kwargs, base_url="http://faden-provenance:8000", token=SERVICE_TOKEN,
            transport=httpx.MockTransport(answer),
        )

    monkeypatch.setattr(provenance_client, "not_written", not_written)
    monkeypatch.setattr(settings, "provenance_enabled", True)
    return seen


@pytest.mark.parametrize("role", ["admin", "auditor"])
async def test_an_auditor_lists_the_writes_that_failed(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, asked: list[httpx.Request], role: str,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, role))
    r = await client.get(URL, params={"since": "2026-10-19T00:00:00+00:00", "limit": 20})
    assert r.status_code == status.HTTP_200_OK, r.text
    assert r.json() == LISTED
    [request] = asked
    assert request.url.path == provenance_client.NOT_WRITTEN_PATH
    assert dict(request.url.params) == {"limit": "20", "since": "2026-10-19T00:00:00+00:00"}
    assert request.headers["Authorization"] == f"Bearer {SERVICE_TOKEN}"


async def test_without_a_since_the_newest_are_listed(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, asked: list[httpx.Request],
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "admin"))
    r = await client.get(URL)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert dict(asked[0].url.params) == {"limit": "50"}


@pytest.mark.parametrize("role", ["default_user", "viewer"])
async def test_it_needs_the_audits_right(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, asked: list[httpx.Request], role: str,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, role))
    r = await client.get(URL)
    assert r.status_code == status.HTTP_403_FORBIDDEN
    assert asked == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"since": "yesterday"}])
async def test_a_wrong_query_is_refused_before_the_engine(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, asked: list[httpx.Request],
    params: dict[str, Any],
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "admin"))
    r = await client.get(URL, params=params)
    assert r.status_code == 422  # Starlette deprecated its constant
    assert asked == []


async def test_nothing_is_listed_while_the_module_is_off(
    client: AsyncClient, fastapi_app: FastAPI, dbsession: AsyncSession, asked: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sign_in(fastapi_app, await _person(dbsession, "admin"))
    monkeypatch.setattr(settings, "provenance_enabled", False)
    r = await client.get(URL)
    assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert r.json()["detail"]["code"] == "module_off"
    assert asked == []
