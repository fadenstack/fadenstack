"""Ray environment management for Fadenstack nodes.

Layering (spec section 2):

* :mod:`.runtime` — bootstrap/process, the ONLY CLI layer.
* :mod:`.core` — live cluster access via the Ray Core Python APIs.
* :mod:`.serve` — Serve lifecycle/status via the ``ray.serve`` Python API.
* :mod:`.metrics` — Tier C Prometheus scrape-target discovery.
* :mod:`.state` — Tier B optional ``ray.util.state`` diagnostics.
* :class:`.manager.RayManager` — the facade the dispatcher talks to.
"""

from faden_agent.ray.core import RayCoreClient
from faden_agent.ray.manager import RayManager
from faden_agent.ray.metrics import RayMetricsDiscovery
from faden_agent.ray.models import (
    RayCapabilities,
    RayEnvironmentStatus,
    RayNodeStatus,
    RayServeStatusTier,
)
from faden_agent.ray.runtime import RayRuntime
from faden_agent.ray.serve import RayServeManager
from faden_agent.ray.state import RayStateDiagnostics

__all__ = [
    "RayCapabilities",
    "RayCoreClient",
    "RayEnvironmentStatus",
    "RayManager",
    "RayMetricsDiscovery",
    "RayNodeStatus",
    "RayRuntime",
    "RayServeManager",
    "RayServeStatusTier",
    "RayStateDiagnostics",
]

