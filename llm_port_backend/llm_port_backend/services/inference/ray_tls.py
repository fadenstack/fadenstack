"""Switching Ray's mutual TLS between a cluster's machines on or off, and saying where it stands.

With it on, every Ray process of the cluster presents a certificate from the
cluster's own CA (``services/machine_tls/ray_ca.py``) and accepts only peers
that do: the object transfers, the GCS and the raylets' gRPC between the
machines are encrypted. Ray reads its certificates when a process starts and
never again, so switching restarts the cluster: every member's runtime
container is made again, its Serve applications with it, and the deployments
load again once the cluster is back.

Every member's agent must know how (``ray_tls`` capability 2, agent 0.1.18):
a cluster half on TLS does not form. Each agent also says what its container
runs, which is what "applied" shows.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import InferenceEnvironment, InferenceEnvironmentNode
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.drivers.ray.schemas import RayEnvironmentConfig

log = logging.getLogger(__name__)

CAPABILITY = "ray_tls"
#: Runs Ray with TLS and says what its container runs (agent 0.1.18).
CAPABILITY_LEVEL = 2


class RayTlsError(Exception):
    """Ray TLS cannot be switched now; the message says why."""


async def _members(session: AsyncSession, environment_id: Any) -> list[InfraNode]:
    return list((await session.execute(
        select(InfraNode)
        .join(InferenceEnvironmentNode, InferenceEnvironmentNode.node_id == InfraNode.id)
        .where(InferenceEnvironmentNode.environment_id == environment_id)
    )).scalars().all())


def supported(capabilities: dict | None) -> bool:
    """Whether a machine's agent runs Ray with TLS and says so."""
    try:
        return int((capabilities or {}).get(CAPABILITY) or 0) >= CAPABILITY_LEVEL
    except (TypeError, ValueError):
        return False


def _old_agents(nodes: list[InfraNode]) -> list[str]:
    return sorted(str(node.host or node.id) for node in nodes if not supported(node.capabilities_json))


async def environment_status(session: AsyncSession, environment_id: Any) -> dict[str, Any]:
    """Whether the cluster runs Ray with TLS, whether that is in place yet, and who cannot."""
    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise RayTlsError("There is no such cluster.")
    nodes = await _members(session, env.id)
    old = _old_agents(nodes)
    on = RayEnvironmentConfig.model_validate(env.config_json or {}).ray_tls
    # What each machine's agent said its runtime container runs.
    reported = (env.observed_status_json or {}).get("ray_tls") or {}
    running = {str(node.host or node.id): reported.get(str(node.id)) for node in nodes}
    if on:
        applied = bool(nodes) and all(value is True for value in running.values())
    else:  # off is the default: only a machine still reporting TLS is behind
        applied = not any(value is True for value in running.values())
    return {
        "on": on,
        # Every machine runs Ray as the switch says.
        "applied": applied,
        "running": running,
        "machines": len(nodes),
        "old_agents": old,
        "can_switch_on": bool(nodes) and not old,
    }


async def switch(session: AsyncSession, environment_id: Any, *, on: bool) -> dict[str, Any]:
    """Have the cluster run Ray with TLS, or without; the next pass restarts it that way."""
    from llm_port_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise RayTlsError("There is no such cluster.")
    if on:
        old = _old_agents(await _members(session, env.id))
        if old:
            raise RayTlsError(
                "Ray TLS needs agent 0.1.18 or later on every machine of the cluster; update "
                + ", ".join(old) + " (re-run the install line on it), then switch it on."
            )
    config = dict(env.config_json or {})
    if bool(config.get("ray_tls")) != on:
        config["ray_tls"] = on
        env.config_json = config
        env.generation += 1
        await session.flush()
        wake_reconciler_after_commit(session)
        log.info("Cluster %s: Ray TLS switched %s; the cluster restarts", env.name, "on" if on else "off")
    return await environment_status(session, env.id)
