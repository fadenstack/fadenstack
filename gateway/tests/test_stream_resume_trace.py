"""A resumed stream carries its trace id, as the stream it resumes did.

A chat reloaded mid-reply reconnects to the buffered stream; without the
trace id that reply's info button did nothing until another reload.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.stream_buffer import StreamBuffer

pytestmark = pytest.mark.anyio


async def test_the_resumed_stream_sends_the_trace_id(fastapi_app: FastAPI, client: AsyncClient) -> None:
    buf = StreamBuffer()
    buf.start("s1", trace_id="tr-9")
    buf.push("s1", b'data: {"choices":[{"delta":{"content":"po"}}]}\n\n')
    buf.finish("s1")
    fastapi_app.state.stream_buffer = buf
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="u1", tenant_id="t1", raw_claims={},
    )

    resp = await client.get("/v1/sessions/s1/stream")

    assert resp.status_code == 200
    assert resp.headers["x-langfuse-trace-id"] == "tr-9"
    assert b'"po"' in resp.content


async def test_a_stream_started_without_one_sends_none(fastapi_app: FastAPI, client: AsyncClient) -> None:
    buf = StreamBuffer()
    buf.start("s2")
    buf.finish("s2")
    fastapi_app.state.stream_buffer = buf
    fastapi_app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        user_id="u1", tenant_id="t1", raw_claims={},
    )

    resp = await client.get("/v1/sessions/s2/stream")

    assert "x-langfuse-trace-id" not in resp.headers
