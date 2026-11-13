import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette import status

from faden_backend.settings import settings


async def test_health(client: AsyncClient, fastapi_app: FastAPI) -> None:
    """
    Checks the health endpoint.

    :param client: client for the app.
    :param fastapi_app: current FastAPI application.
    """
    url = fastapi_app.url_path_for("health_check")
    response = await client.get(url)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.parametrize("environment", ["dev", "production"])
async def test_the_anonymous_health_answer_says_only_ok(
    environment: str, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sdk/51 F01: no environment details to an anonymous caller (ASVS 5.0 V13)."""
    monkeypatch.setattr(settings, "environment", environment)
    response = await client.get("/api/health")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"status": "ok"}
