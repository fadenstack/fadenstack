"""The service's own routes need the service token; only health is open."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from faden_pii.settings import settings

ROUTES = [
    ("GET", "/api/v1/pii/options", None),
    ("POST", "/api/v1/pii/scan", {"text": "Anna Schmidt"}),
    ("POST", "/api/v1/pii/redact", {"text": "Anna Schmidt"}),
    ("POST", "/api/v1/pii/sanitize", {"payload": {"messages": []}, "mode": "redact"}),
    ("POST", "/api/v1/pii/detokenize", {"payload": {}, "token_mapping": {"[PERSON_1]": "Anna Schmidt"}}),
]


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "url", "body"), ROUTES)
async def test_no_token_is_refused(client: AsyncClient, method: str, url: str, body: dict | None) -> None:
    client.headers.pop("authorization")
    response = await client.request(method, url, json=body)
    assert response.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "url", "body"), ROUTES)
async def test_a_wrong_token_is_refused(client: AsyncClient, method: str, url: str, body: dict | None) -> None:
    client.headers["authorization"] = "Bearer not-the-token"
    response = await client.request(method, url, json=body)
    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "url", "body"), ROUTES)
async def test_an_empty_token_refuses_everyone(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, method: str, url: str, body: dict | None,
) -> None:
    """Not set means closed. Neither the empty bearer nor the right-looking one gets in."""
    monkeypatch.setattr(settings, "service_token", "")
    for header in ("Bearer ", "Bearer test-pii-token"):
        client.headers["authorization"] = header
        assert (await client.request(method, url, json=body)).status_code == 503
    client.headers.pop("authorization")
    assert (await client.request(method, url, json=body)).status_code == 503


@pytest.mark.anyio
async def test_the_right_token_gets_through(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/pii/options")).status_code == 200


@pytest.mark.anyio
async def test_health_needs_no_token(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client.headers.pop("authorization")
    assert (await client.get("/api/health")).status_code == 200
    monkeypatch.setattr(settings, "service_token", "")
    assert (await client.get("/api/health")).status_code == 200
