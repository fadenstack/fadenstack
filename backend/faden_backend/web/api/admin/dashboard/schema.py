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


# ── Redesigned dashboard: capacity and attention ───────────────────


class GpuDTO(BaseModel):
    """One accelerator's load and temperature, as its machine last reported."""

    index: int
    util_pct: float | None = None
    vram_allocated_pct: float | None = None
    temp_c: float | None = None


class MachineRefDTO(BaseModel):
    """A machine named in a group's exceptions."""

    id: str
    name: str
    health: str


class MachineDTO(MachineRefDTO):
    """A machine: its reported health, and its load (kept apart from health)."""

    last_seen: datetime | None = None
    cpu_pct: float | None = None
    #: The busiest GPU's utilization.
    gpu_util_pct: float | None = None
    #: The fullest GPU's allocated VRAM (vLLM allocates ~90 % by design).
    vram_allocated_pct: float | None = None
    gpus: list[GpuDTO] = Field(default_factory=list)


class LoadHistogramDTO(BaseModel):
    """Machines per 20 % band: [0-20), [20-40), [40-60), [60-80), [80-100]."""

    gpu_util: list[int]
    vram_allocated: list[int]


class CapacityGroupDTO(BaseModel):
    """A cluster, or the machines in no cluster, with aggregates for large fleets."""

    kind: str  # cluster | unassigned
    id: str | None = None
    name: str | None = None
    status: str | None = None
    #: When the cluster figures were observed (null: not stamped yet, or no cluster).
    observed_at: datetime | None = None
    stale: bool = False
    machines_total: int
    machines_online: int
    gpus_total: int | None = None
    #: Free GPUs from the stored Ray observation; null when never observed.
    gpus_free: float | None = None
    health_counts: dict[str, int]
    load_histogram: LoadHistogramDTO
    exceptions: list[MachineRefDTO] = Field(default_factory=list)
    #: The machines; left out for large fleets unless this group was asked for.
    machines: list[MachineDTO] | None = None
    #: For a group asked for: the cursor of its next page, if any.
    next_cursor: str | None = None


class CapacityTotalsDTO(BaseModel):
    """Counts across the whole fleet."""

    clusters: int
    clusters_healthy: int
    machines: int
    machines_online: int
    gpus: int
    gpus_free: float | None = None
    gpus_free_unknown_clusters: int = 0


class CapacityDTO(BaseModel):
    """Local AI capacity."""

    generated_at: datetime
    totals: CapacityTotalsDTO
    groups: list[CapacityGroupDTO]
    #: Whether every group carries its machines (small fleets).
    machines_included: bool


class AttentionItemDTO(BaseModel):
    """One current problem: a code and parameters the UI renders in the viewer's language."""

    id: str
    code: str
    severity: str
    category: str
    params: dict[str, Any] = Field(default_factory=dict)
    since: datetime | None = None
    link: str | None = None


class MutedAttentionItemDTO(AttentionItemDTO):
    """A current problem an administrator muted: left out of ``items`` and the counts."""

    acknowledged_at: datetime
    #: Who muted it (email); ``None`` once that user is gone.
    acknowledged_by: str | None = None


class AttentionDTO(BaseModel):
    """Current problems, most severe first."""

    generated_at: datetime
    items: list[AttentionItemDTO]
    #: Of ``items`` only: what is muted is not counted.
    counts: dict[str, int]
    #: Rules that could not be evaluated this time.
    rule_errors: list[str] = Field(default_factory=list)
    muted: list[MutedAttentionItemDTO] = Field(default_factory=list)


class AcknowledgeDTO(BaseModel):
    """Muting an item: the severity it had when the administrator saw it."""

    severity: str


class AcknowledgementDTO(BaseModel):
    """An item's mute after a change."""

    item_id: str
    muted: bool


class ProvidersSummaryDTO(BaseModel):
    """Configured LLM providers by residency class, and remote ones not answering."""

    total: int
    by_residency: dict[str, int]
    unreachable: int = 0


class McpSummaryDTO(BaseModel):
    """MCP servers: registered, enabled, active, and enabled ones with a problem."""

    total: int
    enabled: int
    healthy: int
    problems: int


class SkillsSummaryDTO(BaseModel):
    """Skills that are not archived, and those published and enabled."""

    total: int
    active: int


class IntegrationsDTO(BaseModel):
    """What the platform is connected to; a section is null when its module is off."""

    generated_at: datetime
    providers: ProvidersSummaryDTO
    mcp: McpSummaryDTO | None = None
    skills: SkillsSummaryDTO | None = None
    #: Data connectors arrive with the Knowledge widget.
    connectors: dict[str, int] | None = None
    #: Modules that are on but could not be read this time.
    unavailable: list[str] = Field(default_factory=list)
