"""The provenance module's status (``/api/admin/provenance``), read on the console's Modules page.

Switching the module on and off is the services routes' (``/api/admin/services/provenance/...``), like every
module's; switching it off there needs an explicit confirmation and is recorded.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User
from faden_backend.services.provenance.client import engine_info
from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values
from faden_backend.settings import settings
from faden_backend.web.api.rbac import require_permission

router = APIRouter()


@router.get("/status", name="provenance_status")
async def provenance_status(
    _user: Annotated[User, Depends(require_permission("modules", "read"))],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Whether the module is switched on, and what its engine says about itself.

    The engine is asked with the service token. One that does not answer is reported as ``not_running``
    (``refused`` when it turns the token down), never as an error.
    """
    # Switched or re-pointed in another worker: read the current values here too.
    await refresh_runtime_values(session, prefix="faden_gateway.provenance_")
    info = await engine_info()
    return {"enabled": bool(settings.provenance_enabled), **info.as_dict()}
