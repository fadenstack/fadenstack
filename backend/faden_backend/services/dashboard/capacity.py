"""Local AI capacity: machines and clusters, their health, their load, and free GPUs.

Everything is read from what background loops stored; nothing is probed:

- a machine is its node row (reported status, ``last_seen``, maintenance,
  draining) and its newest inventory snapshot, whose utilization -- per-GPU
  devices included -- the agent sends with every heartbeat (~15 s);
- a cluster is its environment row and ``observed_status_json["cluster"]``,
  which the reconciler writes about every 30 s. A live Ray status call takes
  10-25 s on this hardware; the dashboard never makes one. A cluster never
  observed reports its free GPUs as unknown, never as 0.

Health and load are separate questions. Health is what the machine reports,
or "stale" when it has stopped reporting; it is never computed from load. A GPU
at 100 % is doing its job, and vLLM allocates ~90 % of VRAM by design -- so
load is reported as numbers and histograms, and never coloured as a fault.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.node_control_dao import NodeControlDAO
from faden_backend.db.models.inference import (
    EnvironmentDesiredState,
    EnvironmentStatus,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.node_control import InfraNode, NodeHealthStatus

#: A machine that reported healthy but has been silent this long is "stale":
#: three missed heartbeats at the agent's default 15 s.
STALE_AFTER = timedelta(seconds=45)
#: A cluster snapshot this old is marked stale: three reconcile passes (30 s).
CLUSTER_STALE_AFTER = timedelta(seconds=90)
#: A GPU at or above this utilization counts as in use (the gpus_busy KPI).
BUSY_UTIL_PCT = 10.0
#: Up to this many machines, every group's machine list is sent; beyond it,
#: aggregates only and a group's machines on request (paged).
MACHINE_LIST_LIMIT = 250
PAGE_SIZE = 250
EXCEPTIONS_PER_GROUP = 20

HEALTH_STATES = ("ok", "degraded", "error", "maintenance", "offline", "stale")
#: Worst first, for exception lists.
_SEVERITY = {"error": 0, "offline": 1, "degraded": 2, "stale": 3, "maintenance": 4, "ok": 5}
_HEALTHY_CLUSTER = {EnvironmentStatus.READY.value, EnvironmentStatus.RUNNING.value}


@dataclass
class Gpu:
    """One accelerator's load and temperature, as the agent reported them."""

    index: int
    util_pct: float | None
    vram_allocated_pct: float | None
    temp_c: float | None


@dataclass
class Machine:
    """A machine as the capacity widget needs it."""

    id: str
    name: str
    health: str
    last_seen: datetime | None
    cpu_pct: float | None
    gpus: list[Gpu] = field(default_factory=list)
    #: VRAM allocated when the agent reports totals but no devices.
    vram_allocated_pct: float | None = None

    @property
    def gpu_util_pct(self) -> float | None:
        """The busiest GPU's utilization."""
        values = [g.util_pct for g in self.gpus if g.util_pct is not None]
        return max(values) if values else None

    @property
    def vram_pct(self) -> float | None:
        """The fullest GPU's allocated VRAM."""
        values = [g.vram_allocated_pct for g in self.gpus if g.vram_allocated_pct is not None]
        return max(values) if values else self.vram_allocated_pct


@dataclass
class Group:
    """A cluster, or the machines in no cluster."""

    kind: str  # cluster | unassigned
    id: str | None
    name: str | None
    status: str | None
    machines: list[Machine]
    observed_at: datetime | None = None
    stale: bool = False
    gpus_total: int | None = None
    gpus_free: float | None = None


@dataclass
class Capacity:
    """The fleet: groups of machines, plus what is counted across them."""

    groups: list[Group]
    machines: list[Machine]
    clusters: int
    clusters_healthy: int
    #: Clusters whose free GPUs are not known (never observed).
    gpus_free_unknown_clusters: int


def machine_health(node: Any, now: datetime) -> str:
    """What the machine reports, or "stale" when it has gone quiet. Never from load."""
    status = str(node.status or "").lower()
    if status == NodeHealthStatus.OFFLINE.value:
        return "offline"
    if (
        node.maintenance_mode
        or node.draining
        or status
        in (
            NodeHealthStatus.MAINTENANCE.value,
            NodeHealthStatus.DRAINING.value,
        )
    ):
        return "maintenance"
    if node.last_seen is None or now - node.last_seen > STALE_AFTER:
        return "stale"
    if status == NodeHealthStatus.ERROR.value:
        return "error"
    if status == NodeHealthStatus.DEGRADED.value:
        return "degraded"
    return "ok"


def gpus_from(utilization: dict[str, Any] | None) -> tuple[list[Gpu], float | None]:
    """Per-GPU load from an agent's utilization report, and a machine-level VRAM figure."""
    gpu = (utilization or {}).get("gpu") or {}
    out: list[Gpu] = []
    for i, device in enumerate(gpu.get("devices") or []):
        if not isinstance(device, dict):
            continue
        used, total = device.get("memory_used_mib"), device.get("memory_total_mib")
        out.append(
            Gpu(
                index=int(device.get("index", i)),
                util_pct=_num(device.get("utilization_pct")),
                # Unified-memory GPUs (GB10) report no total of their own.
                vram_allocated_pct=(float(used) / float(total) * 100.0)
                if used is not None and total
                else None,
                temp_c=_num(device.get("temperature_c")),
            )
        )
    total_b, used_b = gpu.get("total_vram_bytes"), gpu.get("used_vram_bytes")
    machine_vram = (float(used_b) / float(total_b) * 100.0) if used_b is not None and total_b else None
    return out, machine_vram


async def load(session: AsyncSession, now: datetime) -> Capacity:
    """Read the fleet from the database (no probes)."""
    nodes = list((await session.execute(select(InfraNode).order_by(InfraNode.host))).scalars().all())
    snapshots = await NodeControlDAO(session).latest_inventory_snapshots(node_ids=[n.id for n in nodes])
    environments = list(
        (
            await session.execute(
                select(InferenceEnvironment)
                .where(InferenceEnvironment.desired_state != EnvironmentDesiredState.DELETED.value)
                .order_by(InferenceEnvironment.name)
            )
        )
        .scalars()
        .all()
    )
    members = (
        await session.execute(
            select(InferenceEnvironmentNode.environment_id, InferenceEnvironmentNode.node_id)
        )
    ).all()

    machines: dict[str, Machine] = {}
    for node in nodes:
        snap = snapshots.get(node.id)
        utilization = snap.utilization_json if snap else None
        gpus, machine_vram = gpus_from(utilization)
        machines[str(node.id)] = Machine(
            id=str(node.id),
            name=node.host or node.agent_id,
            health=machine_health(node, now),
            last_seen=node.last_seen,
            cpu_pct=_num((utilization or {}).get("cpu_percent")),
            gpus=gpus,
            vram_allocated_pct=machine_vram,
        )

    by_env: dict[str, list[str]] = {}
    for env_id, node_id in members:
        by_env.setdefault(str(env_id), []).append(str(node_id))

    groups: list[Group] = []
    placed: set[str] = set()
    unknown_free = 0
    healthy = 0
    for env in environments:
        member_ids = [m for m in by_env.get(str(env.id), []) if m in machines]
        placed.update(member_ids)
        group_machines = sorted((machines[m] for m in member_ids), key=lambda m: m.name)
        observed = (env.observed_status_json or {}).get("cluster")
        observed_at = _parse_time((env.observed_status_json or {}).get("cluster_observed_at"))
        gpus_total: int | None
        gpus_free: float | None
        if isinstance(observed, dict) and observed:
            gpus_total = int(observed.get("total_gpus") or 0)
            gpus_free = float(observed.get("available_gpus") or 0)
        else:
            gpus_total = sum(len(m.gpus) for m in group_machines) or None
            gpus_free = None
            unknown_free += 1
        if str(env.status) in _HEALTHY_CLUSTER:
            healthy += 1
        groups.append(
            Group(
                kind="cluster",
                id=str(env.id),
                name=env.name,
                status=str(env.status),
                machines=group_machines,
                observed_at=observed_at,
                stale=observed_at is not None and now - observed_at > CLUSTER_STALE_AFTER,
                gpus_total=gpus_total,
                gpus_free=gpus_free,
            )
        )

    unassigned = [m for mid, m in machines.items() if mid not in placed]
    if unassigned:
        groups.append(
            Group(
                kind="unassigned",
                id=None,
                name=None,
                status=None,
                machines=sorted(unassigned, key=lambda m: m.name),
                gpus_total=sum(len(m.gpus) for m in unassigned),
            )
        )

    return Capacity(
        groups=groups,
        machines=list(machines.values()),
        clusters=len(environments),
        clusters_healthy=healthy,
        gpus_free_unknown_clusters=unknown_free,
    )


def health_counts(machines: list[Machine]) -> dict[str, int]:
    """Machines per health state (every state present)."""
    counts = Counter(m.health for m in machines)
    return {state: counts.get(state, 0) for state in HEALTH_STATES}


def load_histogram(values: list[float | None]) -> list[int]:
    """Machines per 20 % band of load: [0-20), [20-40), [40-60), [60-80), [80-100]."""
    bands = [0] * 5
    for v in values:
        if v is not None:
            bands[min(int(v // 20), 4) if v >= 0 else 0] += 1
    return bands


def exceptions(machines: list[Machine], limit: int = EXCEPTIONS_PER_GROUP) -> list[Machine]:
    """Machines that are not ok, worst first."""
    bad = [m for m in machines if m.health != "ok"]
    return sorted(bad, key=lambda m: (_SEVERITY.get(m.health, 9), m.name))[:limit]


def gpus_busy(machines: list[Machine]) -> tuple[int, int]:
    """(GPUs at or above BUSY_UTIL_PCT, GPUs reporting utilization)."""
    reporting = [g for m in machines for g in m.gpus if g.util_pct is not None]
    return sum(1 for g in reporting if (g.util_pct or 0) >= BUSY_UTIL_PCT), len(reporting)


def _num(value: object) -> float | None:
    try:
        return float(value) if value is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
