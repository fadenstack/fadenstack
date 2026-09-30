"""Data access layer for chat projects, sessions, messages, summaries and memory facts."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import Depends
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dependencies import get_db_session
from faden_gateway.db.models.gateway import (
    ChatAttachment,
    ChatMessage,
    ChatProject,
    ChatSession,
    LLMGatewayRequestLog,
    MemoryFact,
    MemoryFactScope,
    MemoryFactStatus,
    SessionStatus,
    SessionSummary,
)


class SessionDAO:
    """Data-access layer for projects, sessions, messages and memory."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    # ── Projects ──────────────────────────────────────────────────

    async def count_projects(self, *, tenant_id: str, user_id: str) -> int:
        """Count non-deleted projects for capacity enforcement."""
        result = await self.session.execute(
            select(func.count(ChatProject.id)).where(
                ChatProject.tenant_id == tenant_id,
                ChatProject.user_id == user_id,
            ),
        )
        return result.scalar_one()

    async def create_project(
        self,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        description: str | None = None,
        system_instructions: str | None = None,
        model_alias: str | None = None,
        metadata_json: dict | None = None,
    ) -> ChatProject:
        project = ChatProject(
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            description=description,
            system_instructions=system_instructions,
            model_alias=model_alias,
            metadata_json=metadata_json,
        )
        self.session.add(project)
        await self.session.flush()
        return project

    async def get_project(
        self, *, project_id: uuid.UUID, tenant_id: str, user_id: str,
    ) -> ChatProject | None:
        result = await self.session.execute(
            select(ChatProject).where(
                ChatProject.id == project_id,
                ChatProject.tenant_id == tenant_id,
                ChatProject.user_id == user_id,
            ),
        )
        return result.scalar_one_or_none()

    async def list_projects(
        self, *, tenant_id: str, user_id: str,
    ) -> list[ChatProject]:
        result = await self.session.execute(
            select(ChatProject)
            .where(
                ChatProject.tenant_id == tenant_id,
                ChatProject.user_id == user_id,
            )
            .order_by(ChatProject.updated_at.desc()),
        )
        return list(result.scalars().all())

    async def update_project(
        self,
        *,
        project_id: uuid.UUID,
        tenant_id: str,
        user_id: str,
        **fields: object,
    ) -> ChatProject | None:
        allowed = {"name", "description", "system_instructions", "model_alias", "metadata_json"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not _UNSET}
        if not updates:
            return await self.get_project(
                project_id=project_id, tenant_id=tenant_id, user_id=user_id,
            )
        await self.session.execute(
            update(ChatProject)
            .where(
                ChatProject.id == project_id,
                ChatProject.tenant_id == tenant_id,
                ChatProject.user_id == user_id,
            )
            .values(**updates),
        )
        await self.session.flush()
        return await self.get_project(
            project_id=project_id, tenant_id=tenant_id, user_id=user_id,
        )

    async def delete_project(
        self, *, project_id: uuid.UUID, tenant_id: str, user_id: str,
    ) -> bool:
        result = await self.session.execute(
            delete(ChatProject).where(
                ChatProject.id == project_id,
                ChatProject.tenant_id == tenant_id,
                ChatProject.user_id == user_id,
            ),
        )
        await self.session.flush()
        return (result.rowcount or 0) > 0

    # ── Sessions ──────────────────────────────────────────────────

    async def create_session(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: uuid.UUID | None = None,
        title: str | None = None,
        metadata_json: dict | None = None,
    ) -> ChatSession:
        sess = ChatSession(
            tenant_id=tenant_id,
            user_id=user_id,
            project_id=project_id,
            title=title,
            metadata_json=metadata_json,
        )
        self.session.add(sess)
        await self.session.flush()
        return sess

    async def get_session(
        self, *, session_id: uuid.UUID, tenant_id: str, user_id: str,
    ) -> ChatSession | None:
        result = await self.session.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == tenant_id,
                ChatSession.user_id == user_id,
                ChatSession.status != SessionStatus.DELETED,
            ),
        )
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_id: uuid.UUID | None = None,
        status: SessionStatus | None = None,
    ) -> list[ChatSession]:
        query = select(ChatSession).where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.user_id == user_id,
            ChatSession.status != SessionStatus.DELETED,
        )
        if project_id is not None:
            query = query.where(ChatSession.project_id == project_id)
        if status is not None:
            query = query.where(ChatSession.status == status)
        result = await self.session.execute(
            query.order_by(ChatSession.updated_at.desc()),
        )
        return list(result.scalars().all())

    async def update_session(
        self,
        *,
        session_id: uuid.UUID,
        tenant_id: str,
        user_id: str,
        **fields: object,
    ) -> ChatSession | None:
        # project_id: the caller checks the project is the user's own.
        allowed = {"title", "status", "metadata_json", "project_id"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not _UNSET}
        if not updates:
            return await self.get_session(
                session_id=session_id, tenant_id=tenant_id, user_id=user_id,
            )
        await self.session.execute(
            update(ChatSession)
            .where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == tenant_id,
                ChatSession.user_id == user_id,
            )
            .values(**updates),
        )
        await self.session.flush()
        return await self.get_session(
            session_id=session_id, tenant_id=tenant_id, user_id=user_id,
        )

    async def delete_session(
        self, *, session_id: uuid.UUID, tenant_id: str, user_id: str,
    ) -> bool:
        """Soft-delete a session."""
        result = await self.session.execute(
            update(ChatSession)
            .where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == tenant_id,
                ChatSession.user_id == user_id,
            )
            .values(status=SessionStatus.DELETED),
        )
        await self.session.flush()
        return (result.rowcount or 0) > 0

    # ── Messages ──────────────────────────────────────────────────

    async def append_message(
        self,
        *,
        session_id: uuid.UUID,
        role: str,
        content: str,
        content_parts_json: list | None = None,
        model_alias: str | None = None,
        provider_instance_id: uuid.UUID | None = None,
        token_estimate: int | None = None,
        trace_id: str | None = None,
        tool_call_json: dict | None = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            content_parts_json=content_parts_json,
            model_alias=model_alias,
            provider_instance_id=provider_instance_id,
            token_estimate=token_estimate,
            trace_id=trace_id,
            tool_call_json=tool_call_json,
        )
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def list_messages(
        self,
        *,
        session_id: uuid.UUID,
        limit: int | None = None,
        after_message_id: uuid.UUID | None = None,
    ) -> list[ChatMessage]:
        """List messages in chronological order, optionally after a given message.

        Oldest first *before* the limit: the first ``limit`` messages (after
        ``after_message_id``) -- the order the summarizer reads a session in.
        For the latest N (the chat's history window) use
        :meth:`get_recent_messages`; do not add a newest-first variant here
        (see the MERGE NOTE at the messages endpoint in web/api/v1/sessions.py).
        """
        query = select(ChatMessage).where(ChatMessage.session_id == session_id)
        if after_message_id is not None:
            subq = select(ChatMessage.created_at).where(
                ChatMessage.id == after_message_id,
            ).scalar_subquery()
            query = query.where(ChatMessage.created_at > subq)
        query = query.order_by(ChatMessage.created_at.asc())
        if limit is not None:
            query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def message_usage(
        self, *, session_id: uuid.UUID, trace_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        """The token usage the gateway logged for these replies, by trace id.

        A reply stores only its own size (``token_estimate``, the completion
        tokens -- what the context assembler budgets with). What its request
        used is in the request log; this finds it through the session (an
        indexed column) and takes the latest row per trace, as the debug
        panel does. Replies with no logged counts are left out.
        """
        if not trace_ids:
            return {}
        log = LLMGatewayRequestLog
        rows = await self.session.execute(
            select(log.trace_id, log.prompt_tokens, log.completion_tokens, log.total_tokens)
            .where(log.session_id == str(session_id), log.trace_id.in_(trace_ids))
            .order_by(log.trace_id, log.created_at.desc())
            .distinct(log.trace_id),
        )
        usage: dict[str, dict[str, int]] = {}
        for trace_id, prompt, completion, total in rows.all():
            if prompt is None and completion is None and total is None:
                continue
            prompt, completion = prompt or 0, completion or 0
            usage[trace_id] = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total if total is not None else prompt + completion,
            }
        return usage

    async def get_recent_messages(
        self, *, session_id: uuid.UUID, limit: int = 20,
    ) -> list[ChatMessage]:
        """Get the most recent N messages (returned in chronological order)."""
        sub = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(limit)
            .subquery()
        )
        result = await self.session.execute(
            select(ChatMessage)
            .from_statement(
                select(sub).order_by(sub.c.created_at.asc()),
            ),
        )
        return list(result.scalars().all())

    async def running_turn_sessions(
        self, *, tenant_id: str, user_id: str, alive_since: datetime,
    ) -> list[uuid.UUID]:
        """The user's chats with a reply being written: one per reply alive since *alive_since*."""
        result = await self.session.execute(
            select(ChatMessage.session_id)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .where(
                ChatSession.tenant_id == tenant_id,
                ChatSession.user_id == user_id,
                ChatMessage.status == "running",
                ChatMessage.updated_at > alive_since,
            ),
        )
        return [row[0] for row in result.all()]

    async def delete_message(self, *, message_id: uuid.UUID) -> bool:
        """Delete one message: a reply that is replaced by trying again."""
        result = await self.session.execute(delete(ChatMessage).where(ChatMessage.id == message_id))
        await self.session.flush()
        return (result.rowcount or 0) > 0

    async def count_messages(self, *, session_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.session_id == session_id,
            ),
        )
        return result.scalar_one()

    # ── Summaries ─────────────────────────────────────────────────

    async def get_latest_summary(
        self, *, session_id: uuid.UUID,
    ) -> SessionSummary | None:
        result = await self.session.execute(
            select(SessionSummary)
            .where(SessionSummary.session_id == session_id)
            .order_by(SessionSummary.created_at.desc())
            .limit(1),
        )
        return result.scalar_one_or_none()

    async def create_summary(
        self,
        *,
        session_id: uuid.UUID,
        summary_text: str,
        last_message_id: uuid.UUID,
        token_estimate: int = 0,
    ) -> SessionSummary:
        summary = SessionSummary(
            session_id=session_id,
            summary_text=summary_text,
            last_message_id=last_message_id,
            token_estimate=token_estimate,
        )
        self.session.add(summary)
        await self.session.flush()
        return summary

    # ── Memory Facts ──────────────────────────────────────────────

    async def list_active_facts(
        self,
        *,
        tenant_id: str,
        user_id: str,
        scope: MemoryFactScope | None = None,
        project_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
    ) -> list[MemoryFact]:
        query = select(MemoryFact).where(
            MemoryFact.tenant_id == tenant_id,
            MemoryFact.user_id == user_id,
            MemoryFact.status == MemoryFactStatus.ACTIVE,
        )
        if scope is not None:
            query = query.where(MemoryFact.scope == scope)
        if project_id is not None:
            query = query.where(MemoryFact.project_id == project_id)
        if session_id is not None:
            query = query.where(MemoryFact.session_id == session_id)
        result = await self.session.execute(
            query.order_by(MemoryFact.created_at.desc()),
        )
        return list(result.scalars().all())

    async def upsert_fact(
        self,
        *,
        tenant_id: str,
        user_id: str,
        scope: MemoryFactScope,
        key: str,
        value: str,
        confidence: float = 1.0,
        session_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        source_message_id: uuid.UUID | None = None,
        status: MemoryFactStatus = MemoryFactStatus.CANDIDATE,
    ) -> MemoryFact:
        """Insert or update a memory fact by (tenant, user, scope, key)."""
        existing_q = select(MemoryFact).where(
            MemoryFact.tenant_id == tenant_id,
            MemoryFact.user_id == user_id,
            MemoryFact.scope == scope,
            MemoryFact.key == key,
            MemoryFact.status != MemoryFactStatus.EXPIRED,
        )
        if session_id is not None:
            existing_q = existing_q.where(MemoryFact.session_id == session_id)
        if project_id is not None:
            existing_q = existing_q.where(MemoryFact.project_id == project_id)

        result = await self.session.execute(existing_q)
        existing = result.scalar_one_or_none()

        if existing:
            await self.session.execute(
                update(MemoryFact)
                .where(MemoryFact.id == existing.id)
                .values(
                    value=value,
                    confidence=confidence,
                    source_message_id=source_message_id,
                    status=status,
                ),
            )
            await self.session.flush()
            # Re-fetch to return updated state
            refreshed = await self.session.execute(
                select(MemoryFact).where(MemoryFact.id == existing.id),
            )
            return refreshed.scalar_one()

        fact = MemoryFact(
            tenant_id=tenant_id,
            user_id=user_id,
            scope=scope,
            key=key,
            value=value,
            confidence=confidence,
            session_id=session_id,
            project_id=project_id,
            source_message_id=source_message_id,
            status=status,
        )
        self.session.add(fact)
        await self.session.flush()
        return fact

    async def update_fact_status(
        self, *, fact_id: uuid.UUID, tenant_id: str, user_id: str, status: MemoryFactStatus,
    ) -> bool:
        result = await self.session.execute(
            update(MemoryFact)
            .where(
                MemoryFact.id == fact_id,
                MemoryFact.tenant_id == tenant_id,
                MemoryFact.user_id == user_id,
            )
            .values(status=status),
        )
        await self.session.flush()
        return (result.rowcount or 0) > 0

    async def delete_fact(
        self, *, fact_id: uuid.UUID, tenant_id: str, user_id: str,
    ) -> bool:
        result = await self.session.execute(
            delete(MemoryFact).where(
                MemoryFact.id == fact_id,
                MemoryFact.tenant_id == tenant_id,
                MemoryFact.user_id == user_id,
            ),
        )
        await self.session.flush()
        return (result.rowcount or 0) > 0

    # ── Attachments ───────────────────────────────────────────────

    async def create_attachment(self, **kwargs: object) -> ChatAttachment:
        attachment = ChatAttachment(**kwargs)
        self.session.add(attachment)
        await self.session.flush()
        return attachment

    async def get_attachment(
        self, *, attachment_id: uuid.UUID, tenant_id: str, user_id: str,
    ) -> ChatAttachment | None:
        result = await self.session.execute(
            select(ChatAttachment).where(
                ChatAttachment.id == attachment_id,
                ChatAttachment.tenant_id == tenant_id,
                ChatAttachment.user_id == user_id,
            ),
        )
        return result.scalar_one_or_none()

    async def list_attachments_for_session(
        self, *, session_id: uuid.UUID,
    ) -> list[ChatAttachment]:
        result = await self.session.execute(
            select(ChatAttachment)
            .where(ChatAttachment.session_id == session_id)
            .order_by(ChatAttachment.created_at.asc()),
        )
        return list(result.scalars().all())

    async def attachment_names(
        self, *, session_id: uuid.UUID, tenant_id: str, user_id: str,
    ) -> list[tuple[str, str]]:
        """``(filename, content_type)`` of the files a chat has: its own, its project's.

        Only for the chat's owner; a chat that is not theirs has none.
        """
        chat = (
            select(ChatSession.id, ChatSession.project_id)
            .where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == tenant_id,
                ChatSession.user_id == user_id,
            )
            .subquery()
        )
        result = await self.session.execute(
            select(ChatAttachment.filename, ChatAttachment.content_type).join(
                chat,
                or_(
                    ChatAttachment.session_id == chat.c.id,
                    and_(
                        chat.c.project_id.is_not(None),
                        ChatAttachment.project_id == chat.c.project_id,
                    ),
                ),
            ),
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_attachments_for_project(
        self, *, project_id: uuid.UUID,
    ) -> list[ChatAttachment]:
        result = await self.session.execute(
            select(ChatAttachment)
            .where(ChatAttachment.project_id == project_id)
            .order_by(ChatAttachment.created_at.asc()),
        )
        return list(result.scalars().all())

    async def delete_attachment(self, *, attachment_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(ChatAttachment).where(ChatAttachment.id == attachment_id),
        )
        await self.session.flush()
        return (result.rowcount or 0) > 0

    async def delete_attachments_for_session(
        self, *, session_id: uuid.UUID,
    ) -> int:
        result = await self.session.execute(
            delete(ChatAttachment).where(
                ChatAttachment.session_id == session_id,
            ),
        )
        await self.session.flush()
        return result.rowcount or 0

    async def link_attachment_to_message(
        self, *, attachment_id: uuid.UUID, message_id: uuid.UUID,
    ) -> None:
        await self.session.execute(
            update(ChatAttachment)
            .where(ChatAttachment.id == attachment_id)
            .values(message_id=message_id),
        )
        await self.session.flush()

    async def attachment_stats(
        self, *, tenant_id: str, user_id: str | None = None,
    ) -> dict[str, int]:
        """Return ``{count, total_bytes}`` for tenant (optionally per-user)."""
        q_count = select(func.count(ChatAttachment.id)).where(
            ChatAttachment.tenant_id == tenant_id,
        )
        q_bytes = select(
            func.coalesce(func.sum(ChatAttachment.size_bytes), 0),
        ).where(ChatAttachment.tenant_id == tenant_id)
        if user_id is not None:
            q_count = q_count.where(ChatAttachment.user_id == user_id)
            q_bytes = q_bytes.where(ChatAttachment.user_id == user_id)
        count = (await self.session.execute(q_count)).scalar_one()
        total_bytes = (await self.session.execute(q_bytes)).scalar_one()
        return {"count": count, "total_bytes": total_bytes}


# Sentinel for distinguishing "not provided" from None
_UNSET = object()
