"""What administrators should know now (``GET /api/admin/notices``).

The notices come from the sources registered in ``services/extensions.py`` (``admin_notices``); core has
none of its own. Each is a code, a severity and its values: the console words it in the reader's
language. A source that fails is logged and left out, so one broken source never hides the others.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User
from faden_backend.services.extensions import registry
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
) -> list[AdminNoticeOut]:
    """Every registered source's notices, errors first."""
    found: list[AdminNoticeOut] = []
    for source in registry.admin_notices:
        try:
            notices = await source(session)
        except Exception:
            log.exception("An admin notice source failed: %s", getattr(source, "__qualname__", source))
            continue
        found.extend(AdminNoticeOut(code=n.code, severity=n.severity, params=dict(n.params)) for n in notices)
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(found, key=lambda notice: order[notice.severity])
