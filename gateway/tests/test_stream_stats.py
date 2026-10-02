"""A stream's stats read the model's chunks, not the gateway's own events."""

from collections.abc import AsyncIterator

import pytest

from faden_gateway.services.gateway import faden_events
from faden_gateway.services.gateway.stream import wrap_sse_stream

_ANSWER = (
    b'data: {"choices": [{"index": 0, "delta": {"content": "No."}, "finish_reason": "stop"}], '
    b'"usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}}\n\n'
)


async def _chunks(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


@pytest.mark.anyio
async def test_a_gateway_tool_that_failed_is_not_the_answer_failing() -> None:
    # A refused tool rule, as the agent route streams it: the answer itself is whole.
    end = faden_events.sse_event({
        "type": "TOOL_CALL_END", "toolCallId": "c1", "toolCallName": "delete_page", "durationMs": 12, "error": True,
    })
    stream, stats = await wrap_sse_stream(_chunks(end, _ANSWER, b"data: [DONE]\n\n"))
    assert [chunk async for chunk in stream] == [end, _ANSWER, b"data: [DONE]\n\n"]
    assert stats.error is None, "logged as upstream_refused, and a chat turn stored as failed"
    assert stats.usage.total_tokens == 6


@pytest.mark.anyio
async def test_the_models_error_event_is_still_seen() -> None:
    stream, stats = await wrap_sse_stream(_chunks(b'data: {"error": {"message": "context too long"}}\n\n'))
    _ = [chunk async for chunk in stream]
    assert stats.error == "context too long"
