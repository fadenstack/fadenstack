"""The app list: the apps people run against this server, which of them are in use here, and their releases.

The list (``apps.json`` with ``apps.json.sig``, beside the release feed) is read with the feed, once a day, the
same way: a GET of a public file, nothing about this install or its apps sent. It is kept as published in
``release_feed_state`` row 2 and verified again whenever it is read. Which apps run here is read from the
gateway: each app reports its manifest (name and version) when it starts. Everything is compared here.

People who are not administrators see only the apps an administrator offers them (``updates.apps_offered``);
nothing from the list reaches them otherwise.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.updates import ReleaseFeedState
from faden_backend.services.updates import check, release_feed
from faden_backend.settings import settings

log = logging.getLogger(__name__)

#: The row of ``release_feed_state`` the app list is kept in (row 1 is the feed's).
APPS_ROW = 2
#: An app version seen within this long counts as in use.
IN_USE_DAYS = 30
#: An app first released within this long counts as new.
NEW_DAYS = 30

#: The versions of each app (``app_name``) its people ran lately: from the gateway's manifests.
IN_USE_SQL = """
SELECT app_name, app_version, MAX(last_seen_at) AS last_seen_at
FROM agent_client_manifest
WHERE source = 'app' AND last_seen_at >= :since AND app_name <> '' AND app_version <> ''
GROUP BY app_name, app_version
"""


def apps_url() -> str:
    """Where the app list is read: beside the feed (or its configured mirror)."""
    return release_feed.apps_url(check.feed_url())


async def load_state(session: AsyncSession) -> ReleaseFeedState | None:
    """The kept app list and last attempt; None before the first."""
    return await session.get(ReleaseFeedState, APPS_ROW)


def stored(state: ReleaseFeedState | None) -> release_feed.AppCatalogue | None:
    """The kept app list, verified again; None when there is none or it does not verify."""
    if state is None or not state.index_text or not state.signature_text:
        return None
    try:
        return release_feed.verify_apps(state.index_text.encode(), state.signature_text.encode())
    except release_feed.FeedError as exc:
        log.warning("The kept app list does not verify: %s", exc)
        return None


async def _state_for_update(session: AsyncSession) -> ReleaseFeedState:
    state = await load_state(session)
    if state is None:
        state = ReleaseFeedState(id=APPS_ROW, notified_json=[])
        session.add(state)
    return state


def _keep(
    state: ReleaseFeedState, index: bytes, signature: bytes, *, source: str, url: str | None, now: datetime,
) -> release_feed.AppCatalogue:
    catalogue = release_feed.verify_apps(index, signature)
    release_feed.not_older_apps(catalogue, state.generated_at)
    state.index_text = index.decode()
    state.signature_text = signature.decode()
    state.generated_at = catalogue.generated_at
    state.source = source
    state.url = url
    state.succeeded_at = now
    state.error = None
    return catalogue


async def check_now(session: AsyncSession, *, now: datetime | None = None) -> ReleaseFeedState:
    """Read the app list now and keep it; a failure is kept as the row's ``error``, the list before it stays."""
    now = now or datetime.now(UTC)
    state = await _state_for_update(session)
    url = apps_url()
    state.checked_at = now
    try:
        got = await check.fetch(url, etag=state.etag if state.url == url and state.source == "url" else None)
        if got is None:
            state.succeeded_at = now
            state.error = None
        else:
            index, signature, etag = got
            _keep(state, index, signature, source="url", url=url, now=now)
            state.etag = (etag or "")[:256] or None
    except (httpx.HTTPError, release_feed.FeedError, UnicodeDecodeError) as exc:
        state.error = check._describe(exc, url)  # noqa: SLF001 -- the same words as the feed's
        log.info("App list: %s", state.error)
    await session.flush()
    return state


async def store_upload(session: AsyncSession, index: bytes, signature: bytes, *, now: datetime | None = None) -> None:
    """Keep an app list an administrator uploaded (a site without internet). Raises FeedError when refused."""
    now = now or datetime.now(UTC)
    state = await _state_for_update(session)
    try:
        _keep(state, index, signature, source="upload", url=None, now=now)
    except UnicodeDecodeError as exc:
        raise release_feed.FeedError("The files are not text.") from exc
    state.checked_at = now
    state.etag = None
    await session.flush()


@dataclass(frozen=True)
class Seen:
    """A version of an app its people ran, and when last."""

    version: str
    last_seen: datetime


async def in_use(gateway: AsyncSession | None, now: datetime) -> dict[str, list[Seen]]:
    """``app_name`` -> the versions run within :data:`IN_USE_DAYS`; empty without the gateway's database.

    Names of the people are not read: what matters here is which versions run, not who runs them.
    """
    if gateway is None:
        return {}
    try:
        rows = (await gateway.execute(text(IN_USE_SQL), {"since": now - timedelta(days=IN_USE_DAYS)})).mappings()
    except Exception as exc:  # the gateway's database out of reach: no versions known, said on the page
        log.warning("Apps in use could not be read from the gateway: %s", exc)
        return {}
    found: dict[str, list[Seen]] = {}
    for row in rows:
        found.setdefault(str(row["app_name"]), []).append(Seen(str(row["app_version"]), row["last_seen_at"]))
    return found


def versions_of(app: release_feed.App, seen: dict[str, list[Seen]]) -> list[Seen]:
    """The versions of *app* in use here, by the names it reports as."""
    return [s for name in app.reports_as for s in seen.get(name, [])]


def offered_ids() -> set[str]:
    """The apps an administrator offers to people (``updates.apps_offered``)."""
    return {p.strip() for p in (settings.updates_apps_offered or "").split(",") if p.strip()}


def assessments(
    catalogue: release_feed.AppCatalogue | None, seen: dict[str, list[Seen]], *, today: date | None = None,
) -> list[release_feed.AppAssessment]:
    """Every app of the list, measured against what runs here."""
    if catalogue is None:
        return []
    server = check.current_release()
    return [
        release_feed.assess_app(
            app, settings.updates_channel, [s.version for s in versions_of(app, seen)], server, today,
        )
        for app in catalogue.apps
    ]


def first_released(app: release_feed.App) -> date | None:
    """The day of the app's first release in the list, when the list says."""
    days = []
    for r in app.releases:
        try:
            days.append(date.fromisoformat(r.date))
        except ValueError:
            continue
    return min(days) if days else None


def is_new(app: release_feed.App, today: date) -> bool:
    """The app's first release is recent: an app this server's administrators may not know yet."""
    first = first_released(app)
    return first is not None and today - first <= timedelta(days=NEW_DAYS)


def _app_head(app: release_feed.App, lang: str | None) -> dict[str, Any]:
    return {
        "id": app.id,
        "name": release_feed.localized(app.name, lang),
        "description": release_feed.localized(app.description, lang),
        "page": app.page,
        "store": app.store,
        "connect": app.connect,
        "agents": dict(app.agents),
    }


def admin_view(
    state: ReleaseFeedState | None, seen: dict[str, list[Seen]], *, gateway_ok: bool, lang: str | None,
    now: datetime,
) -> dict[str, Any]:
    """Everything the Updates page shows about apps."""
    catalogue = stored(state)
    offered = offered_ids()
    today = now.date()
    apps = []
    for found in assessments(catalogue, seen, today=today):
        app = found.app
        last_seen = {s.version: s.last_seen for s in versions_of(app, seen)}
        apps.append({
            **_app_head(app, lang),
            "offered": app.id in offered,
            "new": is_new(app, today),
            "latest": release_feed.app_release_dict(found.latest, lang) if found.latest else None,
            "in_use": [{"version": v, "last_seen": last_seen.get(v)} for v in found.in_use],
            "outdated": list(found.outdated),
            "severity": found.severity,
            "newer": [release_feed.app_release_dict(r, lang) for r in found.newer],
            "unsupported": list(found.unsupported),
            "supported_until": found.supported_until,
            "server_too_old": found.server_too_old,
            "connect_files": connect_kinds(app),
        })
    return {
        "check": {
            "enabled": settings.updates_check_enabled,
            "url": apps_url(),
            "source": state.source if state else None,
            "checked_at": state.checked_at if state else None,
            "succeeded_at": state.succeeded_at if state else None,
            "list_generated_at": state.generated_at if state else None,
            "error": state.error if state else None,
        },
        "news": settings.updates_app_news,
        "in_use_days": IN_USE_DAYS,
        "in_use_known": gateway_ok,
        "apps": apps,
    }


def people_view(state: ReleaseFeedState | None, *, origin: str, lang: str | None) -> dict[str, Any]:
    """What people see: the apps an administrator offers, their newest release, and how to connect them here."""
    catalogue = stored(state)
    offered = offered_ids()
    apps = []
    for app in catalogue.apps if catalogue else ():
        if app.id not in offered:
            continue
        releases = release_feed.offered_app_releases(app, settings.updates_channel)
        latest = releases[0] if releases else None
        until = app.supported_until.get(f"{latest.parsed.major}.{latest.parsed.minor}") if latest else None
        apps.append({
            **_app_head(app, lang),
            "latest": release_feed.app_release_dict(latest, lang) if latest else None,
            "supported_until": until,
        })
    return {"server": origin, "apps": apps}


async def notify(session: AsyncSession, state: ReleaseFeedState, seen: dict[str, list[Seen]]) -> int:
    """Email the administrators once per app release that fixes security problems of a version in use.

    Returns how many were queued. Only security: news about apps never goes out by mail.
    """
    notified = list(state.notified_json or [])
    queued = 0
    for found in assessments(stored(state), seen):
        if found.latest is None or found.severity not in ("security", "critical") or not found.outdated:
            continue
        mark = f"{found.app.id}:{found.latest.version}"
        if mark in notified:
            continue
        notified.append(mark)
        from faden_backend.services.extensions import mail_available  # noqa: PLC0415

        if not mail_available():
            continue
        from faden_backend.services.notifications.service import NotificationService  # noqa: PLC0415

        name = release_feed.localized(found.app.name, "en")
        urgent = found.severity == "critical"
        fixing = [r for r in found.newer if r.severity in ("security", "critical")]
        details = "\n".join([
            f"People here run {name} {', '.join(found.outdated)}. The newest release is {found.latest.version}.",
            *(f"{r.version} ({r.severity}): {release_feed.localized(r.summary, 'en')}" for r in fixing),
            f"Release notes: {found.latest.notes_url}" if found.latest.notes_url else "",
            "",
            "Have the people who use it install the newest release. The console shows the same under Updates, Apps.",
        ]).strip()
        if await NotificationService(session).maybe_enqueue_admin_alert(
            subject=f"{name} {found.latest.version}: {'critical update' if urgent else 'security update'}",
            severity="critical" if urgent else "warning",
            fingerprint=f"app-release:{mark}",
            summary=f"{name} versions in use here have security problems fixed in {found.latest.version}.",
            details=details,
            source="update_check",
        ):
            queued += 1
    state.notified_json = notified[-50:]
    return queued


# --------------------------------------------------------------------------------------------------------------
# Connecting an app to this server: files and lines filled in with this server's address. None carries a secret:
# each person still signs in (or pairs) with their own account.
# --------------------------------------------------------------------------------------------------------------

#: The Chrome Web Store's update address, for an IT policy that installs an extension from it.
CHROME_UPDATE_URL = "https://clients2.google.com/service/update2/crx"
#: Where the Office policy expects the server's certificate authority, when the server has its own.
OFFICE_CA_PATH = r"%PROGRAMDATA%\Fadenstack\Office\fadenstack-ca.crt"


def connect_kinds(app: release_feed.App) -> list[str]:
    """The connection files this server can make for *app*."""
    if app.connect == "office":
        return ["office-policy"]
    if app.connect == "nextcloud":
        return ["nextcloud-occ"]
    if app.connect == "chrome" and app.store_id:
        return ["chrome-policy"]
    return []


@dataclass(frozen=True)
class ConnectFile:
    """A file to download: its fixed name, media type and content."""

    filename: str
    media_type: str
    content: str


def connect_file(app: release_feed.App, kind: str, *, origin: str, own_ca: bool) -> ConnectFile | None:
    """The connection file *kind* for *app*, filled in for this server at *origin*; None when there is none."""
    if kind not in connect_kinds(app):
        return None
    if kind == "office-policy":
        roles = ("excel", "word", "powerpoint", "outlook")
        policy = {
            "serverUrl": origin,
            "agents": {role: app.agents.get(role, f"{role}-assistant") for role in roles},
            "trustedCaCertificate": OFFICE_CA_PATH if own_ca else None,
        }
        return ConnectFile("policy.json", "application/json", json.dumps(policy, indent=2) + "\n")
    if kind == "nextcloud-occ":
        agent = app.agents.get("default", "nextcloud-assistant")
        lines = [
            "# Connect the Fadenstack app in Nextcloud to this server. Run on the Nextcloud server,",
            "# as the web server's user (www-data on Debian and Ubuntu), in Nextcloud's folder.",
            "php occ app:enable fadenstack_agent",
            f"php occ config:app:set fadenstack_agent server_url --value={origin}",
            f"php occ config:app:set fadenstack_agent agent --value={agent}",
            "php occ config:app:set fadenstack_agent integration_enabled --value=1",
        ]
        return ConnectFile("connect-nextcloud.sh", "text/plain", "\n".join(lines) + "\n")
    if kind == "chrome-policy":
        policy = {
            "ExtensionInstallForcelist": [f"{app.store_id};{CHROME_UPDATE_URL}"],
            "3rdparty": {
                "extensions": {
                    app.store_id: {"serverUrl": origin, "agent": app.agents.get("default", "browser-assistant")},
                },
            },
        }
        return ConnectFile("fadenstack-chrome-policy.json", "application/json", json.dumps(policy, indent=2) + "\n")
    return None
