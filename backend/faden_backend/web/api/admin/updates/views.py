"""Updates: which release this install runs, what is newer, and what to run on the server to get it.

Administrators only. Nothing here installs anything (deployment decisions of
29 September 2026): the page shows the commands, and an operator runs them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User
from faden_backend.services import edge_trust
from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values
from faden_backend.services.system_settings.service import SystemSettingsService
from faden_backend.services.updates import apps, check, overview, release_feed
from faden_backend.web.api.about import vendor_links
from faden_backend.web.api.admin.dependencies import require_superuser
from faden_backend.web.api.admin.system.views import get_system_settings_service
from faden_backend.web.origin import public_origin

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
    """Read the release list and the app list now (also when the daily check is off: an administrator asked)."""
    await _current_settings(session)
    now = datetime.now(UTC)
    await check.check_feed(session, now=now)
    # Apart from the release check: an error in the app list's part never undoes it (check.check_apps).
    await check.check_apps(session, now=now, gateway_factory=_gateway_factory(request))
    await session.commit()
    return await overview.overview(session, getattr(request.app.state, "docker", None), now)


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


# -- Apps: the app list (apps.json), the apps in use here, what people are offered ------------------------------


def _gateway_factory(request: Request) -> Any:
    return getattr(request.app.state, "llm_graph_trace_session_factory", None)


async def _apps_view(request: Request, session: AsyncSession, lang: str | None) -> dict[str, Any]:
    now = datetime.now(UTC)
    factory = _gateway_factory(request)
    seen = await check.apps_in_use(factory, now)
    view = apps.admin_view(await apps.load_state(session), seen, gateway_ok=factory is not None, lang=lang, now=now)
    view["vendor_links"] = vendor_links()
    view["own_ca"] = edge_trust.needs_ca()
    return view


@router.get("/apps", name="admin_updates_apps")
async def get_apps(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    lang: Annotated[str | None, Query(max_length=16)] = None,
) -> dict[str, Any]:
    """Every app of the app list: its newest release, the versions in use here, whether people are offered it."""
    await _current_settings(session)
    return await _apps_view(request, session, lang)


@router.post("/apps/feed", name="admin_updates_upload_apps")
async def post_apps_feed(
    request: Request,
    index: Annotated[UploadFile, File(description="apps.json, as published")],
    signature: Annotated[UploadFile, File(description="apps.json.sig, as published")],
    _user: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    lang: Annotated[str | None, Query(max_length=16)] = None,
) -> dict[str, Any]:
    """Keep an app list copied from fadenstack.com by hand: for a server without internet."""
    raw_index = await index.read(MAX_UPLOAD_BYTES + 1)
    raw_signature = await signature.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_index) > MAX_UPLOAD_BYTES or len(raw_signature) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="That is not an app list: it is far too large.")
    await _current_settings(session)
    try:
        await apps.store_upload(session, raw_index, raw_signature)
    except release_feed.FeedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    state = await apps.load_state(session)
    await apps.notify(session, state, await check.apps_in_use(_gateway_factory(request), datetime.now(UTC)))  # type: ignore[arg-type]
    await session.commit()
    return await _apps_view(request, session, lang)


class OfferedDTO(BaseModel):
    """Whether people are offered an app on their Apps page."""

    offered: bool


@router.put("/apps/{app_id}/offered", name="admin_updates_app_offered")
async def put_app_offered(
    request: Request,
    app_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{0,39}$")],
    body: OfferedDTO,
    user: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    service: Annotated[SystemSettingsService, Depends(get_system_settings_service)],
    lang: Annotated[str | None, Query(max_length=16)] = None,
) -> dict[str, Any]:
    """Offer an app to people, or stop: kept in the setting ``updates.apps_offered``."""
    await _current_settings(session)
    catalogue = apps.stored(await apps.load_state(session))
    known = {a.id for a in catalogue.apps} if catalogue else set()
    offered = apps.offered_ids()
    if body.offered and app_id not in known:
        raise HTTPException(status_code=404, detail="The app list has no such app.")
    offered = (offered | {app_id}) if body.offered else (offered - {app_id})
    await service.update_value(
        key="updates.apps_offered",
        value=",".join(sorted(offered)),
        actor_id=user.id,
        root_mode_active=False,
    )
    await session.commit()
    return await _apps_view(request, session, lang)


@router.get("/apps/{app_id}/connect/{kind}", name="admin_updates_app_connect")
async def get_app_connect_file(
    request: Request,
    app_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{0,39}$")],
    kind: Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{0,39}$")],
    _user: Annotated[User, Depends(require_superuser)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """A file that connects the app to this server (an IT policy, the commands to run): nothing secret in it.

    Served as an attachment under a fixed name, never rendered, its type not guessed (``nosniff`` here too, not
    only from nginx: a console reached without it): OWASP ASVS 5.0 3.2.1, 3.4.4, 5.4.1, 5.4.2.
    """
    await _current_settings(session)
    catalogue = apps.stored(await apps.load_state(session))
    app = catalogue.app(app_id) if catalogue else None
    found = (
        apps.connect_file(app, kind, origin=public_origin(request), own_ca=edge_trust.needs_ca()) if app else None
    )
    if found is None:
        raise HTTPException(status_code=404, detail="No such file for this app.")
    return Response(
        found.content,
        media_type=found.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{found.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
