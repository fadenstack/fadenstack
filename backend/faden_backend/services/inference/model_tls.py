"""Moving a cluster's models behind its head's model-TLS proxy, and saying which are not yet.

A cluster started before model TLS has Serve listening on the network: Serve
binds once, so its models stay reachable over plain HTTP -- and are published
that way (``drivers/ray/deployment._publish_endpoint``) -- until Serve starts
again. ``move`` does that on an administrator's word: Serve is shut down on the
head (applications and proxy), every deployment of the cluster is marked to be
applied again, and the first one starts Serve on the loopback address behind
the proxy. The models are down while they load again, which is why this is a
button and not something the upgrade does on its own.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import (
    DeploymentDesiredState,
    InferenceDeployment,
    InferenceEndpoint,
    InferenceEnvironment,
)
from faden_backend.db.models.node_control import InfraNode
from faden_backend.services import machine_tls
from faden_backend.settings import settings

log = logging.getLogger(__name__)


class ModelTlsMoveError(Exception):
    """The cluster cannot be moved now; the message says why."""


async def environment_status(session: AsyncSession, environment_id: Any) -> dict[str, Any]:
    """Which of a cluster's models are reached over TLS, and whether the rest can be moved."""
    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise ModelTlsMoveError("There is no such cluster.")
    head = await session.get(InfraNode, env.head_node_id) if env.head_node_id else None
    deployments = (await session.execute(
        select(InferenceDeployment).where(InferenceDeployment.environment_id == env.id)
    )).scalars().all()
    ids = [d.id for d in deployments]
    endpoints = (await session.execute(
        select(InferenceEndpoint).where(InferenceEndpoint.deployment_id.in_(ids))
    )).scalars().all() if ids else []
    protected, plain = [], []
    for endpoint in endpoints:
        published = endpoint.published_json or {}
        if not published.get("base_url"):
            continue
        entry = {"deployment_id": str(endpoint.deployment_id), "base_url": published.get("base_url")}
        (protected if published.get("machine_tls") else plain).append(entry)
    head_supported = bool(head is not None and machine_tls.supported(head.capabilities_json))
    return {
        "enabled": settings.machine_tls_enabled,
        "head_supported": head_supported,
        "head_agent_version": getattr(head, "version", None),
        "protected": protected,
        "plain": plain,
        "can_move": bool(settings.machine_tls_enabled and head_supported and plain),
    }


async def move(session: AsyncSession, gateway: Any, environment_id: Any) -> dict[str, Any]:
    """Shut Serve down on the head and have every deployment applied again, behind the proxy."""
    from faden_backend.services.inference.drivers.ray.client import RayClusterClient  # noqa: PLC0415
    from faden_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    if not settings.machine_tls_enabled:
        raise ModelTlsMoveError("Model traffic TLS is switched off on this server.")
    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise ModelTlsMoveError("There is no such cluster.")
    if env.head_node_id is None:
        raise ModelTlsMoveError(f"{env.name} has no head machine.")
    head = await session.get(InfraNode, env.head_node_id)
    if head is None or not machine_tls.supported(head.capabilities_json):
        raise ModelTlsMoveError(
            f"The agent on {getattr(head, 'host', 'the head machine')} cannot run the model-TLS proxy yet: "
            "update it (re-run the install line on that machine), then try again."
        )
    result = await RayClusterClient(gateway).shutdown_serve(node_id=env.head_node_id)
    if not result or not result.get("shut_down"):
        raise ModelTlsMoveError(
            "The head machine did not confirm that Serve stopped; see the machine's command history."
        )
    deployments = (await session.execute(
        select(InferenceDeployment).where(
            InferenceDeployment.environment_id == env.id,
            InferenceDeployment.desired_state != DeploymentDesiredState.DELETED.value,
        )
    )).scalars().all()
    for deployment in deployments:
        observed = dict(deployment.observed_status_json or {})
        # Applied again even though nothing in it changed: Serve is gone.
        observed.pop("applied_config_hash", None)
        deployment.observed_status_json = observed
        deployment.generation += 1
    await session.flush()
    wake_reconciler_after_commit(session)
    log.info("Cluster %s: Serve shut down to move %d model(s) behind the model-TLS proxy", env.name, len(deployments))
    return {"moved": len(deployments), "environment_id": str(env.id)}
