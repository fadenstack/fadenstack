"""``/v1/services`` is for signed-in callers, like the other /v1 routes.

It ran a health check against every enabled module for anyone, and told them which modules the install runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_gateway.services.gateway.auth import AuthContext, get_auth_context

pytestmark = pytest.mark.anyio


@pytest.fixture
def registry(fastapi_app: FastAPI) -> SimpleNamespace:
    fake = SimpleNamespace(check_health=AsyncMock(), to_dict=lambda: {"services": [{"name": "pii"}]})
    fastapi_app.state.service_registry = fake
    fastapi_app.state.http_client = None
    return fake


async def test_the_manifest_needs_a_sign_in(client: AsyncClient, registry: SimpleNamespace) -> None:
    response = await client.get("/v1/services")
    assert response.status_code == 401
    registry.check_health.assert_not_awaited()


async def test_a_signed_in_caller_gets_the_manifest(
    fastapi_app: FastAPI, client: AsyncClient, registry: SimpleNamespace,
) -> None:
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="u1", tenant_id="default", raw_claims={}, token="t",
    )
    try:
        response = await client.get("/v1/services")
    finally:
        fastapi_app.dependency_overrides.pop(get_auth_context, None)
    assert response.status_code == 200
    assert response.json() == {"services": [{"name": "pii"}]}
