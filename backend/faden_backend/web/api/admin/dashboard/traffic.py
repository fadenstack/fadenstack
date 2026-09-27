"""Dashboard traffic: key metrics, where requests were served, and requests per day.

Superuser-only like every dashboard endpoint. The access check is a FastAPI
dependency, resolved before any cached figure is read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from faden_backend.db.models.users import User
from faden_backend.services.dashboard import kpis as kpi_service
from faden_backend.services.dashboard import timewindows as tw
from faden_backend.services.dashboard import traffic
from faden_backend.services.dashboard.facts import Facts, GatewayUnavailableError
from faden_backend.web.api.admin.dashboard.common import build_facts, dashboard_now
from faden_backend.web.api.admin.dashboard.schema import (
    DestinationDTO,
    ErrorsDTO,
    KpiItemDTO,
    KpisDTO,
    LatencyDTO,
    ResidencyCountDTO,
    SeriesBucketDTO,
    TrafficDTO,
    TrafficSeriesDTO,
    WindowDTO,
)
from faden_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()

TzParam = Annotated[str | None, Query(max_length=64, description="The viewer's IANA time zone")]


def _require_gateway(facts: Facts) -> None:
    if not facts.has_gateway:
        raise HTTPException(status_code=503, detail="Gateway database not available")


@router.get("/kpis", response_model=KpisDTO, name="admin_dashboard_kpis")
async def get_kpis(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    now: Annotated[datetime, Depends(dashboard_now)],
    ids: Annotated[str, Query(max_length=400, description="Comma-separated KPI ids")] = "",
    tz: TzParam = None,
) -> KpisDTO:
    """The asked-for key metrics; unknown ids are ignored, failing ones listed as unavailable."""
    facts = build_facts(request, now, tz)
    wanted = [i.strip() for i in ids.split(",") if i.strip()] or list(kpi_service.KPIS)
    items, unavailable = await kpi_service.compute(wanted, facts)
    return KpisDTO(
        generated_at=now,
        items=[KpiItemDTO(**item) for item in items],
        unavailable=unavailable,
    )


@router.get("/traffic", response_model=TrafficDTO, name="admin_dashboard_traffic")
async def get_traffic(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    now: Annotated[datetime, Depends(dashboard_now)],
    window: Literal["today", "24h", "7d", "30d"] = "today",
    tz: TzParam = None,
) -> TrafficDTO:
    """Requests in the window: totals, latency, residency split and the top destinations."""
    facts = build_facts(request, now, tz)
    _require_gateway(facts)
    current, _ = tw.window(window, now, facts.tz)
    try:
        totals = await facts.totals(current, latency=True)
        rows = await facts.instances(current)
        attribution = await facts.attribution()
    except GatewayUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    split = traffic.residency_split(rows, attribution)
    top, other = traffic.destinations(rows, attribution)
    by_destination = [
        DestinationDTO(
            kind=d.destination.kind,
            id=d.destination.id,
            name=d.destination.name,
            residency=d.destination.residency,
            requests=d.requests,
        )
        for d in top
    ]
    if other:
        by_destination.append(DestinationDTO(kind="other", requests=other))

    return TrafficDTO(
        generated_at=now,
        window=WindowDTO(start=current.start, end=current.end),
        requests=totals.requests,
        routed=totals.routed,
        tokens=totals.tokens,
        errors=ErrorsDTO(count=totals.errors, rate=totals.error_rate),
        latency_ms=LatencyDTO(p50=totals.p50_ms, p95=totals.p95_ms),
        by_residency=[ResidencyCountDTO(residency=k, requests=v) for k, v in split.items()],
        by_destination=by_destination,
    )


@router.get("/traffic/series", response_model=TrafficSeriesDTO, name="admin_dashboard_traffic_series")
async def get_traffic_series(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    now: Annotated[datetime, Depends(dashboard_now)],
    range_: Annotated[Literal["7d", "30d"], Query(alias="range")] = "7d",
    granularity: Literal["day", "hour"] = "day",
    tz: TzParam = None,
) -> TrafficSeriesDTO:
    """Routed requests per local day (or hour), per residency class; every bucket present."""
    facts = build_facts(request, now, tz)
    _require_gateway(facts)
    days = 7 if range_ == "7d" else 30
    try:
        attribution = await facts.attribution()
        if granularity == "day":
            span, dates = tw.day_range(now, facts.tz, days)
            folded = traffic.fold_buckets(await facts.day_buckets(span), attribution)
            labels = [d.isoformat() for d in dates]
            buckets = [
                SeriesBucketDTO(bucket=label, partial=(label == labels[-1]), **folded.get(label, {}))
                for label in labels
            ]
        else:
            first = (now - timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
            folded = traffic.fold_buckets(await facts.hour_buckets(tw.Window(first, now)), attribution)
            by_hour = {k.astimezone(UTC).replace(tzinfo=UTC): v for k, v in folded.items()}
            buckets = []
            hour = first
            while hour <= now:
                buckets.append(
                    SeriesBucketDTO(
                        bucket=hour.astimezone(facts.tz).isoformat(),
                        partial=hour + timedelta(hours=1) > now,
                        **by_hour.get(hour, {}),
                    )
                )
                hour += timedelta(hours=1)
    except GatewayUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return TrafficSeriesDTO(generated_at=now, granularity=granularity, tz=facts.tz.key, buckets=buckets)
