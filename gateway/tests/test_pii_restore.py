"""Tokenized values are put back into the answer, streamed or whole."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from faden_gateway.services.gateway.pii_restore import redact_tokens, restore_payload, restore_sse

MAPPING = {"[PERSON_1]": "Alice Meyer", "[EMAIL_ADDRESS_1]": "alice@example.com"}


def _event(content: str | None = None, finish: str | None = None, **extra: Any) -> bytes:
    delta = {} if content is None else {"content": content}
    body = {"id": "c", "object": "chat.completion.chunk", "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
    return f"data: {json.dumps(body)}\n\n".encode()


async def _stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def _collect(chunks: tuple[bytes, ...], mapping: dict[str, str] | None = MAPPING) -> tuple[str, list[bytes]]:
    out = [c async for c in restore_sse(_stream(*chunks), mapping)]
    text = []
    for raw in b"".join(out).decode().split("\n\n"):
        if raw.startswith("data: ") and raw != "data: [DONE]":
            for choice in json.loads(raw[6:]).get("choices", []):
                text.append((choice.get("delta") or {}).get("content") or "")
    return "".join(text), out


@pytest.mark.anyio
async def test_a_token_split_across_chunks_comes_back_whole() -> None:
    text, _ = await _collect((_event("Hello [PER"), _event("SON_1], how"), _event(" are you?"),
                              _event(finish="stop"), b"data: [DONE]\n\n"))
    assert text == "Hello Alice Meyer, how are you?"


@pytest.mark.anyio
async def test_no_piece_of_a_token_is_sent_before_it_is_complete() -> None:
    _, out = await _collect((_event("Mail [EMAIL_ADD"), _event("RESS_1] now"), b"data: [DONE]\n\n"))
    assert b"EMAIL" not in b"".join(out)


@pytest.mark.anyio
async def test_a_bracket_that_is_no_token_is_not_lost() -> None:
    text, _ = await _collect((_event("See [1] and [the notes"), _event(" below]"), _event(finish="stop"),
                              b"data: [DONE]\n\n"))
    assert text == "See [1] and [the notes below]"


@pytest.mark.anyio
async def test_what_is_held_goes_out_when_the_answer_ends() -> None:
    """The model stopped in the middle of something that looked like a token."""
    text, _ = await _collect((_event("Ends with [PERS"), b"data: [DONE]\n\n"))
    assert text == "Ends with [PERS"

    text, _ = await _collect((_event("Ends with [PERS"), _event(finish="length")))
    assert text == "Ends with [PERS"

    text, _ = await _collect((_event("No DONE [PERS"),))
    assert text == "No DONE [PERS"


@pytest.mark.anyio
async def test_the_rest_of_each_event_is_kept() -> None:
    _, out = await _collect((_event("Hi [PERSON_1]", usage=None),
                             _event(finish="stop", usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}),
                             b"data: [DONE]\n\n"))
    last = json.loads(out[-2].decode()[6:])
    assert last["usage"]["total_tokens"] == 5
    assert last["choices"][0]["finish_reason"] == "stop"
    assert out[-1] == b"data: [DONE]\n\n"


@pytest.mark.anyio
async def test_without_a_mapping_the_stream_is_untouched() -> None:
    chunks = (_event("Hi [PERSON_1]"), b"data: [DONE]\n\n")
    _, out = await _collect(chunks, mapping=None)
    assert out == list(chunks)


def test_a_whole_answer_is_restored() -> None:
    payload = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi [PERSON_1]"}}], "usage": {}}
    assert restore_payload(payload, MAPPING)["choices"][0]["message"]["content"] == "Hi Alice Meyer"
    assert payload["choices"][0]["message"]["content"] == "Hi [PERSON_1]", "the original is not changed"


def test_a_tokenized_request_becomes_the_redacted_one() -> None:
    request = {"model": "m", "input": ["Mail [EMAIL_ADDRESS_1]"], "messages": [
        {"role": "user", "content": "I am [PERSON_1]"},
        {"role": "user", "content": [{"type": "text", "text": "Hi [PERSON_1]"}, {"type": "image_url", "image_url": {}}]},
    ]}
    out = redact_tokens(request, MAPPING)
    assert out["messages"][0]["content"] == "I am <PERSON>"
    assert out["messages"][1]["content"][0]["text"] == "Hi <PERSON>"
    assert out["messages"][1]["content"][1] == {"type": "image_url", "image_url": {}}
    assert out["input"] == ["Mail <EMAIL_ADDRESS>"]
    assert request["messages"][0]["content"] == "I am [PERSON_1]", "the original is not changed"


# -- the client's tool calls ------------------------------------------------------------

QUOTED = {"[PERSON_2]": 'Sean "Jack" O\'Brien'}


def _call_event(fragment: dict[str, Any], finish: str | None = None) -> bytes:
    body = {"id": "c", "object": "chat.completion.chunk", "model": "m",
            "choices": [{"index": 0, "delta": {"tool_calls": [fragment]}, "finish_reason": finish}]}
    return f"data: {json.dumps(body)}\n\n".encode()


def _arguments(out: list[bytes]) -> dict[int, str]:
    """The arguments each streamed call carried, joined."""
    args: dict[int, str] = {}
    for raw in b"".join(out).decode().split("\n\n"):
        if raw.startswith("data: ") and raw != "data: [DONE]":
            for choice in json.loads(raw[6:]).get("choices", []):
                for call in (choice.get("delta") or {}).get("tool_calls") or []:
                    piece = (call.get("function") or {}).get("arguments") or ""
                    args[call["index"]] = args.get(call["index"], "") + piece
    return args


@pytest.mark.anyio
async def test_a_client_tools_arguments_are_restored_across_fragments() -> None:
    chunks = (
        _call_event({"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "open_file", "arguments": '{"owner": "[PER'}}),
        _call_event({"index": 0, "function": {"arguments": 'SON_1]", "mail": "[EMAIL_ADDRESS_1]"}'}}),
        _event(finish="tool_calls"),
        b"data: [DONE]\n\n",
    )
    out = [c async for c in restore_sse(_stream(*chunks), MAPPING, {"open_file"})]
    assert b"PER" not in b"".join(out)
    assert json.loads(_arguments(out)[0]) == {"owner": "Alice Meyer", "mail": "alice@example.com"}


@pytest.mark.anyio
async def test_held_arguments_go_out_when_the_call_ends() -> None:
    chunks = (
        _call_event({"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "open_file", "arguments": '{"owner": "[PERSON_1'}}),
        _call_event({"index": 0, "function": {"arguments": ""}}, finish="tool_calls"),
    )
    out = [c async for c in restore_sse(_stream(*chunks), MAPPING, {"open_file"})]
    assert _arguments(out)[0] == '{"owner": "[PERSON_1', "an unfinished token is not a token: kept as sent"


@pytest.mark.anyio
async def test_a_value_is_escaped_inside_the_arguments() -> None:
    chunks = (
        _call_event({"index": 0, "id": "call_1", "type": "function",
                     "function": {"name": "greet", "arguments": '{"name": "[PERSON_2]"}'}}),
        b"data: [DONE]\n\n",
    )
    out = [c async for c in restore_sse(_stream(*chunks), QUOTED, {"greet"})]
    assert json.loads(_arguments(out)[0]) == {"name": 'Sean "Jack" O\'Brien'}


@pytest.mark.anyio
async def test_only_the_clients_tools_and_not_the_sdks_own_tokens() -> None:
    chunks = (
        _call_event({"index": 0, "id": "a", "type": "function",
                     "function": {"name": "gateway_tool", "arguments": '{"who": "[PERSON_1]"}'}}),
        _call_event({"index": 1, "id": "b", "type": "function",
                     "function": {"name": "open_file", "arguments": '{"who": "[[fs:PERSON_1]]"}'}}),
        b"data: [DONE]\n\n",
    )
    out = [c async for c in restore_sse(_stream(*chunks), MAPPING, {"open_file"})]
    args = _arguments(out)
    assert json.loads(args[0]) == {"who": "[PERSON_1]"}, "not the client's: left for the gateway"
    assert json.loads(args[1]) == {"who": "[[fs:PERSON_1]]"}, "masked on the device: left alone"


def test_a_whole_answers_tool_calls_are_restored() -> None:
    payload = {"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None,
        "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "greet", "arguments": '{"name": "[PERSON_2]"}'}},
            {"id": "b", "type": "function", "function": {"name": "other", "arguments": '{"name": "[PERSON_2]"}'}},
        ],
    }}]}
    calls = restore_payload(payload, QUOTED, {"greet"})["choices"][0]["message"]["tool_calls"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"name": 'Sean "Jack" O\'Brien'}
    assert json.loads(calls[1]["function"]["arguments"]) == {"name": "[PERSON_2]"}
