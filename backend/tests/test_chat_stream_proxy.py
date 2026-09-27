"""The chat proxy passes the gateway's trace id on with a streamed reply.

Without it a new reply's info button (which opens the debug panel by trace
id) did nothing until the page was reloaded.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from faden_backend.services.chat import gateway_client
from faden_backend.services.chat.gateway_client import GatewayChatClient
from faden_backend.web.api.chat import views as chat_views

pytestmark = pytest.mark.anyio()

SSE = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'


def _client(handler: httpx.MockTransport) -> GatewayChatClient:
    client = GatewayChatClient(base_url="http://gateway")
    client._client = httpx.AsyncClient(transport=handler, base_url="http://gateway")  # noqa: SLF001
    return client


async def _read(stream: gateway_client.GatewayStream) -> bytes:
    return b"".join([chunk async for chunk in stream.body])


async def test_the_trace_id_comes_back_before_the_body() -> None:
    def gateway(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer jwt"
        return httpx.Response(200, headers={"x-langfuse-trace-id": "trace-1"}, content=SSE)

    stream = await _client(httpx.MockTransport(gateway)).open_stream_chat({"stream": True}, "jwt")

    assert stream.headers == {"x-langfuse-trace-id": "trace-1"}
    assert await _read(stream) == SSE
    await stream.aclose()  # after the body closed it: harmless


async def test_a_gateway_error_is_an_sse_error_event() -> None:
    def gateway(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down", "type": "rate_limit"}})

    stream = await _client(httpx.MockTransport(gateway)).open_stream_chat({}, "jwt")

    assert stream.headers == {}
    events = (await _read(stream)).decode().split("\n\n")
    error = json.loads(events[0].removeprefix("data: "))["error"]
    assert (error["message"], error["type"], error["status"]) == ("slow down", "rate_limit", 429)
    assert events[1] == "data: [DONE]"


async def test_an_unreachable_gateway_is_an_sse_error_event() -> None:
    def gateway(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    stream = await _client(httpx.MockTransport(gateway)).open_stream_chat({}, "jwt")

    body = (await _read(stream)).decode()
    assert '"code": "gateway_unavailable"' in body
    assert body.endswith("data: [DONE]\n\n")


async def test_the_endpoint_sends_the_trace_id_header(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gateway(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-langfuse-trace-id": "trace-2"}, content=SSE)

    fake = _client(httpx.MockTransport(gateway))
    monkeypatch.setattr(chat_views, "_client", lambda: fake)
    client.cookies.set("fapiauth", "jwt")

    resp = await client.post(
        fastapi_app.url_path_for("chat_completions"),
        json={"model": "m", "stream": True, "messages": []},
    )

    assert resp.status_code == 200
    assert resp.headers["x-langfuse-trace-id"] == "trace-2"
    assert resp.content == SSE


async def test_a_resumed_stream_brings_its_trace_id_and_no_buffer_is_empty() -> None:
    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/sessions/s1/stream":
            return httpx.Response(200, headers={"x-langfuse-trace-id": "trace-3"}, content=SSE)
        return httpx.Response(204)

    client = _client(httpx.MockTransport(gateway))
    resumed = await client.open_stream_resume("s1", "jwt")
    nothing = await client.open_stream_resume("s2", "jwt")

    assert resumed.headers == {"x-langfuse-trace-id": "trace-3"}
    assert await _read(resumed) == SSE
    assert (nothing.headers, await _read(nothing)) == ({}, b"")
