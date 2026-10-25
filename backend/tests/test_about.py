"""GET /api/about: the About dialog's facts, for anyone signed in, never for an anonymous caller."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette import status

from faden_backend.db.models.users import User, current_active_user
from faden_backend.settings import settings


def _signed_in(app: FastAPI) -> None:
    user = User(id=uuid.uuid4(), email="viewer@example.com", hashed_password="x", is_active=True,
                is_verified=True, is_superuser=False)
    app.dependency_overrides[current_active_user] = lambda: user


@pytest.mark.anyio
async def test_a_signed_in_user_reads_the_release(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "install_release", "0.4.5")
    _signed_in(fastapi_app)
    r = await client.get(fastapi_app.url_path_for("about"))
    assert r.status_code == status.HTTP_200_OK
    assert r.json() == {
        "product": "Fadenstack", "edition": "core", "version": "0.4.5", "licence": "Apache-2.0",
        "vendor_links": True, "features": [],
    }


@pytest.mark.anyio
async def test_the_features_an_extension_adds(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console shows a feature's screens only while it is listed."""
    from faden_backend.services.extensions import registry

    monkeypatch.setattr(registry, "features", {"tool_mode_ceilings", "agent_exclusions"})
    _signed_in(fastapi_app)
    r = await client.get(fastapi_app.url_path_for("about"))
    assert r.json()["features"] == ["agent_exclusions", "tool_mode_ceilings"]


@pytest.mark.anyio
async def test_nobody_reads_it_without_signing_in(fastapi_app: FastAPI, client: AsyncClient) -> None:
    """A version on a page anyone can open helps someone looking for an unpatched server."""
    r = await client.get(fastapi_app.url_path_for("about"))
    assert r.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.anyio
async def test_core_keeps_the_links_and_only_another_edition_switches_them_off(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _signed_in(fastapi_app)
    monkeypatch.setattr(settings, "vendor_links", False)
    r = await client.get(fastapi_app.url_path_for("about"))
    assert r.json()["vendor_links"] is True  # core ignores the switch
    monkeypatch.setattr(settings, "edition", "enterprise")
    r = await client.get(fastapi_app.url_path_for("about"))
    assert (r.json()["edition"], r.json()["vendor_links"], r.json()["licence"]) == ("enterprise", False, "")
