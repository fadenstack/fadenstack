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

# ── The budget (0.7.2 K; fadenstack-dev reports/"Compaction in Copilot and Open WebUI.md") ─────────────────

#: The prompt budget when the model's context length is not known: the fixed budget of 0.7.1 and before.
FALLBACK_BUDGET = 4096
#: Of the window less the reply's reserve, what the prompt may fill (Copilot keeps the same 10% margin).
MARGIN = 0.9
#: The reply's reserve when the request sets no max_tokens; with thinking on, twice that.
REPLY_RESERVE = 4096
#: The history at most, however long the window: a batch drop there recomputes a long prefix, and a long
#: context answers worse (context rot).
HISTORY_CAP = 32_000
#: A batch drop cuts the history down to this share of its budget, so the cut moves rarely and the prompt's
#: prefix stays the same -- and cached -- between drops (Open WebUI keeps 40%, summarizes 60%).
LOW_WATER = 0.6
#: The project's files go inline while all of them fit this share of the prompt budget; past it the whole
#: project switches to one index line and ``attachment_search`` (as Claude.ai switches whole projects).
PROJECT_FILES_SHARE = 0.25

#: The model's line where the dropped messages were: for a model that can search them (``search_chat``), and
#: for one that cannot. Fixed texts without counters that change between drops, so the prefix stays stable.
DROPPED_SEARCHABLE = (
    "[The first {n} messages of this chat are not included here. They are kept: search them with search_chat "
    "if the question needs them. Do not answer from memory.]"
)
DROPPED = (
    "[The first {n} messages of this chat are not included here. If the question needs them, say so and ask "
    "the person to repeat the detail. Do not guess.]"
)
#: How a dropped-messages line begins, to find it again (``service.py`` words it for the turn's tools).
DROPPED_PREFIX = "[The first "


def prompt_budget(window: int | None, reserve: int, *, cap: int | None = None, factor: float = 1.0) -> int:
    """P = (W - R) x 0.9 for a known window W and the reply's reserve R, at most *cap*; else *cap* or 4,096.

    *factor*: 0.75 for the one retry after the model said the context was too long.
    """
    if window and window > reserve:
        budget = int((window - reserve) * MARGIN)
        if cap:
            budget = min(budget, cap)
    else:
        budget = cap or FALLBACK_BUDGET
    return max(1, int(budget * factor))


def batch_cut(sizes: list[int], limit: int) -> int:
    """How many of the oldest messages to leave out: the replay rule, which needs no stored state.

    The history's growth is replayed from its first message: whenever the kept span passes *limit*, the
    cut moves forward until the span is at or under 60% of it. The newest message is always kept. The
    sizes of earlier messages never change, so every turn finds the same cut until the next drop.
    """
    cut = span = 0
    for index, size in enumerate(sizes):
        span += size
        if span > limit:
            while cut < index and span > LOW_WATER * limit:
                span -= sizes[cut]
                cut += 1
    return cut


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
    #: Files whose text could not be read: named to the model and to the chat, not sent (0.7.2 H.3).
    files_unreadable: list[ChatAttachment] = field(default_factory=list)
    total_token_estimate: int = 0
    #: The prompt budget P this context was fitted to, and the model's window W it came from (None: unknown).
    budget: int = 0
    window: int | None = None
    #: How many of the chat's earlier messages are not included (0: all of them are), and when the first one
    #: that is included was written: ``search_chat`` searches the ones before it.
    dropped: int = 0
    kept_from: Any = None
    #: The project's files did not fit their share: named in one line, searched with ``attachment_search``.
    project_files_indexed: bool = False


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


def _readable(att: ChatAttachment) -> bool:
    """A file whose text was read (a document; not a picture, not one that failed or is still pending)."""
    return att.extraction_status == ExtractionStatus.COMPLETED and bool(att.extracted_text)


def _files_tokens(files: list[ChatAttachment]) -> int:
    """About how many tokens *files* take inline: their texts as sent, a picture's estimate each."""
    total = 0
    for att in files:
        if _readable(att):
            total += _estimate_tokens(f"[Attached: {att.filename}]\n{att.extracted_text}")
        elif att.extraction_status == ExtractionStatus.SKIPPED:
            total += _IMAGE_TOKEN_ESTIMATE
    return total


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
        token_budget: int = FALLBACK_BUDGET,
        file_store: FileStore | None = None,
        searchable: bool = False,
        vision: Callable[[], Awaitable[bool | None]] | None = None,
        window: int | None = None,
        reply_reserve: int = REPLY_RESERVE,
        budget_cap: int | None = None,
        history_cap: int = HISTORY_CAP,
        factor: float = 1.0,
    ) -> None:
        """The budget from *window*, the model's context length, when known.

        It is then ``prompt_budget``'s, at most *budget_cap*; without a window, *token_budget* is the
        budget, as before. *factor*: 0.75 for the retry after the model said the context was too long.
        """
        self.dao = dao
        self.max_recent_messages = max_recent_messages
        self.window = window
        self.token_budget = (
            prompt_budget(window, reply_reserve, cap=budget_cap, factor=factor) if window
            else max(1, int(token_budget * factor))
        )
        self.history_cap = history_cap
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
        result = AssembledContext(messages=[], budget=self.token_budget, window=self.window)
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

        # 4. The current request's messages are always sent: counted before the history, which gets what is
        # left (it used to fill the budget first, and the question went on top).
        current_tokens = sum(_estimate_tokens(m.get("content") or "") for m in current_messages)
        budget -= current_tokens
        total_tokens += current_tokens

        # 5. The history, newest messages within the token budget (at most max_recent_messages are read),
        # after the summary's cutoff; the oldest left out in batches, by the replay rule, with a line in
        # their place. It used to be the newest 20 that fitted, and older turns went without a word.
        loaded = await self.dao.get_recent_messages(
            session_id=session_id,
            limit=self.max_recent_messages,
        )
        recent = loaded
        if summary and summary.last_message_id and recent:
            recent = [m for m in recent if m.created_at > summary.created_at]
        recent = [m for m in recent if in_context(m)]
        exchanges = [_replayed_exchange(m) for m in recent]
        sizes = [
            (m.token_estimate or _estimate_tokens(m.content))
            + sum(_estimate_tokens(json.dumps(x)) for x in ex)
            for m, ex in zip(recent, exchanges, strict=True)
        ]
        history_budget = max(0, min(self.history_cap, budget))
        cut = batch_cut(sizes, history_budget)
        # Messages older than the ones read count as left out too (a chat of more than the cap).
        unread = 0
        if len(loaded) >= self.max_recent_messages and not summary:
            unread = max(0, await self.dao.count_messages(session_id=session_id) - len(loaded))
        result.dropped = cut + unread
        if result.dropped:
            line = DROPPED_SEARCHABLE.format(n=result.dropped)
            result.messages.append({"role": "system", "content": line})
            total_tokens += _estimate_tokens(line)
        for msg, exchange in zip(recent[cut:], exchanges[cut:], strict=True):
            # The tool calls and results that led to a reply, before it.
            result.messages.extend(exchange)
            result.messages.append({"role": msg.role, "content": msg.content})
        result.kept_from = recent[cut].created_at if cut < len(recent) else None
        total_tokens += sum(sizes[cut:])

        # 6. Current request messages (always included)
        result.messages.extend(current_messages)

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
        """The project's files and the chat's own: documents as text, pictures as pictures.

        The project's files come first, in a stable order (oldest first), inline while all of them fit their
        share of the budget (``PROJECT_FILES_SHARE``); past it the whole project goes as one index line and
        its files are searched with ``attachment_search``, so one more file does not push the others out one
        by one. Then the chat's own files, each inline while it fits.
        """
        own = await self.dao.list_attachments_for_session(session_id=session_id)
        project_files: list[ChatAttachment] = []
        if project_id:
            seen = {a.id for a in own}
            project_files = [
                a for a in await self.dao.list_attachments_for_project(project_id=project_id)
                if a.id not in seen and a.session_id is None
            ]
        if project_files and _files_tokens(project_files) > PROJECT_FILES_SHARE * self.token_budget:
            budget, total_tokens = self._project_index(result, project_files, budget, total_tokens)
            project_files = [a for a in project_files if not _readable(a)]
        for att in [*project_files, *own]:
            budget, total_tokens = await self._inject_one(result, att, budget, total_tokens)
        return budget, total_tokens

    def _project_index(
        self,
        result: AssembledContext,
        files: list[ChatAttachment],
        budget: int,
        total_tokens: int,
    ) -> tuple[int, int]:
        """The project's readable files named in one line, for ``attachment_search`` (or named only)."""
        readable = [a for a in files if _readable(a)]
        names = ", ".join(a.filename for a in readable[:10]) + (", ..." if len(readable) > 10 else "")
        count = f"{len(readable)} file" + ("" if len(readable) == 1 else "s")
        if self.searchable:
            line = (
                f"[Project files, not included in full: {names} ({count}). "
                "Use attachment_search to look up what the question needs.]"
            )
            result.searchable.extend(readable)
        else:
            line = f"[Project files, not included in full: {names} ({count}).]"
        result.project_files_indexed = True
        line_tokens = _estimate_tokens(line)
        result.messages.append({"role": "system", "content": line})
        return budget - line_tokens, total_tokens + line_tokens

    async def _inject_one(
        self,
        result: AssembledContext,
        att: ChatAttachment,
        budget: int,
        total_tokens: int,
    ) -> tuple[int, int]:
        """One file: its text while it fits, a picture as a picture, an unreadable one named."""
        if att.extraction_status == ExtractionStatus.COMPLETED and att.extracted_text:
            # Document attachment → inject as system message
            truncation_note = ""
            if att.truncated:
                truncation_note = " (truncated)"
            text_block = f"[Attached: {att.filename}{truncation_note}]\n{att.extracted_text}"
            text_tokens = _estimate_tokens(text_block)
            if text_tokens > budget:
                return self._too_long(result, att, text_tokens, budget, total_tokens)
            result.messages.append({
                "role": "system",
                "content": text_block,
            })
            result.attachments_used.append(att)
            total_tokens += text_tokens
            budget -= text_tokens

        elif att.extraction_status == ExtractionStatus.FAILED or (
            att.extraction_status == ExtractionStatus.COMPLETED and not att.extracted_text
        ):
            budget, total_tokens = self._unreadable(result, att, budget, total_tokens)

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

    def _unreadable(
        self,
        result: AssembledContext,
        att: ChatAttachment,
        budget: int,
        total_tokens: int,
    ) -> tuple[int, int]:
        """A file whose text could not be read: named, not sent.

        The model never saw it and nobody said so, so it answered as if nothing had
        been attached. Now the model is told, and the chat through ``files_unreadable``.
        """
        note = f"[Attached file: {att.filename} -- not sent: its text could not be read.]"
        note_tokens = _estimate_tokens(note)
        result.messages.append({"role": "system", "content": note})
        result.files_unreadable.append(att)
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
