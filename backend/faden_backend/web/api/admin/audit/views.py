"""Admin audit log: read, export, check.

Reading the trail is itself recorded (by the request recorder), exporting it is recorded with how many events
left, and the chain check says whether the trail is as it was written.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.users import User
from faden_backend.services.audit import chain
from faden_backend.settings import settings
from faden_backend.web.api.admin.audit.schema import AuditChainDTO, AuditEventDTO
from faden_backend.web.api.rbac import require_permission

router = APIRouter()

#: The most an export holds; narrow the filters for more.
_EXPORT_LIMIT = 100_000


@router.get("/", response_model=list[AuditEventDTO], name="list_audit_events")
async def list_audit_events(
    response: Response,
    actor_id: uuid.UUID | None = Query(default=None),
    actor_email: str | None = Query(default=None, max_length=320),
    action: str | None = Query(default=None, description="An event name, or a prefix ending in '.'."),
    target_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    result: AuditResult | None = Query(default=None),
    severity: str | None = Query(default=None, max_length=32),
    source: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    audit_dao: AuditDAO = Depends(),
    _user: Annotated[User, Depends(require_permission("audit", "read"))] = None,  # type: ignore[assignment]
) -> list[AuditEventDTO]:
    """Audit events, newest first; ``X-Total-Count`` says how many match."""
    filters = {
        "actor_id": actor_id, "actor_email": actor_email, "action": action, "target_id": target_id,
        "since": since, "until": until, "result": result, "severity": severity, "source": source,
    }
    events = await audit_dao.list_events(**filters, limit=limit, offset=offset)
    response.headers["X-Total-Count"] = str(await audit_dao.count_events(**filters))
    return [AuditEventDTO.model_validate(e) for e in events]


@router.get("/export.csv", name="export_audit_events")
async def export_audit_events(
    actor_email: str | None = Query(default=None, max_length=320),
    action: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    result: AuditResult | None = Query(default=None),
    severity: str | None = Query(default=None, max_length=32),
    source: str | None = Query(default=None, max_length=16),
    audit_dao: AuditDAO = Depends(),
    user: Annotated[User, Depends(require_permission("audit", "export"))] = None,  # type: ignore[assignment]
) -> StreamingResponse:
    """The matching events as CSV (at most 100,000); the export itself is recorded."""
    filters = {
        "actor_email": actor_email, "action": action, "target_id": target_id, "since": since, "until": until,
        "result": result, "severity": severity, "source": source,
    }
    events = await audit_dao.list_events(**filters, limit=_EXPORT_LIMIT, offset=0)
    await audit_dao.log(
        action="audit.export",
        target_type="audit",
        target_id="export.csv",
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="high",
        metadata_json=json.dumps({
            "events": len(events),
            "filters": {
                k: (v.isoformat() if isinstance(v, datetime) else str(v)) for k, v in filters.items() if v
            },
        }),
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "time", "actor_email", "actor_id", "action", "target_type", "target_id", "result", "severity",
        "source", "client_ip", "request_id", "user_agent", "metadata_json", "hash",
    ])
    for e in events:
        writer.writerow([
            e.time.isoformat(), e.actor_email or "", e.actor_id or "", e.action, e.target_type, e.target_id,
            e.result.value if isinstance(e.result, AuditResult) else e.result, e.severity, e.source,
            e.client_ip or "", e.request_id or "", e.user_agent or "", e.metadata_json or "", e.hash or "",
        ])
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )


@router.get("/chain", response_model=AuditChainDTO, name="check_audit_chain")
async def check_audit_chain(
    audit_dao: AuditDAO = Depends(),
    _user: Annotated[User, Depends(require_permission("audit", "read"))] = None,  # type: ignore[assignment]
) -> AuditChainDTO:
    """Whether every event is as it was written, the chain unbroken and the file copies agree."""
    verdict = await chain.verify(audit_dao.session, settings.audit_dir or None)
    return AuditChainDTO(
        ok=verdict.ok,
        events=verdict.events,
        legacy_rows=verdict.legacy_rows,
        intact_until=verdict.intact_until,
        problems=(verdict.problems or [])[:20],
    )
