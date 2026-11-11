"""GET /api/apps: the Fadenstack apps an administrator offers here, for anyone signed in.

Each with its newest release (the download links go to where the app is published; nothing is served from here)
and this server's address, to connect it. An app no administrator offers is not listed: news about apps is for
administrators, not for the people of an organisation.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values
from faden_backend.services.updates import apps
from faden_backend.web.api.about import vendor_links
from faden_backend.web.origin import public_origin

router = APIRouter()


@router.get("/apps", name="apps_offered")
async def get_offered_apps(
    request: Request,
    _user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    lang: Annotated[str | None, Query(max_length=16)] = None,
) -> dict[str, Any]:
    """The apps offered here, their newest release, and this server's address."""
    await refresh_runtime_values(session, prefix="updates.")
    view = apps.people_view(await apps.load_state(session), origin=public_origin(request), lang=lang)
    view["vendor_links"] = vendor_links()
    return view
