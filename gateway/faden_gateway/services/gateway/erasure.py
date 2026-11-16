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

An attachment's row, with its storage key, goes only once its file is gone (a file already missing is
gone). A file that could not be deleted keeps its row, emptied of its name and text, under a chat marked
deleted and due at once (:data:`RETRY_DUE`): the chat it was in, or, when that goes (a project's or an
account's own file, a single file erased), a chat with nothing in it but that file. The next purge
(:func:`purge_deleted`, hourly) tries again. ``Erased.failed_files`` counts such files, and the caller
says the erase is partial (GDPR Art. 17: erased without undue delay, and never a file left that no row
names).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import (
    ApprovalGrant,
    AttachmentScope,
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
#: When a chat kept for a file that could not be deleted counts as deleted: long past every retention, so
#: the next purge takes it up whatever the organisation's retention is. No person's delete has this time.
RETRY_DUE = datetime(1970, 1, 1, tzinfo=UTC)


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
    #: Files that could not be deleted: their rows wait, emptied, for the next purge.
    failed_files: int = 0

    @property
    def partial(self) -> bool:
        """Whether a file could not be deleted: the erase finishes at the next purge."""
        return self.failed_files > 0

    def add(self, other: Erased) -> Erased:
        """This plus *other*."""
        self.chats += other.chats
        self.projects += other.projects
        self.files += other.files
        self.memory_facts += other.memory_facts
        self.request_logs += other.request_logs
        self.usage_days += other.usage_days
        self.failed_files += other.failed_files
        return self


async def _delete_files(file_store: Any, keys: list[str]) -> tuple[int, set[str]]:
    """Delete each key's file: how many were removed, and the keys whose file may still be there.

    A file already missing counts as gone (``delete`` answers ``False``). Without a file store (chats
    off), nothing can be deleted or checked: every key counts as not deleted.
    """
    if not keys:
        return 0, set()
    if file_store is None:
        log.warning("%d attachment file(s) could not be deleted: there is no file store", len(keys))
        return 0, set(keys)
    removed, failed = 0, set()
    for key in keys:
        try:
            if await file_store.delete(key):
                removed += 1
        except (OSError, ValueError):
            log.warning("An attachment file could not be deleted (%s)", key[:80])
            failed.add(key)
    return removed, failed


def _tombstones() -> Any:
    """The chats kept only for files that could not be deleted."""
    return select(ChatSession.id).where(
        ChatSession.status == SessionStatus.DELETED, ChatSession.deleted_at == RETRY_DUE,
    )


async def _attachments(session: AsyncSession, *where: Any) -> list[tuple[uuid.UUID, str, str, str]]:
    """The attachments *where* names: id, storage key, tenant and person."""
    rows = await session.execute(
        select(
            ChatAttachment.id, ChatAttachment.storage_key, ChatAttachment.tenant_id, ChatAttachment.user_id,
        )
        .where(*where),
    )
    return [(row[0], row[1], row[2], row[3]) for row in rows]


async def _keep_for_retry(
    session: AsyncSession, rows: list[tuple[uuid.UUID, str, str, str]], *, chat_id: uuid.UUID | None,
) -> None:
    """Keep *rows*, whose files could not be deleted, for the next purge.

    They are emptied of their name and text, and kept under *chat_id* (which the caller makes a
    tombstone), or under a new tombstone per person.
    """
    by_person: dict[tuple[str, str], list[uuid.UUID]] = {}
    for attachment_id, _key, tenant_id, user_id in rows:
        by_person.setdefault((tenant_id, user_id), []).append(attachment_id)
    for (tenant_id, user_id), ids in by_person.items():
        holder = chat_id
        if holder is None:
            tombstone = ChatSession(
                tenant_id=tenant_id, user_id=user_id, title=None, metadata_json=None,
                status=SessionStatus.DELETED, deleted_at=RETRY_DUE,
            )
            session.add(tombstone)
            await session.flush()
            holder = tombstone.id
        await session.execute(
            update(ChatAttachment).where(ChatAttachment.id.in_(ids)).values(
                session_id=holder, project_id=None, message_id=None, scope=AttachmentScope.SESSION,
                filename="", extracted_text=None,
            ),
        )


async def _tombstone(session: AsyncSession, chat_id: uuid.UUID) -> None:
    """The chat stays only for its files that could not be deleted: nothing else of it, due at once."""
    await session.execute(
        update(ChatSession).where(ChatSession.id == chat_id).values(
            status=SessionStatus.DELETED, deleted_at=RETRY_DUE, project_id=None, title=None,
            metadata_json=None,
        ),
    )


async def erase_session(session: AsyncSession, file_store: Any, session_id: uuid.UUID) -> Erased:
    """One chat: its files, attachments, messages, summaries, its own memory facts, the chat itself.

    When a file could not be deleted, its row and the chat stay, emptied, as the file's tombstone.
    """
    rows = await _attachments(session, ChatAttachment.session_id == session_id)
    removed, failed = await _delete_files(file_store, [row[1] for row in rows])
    erased = Erased(files=removed)
    kept = [row for row in rows if row[1] in failed]
    gone = ChatAttachment.session_id == session_id
    if kept:
        await _keep_for_retry(session, kept, chat_id=session_id)
        gone = gone & ChatAttachment.id.not_in([row[0] for row in kept])
    await session.execute(delete(ChatAttachment).where(gone))
    await session.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await session.execute(delete(SessionSummary).where(SessionSummary.session_id == session_id))
    facts = await session.execute(delete(MemoryFact).where(MemoryFact.session_id == session_id))
    erased.memory_facts = facts.rowcount or 0
    if kept:
        await _tombstone(session, session_id)
        erased.failed_files = len(kept)
        return erased
    chats = await session.execute(delete(ChatSession).where(ChatSession.id == session_id))
    erased.chats = chats.rowcount or 0
    return erased


async def erase_project(session: AsyncSession, file_store: Any, project_id: uuid.UUID) -> Erased:
    """A project with every chat in it, its files and memory facts.

    A chat kept for a file leaves the project as its tombstone; the project's own files that could not be
    deleted go to a tombstone of their own. The project itself always goes.
    """
    erased = Erased()
    for chat_id in list((await session.execute(
        select(ChatSession.id).where(ChatSession.project_id == project_id),
    )).scalars()):
        erased.add(await erase_session(session, file_store, chat_id))
    rows = await _attachments(session, ChatAttachment.project_id == project_id)
    removed, failed = await _delete_files(file_store, [row[1] for row in rows])
    erased.files += removed
    kept = [row for row in rows if row[1] in failed]
    if kept:
        await _keep_for_retry(session, kept, chat_id=None)
        erased.failed_files += len(kept)
    await session.execute(delete(ChatAttachment).where(ChatAttachment.project_id == project_id))
    facts = await session.execute(delete(MemoryFact).where(MemoryFact.project_id == project_id))
    erased.memory_facts += facts.rowcount or 0
    projects = await session.execute(delete(ChatProject).where(ChatProject.id == project_id))
    erased.projects = projects.rowcount or 0
    return erased


async def erase_attachment(session: AsyncSession, file_store: Any, attachment_id: uuid.UUID) -> Erased:
    """One attached file: on disk and its row.

    A file that could not be deleted leaves its chat or project for a tombstone of its own, where the
    next purge tries again.
    """
    rows = await _attachments(session, ChatAttachment.id == attachment_id)
    removed, failed = await _delete_files(file_store, [row[1] for row in rows])
    erased = Erased(files=removed)
    if failed:
        await _keep_for_retry(session, rows, chat_id=None)
        erased.failed_files = len(rows)
        return erased
    await session.execute(delete(ChatAttachment).where(ChatAttachment.id == attachment_id))
    return erased


async def erase_user(session: AsyncSession, file_store: Any, user_id: str) -> Erased:
    """Everything of one person: projects, chats, files, memory, grants, and their usage (an account deleted).

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
    # Not the tombstones: their files were just tried, or wait for the purge; they are tried there.
    for chat_id in list((await session.execute(
        select(ChatSession.id).where(ChatSession.user_id == user_id, ChatSession.id.not_in(_tombstones())),
    )).scalars()):
        erased.add(await erase_session(session, file_store, chat_id))
    # Their files in no chat or project (and in none that is gone), but those a tombstone keeps.
    own = (
        ChatAttachment.user_id == user_id,
        or_(ChatAttachment.session_id.is_(None), ChatAttachment.session_id.not_in(_tombstones())),
    )
    rows = await _attachments(session, *own)
    removed, failed = await _delete_files(file_store, [row[1] for row in rows])
    erased.files += removed
    kept = [row for row in rows if row[1] in failed]
    if kept:
        await _keep_for_retry(session, kept, chat_id=None)
        erased.failed_files += len(kept)
    await session.execute(delete(ChatAttachment).where(*own))
    facts = await session.execute(delete(MemoryFact).where(MemoryFact.user_id == user_id))
    erased.memory_facts += facts.rowcount or 0
    # Their grants, and with them the calls each covered (their arguments), as their tool calls above.
    await session.execute(delete(ApprovalGrant).where(ApprovalGrant.user_id == user_id))
    return erased


async def purge_deleted(
    session: AsyncSession, file_store: Any, *, deleted_before: datetime, limit: int = PURGE_BATCH,
) -> Erased:
    """Erase chats people deleted before *deleted_before*, at most *limit*; the caller commits.

    The tombstones of files that could not be deleted (:data:`RETRY_DUE`) are tried again after the
    other chats due, so files that keep failing never hold those back. A tombstone that fails again
    counts in ``failed_files``, not in ``chats``: a pass with any is short, and the purge stops till the
    next hour, never trying the same file twice in one.
    """
    erased = Erased()
    for chat_id in list((await session.execute(
        select(ChatSession.id)
        .where(ChatSession.status == SessionStatus.DELETED, ChatSession.deleted_at < deleted_before)
        .order_by((ChatSession.deleted_at == RETRY_DUE).asc(), ChatSession.deleted_at)
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
