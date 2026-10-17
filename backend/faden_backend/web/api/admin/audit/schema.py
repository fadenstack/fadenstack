"""Pydantic schemas for the audit log API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from faden_backend.db.models.containers import AuditResult


class AuditEventDTO(BaseModel):
    """Read-only representation of an audit event."""

    id: uuid.UUID
    time: datetime
    actor_id: uuid.UUID | None = None
    #: Who acted, as written at the time (kept when the account is deleted).
    actor_email: str | None = None
    action: str
    target_type: str
    target_id: str
    result: AuditResult
    severity: str
    metadata_json: str | None = None
    client_ip: str | None = None
    request_id: str | None = None
    user_agent: str | None = None
    #: ``console``, ``api``, ``cli`` or ``system``.
    source: str = "console"
    hash: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AuditChainDTO(BaseModel):
    """Whether the trail is as it was written (services/audit/chain.py)."""

    ok: bool
    events: int
    #: Events from before the chain began, covered by its first event's digest.
    legacy_rows: int
    #: The last event up to which everything checks out.
    intact_until: datetime | None = None
    #: What does not check out, first ones first (at most 20).
    problems: list[str] = []
