"""Put the values PII tokenization took out back into the model's answer.

Tokenize mode sends ``[PERSON_1]`` to the model in place of a name and keeps
the mapping. The answer comes back with the tokens in it, and the client
must get the names. The PII service does this with a plain replacement over
``choices[].message.content`` (``/api/v1/pii/detokenize``); the gateway holds
the mapping already, so it does the same here, without a round trip.

A streamed answer needs more than that, and did not get it: the token mapping
was dropped on the streaming path, so the chat page -- which always streams --
showed "Hello [PERSON_1]". A token can also arrive split across two chunks
(``"[PER"`` then ``"SON_1]"``), so the text that might be the start of one is
held back until the rest arrives or the answer ends.

Tool calls the client runs get their arguments back too: a host tool asked to
"open the file of [PERSON_1]" had nobody to look for. The arguments are JSON,
so the value goes in escaped (a name with a quote must not break them), and
streamed arguments arrive in fragments per call, held back the same way.
Only the client's own tools: the gateway's tools restore where they run.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


def restore_text(text: str, mapping: dict[str, str]) -> str:
    """*text* with every token in *mapping* replaced by its value."""
    for token, value in mapping.items():
        if token in text:
            text = text.replace(token, value)
    return text


def restore_json_text(text: str, mapping: dict[str, str]) -> str:
    """*text*, JSON, with each token replaced by its value escaped for a JSON string."""
    for token, value in mapping.items():
        if token in text:
            text = text.replace(token, json.dumps(value)[1:-1])
    return text


def _is_clients(name: str | None, client_tools: set[str] | None) -> bool:
    """Whether a call to *name* goes to the client (all calls, when unsaid)."""
    return client_tools is None or name is None or name in client_tools


def restore_payload(
    payload: dict[str, Any],
    mapping: dict[str, str] | None,
    client_tools: set[str] | None = None,
) -> dict[str, Any]:
    """A whole (non-streamed) chat completion, with its answer and client tool calls restored."""
    if not mapping or not isinstance(payload.get("choices"), list):
        return payload
    return {**payload, "choices": [_restore_choice(c, mapping, client_tools) for c in payload["choices"]]}


def restore_tool_calls(
    calls: list[Any], mapping: dict[str, str] | None, client_tools: set[str] | None = None,
) -> list[Any]:
    """Tool calls with the values put back into the client's calls' arguments."""
    if not mapping:
        return calls
    out = []
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if (
            isinstance(function, dict)
            and isinstance(function.get("arguments"), str)
            and _is_clients(function.get("name"), client_tools)
        ):
            arguments = restore_json_text(function["arguments"], mapping)
            out.append({**call, "function": {**function, "arguments": arguments}})
        else:
            out.append(call)
    return out


def _restore_choice(choice: Any, mapping: dict[str, str], client_tools: set[str] | None = None) -> Any:
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        return choice
    if isinstance(message.get("tool_calls"), list):
        message = {**message, "tool_calls": restore_tool_calls(message["tool_calls"], mapping, client_tools)}
    content = message.get("content")
    if isinstance(content, str):
        content = restore_text(content, mapping)
    elif isinstance(content, list):
        content = [
            {**part, "text": restore_text(part["text"], mapping)}
            if isinstance(part, dict) and isinstance(part.get("text"), str) else part
            for part in content
        ]
    return {**choice, "message": {**message, "content": content}}


def redact_tokens(payload: dict[str, Any], mapping: dict[str, str] | None) -> dict[str, Any]:
    """A tokenized request with each token as the PII service's redaction.

    ``[PERSON_1]`` becomes ``<PERSON>`` -- what ``mode=redact`` writes -- so
    the copy kept for tracing is the redacted one without scanning the text a
    second time. The mapping is not kept anywhere, so the trace cannot be
    turned back into the values.
    """
    if not mapping:
        return payload
    placeholders = {token: f"<{token[1:-1].rsplit('_', 1)[0]}>" for token in mapping}

    def swap(value: Any) -> Any:
        if isinstance(value, str):
            return restore_text(value, placeholders)
        if isinstance(value, list):
            return [
                {**part, "text": restore_text(part["text"], placeholders)}
                if isinstance(part, dict) and isinstance(part.get("text"), str)
                else swap(part) if isinstance(part, str) else part
                for part in value
            ]
        return value

    out = dict(payload)
    if isinstance(out.get("messages"), list):
        out["messages"] = [
            {**m, "content": swap(m.get("content"))} if isinstance(m, dict) else m
            for m in out["messages"]
        ]
    if "input" in out:
        out["input"] = swap(out["input"])
    return out


#: The streamed text that may carry tokens: the answer, and a reasoning model's thinking.
_FIELDS = ("content", "reasoning_content")


class _Held:
    """The text of one choice that may still turn out to be part of a token."""

    def __init__(self, mapping: dict[str, str], restore: Any = restore_text) -> None:
        self.mapping = mapping
        self.restore = restore
        self.longest = max(len(t) for t in mapping)
        self.pending = ""

    def feed(self, text: str) -> str:
        """Take the next piece; return what is safe to send now, restored."""
        self.pending += text
        cut = self._unfinished_token_start()
        ready, self.pending = self.pending[:cut], self.pending[cut:]
        return self.restore(ready, self.mapping)

    def flush(self) -> str:
        ready, self.pending = self.pending, ""
        return self.restore(ready, self.mapping)

    def _unfinished_token_start(self) -> int:
        """Where a token that has not been closed yet may begin, else the end."""
        start = self.pending.rfind("[")
        if start == -1 or "]" in self.pending[start:]:
            return len(self.pending)
        if len(self.pending) - start >= self.longest:
            return len(self.pending)  # longer than any token: it is not one
        return start


class _Calls:
    """The streamed tool calls of a response: their names, and arguments held back."""

    def __init__(self, mapping: dict[str, str], client_tools: set[str] | None) -> None:
        self.mapping = mapping
        self.client_tools = client_tools
        self.names: dict[tuple[int, int], str] = {}
        self.held: dict[tuple[int, int], _Held] = {}

    def feed(self, choice_index: int, fragments: list[Any]) -> None:
        """Restore the arguments of *fragments* in place, holding back what may be a token."""
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            key = (choice_index, fragment.get("index", 0))
            function = fragment.get("function")
            if not isinstance(function, dict):
                continue
            if function.get("name"):
                self.names[key] = function["name"]
            if not _is_clients(self.names.get(key), self.client_tools):
                continue
            if isinstance(function.get("arguments"), str):
                state = self.held.setdefault(key, _Held(self.mapping, restore_json_text))
                function["arguments"] = state.feed(function["arguments"])

    def flush(self, choice_index: int | None = None) -> dict[int, list[dict[str, Any]]]:
        """Fragments carrying what is still held, per choice (one choice, or all)."""
        out: dict[int, list[dict[str, Any]]] = {}
        for (choice, call), state in self.held.items():
            if state.pending and (choice_index is None or choice == choice_index):
                out.setdefault(choice, []).append({"index": call, "function": {"arguments": state.flush()}})
        return out


async def restore_sse(
    stream: AsyncIterator[bytes],
    mapping: dict[str, str] | None,
    client_tools: set[str] | None = None,
) -> AsyncIterator[bytes]:
    """A streamed chat completion (SSE), with its answer restored as it goes.

    Events pass through as they are, except for the answer text in
    ``choices[].delta.content`` and the arguments of the client's tool calls
    in ``choices[].delta.tool_calls``. What is held back goes out with the
    event that ends the choice, or before ``[DONE]``.
    """
    if not mapping:
        async for chunk in stream:
            yield chunk
        return

    held: dict[tuple[int, str], _Held] = {}
    calls = _Calls(mapping, client_tools)
    last: dict[str, Any] = {}
    buffer = b""
    async for chunk in stream:
        buffer += chunk
        while b"\n\n" in buffer:
            event, buffer = buffer.split(b"\n\n", 1)
            for out in _restore_event(event, held, mapping, last, calls):
                yield out
    if buffer.strip():
        for out in _restore_event(buffer, held, mapping, last, calls):
            yield out
    for out in _flush(held, last, calls):  # a stream that ended without [DONE]
        yield out


def _restore_event(
    event: bytes,
    held: dict[tuple[int, str], _Held],
    mapping: dict[str, str],
    last: dict[str, Any],
    calls: _Calls,
) -> list[bytes]:
    text = event.decode("utf-8", errors="replace").strip()
    if not text.startswith("data:"):
        return [event + b"\n\n"]
    data = text[len("data:"):].strip()
    if data == "[DONE]":
        return [*_flush(held, last, calls), b"data: [DONE]\n\n"]
    try:
        body = json.loads(data)
    except json.JSONDecodeError:
        return [event + b"\n\n"]
    if not isinstance(body, dict) or not isinstance(body.get("choices"), list):
        return [event + b"\n\n"]

    last.update({k: body[k] for k in ("id", "object", "created", "model") if k in body})
    for choice in body["choices"]:
        if not isinstance(choice, dict):
            continue
        index = choice.get("index", 0)
        delta = choice.get("delta")
        # The answer and the model's thinking each: a reasoning model thinks
        # about "[PERSON_1]" as much as it answers about them.
        for field in _FIELDS:
            state = held.setdefault((index, field), _Held(mapping))
            if isinstance(delta, dict) and isinstance(delta.get(field), str):
                delta[field] = state.feed(delta[field])
            if choice.get("finish_reason") is not None and state.pending:
                if not isinstance(delta, dict):
                    delta = choice["delta"] = {}
                delta[field] = (delta.get(field) or "") + state.flush()
        _restore_calls(choice, index, calls)
    return [f"data: {json.dumps(body)}\n\n".encode()]


def _restore_calls(choice: dict[str, Any], index: int, calls: _Calls) -> None:
    """The choice's tool call fragments restored; the held rest added when it ends."""
    delta = choice.get("delta")
    if isinstance(delta, dict) and isinstance(delta.get("tool_calls"), list):
        calls.feed(index, delta["tool_calls"])
    if choice.get("finish_reason") is None:
        return
    rest = calls.flush(index).get(index)
    if rest:
        if not isinstance(delta, dict):
            delta = choice["delta"] = {}
        delta["tool_calls"] = [*(delta.get("tool_calls") or []), *rest]


def _flush(
    held: dict[tuple[int, str], _Held], last: dict[str, Any], calls: _Calls | None = None,
) -> list[bytes]:
    """An event carrying whatever is still held, for each choice and field."""
    out = []
    for (index, field), state in held.items():
        if state.pending:
            choice = {"index": index, "delta": {field: state.flush()}, "finish_reason": None}
            body = {**last, "choices": [choice]}
            out.append(f"data: {json.dumps(body)}\n\n".encode())
    for index, fragments in (calls.flush() if calls is not None else {}).items():
        choice = {"index": index, "delta": {"tool_calls": fragments}, "finish_reason": None}
        out.append(f"data: {json.dumps({**last, 'choices': [choice]})}\n\n".encode())
    return out
