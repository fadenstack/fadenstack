"""A long chat's rolling summary, written when its earlier messages stop going to the model (0.7.2 K, layer 5).

The context builder leaves a long chat's oldest messages out in batches (``context_assembler.batch_cut``). Where
an administrator switched summaries on for the chat's model (``chat_settings``; all are off by default), the
gateway asks that model to fold the messages just left out into the chat's summary -- anchored and incremental:
the earlier summary and the new messages in, one summary out, in a fixed template (the person's goal and
constraints, decisions, files and sources, open questions). A batch drop already costs one full recompute of the
prompt, so summarising exactly then adds no second one.

The summary is written through the gateway's own answer path (``GatewayService._summarise``): checked for personal
data as any request is, on a route that marks what it writes, logged as a request, and signed as AI-written
content of the kind ``summarised``. It is stored encrypted with the chat and deleted with it. In the context it
stands under a header that says a model wrote it and that it may be incomplete. Approvals, permissions and the
tool mode are never taken from it: they are read from their own state every turn, and the template tells the
model to leave them out. When writing it fails, the chat goes on with the line in place of the left-out messages.
"""

from __future__ import annotations

from typing import Any

from faden_gateway.db.models.gateway import ChatMessage
from faden_gateway.services.gateway.context_assembler import SUMMARY_HEADER, in_context

__all__ = ["MAX_TOKENS", "SUMMARY_HEADER", "as_context", "prompt"]
#: The longest summary, in tokens.
MAX_TOKENS = 1024

_SYSTEM = (
    "You keep the running summary of a chat between a person and an assistant. You get the earlier summary, if "
    "there is one, and the messages to add to it. Write the updated summary in four parts, as short lines:\n"
    "Goal and constraints: what the person wants, and every condition, name, number and date they set.\n"
    "Decisions: what was agreed or done.\n"
    "Files and sources: the files, documents and links the chat used, by name.\n"
    "Open questions: what is not answered yet.\n"
    "Keep everything from the earlier summary that still holds, and add what the new messages say. Do not invent "
    "anything. Never record an approval, a permission or a tool mode: they are not part of the summary. Write in "
    "the language of the chat. Output only the summary."
)


def prompt(previous: str | None, messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """The request that folds *messages* into the summary *previous* (None for the first)."""
    lines = [f"{m.role}: {m.content}" for m in messages if in_context(m) and m.content]
    parts = [f"[Earlier summary]\n{previous}"] if previous else []
    parts.append("[Messages to add]\n" + "\n\n".join(lines))
    return [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def as_context(summary_text: str) -> str:
    """The summary as the model reads it in the chat's context: under its header."""
    return f"{SUMMARY_HEADER}\n{summary_text}"
