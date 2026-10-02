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


async def test_the_chat_asks_for_the_gateways_events_and_answers_approvals() -> None:
    seen: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/v1/approvals/"):
            return httpx.Response(200, json={"approval_id": "a1", "approved": json.loads(request.content)["approved"]})
        return httpx.Response(200, content=SSE)

    client = _client(httpx.MockTransport(gateway))
    stream = await client.open_stream_chat({"stream": True}, "jwt")
    await _read(stream)
    assert seen[0].headers["x-faden-events"] == "ag-ui"
    assert await client.answer_approval("a1", True, "jwt") == {"approval_id": "a1", "approved": True}
    assert seen[1].url.path == "/v1/approvals/a1" and seen[1].headers["authorization"] == "Bearer jwt"


async def test_a_gateway_error_is_an_sse_error_event() -> None:
    def gateway(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down", "type": "rate_limit"}})

    stream = await _client(httpx.MockTransport(gateway)).open_stream_chat({}, "jwt")

    assert stream.headers == {}
    events = (await _read(stream)).decode().split("\n\n")
    error = json.loads(events[0].removeprefix("data: "))["error"]
    assert (error["message"], error["type"], error["status"]) == ("slow down", "rate_limit", 429)
    assert events[1] == "data: [DONE]"


async def test_a_refused_prompt_keeps_its_governance_notice() -> None:
    notice = {"governance": {"outcome": "prompt_blocked", "stage": "input", "message": "Not here.",
                             "decision_id": "d-1", "message_id": "m-1"}}

    def gateway(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={
            "error": {"message": "Not here.", "type": "governance_error", "code": "policy_blocked"},
            "faden": notice,
        })

    stream = await _client(httpx.MockTransport(gateway)).open_stream_chat({}, "jwt")
    event = json.loads((await _read(stream)).decode().split("\n\n")[0].removeprefix("data: "))
    assert (event["error"]["code"], event["error"]["status"]) == ("policy_blocked", 422)
    assert event["faden"] == notice


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


async def test_a_retried_reply_streams_with_its_new_turn_id(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page tries a failed reply again through the proxy, and learns the new reply's row."""
    asked: list[tuple[str, dict[str, object]]] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(200, headers={"x-faden-turn-id": "turn-2", "x-langfuse-trace-id": "t"}, content=SSE)

    fake = _client(httpx.MockTransport(gateway))
    monkeypatch.setattr(chat_views, "_client", lambda: fake)
    client.cookies.set("fapiauth", "jwt")

    resp = await client.post(
        fastapi_app.url_path_for("retry_turn", session_id="s1", turn_id="turn-1"), json={"model": "other"},
    )

    assert resp.status_code == 200 and resp.content == SSE
    assert resp.headers["x-faden-turn-id"] == "turn-2"
    assert asked == [("/v1/sessions/s1/turns/turn-1/retry", {"model": "other"})]


async def test_a_reply_is_stopped_through_the_proxy(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gateway(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/sessions/s1/turns/turn-1/stop"
        return httpx.Response(200, json={"stopping": True})

    fake = _client(httpx.MockTransport(gateway))
    monkeypatch.setattr(chat_views, "_client", lambda: fake)
    client.cookies.set("fapiauth", "jwt")

    resp = await client.post(fastapi_app.url_path_for("stop_turn", session_id="s1", turn_id="turn-1"))

    assert (resp.status_code, resp.json()) == (200, {"stopping": True})
