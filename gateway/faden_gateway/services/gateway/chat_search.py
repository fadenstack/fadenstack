"""A chat's own earlier messages, no longer sent to the model: searched by the model (0.7.2 K, layer 4).

A long chat leaves its oldest messages out of the model's context (``context_assembler``'s batch drop),
and the model used to answer as if they had never been said. They are all still stored with the chat, so
a model that calls tools gets ``search_chat`` to look through the ones left out -- the exact words, not a
model-written summary (Copilot points its model to the full transcript; Amp's ``read_thread``, Letta's
recall storage).

It reads only this chat's stored messages, for the chat's own owner (the gateway resolved the chat for the
caller before the turn began), and only those older than the first message still in the context. It builds
no index and stores nothing: the messages are ranked in memory, by BM25, as ``attachment_search`` ranks a
chat's files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from faden_gateway.services.gateway.attachment_search import AttachmentSearch, SearchableFile

NAME = "search_chat"

#: Passages a search returns at most, whatever the model asks for.
_MAX_RESULTS = 8

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": NAME,
            "description": (
                "Search this chat's earlier messages that are no longer included in the conversation. "
                "Returns the passages that best match the query, each with its message's number, who wrote "
                "it and when. Use it when the question needs something said earlier in this chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to look for, in the words the chat would use.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": f"How many passages to return (1-{_MAX_RESULTS}, default 4).",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


@dataclass(frozen=True, slots=True)
class EarlierMessage:
    """A message of the chat no longer in the model's context: its id, place, writer and text."""

    id: str
    #: Its place in the chat, from 1 for the first message.
    number: int
    #: ``user`` (the person) or ``assistant`` (the model).
    role: str
    #: When it was written (ISO 8601).
    written: str
    text: str


class ChatSearch:
    """Runs ``search_chat`` over the earlier messages of one chat."""

    def __init__(self, messages: list[EarlierMessage]) -> None:
        self.messages = messages

    def run(self, arguments: dict[str, Any]) -> tuple[str, bool]:
        """The tool's answer for the model, and whether it is an error."""
        try:
            return json.dumps(self.search(arguments)), False
        except Exception as exc:  # the model is told, and can go on
            return json.dumps({"error": str(exc)}), True

    def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("search_chat needs a query.")
        top_k = max(1, min(int(arguments.get("top_k") or 4), _MAX_RESULTS))
        if not self.messages:
            return {"query": query, "results": []}
        by_key = {f"message {m.number}": m for m in self.messages}
        found = AttachmentSearch([
            SearchableFile(id=f"chat-message:{m.id}", filename=key, text=m.text) for key, m in by_key.items()
        ]).search({"query": query, "top_k": top_k})
        return {
            "query": query,
            "results": [
                {
                    "message": by_key[hit["file"]].number,
                    "role": by_key[hit["file"]].role,
                    "written": by_key[hit["file"]].written,
                    "score": hit["score"],
                    "text": hit["text"],
                }
                for hit in found["results"]
            ],
        }
