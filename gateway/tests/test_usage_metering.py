"""What a request used, and what it reserves against the caps.

The provider's counts with their cached and reasoning parts, and the gateway's estimate.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from faden_gateway.services.gateway import answer_variety
from faden_gateway.services.gateway.stream import wrap_sse_stream
from faden_gateway.services.gateway.usage import (
    DEFAULT_OUTPUT_RESERVATION,
    NO_USAGE,
    add_usage,
    estimated_usage,
    reserved_tokens,
    usage_from_payload,
)


def test_openai_and_vllm_details_are_read() -> None:
    usage = usage_from_payload({"usage": {
        "prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 100},
        "completion_tokens_details": {"reasoning_tokens": 64},
    }})
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (120, 80, 200)
    assert (usage.cached_tokens, usage.reasoning_tokens, usage.cache_write_tokens) == (100, 64, None)
    assert usage.reported and usage.tokens == 200


def test_anthropics_cache_counts_through_litellm_are_read() -> None:
    usage = usage_from_payload({"usage": {
        "prompt_tokens": 2000, "completion_tokens": 10, "total_tokens": 2010,
        "cache_read_input_tokens": 1500, "cache_creation_input_tokens": 400,
        "prompt_tokens_details": {"cached_tokens": None},
    }})
    assert (usage.cached_tokens, usage.cache_write_tokens) == (1500, 400)
    # LiteLLM's own name for the cache write, when only that is there.
    assert usage_from_payload({"usage": {
        "prompt_tokens": 5, "prompt_tokens_details": {"cache_creation_tokens": 3},
    }}).cache_write_tokens == 3


def test_no_usage_and_odd_values_are_none() -> None:
    assert usage_from_payload({}) == NO_USAGE and not NO_USAGE.reported and NO_USAGE.tokens == 0
    odd = usage_from_payload({"usage": {
        "prompt_tokens": True, "completion_tokens": "7", "completion_tokens_details": {"reasoning_tokens": False},
    }})
    assert (odd.prompt_tokens, odd.completion_tokens, odd.reasoning_tokens) == (None, 7, None)


@pytest.mark.anyio
async def test_a_stream_keeps_the_last_usage_with_its_details() -> None:
    async def events() -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"Hi"}}],"usage":null}\n\n'
        yield (
            b'data: {"choices":[],"usage":{"prompt_tokens":9,"completion_tokens":30,'
            b'"prompt_tokens_details":{"cached_tokens":8},"completion_tokens_details":{"reasoning_tokens":20}}}\n\n'
        )
        yield b"data: [DONE]\n\n"

    stream, stats = await wrap_sse_stream(events())
    async for _ in stream:
        pass
    # No total_tokens: the counts are kept all the same (they were dropped before).
    assert (stats.usage.prompt_tokens, stats.usage.completion_tokens) == (9, 30)
    assert (stats.usage.cached_tokens, stats.usage.reasoning_tokens) == (8, 20)


def test_rounds_add_up_with_their_details() -> None:
    total: dict = {}
    add_usage(total, {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 4}})
    add_usage(total, {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27,
                      "completion_tokens_details": {"reasoning_tokens": 3}, "prompt_tokens_details": None})
    add_usage(total, None)
    assert total == {
        "prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 27,
        "prompt_tokens_details": {"cached_tokens": 4}, "completion_tokens_details": {"reasoning_tokens": 3},
    }


def test_split_answers_add_their_cached_and_reasoning_tokens() -> None:
    one = {"choices": [{"message": {"content": "a"}}], "usage": {
        "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
        "prompt_tokens_details": {"cached_tokens": 6}, "completion_tokens_details": {"reasoning_tokens": 1},
    }}
    merged = answer_variety.merged([one, one])
    assert merged["usage"]["prompt_tokens_details"] == {"cached_tokens": 12}
    assert merged["usage"]["completion_tokens_details"] == {"reasoning_tokens": 2}
    assert merged["usage"]["total_tokens"] == 24


def test_an_estimate_counts_four_characters_to_a_token() -> None:
    usage = estimated_usage([{"role": "user", "content": "x" * 400}], "y" * 80)
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (100 + 1, 20, 121)
    assert estimated_usage("abcd", None).completion_tokens == 0


def test_a_chat_reserves_its_answer_and_other_endpoints_their_input() -> None:
    messages = [{"role": "user", "content": "x" * 400}]
    inp = 101  # the role's word and the content
    chat = "/v1/chat/completions"
    assert reserved_tokens({"messages": messages}, chat) == inp + DEFAULT_OUTPUT_RESERVATION == inp + 1024
    assert reserved_tokens({"messages": messages, "max_tokens": 50}, chat) == inp + 50
    assert reserved_tokens({"messages": messages, "max_completion_tokens": 70, "max_tokens": 50}, chat) == inp + 70
    assert reserved_tokens({"messages": messages, "max_tokens": 50, "n": 3}, chat) == inp + 150
    assert reserved_tokens({"messages": messages, "max_tokens": 0}, chat) == inp + 1024
    assert reserved_tokens({"input": "x" * 400}, "/v1/embeddings") == 100
