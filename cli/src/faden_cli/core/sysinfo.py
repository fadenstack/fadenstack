"""System resource detection and scalability tuning calculator.

Uses ``psutil`` to detect host hardware (CPU cores, RAM) and derives
optimal worker counts, DB pool sizes, and message-queue pool sizes
for all fadenstack services.

The control plane is sized to use only a fraction of the host
(``DEFAULT_RESOURCE_PCT``, default 25 %) so that the majority of
CPU and RAM remain available for inference engines (vLLM, etc.)
and other workloads.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import psutil

# ── Resource budget ───────────────────────────────────────────────
# Percentage of total host resources reserved for the fadenstack
# control plane.  The remainder is left for inference / ML workloads.
DEFAULT_RESOURCE_PCT: float = 0.25

# ── Service classification ────────────────────────────────────────
# Heavy services handle high-volume request traffic or CPU-bound work.
# Light services are low-traffic or background-only.

HEAVY_SERVICES = ("backend", "gateway", "pii")
LIGHT_SERVICES: tuple[str, ...] = ()
ALL_SERVICES = HEAVY_SERVICES + LIGHT_SERVICES

# Services whose PostgreSQL pool the CLI sizes. PII has pool settings but
# opens no connection: its database layer is unused.
DB_SERVICES = ("backend", "gateway")

# Services that use a RabbitMQ connection pool.
RABBIT_SERVICES = ("backend", "gateway")

# ── Pools: per worker process, whatever the number of workers ─────
# Each worker process keeps its own pools. They were sized by the worker
# count too -- 3 connections per worker, as many again on demand -- so what a
# service could open grew with the square of its workers: 24 at 8 cores, 96 at
# 16, 384 at 32, per service, past any Postgres.
DB_POOL_PER_WORKER = 5
DB_OVERFLOW_PER_WORKER = {"dev": 10, "prod": 5}
RABBIT_POOL_PER_WORKER = 2
RABBIT_CHANNELS_PER_WORKER = 10

# ── Postgres connections ──────────────────────────────────────────
#: Processes of the backend worker container (taskiq's default), each with
#: the backend's pool.
BACKEND_TASK_WORKERS = 2
#: Services with pools the CLI does not size: 1 worker x (5 + 10).
UNTUNED_CONNECTIONS = {"mcp": 15, "skills": 15}
#: Langfuse's web and worker, migrations, backups, psql.
CONNECTION_HEADROOM = 100
#: The shared compose file's default; tuning never goes below it.
MIN_MAX_CONNECTIONS = 250


# ── System snapshot ───────────────────────────────────────────────


@dataclass(frozen=True)
class SystemInfo:
    """Point-in-time snapshot of host hardware resources."""

    physical_cores: int
    logical_cores: int
    total_ram_bytes: int

    @property
    def total_ram_gb(self) -> float:
        return round(self.total_ram_bytes / (1024**3), 1)


def detect_system() -> SystemInfo:
    """Return a :class:`SystemInfo` snapshot of the current host."""
    return SystemInfo(
        physical_cores=psutil.cpu_count(logical=False) or os.cpu_count() or 1,
        logical_cores=psutil.cpu_count(logical=True) or os.cpu_count() or 1,
        total_ram_bytes=psutil.virtual_memory().total,
    )


# ── Tune profile ─────────────────────────────────────────────────


@dataclass
class TuneProfile:
    """Computed scalability parameters for all services."""

    profile: str  # "dev" or "prod"
    system: SystemInfo
    resource_pct: float = DEFAULT_RESOURCE_PCT

    # Per-service worker counts (service name → count).
    workers: dict[str, int] = field(default_factory=dict)

    # DB pool sizes (service name → pool_size).
    db_pool_size: dict[str, int] = field(default_factory=dict)
    db_max_overflow: dict[str, int] = field(default_factory=dict)

    # RabbitMQ pool sizes (service name → pool_size).
    rabbit_pool_size: dict[str, int] = field(default_factory=dict)
    rabbit_channel_pool_size: dict[str, int] = field(default_factory=dict)

    # ── Prefix map ────────────────────────────────────────────
    _ENV_PREFIX: dict[str, str] = field(
        default_factory=lambda: {
            "backend": "FADEN_BACKEND_",
            "gateway": "FADEN_GATEWAY_",
            "pii": "FADEN_PII_",
        },
        repr=False,
    )

    @property
    def budget_cores(self) -> int:
        """Number of CPU cores allocated to the control plane."""
        return max(1, int(self.system.physical_cores * self.resource_pct))

    def db_connections(self) -> dict[str, int]:
        """What each database client can open at its peak: workers x (kept + on demand)."""
        per_worker = {svc: self.db_pool_size[svc] + self.db_max_overflow[svc] for svc in DB_SERVICES}
        peaks = {svc: self.workers[svc] * per_worker[svc] for svc in DB_SERVICES}
        peaks["backend-worker"] = BACKEND_TASK_WORKERS * per_worker["backend"]
        peaks.update(UNTUNED_CONNECTIONS)
        return peaks

    @property
    def max_connections(self) -> int:
        """Postgres ``max_connections`` for them all, with headroom, in steps of 50."""
        needed = sum(self.db_connections().values()) + CONNECTION_HEADROOM
        return max(MIN_MAX_CONNECTIONS, math.ceil(needed / 50) * 50)

    def to_env_dict(self) -> dict[str, str]:
        """Flatten into a dict of ``FADEN_*`` env var → value."""
        env: dict[str, str] = {
            "POSTGRES_MAX_CONNECTIONS": str(self.max_connections),
            # What deploy and upgrade tune with again.
            "FADEN_TUNE_PROFILE": self.profile,
            "FADEN_TUNE_RESOURCE_PCT": str(self.resource_pct),
        }

        for svc in ALL_SERVICES:
            pfx = self._ENV_PREFIX[svc]
            env[f"{pfx}WORKERS_COUNT"] = str(self.workers[svc])

        for svc in DB_SERVICES:
            pfx = self._ENV_PREFIX[svc]
            env[f"{pfx}DB_POOL_SIZE"] = str(self.db_pool_size[svc])
            env[f"{pfx}DB_MAX_OVERFLOW"] = str(self.db_max_overflow[svc])

        for svc in RABBIT_SERVICES:
            pfx = self._ENV_PREFIX[svc]
            env[f"{pfx}RABBIT_POOL_SIZE"] = str(self.rabbit_pool_size[svc])
            env[f"{pfx}RABBIT_CHANNEL_POOL_SIZE"] = str(
                self.rabbit_channel_pool_size[svc],
            )

        return env


# ── Calculator ────────────────────────────────────────────────────


def calculate_tune_profile(
    profile: str = "dev",
    system: SystemInfo | None = None,
    *,
    resource_pct: float = DEFAULT_RESOURCE_PCT,
) -> TuneProfile:
    """Calculate optimal scalability parameters for *profile*.

    Parameters
    ----------
    profile
        ``"dev"`` for conservative local defaults, ``"prod"`` for
        production-grade sizing.
    system
        Pre-detected system info.  If ``None``, :func:`detect_system`
        is called automatically.
    resource_pct
        Fraction (0.0–1.0) of host CPU to allocate to the control
        plane.  Only affects the ``"prod"`` profile.  Defaults to
        :data:`DEFAULT_RESOURCE_PCT` (25 %).
    """
    sys = system or detect_system()
    tp = TuneProfile(profile=profile, system=sys, resource_pct=resource_pct)

    # Workers: dev keeps things light (1-2); prod scales within the CPU
    # budget (resource_pct of the physical cores, e.g. 2 of 8 at 25 %).
    workers = min(sys.physical_cores, 2) if profile == "dev" else tp.budget_cores
    for svc in HEAVY_SERVICES:
        tp.workers[svc] = max(1, workers)
    for svc in LIGHT_SERVICES:
        tp.workers[svc] = 1

    # Pools: the same per worker process, whatever the number of workers.
    for svc in DB_SERVICES:
        tp.db_pool_size[svc] = DB_POOL_PER_WORKER
        tp.db_max_overflow[svc] = DB_OVERFLOW_PER_WORKER.get(profile, DB_OVERFLOW_PER_WORKER["prod"])
    for svc in RABBIT_SERVICES:
        tp.rabbit_pool_size[svc] = RABBIT_POOL_PER_WORKER
        tp.rabbit_channel_pool_size[svc] = RABBIT_CHANNELS_PER_WORKER

    return tp
