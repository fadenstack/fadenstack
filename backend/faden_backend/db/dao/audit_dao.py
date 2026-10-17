"""DAO for audit events."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import Depends
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.services.audit import chain


class AuditDAO:
    """Write-once audit event log."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def log(
        self,
        action: str,
        target_type: str,
        target_id: str,
        result: AuditResult,
        actor_id: uuid.UUID | None = None,
        severity: str = "normal",
        metadata_json: str | None = None,
        source: str | None = None,
    ) -> AuditEvent:
        """Append an audit event: committed on its own, so a refusal stays recorded when its request fails."""
        return await chain.append(
            chain.NewEvent(
                action=action,
                target_type=target_type,
                target_id=target_id,
                result=result,
                actor_id=actor_id,
                severity=severity,
                metadata_json=metadata_json,
                source=source,
            ),
            request_session=self.session,
        )

    def _filtered(
        self,
        query: Select[Any],
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        target_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        result: AuditResult | None = None,
        severity: str | None = None,
        source: str | None = None,
        actor_email: str | None = None,
    ) -> Select[Any]:
        if actor_id:
            query = query.where(AuditEvent.actor_id == actor_id)
        if actor_email:
            query = query.where(func.lower(AuditEvent.actor_email) == actor_email.lower())
        if action:
            # "user." finds every user event; a full name finds that one.
            query = query.where(
                AuditEvent.action.startswith(action) if action.endswith(".") else AuditEvent.action == action,
            )
        if target_id:
            query = query.where(AuditEvent.target_id == target_id)
        if since:
            query = query.where(AuditEvent.time >= since)
        if until:
            query = query.where(AuditEvent.time < until)
        if result:
            query = query.where(AuditEvent.result == result)
        if severity:
            query = query.where(AuditEvent.severity == severity)
        if source:
            query = query.where(AuditEvent.source == source)
        return query

    async def list_events(
        self,
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        target_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
        until: datetime | None = None,
        result: AuditResult | None = None,
        severity: str | None = None,
        source: str | None = None,
        actor_email: str | None = None,
    ) -> list[AuditEvent]:
        """Query audit events with optional filters, newest first."""
        query = self._filtered(
            select(AuditEvent),
            actor_id, action, target_id, since, until, result, severity, source, actor_email,
        ).order_by(AuditEvent.time.desc(), AuditEvent.seq.desc())
        query = query.limit(limit).offset(offset)
        return list((await self.session.execute(query)).scalars().all())

    async def count_events(
        self,
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        target_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        result: AuditResult | None = None,
        severity: str | None = None,
        source: str | None = None,
        actor_email: str | None = None,
    ) -> int:
        """How many events match the same filters."""
        query = self._filtered(
            select(func.count()).select_from(AuditEvent),
            actor_id, action, target_id, since, until, result, severity, source, actor_email,
        )
        return int((await self.session.execute(query)).scalar_one())
