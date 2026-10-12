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

from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO
from faden_backend.db.models.inference import InferenceEnvironment
from faden_backend.db.models.node_control import InfraNode
from faden_backend.services.inference.drivers.ray.schemas import RayEnvironmentConfig

log = logging.getLogger(__name__)

CAPABILITY = "ray_tls"
#: Runs Ray with TLS and says what its container runs (agent 0.1.18).
CAPABILITY_LEVEL = 2


class RayTlsError(Exception):
    """Ray TLS cannot be switched now; the message says why."""


def supported(capabilities: dict | None) -> bool:
    """Whether a machine's agent runs Ray with TLS and says so."""
    try:
        return int((capabilities or {}).get(CAPABILITY) or 0) >= CAPABILITY_LEVEL
    except (TypeError, ValueError):
        return False


async def _runs_on_host(session: AsyncSession, environment: Any, node: InfraNode) -> bool:
    """Whether no certified runtime covers the machine, so Ray would run on its host."""
    from faden_backend.services.inference.bundles import node_and_bundle_for  # noqa: PLC0415

    _, bundle = await node_and_bundle_for(session, node.id, driver="ray", environment=environment)
    return bundle is None


async def member_refusal(session: AsyncSession, environment: Any, node: InfraNode | None) -> str | None:
    """Why this machine cannot run Ray with TLS, or ``None`` when it can.

    Two things stop it. An agent older than 0.1.18 does not know how. And a
    machine with no certified runtime bundle runs Ray on the host instead of in
    the runtime container: only the container is started with the cluster's
    certificates, so the cluster would say TLS is on while that member runs
    without it, or not form at all.
    """
    if node is None:
        return None  # an unknown machine is refused elsewhere, for that
    name = str(node.host or node.id)
    if not supported(node.capabilities_json):
        return (
            f"Ray TLS needs agent 0.1.18 or later on every machine of the cluster; update {name} "
            "(re-run the install line), or switch Ray TLS off for this cluster."
        )
    if await _runs_on_host(session, environment, node):
        return (
            f"{name} would run Ray on the host, outside the agent's runtime container, and cannot apply "
            "Ray TLS there. Run the agent's runtime container on it, or switch Ray TLS off for this "
            "cluster on purpose."
        )
    return None


def _old_agents(nodes: list[InfraNode]) -> list[str]:
    return sorted(str(node.host or node.id) for node in nodes if not supported(node.capabilities_json))


async def environment_status(session: AsyncSession, environment_id: Any) -> dict[str, Any]:
    """Whether the cluster runs Ray with TLS, whether that is in place yet, and who cannot."""
    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise RayTlsError("There is no such cluster.")
    nodes = await EnvironmentNodeDAO(session).machines(env.id)
    old = _old_agents(nodes)
    host_mode = [str(node.host or node.id) for node in nodes if await _runs_on_host(session, env, node)]
    on =RayEnvironmentConfig.model_validate(env.config_json or {}).ray_tls
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
        # Not yet, and the cluster is to run: it is restarting. A stopped
        # cluster takes the switch when it starts.
        "restarting": env.restarting(applied=applied),
        "running": running,
        "machines": len(nodes),
        "old_agents": old,
        "can_switch_on": bool(nodes) and not old and not host_mode,
        # Machines that run Ray on the host, outside the runtime container.
        "host_mode": host_mode,
    }


async def switch(session: AsyncSession, environment_id: Any, *, on: bool) -> dict[str, Any]:
    """Have the cluster run Ray with TLS, or without; the next pass restarts it that way."""
    from faden_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise RayTlsError("There is no such cluster.")
    if on:
        machines = await EnvironmentNodeDAO(session).machines(env.id)
        old = _old_agents(machines)
        if old:
            raise RayTlsError(
                "Ray TLS needs agent 0.1.18 or later on every machine of the cluster; update "
                + ", ".join(old) + " (re-run the install line on it), then switch it on."
            )
        host_mode = [p for p in [await member_refusal(session, env, m) for m in machines] if p]
        if host_mode:
            raise RayTlsError(" ".join(host_mode))
    config = dict(env.config_json or {})
    if bool(config.get("ray_tls")) != on:
        config["ray_tls"] = on
        env.config_json = config
        env.generation += 1
        await session.flush()
        wake_reconciler_after_commit(session)
        log.info("Cluster %s: Ray TLS switched %s; the cluster restarts", env.name, "on" if on else "off")
    return await environment_status(session, env.id)
