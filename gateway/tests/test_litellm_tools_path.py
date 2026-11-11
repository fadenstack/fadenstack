"""A chat that carries tools goes through the real LiteLLM, whole and streamed.

From 1.108 LiteLLM imports its own MCP handler for every completion with ``tools``, and that handler needs the
``mcp`` package (LiteLLM's ``mcp`` extra, which pyproject.toml asks for). Without it every chat that carried tools
failed with "No module named 'mcp'" (502 "The model failed to answer"), while every other test passed: they all
replace ``litellm.acompletion``. Here nothing is replaced: the adapter calls LiteLLM, and LiteLLM talks to a small
OpenAI-compatible server on localhost.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import web

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look a word up.",
            "parameters": {
                "type": "object",
                "properties": {"word": {"type": "string"}},
                "required": ["word"],
            },
        },
    },
]
MESSAGES = [{"role": "user", "content": "Look up 'thread'."}]


def _answer() -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "echo",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ECHO: thread"},
                "finish_reason": "stop",
            },
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
    }


def _chunk(delta: dict[str, Any], finish: str | None = None) -> bytes:
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "echo",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n".encode()


@pytest.fixture
async def engine() -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
    """An OpenAI-compatible server on 127.0.0.1 that keeps what it was sent; yields its base URL (no /v1)."""
    seen: list[dict[str, Any]] = []

    async def chat(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        seen.append(body)
        if not body.get("stream"):
            return web.json_response(_answer())
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for part in (
            _chunk({"role": "assistant", "content": "ECHO: "}),
            _chunk({"content": "thread"}),
            _chunk({}, finish="stop"),
            b"data: [DONE]\n\n",
        ):
            await response.write(part)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/v1/chat/completions", chat)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield f"http://127.0.0.1:{port}", seen
    finally:
        await runner.cleanup()


async def _complete(base_url: str, *, stream: bool) -> CompletionResult | AsyncIterator[Any]:
    return await LLMAdapter().completion(
        provider_type=ProviderType.VLLM,
        base_url=base_url,
        api_key_encrypted=None,
        litellm_provider=None,
        litellm_model=None,
        extra_params=None,
        payload={"model": "echo", "messages": MESSAGES, "tools": TOOLS},
        stream=stream,
    )


@pytest.mark.anyio
async def test_a_whole_answer_with_tools_comes_back(engine: tuple[str, list[dict[str, Any]]]) -> None:
    base_url, seen = engine
    result = await _complete(base_url, stream=False)
    assert isinstance(result, CompletionResult)
    assert result.status_code == 200, result.payload
    assert result.payload["choices"][0]["message"]["content"] == "ECHO: thread"
    # The tools reached the model.
    assert [tool["function"]["name"] for tool in seen[0]["tools"]] == ["lookup"]


@pytest.mark.anyio
async def test_a_streamed_answer_with_tools_comes_back(engine: tuple[str, list[dict[str, Any]]]) -> None:
    base_url, seen = engine
    stream = await _complete(base_url, stream=True)
    text = b"".join([chunk async for chunk in stream]).decode()  # type: ignore[union-attr]
    assert "ECHO: " in text and "thread" in text, text
    assert "error" not in text, text
    assert [tool["function"]["name"] for tool in seen[0]["tools"]] == ["lookup"]
