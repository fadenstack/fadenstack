"""Chat projects have no limit: a person can make as many as they like.

A limit is only there when a plugin sets one; reaching it says so plainly.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.web.lifespan import resource_capacity

pytestmark = pytest.mark.anyio


def _as(fastapi_app: FastAPI, user: str) -> None:
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id=user, tenant_id="t1", raw_claims={},
    )


def test_the_gateway_starts_with_no_limits() -> None:
    assert resource_capacity(None) == {}


def test_a_plugin_may_set_one() -> None:
    class Plugin:
        def override_resource_capacity(self, capacity: dict[str, int | None]) -> None:
            capacity["projects"] = 5

    assert resource_capacity(Plugin()) == {"projects": 5}


async def test_a_fourth_and_fifth_project_are_made(fastapi_app: FastAPI, client: AsyncClient) -> None:
    fastapi_app.state._resource_capacity = resource_capacity(None)  # noqa: SLF001
    _as(fastapi_app, "u-many")
    for n in range(5):
        r = await client.post("/v1/sessions/projects", json={"name": f"project {n}"})
        assert r.status_code == 201, r.text
    r = await client.get("/v1/sessions/capacity")
    assert r.json()["projects"] == {"current": 5, "limit": None}


async def test_a_limit_a_plugin_sets_is_named_plainly(fastapi_app: FastAPI, client: AsyncClient) -> None:
    fastapi_app.state._resource_capacity = {"projects": 1}  # noqa: SLF001
    _as(fastapi_app, "u-one")
    assert (await client.post("/v1/sessions/projects", json={"name": "first"})).status_code == 201
    r = await client.post("/v1/sessions/projects", json={"name": "second"})
    assert r.status_code == 402
    assert "this server allows 1 projects per person" in r.text
    assert "Enterprise" not in r.text
    fastapi_app.state._resource_capacity = {}  # noqa: SLF001
