"""A model's provider refusing the gateway's credentials is the gateway's problem, not the caller's sign-in.

It reached the caller as a 401 with the provider's words. The SDKs read a 401 as "your token was refused":
they fetch a new token, and drop it on a second 401 or a 403. A wrong provider key in the console so signed
out everyone using that model, and the provider's words can quote part of the organisation's key. It is a
502 now, ``upstream_authentication_failed``, with the provider's words in the gateway's log; the gateway's
own 401s -- the caller's key or sign-in refused -- are as they were.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import litellm
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway.llm_adapter import LLMAdapter
from tests.test_gateway_pipeline_combined import ALIAS, _Observability, _seed, _token
from tests.test_llm_adapter_direct import _engine, _stream

#: What a provider says, with a fragment of the organisation's key in it.
PROVIDER_SAYS = "Incorrect API key provided: sk-proj-****************************7f3a"
REFUSED = {
    "type": "server_error",
    "message": "The model's provider refused the gateway's credentials; ask your administrator to check the provider's key.",
    "param": None,
    "code": "upstream_authentication_failed",
}


def _refusal(kind: str) -> Exception:
    if kind == "401":
        return litellm.exceptions.AuthenticationError(message=PROVIDER_SAYS, llm_provider="openai", model="qwen")
    return litellm.exceptions.PermissionDeniedError(
        message=PROVIDER_SAYS, llm_provider="openai", model="qwen",
        response=httpx.Response(403, request=httpx.Request("POST", "https://api.example.com/v1")),
    )


def _call(**changes: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "provider_type": ProviderType.REMOTE_OPENAI,
        "base_url": "https://api.example.com",
        "api_key_encrypted": None,
        "litellm_provider": None,
        "litellm_model": "qwen",
        "extra_params": None,
    }
    call.update(changes)
    return call


CHAT = {"model": "qwen", "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["401", "403"])
@pytest.mark.parametrize(
    ("method", "patched", "call"),
    [
        ("completion", "acompletion", {"payload": CHAT}),
        ("embedding", "aembedding", {"payload": {"model": "qwen", "input": ["x"]}}),
        ("image_generation", "aimage_generation", {"payload": {"model": "qwen", "prompt": "a lighthouse"}}),
        ("rerank", "arerank", {"requested_model": "qwen", "query": "q", "documents": ["a", "b"]}),
    ],
)
async def test_a_refused_key_is_a_502_with_the_providers_words_in_the_log(
    kind: str, method: str, patched: str, call: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def refuse(**_kwargs: Any) -> Any:
        raise _refusal(kind)

    monkeypatch.setattr(litellm, patched, refuse)
    result = await getattr(LLMAdapter(), method)(**_call(**call))

    assert result.status_code == 502
    assert result.payload["error"] == REFUSED
    assert "7f3a" in caplog.text


@pytest.mark.anyio
async def test_a_stream_through_litellm_says_so_in_its_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(**_kwargs: Any) -> Any:
        raise _refusal("401")

    monkeypatch.setattr(litellm, "acompletion", refuse)
    stream = await LLMAdapter().completion(**_call(payload=CHAT), stream=True)
    events = [json.loads(chunk[len(b"data: "):]) async for chunk in stream if chunk.startswith(b"data: {")]  # type: ignore[union-attr]
    assert events == [{"error": REFUSED}]


@pytest.mark.anyio
@pytest.mark.parametrize("status", [401, 403])
async def test_an_engine_that_refuses_the_gateways_key_says_so_in_the_stream(
    status: int, caplog: pytest.LogCaptureFixture,
) -> None:
    """An engine started with --api-key and asked with another key: the direct stream's refusal."""
    async with _engine([json.dumps({"error": PROVIDER_SAYS}).encode()], [], status=status) as http:
        out = await _stream(LLMAdapter(http_client=http))
    assert json.loads(out[0][len(b"data: "):]) == {"error": REFUSED}
    assert out[-1] == b"data: [DONE]\n\n"
    assert "7f3a" in caplog.text


@pytest.mark.anyio
async def test_the_caller_gets_a_502_and_their_own_refusals_stay_401(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None)
    fastapi_app.state.gateway_observability = _Observability()

    async def refuse(**_kwargs: Any) -> Any:
        raise _refusal("401")

    monkeypatch.setattr(litellm, "acompletion", refuse)
    body = {"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]}

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json=body)
    assert r.status_code == 502
    assert r.json()["error"] == REFUSED
    assert "7f3a" not in r.text

    # The caller's own token refused: the gateway's 401, as before.
    r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer not-a-token"}, json=body)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_token"
