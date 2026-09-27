"""What every dashboard endpoint builds its answer from: the clock and the facts."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException, Request

from faden_backend.services.dashboard import timewindows as tw
from faden_backend.services.dashboard.cache import get_fact_cache
from faden_backend.services.dashboard.facts import Facts, floored_now


def dashboard_now() -> datetime:
    """The instant figures are computed for (a dependency, so tests can fix it)."""
    return floored_now()


def build_facts(request: Request, now: datetime, tz_name: str | None = None) -> Facts:
    """The request's view of the fact cache; an unknown time zone is a 422."""
    try:
        tz = tw.parse_tz(tz_name)
    except tw.InvalidTimeZoneError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown time zone: {tz_name}") from exc
    state = request.app.state
    return Facts(
        cache=get_fact_cache(request),
        backend_factory=getattr(state, "db_session_factory", None),
        gateway_factory=getattr(state, "llm_graph_trace_session_factory", None),
        now=now,
        tz=tz,
        docker=getattr(state, "docker", None),
    )
