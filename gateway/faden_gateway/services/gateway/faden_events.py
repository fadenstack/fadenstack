"""What the gateway did on its own during a request, for a UI and for the SDK.

A streamed answer showed only the answer: the knowledge searches, MCP calls
and skill files the gateway ran in between were read and dropped, so a UI
could not say "searching the knowledge base" and an agent's SDK -- which keeps
the conversation on the device -- lost those rounds for the next request.

A client that sends ``X-Faden-Events: ag-ui`` gets them, in AG-UI's event
vocabulary, as named SSE events (``event: faden``) in the stream, or as a
``faden`` object beside a whole answer. Clients that do not ask get the plain
OpenAI stream: an OpenAI SDK would not know what to do with a named event.

- ``TOOL_CALL_START`` / ``TOOL_CALL_END``: a gateway tool ran (name, kind,
  duration, whether it failed).
- ``CUSTOM`` ``faden.citations``: the sources a knowledge search returned --
  document, chunk, score; not the text.
- ``CUSTOM`` ``faden.pii``: what the PII scan found -- types and counts only.
- ``CUSTOM`` ``faden.gateway_round``: the round as the model saw it (its
  tool-calling turn and the tools' answers, with PII values put back), for
  the SDK to keep and send back as history. The gateway accepts it back.
- ``CUSTOM`` ``faden.attachment_left_out``: a file of the chat that was not
  sent to the model, and why (``no_vision``: a picture, and the model does not
  take pictures).
"""

from __future__ import annotations

import json
from typing import Any

from faden_gateway.services.gateway import attachment_search, knowledge, skill_files

HEADER = "x-faden-events"
_ACCEPTED = frozenset({"ag-ui", "1", "true", "yes"})


def wanted(value: str | None) -> bool:
    """Whether the ``X-Faden-Events`` header asks for the events."""
    return (value or "").strip().lower() in _ACCEPTED


def sse_event(event: dict[str, Any]) -> bytes:
    """One named SSE event, apart from the OpenAI chunks."""
    return f"event: faden\ndata: {json.dumps(event)}\n\n".encode()


def tool_kind(name: str) -> str:
    """Where a gateway tool's answer comes from, for a UI's label."""
    if name in knowledge.NAMES:
        return "knowledge"
    if name == attachment_search.NAME:
        return "attachment"
    if name == skill_files.NAME:
        return "skill_file"
    if name.startswith("mcp") or "." in name:
        return "mcp"
    return "system"


def tool_start(call: dict[str, Any], name: str) -> dict[str, Any]:
    """A gateway tool is about to run."""
    return {
        "type": "TOOL_CALL_START",
        "toolCallId": call.get("id", ""),
        "toolCallName": name,
        "kind": tool_kind(name),
    }


def tool_end(call: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """A gateway tool ran: how long, and whether it failed (from its audit row)."""
    return {
        "type": "TOOL_CALL_END",
        "toolCallId": call.get("id", ""),
        "toolCallName": row.get("tool_name"),
        "durationMs": row.get("latency_ms"),
        "error": bool(row.get("is_error")),
    }


def citations(name: str, content: str) -> dict[str, Any] | None:
    """The sources a knowledge tool's answer names, without their text."""
    if name not in knowledge.NAMES:
        return None
    try:
        body = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    found = body.get("results") if name == knowledge.SEARCH else [body]
    keys = ("source", "document_id", "chunk", "chunks", "score")
    sources = [
        {k: item.get(k) for k in keys if item.get(k) is not None}
        for item in found or []
        if isinstance(item, dict)
    ]
    if not sources:
        return None
    return {"type": "CUSTOM", "name": "faden.citations", "value": {"tool": name, "sources": sources}}


def pii_notice(types: dict[str, int], mode: str | None) -> dict[str, Any] | None:
    """What the PII scan found, by type; ``None`` when it found nothing."""
    if not types:
        return None
    value = {"mode": mode, "entities": dict(sorted(types.items()))}
    return {"type": "CUSTOM", "name": "faden.pii", "value": value}


def picture_left_out(attachment_id: str, filename: str, model: str) -> dict[str, Any]:
    """A picture of the chat not sent: *model* does not take pictures."""
    return {"type": "CUSTOM", "name": "faden.attachment_left_out", "value": {
        "attachmentId": attachment_id,
        "filename": filename,
        "model": model,
        "reason": "no_vision",
        "message": f"{filename} was not sent: {model} does not take pictures. Choose a model that does.",
    }}


def gateway_round(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """A round the gateway ran, for the client to keep and send back."""
    return {"type": "CUSTOM", "name": "faden.gateway_round", "value": {"messages": messages}}
