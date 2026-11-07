import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette import status


@pytest.mark.anyio
async def test_health(client: AsyncClient, fastapi_app: FastAPI) -> None:
    """
    Checks the health endpoint.

    :param client: client for the app.
    :param fastapi_app: current FastAPI application.
    """
    url = fastapi_app.url_path_for("health_check")
    response = await client.get(url)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_pii_options(client: AsyncClient) -> None:
    response = await client.get("/api/v1/pii/options")
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert isinstance(body.get("supported_entities"), list)
    assert body["default_language"] == "en"
    assert body["default_score_threshold"] > 0


@pytest.mark.anyio
async def test_pii_options_list_only_languages_with_models(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    class _Service:
        supported_languages = ["en", "de"]

    fastapi_app.state.pii_service = _Service()
    response = await client.get("/api/v1/pii/options")
    assert response.json()["supported_languages"] == ["en", "de"]


@pytest.mark.anyio
async def test_pii_options_say_what_a_policy_naming_no_types_gets(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    """Per language, so the console can show it next to an empty list of types."""

    standard = {"en": ["PERSON", "US_SSN"], "de": ["PERSON"]}

    class _Service:
        supported_languages = ("en", "de")

        def default_entities(self, language: str) -> list[str]:
            return standard[language]

    fastapi_app.state.pii_service = _Service()
    response = await client.get("/api/v1/pii/options")
    assert response.json()["default_entities"] == standard


@pytest.mark.anyio
async def test_pii_options_without_service_offer_default_language_only(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/pii/options")
    assert response.json()["supported_languages"] == ["en"]
