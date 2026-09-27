"""A reasoning model's thinking reaches the client, on every streamed path.

Found with OpenRouter on the release VM (2026-09-27): deepseek-v4.1-flash
thinks for 0.4-2.8 s before it answers. On a chat, where the gateway offers
its tools, nothing of that reached the client -- the chat showed nothing
until the answer -- while a request without tools got LiteLLM's raw delta:
the thinking twice (``reasoning`` and ``reasoning_content``) and LiteLLM's own
null fields.
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
from faden_gateway.services.gateway.llm_adapter import LLMAdapter, openai_chunk
from faden_gateway.services.gateway.pii_restore import restore_sse
from tests.test_gateway_knowledge_tools import CAN_CALL_TOOLS, _ask, _chunk
from tests.test_gateway_pipeline_combined import _Observability, _seed


def test_litellms_delta_becomes_the_openai_one_with_the_thinking_once() -> None:
    raw = {"id": "c", "object": "chat.completion.chunk", "choices": [{"index": 0, "finish_reason": None, "delta": {
        "content": None, "role": "assistant", "function_call": None, "tool_calls": None, "audio": None,
        "provider_specific_fields": {"reasoning": "Let me think"}, "reasoning": "Let me think",
        "reasoning_content": "Let me think", "refusal": None,
    }}]}
    assert openai_chunk(raw)["choices"][0]["delta"] == {"role": "assistant", "reasoning_content": "Let me think"}
    # OpenRouter's name alone is taken too; an answer chunk keeps its text and tool calls.
    only = {"choices": [{"delta": {"reasoning": "Hmm", "content": None}}]}
    assert openai_chunk(only)["choices"][0]["delta"] == {"reasoning_content": "Hmm"}
    call = [{"index": 0, "function": {"arguments": "{}"}}]
    answer = {"choices": [{"delta": {"content": "Hi", "tool_calls": call, "reasoning_content": None}}]}
    assert openai_chunk(answer)["choices"][0]["delta"] == {"content": "Hi", "tool_calls": call}


def _thinking_then(answer: str) -> Any:
    async def stream() -> AsyncIterator[bytes]:
        for piece in ("The user wants ", "three sentences."):
            yield _chunk({"reasoning_content": piece})
        for i in range(0, len(answer), 5):
            yield _chunk({"content": answer[i:i + 5]})
        yield _chunk({}, finish="stop", usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})
        yield b"data: [DONE]\n\n"

    return stream()


@pytest.mark.anyio
async def test_the_thinking_reaches_a_chat_that_was_offered_tools(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.REMOTE_OPENAI, pii=None, node_metadata=CAN_CALL_TOOLS)
    fastapi_app.state.gateway_observability = _Observability()
    sent: list[dict[str, Any]] = []

    async def completion(self: LLMAdapter, **kwargs: Any) -> Any:
        sent.append(kwargs["payload"])
        return _thinking_then("A plan names the parts.")

    monkeypatch.setattr(LLMAdapter, "completion", completion)
    r = await _ask(client, stream=True)
    assert r.status_code == 200
    assert sent[0].get("tools"), "the gateway's tools were offered: the tool-round path"
    deltas = [
        choice.get("delta") or {}
        for line in r.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"
        for choice in json.loads(line[6:]).get("choices", [])
    ]
    thinking = "".join(d.get("reasoning_content") or "" for d in deltas)
    answer = "".join(d.get("content") or "" for d in deltas)
    assert thinking == "The user wants three sentences."
    assert answer == "A plan names the parts."
    first_answer = next(i for i, d in enumerate(deltas) if d.get("content"))
    assert all(not d.get("content") for d in deltas[:first_answer]) and deltas[0].get("reasoning_content")


@pytest.mark.anyio
async def test_a_token_in_the_thinking_is_put_back_too() -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield _chunk({"reasoning_content": "The user, [PER"})
        yield _chunk({"reasoning_content": "SON_1], asks"})
        yield _chunk({"content": "Hello [PERSON_1]"})
        yield _chunk({}, finish="stop")
        yield b"data: [DONE]\n\n"

    out = b"".join([c async for c in restore_sse(stream(), {"[PERSON_1]": "Alice Meyer"})]).decode()
    deltas = [
        choice.get("delta") or {}
        for line in out.split("\n\n") if line.startswith("data: ") and line != "data: [DONE]"
        for choice in json.loads(line[6:]).get("choices", [])
    ]
    assert "".join(d.get("reasoning_content") or "" for d in deltas) == "The user, Alice Meyer, asks"
    assert "".join(d.get("content") or "" for d in deltas) == "Hello Alice Meyer"
    assert "PERSON" not in out
