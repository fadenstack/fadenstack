"""Assemble the context window for a session-aware chat completion.

The assembler builds the final ``messages`` list injected into the upstream
LLM request by fitting the following segments into a configurable token
budget (ordered by priority):

1. System instructions (project-level ``system_instructions``)
2. Latest rolling summary (compressed older history)
3. Active memory facts (user / project / session scope)
3.5. Attached document/image context
4. RAG context (if present - already injected earlier in pipeline)
5. Recent messages (most recent N turns from the session)
6. Current user message (always included)
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from faden_gateway.db.dao.session_dao import SessionDAO
from faden_gateway.db.models.gateway import (
    TURNS_LEFT_OUT,
    ChatAttachment,
    ChatMessage,
    ChatProject,
    ExtractionStatus,
    MemoryFact,
    SessionSummary,
)
from faden_gateway.services.gateway.file_store import FileStore

logger = logging.getLogger(__name__)

# Rough token estimate for an image content part (~85 tokens).
_IMAGE_TOKEN_ESTIMATE = 85


@dataclass(slots=True)
class AssembledContext:
    """Result of context assembly."""

    messages: list[dict[str, Any]]
    summary_used: SessionSummary | None = None
    facts_used: list[MemoryFact] = field(default_factory=list)
    attachments_used: list[ChatAttachment] = field(default_factory=list)
    #: Files too long to include, for the model to search (``attachment_search``).
    searchable: list[ChatAttachment] = field(default_factory=list)
    #: Pictures not sent: the turn's model does not take pictures.
    pictures_left_out: list[ChatAttachment] = field(default_factory=list)
    total_token_estimate: int = 0


def in_context(msg: Any) -> bool:
    """Whether a stored message goes to the model (04-skills-and-governance §8).

    A refused prompt (``blocked_input``) is left out whole; of a refused
    answer (``blocked_output``) the question stays and the answer goes. A
    reply that failed, was cut off by a restart or is still being written is
    the chat's record, not part of the conversation (``TURNS_LEFT_OUT``).
    """
    return getattr(msg, "status", None) not in TURNS_LEFT_OUT


def merge_consecutive_users(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adjacent user messages as one: strict chat templates refuse roles that do not alternate.

    They follow each other once a blocked or failed turn's answer is left out.
    """
    merged: list[dict[str, Any]] = []
    for message in messages:
        last = merged[-1] if merged else None
        if last is None or last.get("role") != "user" or message.get("role") != "user":
            merged.append(message)
            continue
        before, after = last.get("content"), message.get("content")
        if isinstance(before, str) and isinstance(after, str):
            content: Any = f"{before}\n\n{after}"
        else:
            content = [*_parts(before), *_parts(after)]
        merged[-1] = {**last, **message, "content": content}
    return merged


def _parts(content: Any) -> list[Any]:
    if isinstance(content, list):
        return content
    return [{"type": "text", "text": content or ""}]


def _replayed_exchange(msg: Any) -> list[dict[str, Any]]:
    """The tool calls and results that led to a saved reply, to put before it.

    With the reply's text alone the model saw tool requests answered without
    tools, and stopped calling them after a turn or two. Only an assistant
    reply carries them (``tool_call_json`` = ``{"exchange": [...]}``).
    """
    record = getattr(msg, "tool_call_json", None)
    if msg.role != "assistant" or not isinstance(record, dict):
        return []
    exchange = record.get("exchange")
    if not isinstance(exchange, list):
        return []
    return [_parseable(m) for m in exchange if isinstance(m, dict)]


def _parseable(message: dict[str, Any]) -> dict[str, Any]:
    """*message* with each tool call's arguments as JSON vLLM can parse.

    vLLM parses them for the chat template and refuses the request when it
    cannot; one malformed call kept in the history would refuse every later
    turn of the chat.
    """
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return message
    fixed = []
    for call in calls:
        function = dict(call.get("function") or {})
        try:
            parsed = json.loads(function.get("arguments") or "{}")
        except (TypeError, ValueError):
            parsed = {}
        function["arguments"] = json.dumps(parsed if isinstance(parsed, dict) else {})
        fixed.append({**call, "function": function})
    return {**message, "tool_calls": fixed}


def _estimate_tokens(content: str | list[Any]) -> int:
    """Rough token estimation (~4 chars per token for text, ~85 per image)."""
    if isinstance(content, str):
        return max(1, len(content) // 4)
    # Multimodal content parts list
    total = 0
    for part in content:
        if isinstance(part, dict):
            if part.get("type") == "text":
                total += max(1, len(part.get("text", "")) // 4)
            elif part.get("type") == "image_url":
                total += _IMAGE_TOKEN_ESTIMATE
            else:
                total += 10
        else:
            total += 10
    return max(1, total)


class ContextAssembler:
    """Build the context window for a session-aware request."""

    def __init__(
        self,
        *,
        dao: SessionDAO,
        max_recent_messages: int = 20,
        token_budget: int = 4096,
        file_store: FileStore | None = None,
        searchable: bool = False,
        vision: Callable[[], Awaitable[bool | None]] | None = None,
    ) -> None:
        self.dao = dao
        self.max_recent_messages = max_recent_messages
        self.token_budget = token_budget
        self.file_store = file_store
        #: Whether a file too long to include can be searched by the model.
        self.searchable = searchable
        #: Whether the turn's model takes pictures (True, False, None for not known). Asked once,
        #: and only when the chat has a picture: a turn without one costs no lookup.
        self._vision = vision
        self._sees_pictures: bool | None = None
        self._vision_asked = False

    async def assemble(
        self,
        *,
        session_id: uuid.UUID,
        tenant_id: str,
        user_id: str,
        current_messages: list[dict[str, Any]],
        project: ChatProject | None = None,
    ) -> AssembledContext:
        """Assemble the full context window.

        ``current_messages`` contains the messages from the current request
        (typically just the latest user message).
        """
        result = AssembledContext(messages=[])
        budget = self.token_budget
        total_tokens = 0

        # 1. System instructions from project
        if project and project.system_instructions:
            sys_tokens = _estimate_tokens(project.system_instructions)
            result.messages.append({
                "role": "system",
                "content": project.system_instructions,
            })
            total_tokens += sys_tokens
            budget -= sys_tokens

        # 2. Rolling summary
        summary = await self.dao.get_latest_summary(session_id=session_id)
        if summary:
            summary_tokens = summary.token_estimate or _estimate_tokens(
                summary.summary_text,
            )
            if summary_tokens <= budget:
                result.messages.append({
                    "role": "system",
                    "content": f"[Session Summary]\n{summary.summary_text}",
                })
                result.summary_used = summary
                total_tokens += summary_tokens
                budget -= summary_tokens

        # 3. Memory facts
        facts = await self._collect_facts(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            project_id=project.id if project else None,
        )
        if facts:
            fact_lines = [f"- {f.key}: {f.value}" for f in facts]
            fact_block = "[Memory]\n" + "\n".join(fact_lines)
            fact_tokens = _estimate_tokens(fact_block)
            if fact_tokens <= budget:
                result.messages.append({
                    "role": "system",
                    "content": fact_block,
                })
                result.facts_used = facts
                total_tokens += fact_tokens
                budget -= fact_tokens

        # 3.5. Attached document/image context
        budget, total_tokens = await self._inject_attachments(
            result=result,
            session_id=session_id,
            project_id=project.id if project else None,
            budget=budget,
            total_tokens=total_tokens,
        )

        # 4. Recent messages from history (after the summary cutoff point)
        after_id = summary.last_message_id if summary else None
        recent = await self.dao.get_recent_messages(
            session_id=session_id,
            limit=self.max_recent_messages,
        )
        # If we have a summary, only include messages after the summary cutoff
        if after_id and recent:
            recent = [m for m in recent if m.created_at > summary.created_at]
        recent = [m for m in recent if in_context(m)]

        # The newest turns that fit, kept in order. Filled from the oldest,
        # the budget ran out on the old turns and the latest ones -- the ones
        # the question follows from -- were the ones left out.
        kept: list[dict[str, Any]] = []
        for msg in reversed(recent):
            exchange = _replayed_exchange(msg)
            msg_tokens = (msg.token_estimate or _estimate_tokens(msg.content)) + sum(
                _estimate_tokens(json.dumps(m)) for m in exchange
            )
            if msg_tokens > budget:
                break
            kept.append({
                "role": msg.role,
                "content": msg.content,
            })
            # Before the reply, newest first like the rest: reversed below.
            kept.extend(reversed(exchange))
            total_tokens += msg_tokens
            budget -= msg_tokens
        result.messages.extend(reversed(kept))

        # 5. Current request messages (always included)
        for msg in current_messages:
            content = msg.get("content", "")
            msg_tokens = _estimate_tokens(content)
            result.messages.append(msg)
            total_tokens += msg_tokens

        result.messages = merge_consecutive_users(result.messages)
        result.total_token_estimate = total_tokens
        return result

    async def _inject_attachments(
        self,
        *,
        result: AssembledContext,
        session_id: uuid.UUID,
        project_id: uuid.UUID | None,
        budget: int,
        total_tokens: int,
    ) -> tuple[int, int]:
        """Inject attachment context (documents as text, images as base64)."""
        attachments: list[ChatAttachment] = []
        attachments.extend(
            await self.dao.list_attachments_for_session(session_id=session_id),
        )
        if project_id:
            project_atts = await self.dao.list_attachments_for_project(
                project_id=project_id,
            )
            seen = {a.id for a in attachments}
            attachments.extend(a for a in project_atts if a.id not in seen)

        if not attachments:
            return budget, total_tokens

        for att in attachments:
            if att.extraction_status == ExtractionStatus.COMPLETED and att.extracted_text:
                # Document attachment → inject as system message
                truncation_note = ""
                if att.truncated:
                    truncation_note = " (truncated)"
                text_block = f"[Attached: {att.filename}{truncation_note}]\n{att.extracted_text}"
                text_tokens = _estimate_tokens(text_block)
                if text_tokens > budget:
                    budget, total_tokens = self._too_long(result, att, text_tokens, budget, total_tokens)
                    continue
                result.messages.append({
                    "role": "system",
                    "content": text_block,
                })
                result.attachments_used.append(att)
                total_tokens += text_tokens
                budget -= text_tokens

            elif att.extraction_status == ExtractionStatus.SKIPPED and self.file_store:
                budget, total_tokens = await self._inject_picture(
                    result, att, self.file_store, budget, total_tokens,
                )

        return budget, total_tokens

    async def _inject_picture(
        self,
        result: AssembledContext,
        att: ChatAttachment,
        file_store: FileStore,
        budget: int,
        total_tokens: int,
    ) -> tuple[int, int]:
        """An image attachment as an image_url content part, for a model that may take pictures."""
        if await self._takes_pictures() is False:
            return self._picture_left_out(result, att, budget, total_tokens)
        try:
            img_bytes = await file_store.get_bytes(att.storage_key)
            b64 = base64.b64encode(img_bytes).decode("ascii")
            data_uri = f"data:{att.content_type};base64,{b64}"
            img_tokens = _IMAGE_TOKEN_ESTIMATE
            if img_tokens > budget:
                return budget, total_tokens
            result.messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": f"[Attached image: {att.filename}]"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            })
            result.attachments_used.append(att)
            total_tokens += img_tokens
            budget -= img_tokens
        except Exception:
            logger.warning(
                "Failed to read image attachment %s", att.filename, exc_info=True,
            )
        return budget, total_tokens

    async def _takes_pictures(self) -> bool | None:
        """Whether the turn's model takes pictures; asked of ``vision`` the first time only."""
        if self._vision is None:
            return None
        if not self._vision_asked:
            self._vision_asked = True
            self._sees_pictures = await self._vision()
        return self._sees_pictures

    def _picture_left_out(
        self,
        result: AssembledContext,
        att: ChatAttachment,
        budget: int,
        total_tokens: int,
    ) -> tuple[int, int]:
        """A picture for a model that does not take pictures: named, not sent.

        Sent anyway, it can fail the request at a model that cannot read it.
        The model is told, so it does not answer as if nothing had been
        attached; the chat is told through ``pictures_left_out``.
        """
        note = f"[Attached image: {att.filename} -- not sent: this model does not take pictures.]"
        note_tokens = _estimate_tokens(note)
        result.messages.append({"role": "system", "content": note})
        result.pictures_left_out.append(att)
        return budget - note_tokens, total_tokens + note_tokens

    def _too_long(
        self,
        result: AssembledContext,
        att: ChatAttachment,
        tokens: int,
        budget: int,
        total_tokens: int,
    ) -> tuple[int, int]:
        """A file too long for the budget: named, and searched or shown in part.

        It used to be left out without a word, and the model answered as if
        nothing had been attached. A model that can search it is told to; one
        that cannot gets as much of its beginning as half the budget left
        holds, marked as cut, so the history still has room.
        """
        if self.searchable:
            note = (
                f"[Attached: {att.filename}, about {tokens:,} tokens -- too long to include in full. "
                "Search it with the attachment_search tool.]"
            )
            result.searchable.append(att)
        else:
            text = att.extracted_text or ""
            head = text[: max(budget // 2, 0) * 4]
            if len(head) < len(text) and " " in head:
                head = head.rsplit(None, 1)[0]  # not mid-word
            if len(head) >= 400:
                note = (
                    f"[Attached: {att.filename} -- the first {len(head) // 4:,} of about {tokens:,} "
                    f"tokens; the rest did not fit.]\n{head}"
                )
                result.attachments_used.append(att)
            else:
                note = f"[Attached: {att.filename}, about {tokens:,} tokens -- too long to include.]"
        note_tokens = _estimate_tokens(note)
        result.messages.append({"role": "system", "content": note})
        return budget - note_tokens, total_tokens + note_tokens

    async def _collect_facts(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: uuid.UUID,
        project_id: uuid.UUID | None,
    ) -> list[MemoryFact]:
        """Gather active facts from all relevant scopes.

        User, then project, then session facts, each scope in the order of
        its keys. They are part of the prompt's prefix, which vLLM caches only
        while it stays identical: newest first, one new fact reordered the
        block and everything after it. Sorted here, not in SQL, because the
        key is stored encrypted.
        """
        from faden_gateway.db.models.gateway import MemoryFactScope  # noqa: PLC0415

        def in_key_order(scoped: list[MemoryFact]) -> list[MemoryFact]:
            return sorted(scoped, key=lambda fact: (fact.key, str(fact.id)))

        facts: list[MemoryFact] = []

        # User-level facts
        facts.extend(
            in_key_order(
                await self.dao.list_active_facts(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    scope=MemoryFactScope.USER,
                ),
            ),
        )

        # Project-level facts
        if project_id:
            facts.extend(
                in_key_order(
                    await self.dao.list_active_facts(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        scope=MemoryFactScope.PROJECT,
                        project_id=project_id,
                    ),
                ),
            )

        # Session-level facts
        facts.extend(
            in_key_order(
                await self.dao.list_active_facts(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    scope=MemoryFactScope.SESSION,
                    session_id=session_id,
                ),
            ),
        )

        return facts
