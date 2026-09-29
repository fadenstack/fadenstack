"""A host tool gets the name, not the token, when tokenize mode is on.

The model sees ``[PERSON_1]`` in place of "Alice" and calls the client's tool
with it; the client must get "Alice" back in the arguments, streamed or whole.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway.llm_adapter import CompletionResult, LLMAdapter
from tests.test_gateway_pipeline_combined import ALIAS, _install_pii, _Observability, _pii, _seed, _token

TOOL = {"type": "function", "function": {"name": "open_contact", "parameters": {"type": "object"}}}


def _model_calls_the_tool(monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, Any]]) -> None:
    arguments = json.dumps({"name": "[PERSON_1]"})

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        sent.append(kwargs["payload"])
        if kwargs.get("stream"):
            async def pieces() -> AsyncIterator[bytes]:
                head = {"id": "c", "object": "chat.completion.chunk", "model": ALIAS}
                first = {"index": 0, "id": "call_1", "type": "function",
                         "function": {"name": "open_contact", "arguments": arguments[:13]}}
                rest = {"index": 0, "function": {"arguments": arguments[13:]}}
                for fragment in (first, rest):
                    body = {**head, "choices": [{"index": 0, "delta": {"tool_calls": [fragment]}, "finish_reason": None}]}
                    yield f"data: {json.dumps(body)}\n\n".encode()
                end = {**head, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
                yield f"data: {json.dumps(end)}\n\n".encode()
                yield b"data: [DONE]\n\n"
            return pieces()
        return CompletionResult(status_code=200, payload={
            "id": "c", "object": "chat.completion", "model": ALIAS,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "open_contact", "arguments": arguments}}],
            }}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    monkeypatch.setattr(LLMAdapter, "completion", completion)


def _streamed_arguments(sse: str) -> str:
    out = []
    for line in sse.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            for choice in json.loads(line[6:]).get("choices", []):
                for call in (choice.get("delta") or {}).get("tool_calls") or []:
                    out.append((call.get("function") or {}).get("arguments") or "")
    return "".join(out)


@pytest.mark.anyio
@pytest.mark.parametrize("stream", [False, True], ids=["whole", "streamed"])
async def test_the_client_tool_gets_the_name(
    stream: bool, fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=_pii("tokenize_reversible"))
    fastapi_app.state.gateway_observability = _Observability()
    _install_pii(monkeypatch)
    sent: list[dict[str, Any]] = []
    _model_calls_the_tool(monkeypatch, sent)

    r = await client.post("/v1/chat/completions", headers={"Authorization": f"Bearer {_token()}"}, json={
        "model": ALIAS, "stream": stream, "tools": [TOOL],
        "messages": [{"role": "user", "content": "Open the contact of Alice"}],
    })
    assert r.status_code == 200, r.text
    assert "Alice" not in json.dumps(sent[0]), "the model never sees the name"
    if stream:
        arguments = _streamed_arguments(r.text)
    else:
        arguments = r.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {"name": "Alice"}
