"""Erasing chat data for good: a chat, a project, a file, everything of a person.

Deleting a chat used to set its status: its messages, files and memory stayed for ever, still counted
against the person's storage, and a PATCH brought the chat back. Now (features/admin-guardrails.md, 5):

- a person's delete marks the chat deleted (``deleted_at``), and the purge (:func:`purge_deleted`, run
  hourly by the gateway) erases it after the organisation's retention (``chat.deleted_retention_days``,
  30 by default);
- an administrator's delete in the console, a project's delete and an account's delete erase at once,
  through here, so the files on disk go with the rows (the backend reaches them only through the gateway).

Every erase removes the files first, then the rows: a stop in between leaves rows whose file is gone,
which the next erase finishes, never files nobody can find. Children are deleted explicitly, not only by
the database's cascades, so the same code holds wherever the foreign keys are not enforced.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import (
    ChatAttachment,
    ChatMessage,
    ChatProject,
    ChatSession,
    LLMGatewayRequestLog,
    LLMToolCallLog,
    MemoryFact,
    SessionStatus,
    SessionSummary,
)
from faden_gateway.db.models.usage import UsageDailyPerson, UsageErased

log = logging.getLogger(__name__)

#: Chats erased per pass of the purge, each pass in its own transaction.
PURGE_BATCH = 200
#: Advisory lock key: one gateway worker purges at a time ("faden-purge" in ASCII, cut to 63 bits).
PURGE_LOCK_KEY = 0x6661_6465_6E70_7572 & 0x7FFF_FFFF_FFFF_FFFF


@dataclass
class Erased:
    """What one erase removed."""

    chats: int = 0
    projects: int = 0
    files: int = 0
    memory_facts: int = 0
    #: A person's request-log rows and their person-level usage days (an account erased: M12).
    request_logs: int = 0
    usage_days: int = 0

    def add(self, other: Erased) -> Erased:
        """This plus *other*."""
        self.chats += other.chats
        self.projects += other.projects
        self.files += other.files
        self.memory_facts += other.memory_facts
        self.request_logs += other.request_logs
        self.usage_days += other.usage_days
        return self


async def _delete_files(file_store: Any, keys: list[str]) -> int:
    removed = 0
    if file_store is None:
        return 0
    for key in keys:
        try:
            if await file_store.delete(key):
                removed += 1
        except (OSError, ValueError):
            log.warning("An attachment file could not be deleted (%s)", key[:80])
    return removed


async def erase_session(session: AsyncSession, file_store: Any, session_id: uuid.UUID) -> Erased:
    """One chat: its files, attachments, messages, summaries, its own memory facts, the chat itself."""
    keys = list((await session.execute(
        select(ChatAttachment.storage_key).where(ChatAttachment.session_id == session_id),
    )).scalars())
    erased = Erased(files=await _delete_files(file_store, keys))
    await session.execute(delete(ChatAttachment).where(ChatAttachment.session_id == session_id))
    await session.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await session.execute(delete(SessionSummary).where(SessionSummary.session_id == session_id))
    facts = await session.execute(delete(MemoryFact).where(MemoryFact.session_id == session_id))
    erased.memory_facts = facts.rowcount or 0
    chats = await session.execute(delete(ChatSession).where(ChatSession.id == session_id))
    erased.chats = chats.rowcount or 0
    return erased


async def erase_project(session: AsyncSession, file_store: Any, project_id: uuid.UUID) -> Erased:
    """A project with every chat in it, its files and memory facts."""
    erased = Erased()
    for chat_id in list((await session.execute(
        select(ChatSession.id).where(ChatSession.project_id == project_id),
    )).scalars()):
        erased.add(await erase_session(session, file_store, chat_id))
    keys = list((await session.execute(
        select(ChatAttachment.storage_key).where(ChatAttachment.project_id == project_id),
    )).scalars())
    erased.files += await _delete_files(file_store, keys)
    await session.execute(delete(ChatAttachment).where(ChatAttachment.project_id == project_id))
    facts = await session.execute(delete(MemoryFact).where(MemoryFact.project_id == project_id))
    erased.memory_facts += facts.rowcount or 0
    projects = await session.execute(delete(ChatProject).where(ChatProject.id == project_id))
    erased.projects = projects.rowcount or 0
    return erased


async def erase_attachment(session: AsyncSession, file_store: Any, attachment_id: uuid.UUID) -> Erased:
    """One attached file: on disk and its row."""
    keys = list((await session.execute(
        select(ChatAttachment.storage_key).where(ChatAttachment.id == attachment_id),
    )).scalars())
    erased = Erased(files=await _delete_files(file_store, keys))
    await session.execute(delete(ChatAttachment).where(ChatAttachment.id == attachment_id))
    return erased


async def erase_user(session: AsyncSession, file_store: Any, user_id: str) -> Erased:
    """Everything of one person: projects, chats, files, memory, and their usage (an account deleted).

    Their usage is their request-log rows (with their tool calls) and their person-level usage days
    (M12). The hourly and daily sums, which name no person, keep their totals: the rollup job recomputes
    only the current and the previous hour and day from the log. The person is noted in ``usage_erased``:
    a rollup run that read the log before this, or a request of theirs that ends after it, would write some
    of it again, and each run removes that for 30 days (``usage_rollup.forget_erased``).
    """
    erased = Erased()
    await session.merge(UsageErased(user_id=user_id, erased_at=datetime.now(UTC)))
    logs = select(LLMGatewayRequestLog.id).where(LLMGatewayRequestLog.user_id == user_id)
    await session.execute(delete(LLMToolCallLog).where(LLMToolCallLog.request_log_id.in_(logs)))
    rows = await session.execute(delete(LLMGatewayRequestLog).where(LLMGatewayRequestLog.user_id == user_id))
    erased.request_logs = cast("CursorResult[Any]", rows).rowcount or 0
    days = await session.execute(delete(UsageDailyPerson).where(UsageDailyPerson.user_id == user_id))
    erased.usage_days = cast("CursorResult[Any]", days).rowcount or 0
    for project_id in list((await session.execute(
        select(ChatProject.id).where(ChatProject.user_id == user_id),
    )).scalars()):
        erased.add(await erase_project(session, file_store, project_id))
    for chat_id in list((await session.execute(
        select(ChatSession.id).where(ChatSession.user_id == user_id),
    )).scalars()):
        erased.add(await erase_session(session, file_store, chat_id))
    keys = list((await session.execute(
        select(ChatAttachment.storage_key).where(ChatAttachment.user_id == user_id),
    )).scalars())
    erased.files += await _delete_files(file_store, keys)
    await session.execute(delete(ChatAttachment).where(ChatAttachment.user_id == user_id))
    facts = await session.execute(delete(MemoryFact).where(MemoryFact.user_id == user_id))
    erased.memory_facts += facts.rowcount or 0
    return erased


async def purge_deleted(
    session: AsyncSession, file_store: Any, *, deleted_before: datetime, limit: int = PURGE_BATCH,
) -> Erased:
    """Erase chats people deleted before *deleted_before*, at most *limit*; the caller commits."""
    erased = Erased()
    for chat_id in list((await session.execute(
        select(ChatSession.id)
        .where(ChatSession.status == SessionStatus.DELETED, ChatSession.deleted_at < deleted_before)
        .order_by(ChatSession.deleted_at)
        .limit(limit),
    )).scalars()):
        erased.add(await erase_session(session, file_store, chat_id))
    return erased


async def take_purge_lock(session: AsyncSession) -> bool:
    """Whether this worker may purge now (Postgres: a transaction-level advisory lock; elsewhere: yes)."""
    if session.bind.dialect.name != "postgresql":
        return True
    return bool((await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": PURGE_LOCK_KEY},
    )).scalar())
