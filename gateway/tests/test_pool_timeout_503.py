"""An exhausted database pool is the gateway over capacity: 503 with Retry-After, not a bare 500."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.exc import TimeoutError as PoolTimeoutError


@pytest.mark.anyio
async def test_pool_timeout_answers_503_in_the_openai_error_shape(fastapi_app: FastAPI, client: AsyncClient) -> None:
    async def boom() -> None:
        raise PoolTimeoutError("QueuePool limit of size 5 overflow 3 reached, connection timed out, timeout 30.00")

    fastapi_app.add_api_route("/_test_pool_timeout", boom)
    resp = await client.get("/_test_pool_timeout")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "5"
    err = resp.json()["error"]
    assert err["type"] == "server_error"
    assert err["code"] == "gateway_overloaded"
    assert "QueuePool" not in resp.text
