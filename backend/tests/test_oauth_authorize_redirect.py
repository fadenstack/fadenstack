"""Sign-in with a provider goes to the auth module's address for that provider, and only there."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.settings import settings

pytestmark = pytest.mark.anyio


async def test_authorize_redirects_to_the_auth_module(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "auth_service_url", "https://sso.example.test/")
    provider_id = uuid.uuid4()

    resp = await client.get(f"/api/admin/auth-providers/{provider_id}/authorize", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == f"https://sso.example.test/api/providers/{provider_id}/authorize"

    resp = await client.get("/api/admin/auth-providers/evil.example/authorize", follow_redirects=False)
    assert resp.status_code == 422  # a provider id is a UUID
