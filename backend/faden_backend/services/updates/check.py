"""Reading the release feed: once a day in the background, now on request, or from an upload.

The feed (``release_feed.FEED_URL``, or the mirror in ``updates.feed_url``) is
a public file: reading it sends nothing about this install, and what it says
is compared here. It is kept as published, with its signature, in
``release_feed_state`` and verified again whenever it is read.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.updates import ReleaseFeedState
from faden_backend.services.updates import release_feed, versions
from faden_backend.settings import settings

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 10.0
#: How often the feed is read.
CHECK_EVERY = timedelta(hours=24)
#: After a failed attempt, the next one.
RETRY_AFTER = timedelta(hours=6)
#: A check that has not worked for this long is shown on the dashboard.
FAILING_AFTER = timedelta(days=7)
USER_AGENT = "fadenstack-update-check"
_LOCK = "hashtext('faden.update_check')"


def feed_url() -> str:
    """Where the feed is read: the configured mirror, else fadenstack.com."""
    return (settings.updates_feed_url or "").strip() or release_feed.FEED_URL


def current_release() -> str:
    """The release this install runs: what `faden deploy` pinned, else the backend's own version.

    A pin that is no release (``latest`` in a hand-made .env) says nothing; the package does.
    """
    pinned = (settings.install_release or "").strip()
    return pinned if release_feed.parse_version(pinned) else versions.backend_version()


async def load_state(session: AsyncSession) -> ReleaseFeedState | None:
    """The kept feed and last attempt; None before the first check."""
    return await session.get(ReleaseFeedState, 1)


def stored_feed(state: ReleaseFeedState | None) -> release_feed.Feed | None:
    """The kept feed, verified again; None when there is none or it does not verify."""
    if state is None or not state.index_text or not state.signature_text:
        return None
    try:
        return release_feed.verify(state.index_text.encode(), state.signature_text.encode())
    except release_feed.FeedError as exc:
        log.warning("The kept release feed does not verify: %s", exc)
        return None


def assessment(state: ReleaseFeedState | None) -> release_feed.Assessment | None:
    """This install measured against the kept feed; None without one."""
    feed = stored_feed(state)
    if feed is None:
        return None
    return release_feed.assess(feed, current_release(), settings.updates_channel)


def due(state: ReleaseFeedState | None, now: datetime) -> bool:
    """Whether the feed is to be read again."""
    if state is None or state.checked_at is None:
        return True
    failed_last = state.error is not None and (
        state.succeeded_at is None or state.checked_at > state.succeeded_at
    )
    if failed_last:
        return now - state.checked_at >= RETRY_AFTER
    return now - (state.succeeded_at or state.checked_at) >= CHECK_EVERY


def failing(state: ReleaseFeedState | None, now: datetime) -> bool:
    """The check has not worked for :data:`FAILING_AFTER`, or not once yet after trying."""
    if state is None or state.error is None or state.checked_at is None:
        return False
    if state.succeeded_at is None:
        return True
    return state.checked_at > state.succeeded_at and now - state.succeeded_at >= FAILING_AFTER


def _describe(exc: Exception, url: str) -> str:
    """What went wrong, for an administrator."""
    host = httpx.URL(url).host or url
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc.request.url} answered {exc.response.status_code}."
    if isinstance(exc, httpx.TimeoutException):
        return f"{host} did not answer within {FETCH_TIMEOUT_S:.0f} s."
    if isinstance(exc, (httpx.ConnectError, httpx.ProxyError)):
        return f"Could not reach {host}: no internet from this server, or a proxy is needed ({exc})."
    if isinstance(exc, httpx.HTTPError):
        return f"Reading {url} failed: {exc}"
    return str(exc) or type(exc).__name__


async def fetch(url: str, *, etag: str | None) -> tuple[bytes, bytes, str | None] | None:
    """The feed and its signature; None when the feed did not change since *etag*."""
    from faden_backend.services.tls.connection_ssl import default_httpx_verify  # noqa: PLC0415

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(
        timeout=FETCH_TIMEOUT_S,
        follow_redirects=True,
        verify=default_httpx_verify(),
        proxy=(settings.updates_proxy_url or "").strip() or None,
        headers=headers,
    ) as client:
        index = await client.get(url, headers={"If-None-Match": etag} if etag else None)
        if index.status_code == httpx.codes.NOT_MODIFIED:
            return None
        index.raise_for_status()
        signature = await client.get(release_feed.signature_url(url))
        signature.raise_for_status()
    return index.content, signature.content, index.headers.get("etag")


def _keep(
    state: ReleaseFeedState, index: bytes, signature: bytes, *, source: str, url: str | None, now: datetime
) -> release_feed.Feed:
    """Verify *index*, refuse it when older than the kept one, and keep it."""
    feed = release_feed.verify(index, signature)
    release_feed.not_older(feed, state.generated_at)
    state.index_text = index.decode()
    state.signature_text = signature.decode()
    state.generated_at = feed.generated_at
    state.source = source
    state.url = url
    state.succeeded_at = now
    state.error = None
    return feed


async def _state_for_update(session: AsyncSession) -> ReleaseFeedState:
    state = await load_state(session)
    if state is None:
        state = ReleaseFeedState(id=1, notified_json=[])
        session.add(state)
    return state


async def check_now(session: AsyncSession, *, now: datetime | None = None) -> ReleaseFeedState:
    """Read the feed now and keep what it says; a failure is kept as the state's ``error``."""
    now = now or datetime.now(UTC)
    state = await _state_for_update(session)
    url = feed_url()
    state.checked_at = now
    try:
        got = await fetch(url, etag=state.etag if state.url == url and state.source == "url" else None)
        if got is None:  # not changed
            state.succeeded_at = now
            state.error = None
        else:
            index, signature, etag = got
            _keep(state, index, signature, source="url", url=url, now=now)
            state.etag = (etag or "")[:256] or None
    except (httpx.HTTPError, release_feed.FeedError, UnicodeDecodeError) as exc:
        state.error = _describe(exc, url)
        log.info("Update check: %s", state.error)
    await session.flush()
    return state


async def store_upload(
    session: AsyncSession, index: bytes, signature: bytes, *, now: datetime | None = None
) -> None:
    """Keep a feed an administrator uploaded (a site without internet). Raises FeedError when refused."""
    now = now or datetime.now(UTC)
    state = await _state_for_update(session)
    try:
        _keep(state, index, signature, source="upload", url=None, now=now)
    except UnicodeDecodeError as exc:
        raise release_feed.FeedError("The files are not text.") from exc
    state.checked_at = now
    state.etag = None
    await session.flush()


async def notify(session: AsyncSession, state: ReleaseFeedState) -> bool:
    """Email the administrators once about a release with security fixes; True when one was queued."""
    found = assessment(state)
    if found is None or found.latest is None or found.severity not in ("security", "critical"):
        return False
    version = found.latest.version
    notified = list(state.notified_json or [])
    if version in notified:
        return False
    state.notified_json = [*notified, version][-20:]
    if not settings.mailer_enabled:
        return False
    from faden_backend.services.notifications.service import NotificationService  # noqa: PLC0415

    urgent = found.severity == "critical"
    commands = release_feed.upgrade_commands(settings.cli_installer or None, found)
    # The fix may be in a release between this server's and the newest: name each, not just the newest.
    fixing = [r for r in found.newer if r.severity in ("security", "critical")]
    details = "\n".join([
        f"This server runs Fadenstack {found.current}. The newest release is {version}.",
        *(f"{r.version} ({r.severity}): {r.summary}" for r in fixing),
        f"Release notes: {found.latest.notes_url}" if found.latest.notes_url else "",
        "",
        "On the server, run:" if commands else "",
        *(f"    {c}" for c in commands),
        "",
        "Nothing is installed by itself. The console shows the same under Updates.",
    ]).strip()
    return await NotificationService(session).maybe_enqueue_admin_alert(
        subject=f"Fadenstack {version}: {'critical update -- install now' if urgent else 'security update'}",
        severity="critical" if urgent else "warning",
        fingerprint=f"release:{version}",
        summary=(
            f"Releases after {found.current}, which this server runs, fix security problems: "
            f"upgrade to {version}."
        ),
        details=details,
        source="update_check",
    )


async def run_pass(session_factory: Any, *, now: datetime | None = None) -> bool:
    """One pass of the background check: read the feed when it is due. True when it was read.

    Every uvicorn worker runs the loop; a transaction-scoped advisory lock lets
    one act, and the kept ``checked_at`` stops the others from reading it again.
    """
    from faden_backend.services.system_settings.runtime_mapping import refresh_runtime_values  # noqa: PLC0415

    now = now or datetime.now(UTC)
    async with session_factory() as session:
        await refresh_runtime_values(session, prefix="updates.")
        if not settings.updates_check_enabled:
            return False
        if session.bind.dialect.name == "postgresql":
            locked = (await session.execute(text(f"SELECT pg_try_advisory_xact_lock({_LOCK})"))).scalar()
            if not locked:
                return False
        if not due(await load_state(session), now):
            return False
        state = await check_now(session, now=now)
        if state.error is None:
            await notify(session, state)
        await session.commit()
        return True
