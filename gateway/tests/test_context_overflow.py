"""A request longer than the model's context window gets one stable code (0.7.2 K, layer 6).

vLLM, Ollama and the providers say it in their own words, and the gateway passed those on: a whole answer as "400
<their text>", a streamed one as "upstream 400: <their text>". An SDK and the chat page retry with less of the
conversation when it happens, so they need one code whoever said it: 400 ``context_length_exceeded``, in a whole
answer and in the stream's error event alike. The chat page's retry asks for 0.75 of the budget (``context_factor``).
"""

from __future__ import annotations

import json
from typing import Any

import litellm
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import ProviderType
from faden_gateway.services.gateway import llm_adapter
from faden_gateway.services.gateway.context_assembler import DROPPED_PREFIX
from faden_gateway.services.gateway.llm_adapter import CONTEXT_OVERFLOW, is_context_overflow
from faden_gateway.services.gateway.service import _context_factor, _stream_outcome
from faden_gateway.services.gateway.stream import StreamStats, wrap_sse_stream
from tests.test_chat_search import _long_chat
from tests.test_gateway_knowledge_tools import _ask, _Model, _say
from tests.test_gateway_pipeline_combined import _Observability, _seed

VLLM_SAYS = (
    "This model's maximum context length is 32768 tokens. However, you requested 41000 tokens "
    "(37000 in the messages, 4000 in the completion). Please reduce the length of the messages or completion."
)


@pytest.mark.parametrize("said", [
    VLLM_SAYS,
    "The decoder prompt (length 40000) is longer than the maximum model length of 32768.",
    "the input length exceeds the context length",
    "Error code: 400 - {'error': {'code': 'context_length_exceeded'}}",
    "prompt is too long: 210000 tokens > 200000 maximum",
])
def test_the_engines_ways_of_saying_it_are_one(said: str) -> None:
    assert is_context_overflow(said)


@pytest.mark.parametrize("said", [
    "Invalid value for 'temperature': must be at most 2.",
    "Unknown model qwen-x",
    "tool_choice is not supported",
])
def test_other_refusals_are_not_it(said: str) -> None:
    assert not is_context_overflow(said)


def test_litellms_own_exception_is_it_whatever_it_says() -> None:
    exc = litellm.exceptions.ContextWindowExceededError(message="nope", model="m", llm_provider="openai")
    assert is_context_overflow(exc)


def test_a_streamed_refusal_says_it_with_the_code() -> None:
    error = llm_adapter._engine_refusal(400, VLLM_SAYS)["error"]  # noqa: SLF001
    assert (error["type"], error["code"]) == ("invalid_request_error", CONTEXT_OVERFLOW)
    assert "context window" in error["message"]
    other = llm_adapter._engine_refusal(400, "Unknown model qwen-x")["error"]  # noqa: SLF001
    assert other["code"] != CONTEXT_OVERFLOW and "Unknown model" in other["message"]
    exc = litellm.exceptions.BadRequestError(message=VLLM_SAYS, model="m", llm_provider="openai")
    assert llm_adapter._failure(exc)["error"]["code"] == CONTEXT_OVERFLOW  # noqa: SLF001


@pytest.mark.anyio
async def test_a_whole_answers_refusal_is_a_400_with_the_code(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(**_: Any) -> Any:
        raise litellm.exceptions.BadRequestError(message=VLLM_SAYS, model="m", llm_provider="openai")

    monkeypatch.setattr(litellm, "acompletion", refused)
    result = await llm_adapter.LLMAdapter()._non_stream_completion(model="openai/m", messages=[])  # noqa: SLF001
    assert result.status_code == 400
    assert result.payload["error"]["code"] == CONTEXT_OVERFLOW


@pytest.mark.anyio
async def test_the_streams_error_event_is_logged_as_the_overflow_it_was() -> None:
    async def events() -> Any:
        yield f"data: {json.dumps(llm_adapter._engine_refusal(400, VLLM_SAYS))}\n\n".encode()  # noqa: SLF001
        yield b"data: [DONE]\n\n"

    wrapped, stats = await wrap_sse_stream(events())
    async for _ in wrapped:
        pass
    assert stats.error_code == CONTEXT_OVERFLOW
    assert _stream_outcome(stats, 200, None) == (400, CONTEXT_OVERFLOW)
    plain = StreamStats(error="upstream 500: busy")
    assert _stream_outcome(plain, 200, None) == (502, "upstream_refused")


@pytest.mark.parametrize(("asked", "factor"), [
    (0.75, 0.75), (1, 1.0), (0.5, 0.5), (0.4, 1.0), (1.5, 1.0), (True, 1.0), ("0.75", 1.0), (None, 1.0),
])
def test_the_share_asked_for_is_half_to_all(asked: object, factor: float) -> None:
    assert _context_factor(asked) == factor


@pytest.mark.anyio
async def test_the_chat_pages_retry_sends_less_of_the_chat(
    fastapi_app: FastAPI, client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed(db_session, provider=ProviderType.VLLM, pii=None,
                node_metadata={"task": "chat", "tools": False, "context_length": 16_384})
    fastapi_app.state.gateway_observability = _Observability()
    sid = await _long_chat(db_session, count=120)
    model = _Model(monkeypatch, _say("ok"), _say("ok"))

    def dropped(payload: dict[str, Any]) -> int:
        [line] = [str(m["content"]) for m in payload["messages"] if str(m.get("content")).startswith(DROPPED_PREFIX)]
        return int(line.split()[2])

    assert (await _ask(client, session_id=str(sid))).status_code == 200
    assert (await _ask(client, session_id=str(sid), context_factor=0.75)).status_code == 200
    first, retried = model.sent
    assert dropped(retried) > dropped(first)
    assert "context_factor" not in retried, "not the model's field"
