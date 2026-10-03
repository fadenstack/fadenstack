"""A streamed chat to vLLM goes straight to the engine, and nothing around it changes.

Through LiteLLM every token became pydantic objects and was dumped back to
JSON; with the per-token generator LiteLLM abandoned, that was about 60 % of
the gateway's CPU at 16 users (2026-09-25). vLLM already speaks OpenAI's
format, so its events now pass through. Everything else the gateway does --
PII out and back, usage, the saved reply -- works on those events, so these
tests hold the events to the same shape and the rest to the same results.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import LLMGatewayRequestLog, ProviderType
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from faden_gateway.settings import settings
from tests.test_gateway_pipeline_combined import (
    ALIAS,
    _history,
    _install_pii,
    _Observability,
    _pii,
    _seed,
    _session,
    _token,
)

BASE = "http://vllm.local:8000"


def _event(delta: dict[str, Any] | None = None, **extra: Any) -> bytes:
    body: dict[str, Any] = {"id": "c", "object": "chat.completion.chunk", "model": "qwen", **extra}
    if delta is not None:
        body["choices"] = [{"index": 0, "delta": delta, "finish_reason": None}]
    return f"data: {json.dumps(body)}\n\n".encode()


USAGE = _event(choices=[], usage={"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17})


def _engine(chunks: list[bytes], seen: list[httpx.Request], status: int = 200) -> httpx.AsyncClient:
    """A vLLM that answers with *chunks*, one network read each, and keeps what it was sent."""

    async def body() -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, headers={"content-type": "text/event-stream"}, content=body())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _stream(adapter: LLMAdapter, **changes: Any) -> list[bytes]:
    call: dict[str, Any] = {
        "provider_type": ProviderType.VLLM,
        "base_url": BASE,
        "api_key_encrypted": None,
        "litellm_provider": None,
        "litellm_model": None,
        "extra_params": None,
        "payload": {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]},
        "stream": True,
        "instance_id": None,
    }
    call.update(changes)
    stream = await adapter.completion(**call)
    return [chunk async for chunk in stream]  # type: ignore[union-attr]


# ── What the engine is sent ─────────────────────────────────────────


@pytest.mark.anyio
async def test_the_engine_is_asked_as_an_openai_server() -> None:
    seen: list[httpx.Request] = []
    async with _engine([_event({"content": "ok"}), b"data: [DONE]\n\n"], seen) as http:
        await _stream(LLMAdapter(http_client=http), payload={
            "model": "qwen",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "tools": [{"type": "function", "function": {"name": "f"}}],
            "session_id": "not for the engine",
        })

    (request,) = seen
    assert str(request.url) == f"{BASE}/v1/chat/completions", "the /v1 the backend strips is put back"
    assert request.headers["authorization"] == "Bearer EMPTY"
    body = json.loads(request.content)
    assert body == {
        "model": "qwen",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.2,
        "tools": [{"type": "function", "function": {"name": "f"}}],
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ({"reasoning_effort": "none"}, {"chat_template_kwargs": {"enable_thinking": False}}),
        ({"reasoning_effort": "minimal"}, {"chat_template_kwargs": {"enable_thinking": False}}),
        ({"reasoning_effort": "high"}, {"reasoning_effort": "high"}),
        ({"reasoning_effort": "xhigh"}, {"reasoning_effort": "high"}),
        ({"chat_template_kwargs": {"enable_thinking": False}}, {"chat_template_kwargs": {"enable_thinking": False}}),
        (
            {"reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": True, "x": 1}},
            {"chat_template_kwargs": {"enable_thinking": True, "x": 1}},
        ),
        ({"reasoning_effort": "unheard-of"}, {}),
    ],
    ids=["none", "minimal", "high", "xhigh", "template", "template-wins", "unknown"],
)
async def test_how_much_the_model_reasons_reaches_vllm(given: dict[str, Any], sent: dict[str, Any]) -> None:
    """A client's "no thinking" reaches the engine as Qwen's switch; vLLM's own efforts go as they are."""
    seen: list[httpx.Request] = []
    async with _engine([b"data: [DONE]\n\n"], seen) as http:
        await _stream(LLMAdapter(http_client=http), payload={"model": "qwen", "messages": [{"role": "user", "content": "hi"}], **given})
    body = json.loads(seen[0].content)
    assert {k: body[k] for k in ("reasoning_effort", "chat_template_kwargs") if k in body} == sent


@pytest.mark.anyio
async def test_reasoning_through_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through LiteLLM: OpenAI's field for other providers, vLLM's own fields as extra_body (unknown ones are dropped)."""
    calls = _litellm_answers(monkeypatch)
    payload = {"model": "qwen", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "none"}
    await _stream(LLMAdapter(), provider_type=ProviderType.REMOTE_OPENAI, payload=payload)
    await _stream(LLMAdapter(), extra_params={"api_version": "2024-01"}, payload=payload)
    remote, vllm = calls
    assert remote["reasoning_effort"] == "none" and "chat_template_kwargs" not in remote
    assert "reasoning_effort" not in vllm and vllm["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.anyio
async def test_a_providers_headers_go_along() -> None:
    seen: list[httpx.Request] = []
    async with _engine([b"data: [DONE]\n\n"], seen) as http:
        await _stream(LLMAdapter(http_client=http), extra_params={"extra_headers": {"X-Tenant": "a"}})
    assert seen[0].headers["x-tenant"] == "a"


# ── What comes back ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_events_arrive_whole_however_the_network_cuts_them() -> None:
    first, second = _event({"content": "Hello"}), _event({"content": " world"})
    cut = len(first) // 2
    chunks = [
        first[:cut],  # one event over two reads
        first[cut:] + second,  # its end and a whole one in the same read
        b": keep-alive\n\n",  # a comment the engine sends to hold the line
        USAGE.replace(b"\n\n", b"\r\n\r\n"),  # CRLF line ends are SSE too
        b"data: [DONE]\n\n",
    ]
    async with _engine(chunks, []) as http:
        out = await _stream(LLMAdapter(http_client=http))
    assert out == [first, second, USAGE, b"data: [DONE]\n\n"]


@pytest.mark.anyio
async def test_a_stream_that_ends_without_done_is_closed_once() -> None:
    async with _engine([_event({"content": "cut short"})], []) as http:
        out = await _stream(LLMAdapter(http_client=http))
    assert out[-1] == b"data: [DONE]\n\n"
    assert out.count(b"data: [DONE]\n\n") == 1


@pytest.mark.anyio
async def test_an_engine_that_refuses_becomes_an_error_event() -> None:
    async with _engine([b'{"error": "context length exceeded"}'], [], status=400) as http:
        out = await _stream(LLMAdapter(http_client=http))
    assert out[-1] == b"data: [DONE]\n\n"
    error = json.loads(out[0][len(b"data: "):])["error"]
    assert error["type"] == "server_error"
    assert "upstream 400" in error["message"] and "context length exceeded" in error["message"]


@pytest.mark.anyio
async def test_an_engine_that_cannot_be_reached_becomes_an_error_event() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as http:
        out = await _stream(LLMAdapter(http_client=http))
    assert json.loads(out[0][len(b"data: "):])["error"]["type"] == "server_error"
    assert out[-1] == b"data: [DONE]\n\n"


# ── What keeps LiteLLM ──────────────────────────────────────────────


def _litellm_answers(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def through_litellm(self: LLMAdapter, **kwargs: Any) -> AsyncIterator[bytes]:
        calls.append(kwargs)
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(LLMAdapter, "_stream_completion", through_litellm)
    return calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("change", "why"),
    [
        (lambda: {"provider_type": ProviderType.OLLAMA}, "not vLLM"),
        (lambda: {"provider_type": ProviderType.REMOTE_OPENAI}, "LiteLLM rewrites parameters per model"),
        (lambda: {"extra_params": {"api_version": "2024-01"}}, "an argument meant for LiteLLM"),
        # A TLS setting of the model's own no longer keeps LiteLLM: the stream
        # goes direct over a client made with it (tests/test_upstream_tls.py,
        # against a real server that wants the certificate).
    ],
    ids=["ollama", "remote-openai", "litellm-params"],
)
async def test_what_needs_litellm_keeps_it(
    change: Callable[[], dict[str, Any]], why: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _litellm_answers(monkeypatch)
    seen: list[httpx.Request] = []
    async with _engine([b"data: [DONE]\n\n"], seen) as http:
        await _stream(LLMAdapter(http_client=http), **change())
    assert len(calls) == 1 and not seen, why


@pytest.mark.anyio
async def test_https_under_a_default_ca_bundle_keeps_litellm(monkeypatch: pytest.MonkeyPatch) -> None:
    """The outbound CA bundle is configured for LiteLLM only."""
    calls = _litellm_answers(monkeypatch)
    monkeypatch.setattr(settings, "tls_outbound_default_ca_bundle", "/etc/ssl/company-ca.pem")
    async with _engine([b"data: [DONE]\n\n"], []) as http:
        await _stream(LLMAdapter(http_client=http), base_url="https://vllm.local:8443")
    assert len(calls) == 1


@pytest.mark.anyio
async def test_the_setting_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _litellm_answers(monkeypatch)
    monkeypatch.setattr(settings, "vllm_direct_stream", False)
    async with _engine([b"data: [DONE]\n\n"], []) as http:
        await _stream(LLMAdapter(http_client=http))
    assert len(calls) == 1


# ── Through the gateway ─────────────────────────────────────────────


@pytest.mark.anyio
async def test_pii_usage_and_the_saved_reply_work_on_the_direct_stream(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tokens out and back, usage in the log, the reply in the session: all from vLLM's own events."""
    await _seed(db_session, provider=ProviderType.VLLM, pii=_pii("tokenize_reversible"))
    fastapi_app.state.gateway_observability = _Observability()
    _install_pii(monkeypatch)
    sid = await _session(db_session)
    answer = _event({"content": "[PERSON_1] leads the launch."})
    cut = answer.index(b"PERSON") + 3  # the token itself cut by the network
    seen: list[httpx.Request] = []
    engine = _engine([_event({"role": "assistant", "content": ""}), answer[:cut], answer[cut:], USAGE,
                      b"data: [DONE]\n\n"], seen)
    fastapi_app.state.http_client = engine

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": True, "session_id": str(sid),
        "messages": [{"role": "user", "content": "Who leads the launch, Alice?"}],
    })
    await engine.aclose()

    assert r.status_code == 200
    sent = json.loads(seen[0].content)
    assert "Alice" not in json.dumps(sent["messages"]) and "[PERSON_1]" in json.dumps(sent["messages"])
    text = "".join(
        (json.loads(line[6:]).get("choices") or [{}])[0].get("delta", {}).get("content") or ""
        for line in r.text.splitlines() if line.startswith("data: {")
    )
    assert text == "Alice leads the launch."
    assert r.text.count("data: [DONE]") == 1
    assert await _history(db_session, sid) == [
        ("user", "Who leads the launch, Alice?"), ("assistant", "Alice leads the launch."),
    ]
    row = (await db_session.execute(
        select(LLMGatewayRequestLog).where(LLMGatewayRequestLog.session_id == str(sid)),
    )).scalar_one()
    assert (row.prompt_tokens, row.completion_tokens, row.status_code) == (12, 5, 200)


# ── How long a reply may take to start, and to go on ────────────────


def _slow_engine(first_after: float, then_after: float = 0.0) -> httpx.AsyncClient:
    """A vLLM that holds a request *first_after* seconds before it starts, as its queue does when the KV
    cache is full, and pauses *then_after* seconds before its second event."""

    async def body() -> AsyncIterator[bytes]:
        await asyncio.sleep(first_after)
        yield _event({"role": "assistant", "content": "Hel"})
        await asyncio.sleep(then_after)
        yield _event({"content": "lo"})
        yield b"data: [DONE]\n\n"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _text(chunks: list[bytes]) -> str:
    return "".join(
        (json.loads(line[6:]).get("choices") or [{}])[0].get("delta", {}).get("content") or ""
        for chunk in chunks for line in chunk.decode().splitlines() if line.startswith("data: {")
    )


def _error(chunks: list[bytes]) -> str | None:
    for chunk in chunks:
        for line in chunk.decode().splitlines():
            if line.startswith("data: {") and "error" in json.loads(line[6:]):
                return json.loads(line[6:])["error"]["message"]
    return None


@pytest.mark.anyio
async def test_a_reply_may_take_its_models_wait_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Longer than the wait between events: a busy 27B failed such replies at 60 s (2026-10-01)."""
    monkeypatch.setattr(settings, "litellm_request_timeout", 0.05)
    async with _slow_engine(first_after=0.2) as http:
        chunks = await _stream(LLMAdapter(http_client=http), first_token_timeout_sec=1.0)
    assert _error(chunks) is None
    assert _text(chunks) == "Hello"


@pytest.mark.anyio
async def test_a_reply_that_does_not_start_in_time_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "first_token_timeout_sec", 0.1)  # no wait of the model's own: the default
    async with _slow_engine(first_after=2.0) as http:
        chunks = await _stream(LLMAdapter(http_client=http))
    assert _error(chunks) == "The model did not start its reply within 0.1 s; it may be busy."
    assert chunks[-1] == b"data: [DONE]\n\n"


@pytest.mark.anyio
async def test_a_reply_that_stops_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once started, the wait between events is the short one: a long first wait never covers a hang."""
    monkeypatch.setattr(settings, "litellm_request_timeout", 0.1)
    async with _slow_engine(first_after=0.0, then_after=2.0) as http:
        chunks = await _stream(LLMAdapter(http_client=http), first_token_timeout_sec=30.0)
    assert _text(chunks) == "Hel"
    assert _error(chunks) == "The model stopped sending its reply for 0.1 s."
    assert chunks[-1] == b"data: [DONE]\n\n"


@pytest.mark.parametrize(
    ("hints", "expected"),
    [
        ({"first_token_timeout_sec": 300}, 300.0),
        ({"first_token_timeout_sec": 0}, None),
        ({"first_token_timeout_sec": "300"}, None),
        ({}, None),
        (None, None),
    ],
)
def test_a_models_own_wait_comes_from_its_routing_row(hints: dict[str, Any] | None, expected: float | None) -> None:
    from faden_gateway.services.gateway.service import _first_token_wait  # noqa: PLC0415

    class _Candidate:
        capacity_hints = hints

    assert _first_token_wait(_Candidate()) == expected
