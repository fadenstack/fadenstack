"""What a failure tells an API caller: the upstream's refusal of the request, never the gateway's insides.

``Upstream request failed: {exc}`` named the machine's address and port, a TLS alert, LiteLLM's own words
(its connection error carries a traceback), and pydantic's ``str(exc)`` repeated the body it refused with a
link per error (CodeQL py/stack-trace-exposure, 18 October 2026). The cause now goes to the gateway's log,
with the request id the caller is given to quote. A refusal of the request itself -- too long for the
model's context -- is still said as the upstream said it: the caller can act on it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Literal

import litellm
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway.errors import validation_message
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from faden_gateway.settings import settings
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _token

#: What a machine's connection failure says, with the machine in it.
INSIDES = "TLS handshake with 10.88.10.71:8443 failed: [SSL: TLSV13_ALERT_CERTIFICATE_REQUIRED]"
GENERIC = "The model failed to answer; the gateway's log says why."


# -- the route's own failure ----------------------------------------------------------------


@pytest.mark.anyio
async def test_an_upstream_failure_names_the_request_id_not_the_machine(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()
    monkeypatch.setattr(settings, "retry_pre_first_token", 0)

    async def completion(self: LLMAdapter, **_kwargs: Any) -> Any:
        raise RuntimeError(INSIDES)

    monkeypatch.setattr(LLMAdapter, "completion", completion)

    r = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {_token()}", "X-Request-Id": "req-4711"},
        json={"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert r.status_code == 502
    assert r.json()["error"] == {
        "type": "server_error",
        "message": "Upstream request failed; the gateway's log has the details under request id req-4711.",
        "param": None,
        "code": "upstream_request_failed",
    }
    assert "10.88.10.71" not in r.text
    logged = [record for record in caplog.records if "req-4711" in record.getMessage()]
    assert logged and logged[0].exc_info is not None, "the log has the request id and the traceback"
    assert INSIDES in caplog.text


# -- a refused body ---------------------------------------------------------------------------


class _Message(BaseModel):
    role: Literal["user", "assistant"]


class _Body(BaseModel):
    model: str
    messages: list[_Message]


def test_a_refused_body_is_said_field_by_field_without_what_was_sent() -> None:
    with pytest.raises(ValidationError) as refused:
        _Body.model_validate({"messages": [{"role": "sk-live-s3cr3t"}]})
    assert "s3cr3t" in str(refused.value), "what the caller was sent until now"

    message = validation_message(refused.value)

    assert message == "model: Field required; messages.0.role: Input should be 'user' or 'assistant'"
    assert "pydantic.dev" not in message


def test_a_body_that_is_not_json_has_no_field_to_name() -> None:
    with pytest.raises(ValidationError) as refused:
        _Body.model_validate_json(b'{"model": "s3cr3t", ')
    message = validation_message(refused.value)
    assert message.startswith("Invalid JSON")
    assert "s3cr3t" not in message


@pytest.mark.anyio
async def test_a_route_refuses_a_body_in_those_words(client: AsyncClient) -> None:
    r = await client.post(
        "/v1/images/generations",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"prompt": "a lighthouse", "n": "s3cr3t"},
    )
    assert r.status_code == 400
    error = r.json()["error"]
    assert error["code"] == "validation_error" and error["type"] == "invalid_request_error"
    assert error["message"].startswith("n: ")
    assert "s3cr3t" not in error["message"] and "pydantic.dev" not in error["message"]


# -- the adapter: what LiteLLM and the engines say ------------------------------------------------


def _call(**changes: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "provider_type": ProviderType.VLLM,
        "base_url": "http://vllm.local:8000",
        "api_key_encrypted": None,
        "litellm_provider": None,
        "litellm_model": "qwen",
        "extra_params": None,
    }
    call.update(changes)
    return call


async def _events(stream: Any) -> list[dict[str, Any]]:
    return [
        json.loads(chunk[len(b"data: "):])
        async for chunk in stream
        if chunk.startswith(b"data: {")
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "patched", "call"),
    [
        ("completion", "acompletion", {"payload": {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]}}),
        ("embedding", "aembedding", {"payload": {"model": "qwen", "input": ["x"]}}),
        ("image_generation", "aimage_generation", {"payload": {"model": "qwen", "prompt": "a lighthouse"}}),
        ("rerank", "arerank", {"requested_model": "qwen", "query": "q", "documents": ["a", "b"]}),
    ],
)
async def test_a_failure_in_litellm_is_logged_not_answered(
    method: str, patched: str, call: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail(**_kwargs: Any) -> Any:
        raise litellm.exceptions.APIConnectionError(
            message=f"{INSIDES}\nTraceback (most recent call last): ...", llm_provider="openai", model="qwen",
        )

    monkeypatch.setattr(litellm, patched, fail)
    result = await getattr(LLMAdapter(), method)(**_call(**call))

    assert result.status_code == 502, "the status a caller maps on is the same"
    assert result.payload["error"] == {"type": "server_error", "message": GENERIC, "param": None, "code": "server_error"}
    assert "10.88.10.71" in caplog.text


@pytest.mark.anyio
async def test_a_refusal_of_the_request_is_said_as_the_upstream_said_it(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(**_kwargs: Any) -> Any:
        raise litellm.exceptions.ContextWindowExceededError(
            message="This model's maximum context length is 32768 tokens.", model="qwen", llm_provider="openai",
        )

    monkeypatch.setattr(litellm, "acompletion", refuse)
    payload = {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]}

    whole = await LLMAdapter().completion(**_call(payload=payload))
    assert whole.status_code == 400  # type: ignore[union-attr]
    assert "maximum context length is 32768 tokens" in whole.payload["error"]["message"]  # type: ignore[union-attr]

    # Streamed through LiteLLM (no direct client): the same words, in the stream's error event.
    streamed = await _events(await LLMAdapter().completion(**_call(payload=payload), stream=True))
    assert "maximum context length is 32768 tokens" in streamed[0]["error"]["message"]


@pytest.mark.anyio
async def test_a_stream_through_litellm_that_fails_says_so_without_its_words(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]}

    async def unreachable(**_kwargs: Any) -> Any:
        raise RuntimeError(INSIDES)

    monkeypatch.setattr(litellm, "acompletion", unreachable)
    before = await _events(await LLMAdapter().completion(**_call(payload=payload), stream=True))
    assert before == [{"error": {"type": "server_error", "message": GENERIC, "param": None, "code": "server_error"}}]

    async def breaks(**_kwargs: Any) -> Any:
        async def chunks() -> AsyncIterator[Any]:
            raise RuntimeError(INSIDES)
            yield  # an async generator

        return chunks()

    monkeypatch.setattr(litellm, "acompletion", breaks)
    during = await _events(await LLMAdapter().completion(**_call(payload=payload), stream=True))
    assert during[-1]["error"]["message"] == GENERIC
