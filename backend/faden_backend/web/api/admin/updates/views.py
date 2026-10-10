"""Updates: which release this install runs, what is newer, and what to run on the server to get it.

Administrators only. Nothing here installs anything (deployment decisions of
29 September 2026): the page shows the commands, and an operator runs them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User
from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values
from faden_backend.services.updates import check, overview, release_feed
from faden_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()

#: An uploaded feed is a few kilobytes; anything near this is not one.
MAX_UPLOAD_BYTES = 1024 * 1024


async def _current_settings(session: AsyncSession) -> None:
    # Changed under Settings > Updates in another worker: read them here too.
    await refresh_runtime_values(session, prefix="updates.")


@router.get("", name="admin_updates_overview")
async def get_overview(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """The install's release and parts, the newest release, and the commands to upgrade."""
    await _current_settings(session)
    return await overview.overview(session, getattr(request.app.state, "docker", None), datetime.now(UTC))


@router.get("/notice", name="admin_updates_notice")
async def get_notice(
    _user: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Just enough for the banner on admin pages (the kept feed only: quick)."""
    await _current_settings(session)
    return overview.notice(await check.load_state(session))


@router.post("/check", name="admin_updates_check")
async def post_check(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Read the release list now (also when the daily check is off: an administrator asked)."""
    await _current_settings(session)
    state = await check.check_now(session)
    if state.error is None:
        await check.notify(session, state)
    await session.commit()
    return await overview.overview(session, getattr(request.app.state, "docker", None), datetime.now(UTC))


@router.post("/feed", name="admin_updates_upload_feed")
async def post_feed(
    request: Request,
    index: Annotated[UploadFile, File(description="index.json, as published")],
    signature: Annotated[UploadFile, File(description="index.json.sig, as published")],
    _user: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Keep a release list copied from fadenstack.com by hand: for a server without internet."""
    raw_index = await index.read(MAX_UPLOAD_BYTES + 1)
    raw_signature = await signature.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_index) > MAX_UPLOAD_BYTES or len(raw_signature) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That is not a release list: it is far too large.")
    await _current_settings(session)
    try:
        await check.store_upload(session, raw_index, raw_signature)
    except release_feed.FeedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state = await check.load_state(session)
    await check.notify(session, state)  # type: ignore[arg-type]
    await session.commit()
    return await overview.overview(session, getattr(request.app.state, "docker", None), datetime.now(UTC))
