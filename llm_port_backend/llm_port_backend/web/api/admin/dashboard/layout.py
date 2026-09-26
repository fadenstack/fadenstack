"""The signed-in user's dashboard layout (see ``services/dashboard/layout.py``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.users import User
from llm_port_backend.services.dashboard import layout as layout_store
from llm_port_backend.web.api.admin.dashboard.caching import NO_STORE
from llm_port_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()


class LayoutSaveRequest(BaseModel):
    """A layout and the revision it was edited from."""

    layout: layout_store.DashboardLayout
    base_revision: int = Field(ge=0)


@router.get("/layout", response_model=layout_store.StoredLayout, name="admin_dashboard_layout")
async def get_layout(
    user: Annotated[User, Depends(require_superuser)],
    session: AsyncSession = Depends(get_db_session),
) -> layout_store.StoredLayout:
    """The user's layout and its revision (``layout`` null and revision 0 before the first save)."""
    return await layout_store.load(session, user.id)


@router.put(
    "/layout",
    response_model=layout_store.StoredLayout,
    responses={409: {"model": layout_store.StoredLayout, "description": "Saved elsewhere first"}},
    name="admin_dashboard_layout_save",
)
async def save_layout(
    body: LayoutSaveRequest,
    user: Annotated[User, Depends(require_superuser)],
    session: AsyncSession = Depends(get_db_session),
) -> layout_store.StoredLayout | JSONResponse:
    """Save the layout if nothing was saved since ``base_revision``; else 409 with the stored one."""
    saved, stored = await layout_store.save(session, user.id, body.layout, body.base_revision)
    if saved:
        return stored
    return JSONResponse(
        status_code=409,
        content=stored.model_dump(mode="json"),
        headers={"Cache-Control": NO_STORE},
    )
