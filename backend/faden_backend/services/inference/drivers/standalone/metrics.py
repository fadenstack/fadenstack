"""Engine metrics for a standalone (Mac) environment.

Linux gets its numbers by Prometheus scraping each node's plain ``/metrics``
(``targets.json``, job ``llm-runtimes``). A Mac does the same: its agent
exposes one metrics-only pass-through port (capability ``native_metrics.port``,
see CONTRACTS C4) that forwards ``GET /metrics`` to the running worker. Nothing
here parses or stores the series; it only names the target.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from faden_backend.services.inference.observability import (
    DeploymentMetrics,
    EnvironmentMetrics,
    MetricsPartial,
    ScrapeTarget,
)

log = logging.getLogger(__name__)

#: The label value every Mac target carries as ``job`` (Ray's is ``ray``).
NATIVE_JOB = "native"

_NOT_EXPORTED = (
    "This machine's agent does not export engine metrics yet; upgrade the agent on it."
)


def metrics_port(node: Any) -> int | None:
    """The Mac agent's metrics port from its capabilities, or ``None`` (an older agent)."""
    caps = getattr(node, "capabilities_json", None) or {}
    raw = (caps.get("native_metrics") or {}).get("port") if isinstance(caps.get("native_metrics"), dict) else None
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def scrape_target(node: Any) -> ScrapeTarget | None:
    port = metrics_port(node)
    host = str(getattr(node, "host", "") or "").strip()
    if port is None or not host:
        return None
    return ScrapeTarget(node_id=str(node.id), address=host, port=port, url=f"http://{host}:{port}/metrics")


async def _machines(session: Any, environment: Any) -> list[Any]:
    from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO  # noqa: PLC0415

    return await EnvironmentNodeDAO(session).machines(environment.id)


def _dashboard_url(environment: Any) -> str | None:
    try:
        from faden_backend.services.llm.monitoring import get_monitoring_provisioner  # noqa: PLC0415

        provisioner = get_monitoring_provisioner()
        return provisioner.dashboard_url(environment.id) if provisioner is not None else None
    except Exception:  # noqa: BLE001 - a missing link never fails metrics
        return None


async def deployment_metrics(session: Any, deployment: Any) -> DeploymentMetrics:
    from faden_backend.db.models.inference import DeploymentPhase  # noqa: PLC0415

    environment = await _environment(session, deployment)
    serving = str(getattr(deployment, "phase", "") or "") in (
        DeploymentPhase.RUNNING.value, DeploymentPhase.DEGRADED.value,
    )
    metrics = DeploymentMetrics(
        deployment_id=str(deployment.id),
        app_name=str(getattr(deployment, "name", "") or "") or None,
        replicas_ready=1 if str(getattr(deployment, "phase", "")) == DeploymentPhase.RUNNING.value else 0,
        replicas_total=1,
        app_status=str(getattr(deployment, "phase", "") or "") or None,
        observed_at=datetime.now(tz=UTC),
    )
    if environment is None:
        return metrics
    machines = await _machines(session, environment)
    target = scrape_target(machines[0]) if machines else None
    if target is None:
        if machines:
            metrics.partials.append(MetricsPartial(tier="node_metrics", reason=_NOT_EXPORTED))
        return metrics
    if serving:
        metrics.scrape_targets = [target]
    return metrics


async def environment_metrics(session: Any, environment: Any) -> EnvironmentMetrics:
    machines = await _machines(session, environment)
    metrics = EnvironmentMetrics(
        environment_id=str(environment.id),
        nodes_total=len(machines),
        nodes_alive=sum(1 for m in machines if str(getattr(m, "status", "")) in ("healthy", "degraded", "draining")),
        alive=any(str(getattr(m, "status", "")) in ("healthy", "degraded", "draining") for m in machines),
        dashboard_url=_dashboard_url(environment),
        observed_at=datetime.now(tz=UTC),
    )
    target = scrape_target(machines[0]) if machines else None
    if target is not None:
        metrics.scrape_targets = [target]
    elif machines:
        metrics.partials.append(MetricsPartial(tier="node_metrics", reason=_NOT_EXPORTED))
    return metrics


async def _environment(session: Any, deployment: Any) -> Any:
    from faden_backend.db.models.inference import InferenceEnvironment  # noqa: PLC0415

    return await session.get(InferenceEnvironment, deployment.environment_id)


async def sync_native_targets(session: Any, environment: Any) -> None:
    """Write (or drop) the environment's line in ``targets.json``; best effort, never raises.

    One Mac per environment and one model per Mac, so there is at most one
    target: the machine's metrics port, present while a deployment of the
    environment is wanted running and has a worker (phase running, degraded,
    applying or preparing). It goes on deploy, and comes off on stop or delete.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from faden_backend.db.models.inference import (  # noqa: PLC0415
            DeploymentDesiredState,
            DeploymentPhase,
            InferenceDeployment,
        )
        from faden_backend.services.llm.monitoring import get_monitoring_provisioner  # noqa: PLC0415

        provisioner = get_monitoring_provisioner()
        if provisioner is None:
            return
        rows = await session.execute(
            select(InferenceDeployment).where(InferenceDeployment.environment_id == environment.id)
        )
        live = [
            d for d in rows.scalars()
            if d.desired_state == DeploymentDesiredState.ACTIVE.value
            and str(d.phase) in (
                DeploymentPhase.APPLYING.value, DeploymentPhase.RUNNING.value, DeploymentPhase.DEGRADED.value,
            )
        ]
        machines = await _machines(session, environment)
        target = scrape_target(machines[0]) if machines else None
        if not live or target is None:
            await provisioner.remove_native_targets(environment.id)
            return
        await provisioner.sync_native_targets(
            environment_id=environment.id,
            environment_name=environment.name,
            targets=[{"address": target.address, "port": target.port}],
        )
        await provisioner.provision_environment(
            environment_id=environment.id, environment_name=environment.name,
        )
    except Exception:  # noqa: BLE001 - monitoring never fails a reconcile
        log.exception("Could not register Prometheus targets for environment %s", getattr(environment, "id", "?"))
