"""System resource detection and scalability tuning calculator.

Uses ``psutil`` to detect host hardware (CPU cores, RAM) and derives
optimal worker counts, DB pool sizes, and message-queue pool sizes
for all Fadenstack services.

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
# Percentage of total host resources reserved for the Fadenstack
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

# ── Memory: what each part takes (docker stats, release VM, 2026-09-28) ──
# Sized by CPU alone, a 7 GB server got two of everything: at rest it used
# 4.6 GB, a model download took the task worker to 2.3 GB, swap filled, the
# kernel killed the worker and the web backend's supervisor replaced a child
# that had stopped answering. So the workers are cut to what the memory holds.
#: The operating system and Docker, and some page cache to breathe: 1 GB plus 5 %.
OS_RESERVE_MB = 1024
OS_RESERVE_PCT = 0.05
#: The services that run once whatever the sizing: Postgres ~290, the gateway's
#: task worker ~360, the web app, Loki, Alloy, MinIO, Grafana, the console,
#: ClickHouse, RabbitMQ (capped at 512), nginx, Prometheus, Redis.
FIXED_MB = 1500
#: One worker process of each service ``faden tune`` sizes.
PER_WORKER_MB = {"backend": 160, "gateway": 200, "pii": 320}
#: The optional modules' own processes (PII's includes its task worker).
MODULE_MB = {"mcp": 80, "skills": 60, "pii": 80}
#: One process of the backend's task worker (taskiq), before any download.
TASK_WORKER_MB = 250
#: A model download holds up to one xet range (64 MB) per range it fetches at once.
XET_RANGE_MB = 64
#: Ranges fetched at once: fewer on a small server, never so few a download crawls.
MIN_DOWNLOAD_RANGES, MAX_DOWNLOAD_RANGES = 4, 32


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

    #: Optional modules switched on ("pii", "mcp", "skills"): they take memory.
    modules: frozenset[str] = frozenset()
    #: Processes of the backend's task worker (taskiq ``--workers``).
    task_workers: int = BACKEND_TASK_WORKERS
    #: Ranges a model download fetches at once (``HF_XET_NUM_CONCURRENT_RANGE_GETS``).
    download_ranges: int = MAX_DOWNLOAD_RANGES
    #: Workers the CPU budget allowed, before the memory had its say.
    cpu_workers: int = 0

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
        peaks["backend-worker"] = self.task_workers * per_worker["backend"]
        peaks.update(UNTUNED_CONNECTIONS)
        return peaks

    @property
    def memory_budget_mb(self) -> int:
        """The memory Fadenstack may take on this host: all of it but the OS reserve."""
        total_mb = self.system.total_ram_bytes // (1024**2)
        return max(0, int(total_mb - OS_RESERVE_MB - total_mb * OS_RESERVE_PCT))

    def memory_mb(self) -> dict[str, int]:
        """What each part takes at its sizing, in MB: at rest, and a download under way."""
        parts = {"services that run once": FIXED_MB}
        for module in sorted(self.modules):
            parts[f"{module} module"] = MODULE_MB.get(module, 0)
        for svc in ALL_SERVICES:
            if svc == "pii" and "pii" not in self.modules:
                continue
            parts[f"{svc} x{self.workers[svc]}"] = self.workers[svc] * PER_WORKER_MB[svc]
        parts[f"task worker x{self.task_workers}"] = self.task_workers * TASK_WORKER_MB
        parts["a model download"] = self.download_ranges * XET_RANGE_MB
        return parts

    @property
    def memory_short_mb(self) -> int:
        """How much more memory the smallest sizing needs than this host has; 0 when it fits."""
        return max(0, sum(self.memory_mb().values()) - self.memory_budget_mb)

    def _fit_memory(self) -> None:
        """Cut the workers, then the task worker, then the download's ranges, to what the memory holds."""
        def rest(workers: int, task_workers: int) -> int:
            per_set = sum(
                PER_WORKER_MB[svc] for svc in ALL_SERVICES if svc != "pii" or "pii" in self.modules
            )
            modules = sum(MODULE_MB.get(m, 0) for m in self.modules)
            return FIXED_MB + modules + workers * per_set + task_workers * TASK_WORKER_MB

        budget = self.memory_budget_mb
        smallest_download = MIN_DOWNLOAD_RANGES * XET_RANGE_MB
        workers = max(self.workers.values()) if self.workers else 1
        while workers > 1 and rest(workers, self.task_workers) + smallest_download > budget:
            workers -= 1
        if rest(workers, self.task_workers) + smallest_download > budget:
            self.task_workers = 1
        for svc in HEAVY_SERVICES:
            self.workers[svc] = min(self.workers[svc], workers)
        # A quarter of what is left may go to a download, in whole ranges.
        left = budget - rest(workers, self.task_workers)
        self.download_ranges = max(MIN_DOWNLOAD_RANGES, min(MAX_DOWNLOAD_RANGES, left // 4 // XET_RANGE_MB))

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
            # Sized by the memory (see ``_fit_memory``).
            "FADEN_BACKEND_TASK_WORKERS": str(self.task_workers),
            "FADEN_BACKEND_DOWNLOAD_RANGES": str(self.download_ranges),
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
    modules: frozenset[str] | set[str] = frozenset(),
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
    modules
        The optional modules switched on (``pii``, ``mcp``, ``skills``):
        they take memory, and PII's workers are counted only with it on.

    The CPU sets the number of workers, and the memory then cuts it to what
    the host holds -- see ``TuneProfile._fit_memory``.
    """
    sys = system or detect_system()
    tp = TuneProfile(profile=profile, system=sys, resource_pct=resource_pct, modules=frozenset(modules))

    # Workers: dev keeps things light (1-2); prod scales within the CPU
    # budget (resource_pct of the physical cores, e.g. 2 of 8 at 25 %).
    workers = min(sys.physical_cores, 2) if profile == "dev" else tp.budget_cores
    for svc in HEAVY_SERVICES:
        tp.workers[svc] = max(1, workers)
    for svc in LIGHT_SERVICES:
        tp.workers[svc] = 1
    tp.cpu_workers = max(1, workers)
    tp._fit_memory()

    # Pools: the same per worker process, whatever the number of workers.
    for svc in DB_SERVICES:
        tp.db_pool_size[svc] = DB_POOL_PER_WORKER
        tp.db_max_overflow[svc] = DB_OVERFLOW_PER_WORKER.get(profile, DB_OVERFLOW_PER_WORKER["prod"])
    for svc in RABBIT_SERVICES:
        tp.rabbit_pool_size[svc] = RABBIT_POOL_PER_WORKER
        tp.rabbit_channel_pool_size[svc] = RABBIT_CHANNELS_PER_WORKER

    return tp
