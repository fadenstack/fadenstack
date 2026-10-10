"""The Updates page and the update notice: the release feed and the install's parts, put together."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.updates import ReleaseFeedState
from faden_backend.services.updates import check, release_feed, versions
from faden_backend.settings import settings

log = logging.getLogger(__name__)


def _check_info(state: ReleaseFeedState | None, now: datetime) -> dict[str, Any]:
    return {
        "enabled": settings.updates_check_enabled,
        "channel": settings.updates_channel,
        "feed_url": check.feed_url(),
        "source": state.source if state else None,
        "checked_at": state.checked_at if state else None,
        "succeeded_at": state.succeeded_at if state else None,
        "feed_generated_at": state.generated_at if state else None,
        "error": state.error if state else None,
        "failing": check.failing(state, now),
    }


def notice(state: ReleaseFeedState | None) -> dict[str, Any]:
    """What the banner on admin pages needs: no Docker, no machines, just the feed."""
    found = check.assessment(state)
    if found is None:
        return {
            "current": check.current_release(),
            "latest": None,
            "severity": "none",
            "update_available": False,
            "unsupported": False,
            "supported_until": None,
        }
    return {
        "current": found.current,
        "latest": found.latest.version if found.latest else None,
        "severity": found.severity,
        "update_available": found.update_available,
        "unsupported": found.unsupported,
        "supported_until": found.supported_until,
    }


async def overview(session: AsyncSession, docker: Any, now: datetime) -> dict[str, Any]:
    """Everything the Updates page shows."""
    state = await check.load_state(session)
    found = check.assessment(state)
    current = check.current_release()
    latest = found.latest if found else None

    parts: list[versions.Part] = []
    docker_error: str | None = None
    if docker is not None:
        try:
            parts += await versions.services(docker, current)
        except Exception as exc:  # shown on the page, not a failure of it
            docker_error = f"Docker could not be asked which containers run: {exc}"
            log.warning(docker_error)
    if not any(p.name == "faden-backend" for p in parts):
        # Not in a container (a source checkout), or Docker is out of reach: the backend still knows itself.
        own = versions.backend_version()
        same = release_feed.parse_version(own) == release_feed.parse_version(current)
        parts.insert(0, versions.Part(
            "service", "faden-backend", own, current, "current" if same else "mismatch",
        ))
    parts += await versions.agents(session)
    parts += await versions.clusters(session)
    installer = (settings.cli_installer or "").strip() or None
    parts.append(versions.Part(
        "cli", "faden", settings.install_release or None, current,
        "current" if settings.install_release else "unknown",
        detail=installer,
    ))

    released_agent = latest.components.get("agent") if latest else None
    return {
        # The notice's fields first: its "current" (the release) is replaced by the fuller object below.
        **notice(state),
        "check": _check_info(state, now),
        "current": {
            "release": current,
            "backend": versions.backend_version(),
            "installer": installer,
        },
        "latest_release": release_feed.as_dict(latest) if latest else None,
        "newer": [release_feed.as_dict(r) for r in (found.newer if found else ())],
        "path": list(found.path) if found else [],
        "commands": release_feed.upgrade_commands(installer, found) if found else [],
        "released_agent": released_agent,
        "parts": [p.as_dict() for p in parts],
        "docker_error": docker_error,
    }
