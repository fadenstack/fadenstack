"""Schemas for admin dashboard endpoints."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class DashboardHealthBadge(str):
    """Simple health badge values for summary cards."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class DashboardHealthItemDTO(BaseModel):
    """Health state for a dependency/service."""

    name: str
    status: str
    detail: str | None = None


class DashboardTopUserDTO(BaseModel):
    """Top container by resource dimension."""

    container_id: str
    name: str
    value: float
    unit: str


class DashboardOverviewDTO(BaseModel):
    """Single payload used by the Admin Dashboard landing page."""

    system_status: str
    system_badge: str

    cpu_percent: float | None = None
    load_1m: float | None = None
    load_5m: float | None = None
    load_15m: float | None = None

    ram_used_bytes: int | None = None
    ram_total_bytes: int | None = None
    swap_used_bytes: int | None = None
    swap_total_bytes: int | None = None

    disk_free_bytes: int
    disk_total_bytes: int
    disk_free_percent: float

    network_rx_bytes: int | None = None
    network_tx_bytes: int | None = None

    containers_running: int
    containers_total: int
    containers_restarting: int
    restart_rate_1h: int
    restart_rate_24h: int

    api_error_rate_5xx: float = 0.0

    postgres_connections: int | None = None
    postgres_max_connections: int | None = None

    gpu_util_percent: float | None = None
    gpu_vram_used_bytes: int | None = None
    gpu_vram_total_bytes: int | None = None


class DashboardTopContainersDTO(BaseModel):
    """Running containers ranked by CPU and by memory usage."""

    top_cpu_containers: list[DashboardTopUserDTO] = Field(default_factory=list)
    top_memory_containers: list[DashboardTopUserDTO] = Field(default_factory=list)


class DashboardHealthDTO(BaseModel):
    """Aggregated dependency health response."""

    overall_status: str
    items: list[DashboardHealthItemDTO] = Field(default_factory=list)


class GrafanaPanelDTO(BaseModel):
    """Embed definition for a single Grafana panel."""

    panel_id: int
    title: str
    embed_url: str


class GrafanaPanelsDTO(BaseModel):
    """Grafana embed config returned to the frontend."""

    enabled: bool
    grafana_url: str | None = None
    dashboard_uid: str | None = None
    open_dashboard_url: str | None = None
    panels: list[GrafanaPanelDTO] = Field(default_factory=list)


# ── Redesigned dashboard: traffic and KPIs ─────────────────────────


class MoneyDTO(BaseModel):
    """An amount in one currency; amounts in different currencies are never added."""

    currency: str
    amount: Decimal


class KpiItemDTO(BaseModel):
    """One key metric: its value, the same measure over the comparison window, and context."""

    id: str
    value: float | int | list[MoneyDTO] | None = None
    previous: float | int | list[MoneyDTO] | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class KpisDTO(BaseModel):
    """The asked-for key metrics."""

    generated_at: datetime
    items: list[KpiItemDTO] = Field(default_factory=list)
    #: KPIs asked for that could not be computed (their source is unavailable).
    unavailable: list[str] = Field(default_factory=list)


class WindowDTO(BaseModel):
    """A half-open span of time, in UTC."""

    start: datetime
    end: datetime


class ResidencyCountDTO(BaseModel):
    """Routed requests of one residency class."""

    residency: str
    requests: int


class DestinationDTO(BaseModel):
    """Routed requests served at one destination."""

    #: cluster | this_server | found | provider | unknown | other
    kind: str
    id: str | None = None
    name: str | None = None
    residency: str | None = None
    requests: int


class ErrorsDTO(BaseModel):
    """Requests answered with status >= 400."""

    count: int
    rate: float | None = None


class LatencyDTO(BaseModel):
    """Request latency percentiles, in milliseconds."""

    p50: float | None = None
    p95: float | None = None


class TrafficDTO(BaseModel):
    """Requests in a window: how many, how fast, and where they were served."""

    generated_at: datetime
    window: WindowDTO
    requests: int
    routed: int
    tokens: int
    errors: ErrorsDTO
    latency_ms: LatencyDTO
    by_residency: list[ResidencyCountDTO]
    by_destination: list[DestinationDTO]


class SeriesBucketDTO(BaseModel):
    """Routed requests in one bucket, per residency class."""

    #: A local date ("2026-09-26") for days; a local time with offset for hours.
    bucket: str
    machines: int = 0
    private: int = 0
    external: int = 0
    unknown: int = 0
    #: The bucket is still filling (it contains now).
    partial: bool = False


class TrafficSeriesDTO(BaseModel):
    """Routed requests over time, per residency class."""

    generated_at: datetime
    granularity: str
    tz: str
    buckets: list[SeriesBucketDTO]
