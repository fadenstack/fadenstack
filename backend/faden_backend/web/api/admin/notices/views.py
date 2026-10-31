"""What administrators should know now (``GET /api/admin/notices``).

The notices come from core's own sources (restrictions on the tenant nothing enforces,
``services/tenant_restrictions.py``; the first purge of old request records still ahead,
``services/usage/notices.py``) and the sources registered in ``services/extensions.py``
(``admin_notices``). Each is a code, a severity and its values: the console words it in the reader's
language. A source that fails is logged and left out, so one broken source never hides the others.

``DELETE /api/admin/notices/restrictions/{name}`` lifts a restriction nothing enforces, the action of its
notice.
"""

from __future__ import annotations

import json
import logging
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Path as FastAPIPath
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.users import User
from faden_backend.services import tenant_restrictions
from faden_backend.services.extensions import AdminNoticeSource, registry
from faden_backend.services.usage import notices as usage_notices
from faden_backend.web.api.admin.dependencies import require_superuser

log = logging.getLogger(__name__)

router = APIRouter()


class AdminNoticeOut(BaseModel):
    """One notice: the console's text for ``code``, filled with ``params``."""

    code: str
    severity: Literal["info", "warning", "error"]
    params: dict[str, str | int]


@router.get("", response_model=list[AdminNoticeOut], name="admin_notices")
async def admin_notices(
    _admin: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    request: Request,
) -> list[AdminNoticeOut]:
    """Every registered source's notices, errors first."""
    found: list[AdminNoticeOut] = []
    gateway = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    purge = partial(usage_notices.purge_pending, gateway_sessions=gateway)
    sources: list[AdminNoticeSource] = [tenant_restrictions.notices, purge, *registry.admin_notices]
    for source in sources:
        try:
            notices = await source(session)
        except Exception:
            log.exception("An admin notice source failed: %s", getattr(source, "__qualname__", source))
            continue
        found.extend(AdminNoticeOut(code=n.code, severity=n.severity, params=dict(n.params)) for n in notices)
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda notice: order[notice.severity])


@router.delete(
    "/restrictions/{name}", status_code=status.HTTP_204_NO_CONTENT, name="lift_tenant_restriction",
)
async def lift_tenant_restriction(
    name: Annotated[str, FastAPIPath(max_length=64)],
    admin: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    """Lift a restriction on the tenant that nothing installed enforces; refused while one does."""
    if name not in await tenant_restrictions.marked(session):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="The tenant has no such restriction",
        )
    if name in registry.tenant_restrictions:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This restriction is enforced; change it where it is kept.",
        )
    await tenant_restrictions.set_restricted(session, name, on=False)
    await AuditDAO(session).log(
        action="tenant.restriction.lift", target_type="tenant", target_id=tenant_restrictions.TENANT,
        result=AuditResult.ALLOW, actor_id=admin.id, severity="high",
        metadata_json=json.dumps({"restriction": name}),
    )
