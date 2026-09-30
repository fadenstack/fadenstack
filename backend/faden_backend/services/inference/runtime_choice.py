"""Which runtime a cluster runs, and switching it to another.

This server ships up to three runtimes per flavor of machine
(``runtime-image/README.md``): the stable one clusters get, a newer one as a
preview, an older one to go back to. A cluster's machine runs the runtime the
cluster was switched to, else the one it was started with, else its flavor's
default (``RuntimeBundleRegistry.resolve_for_node``). So nothing moves a
cluster to another runtime by itself -- not a restart, not a recovery, not an
upgrade of this server that ships a newer one.

Switching restarts the cluster on the new runtime: each machine's container is
made again from the new image and the deployments load again. That is why it
is the operator's decision, taken here and nowhere else. Every agent can
follow it: an agent replaces a container that runs another image than the one
it is told to run.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO
from faden_backend.db.models.inference import EnvironmentDesiredState, InferenceEnvironment
from faden_backend.services.inference.bundles import (
    RUNTIME_KEY,
    RuntimeBundleManifest,
    default_bundle_registry,
    started_runtimes,
)

log = logging.getLogger(__name__)


class RuntimeChoiceError(Exception):
    """The cluster cannot be switched to that runtime; the message says why."""


async def _environment(session: AsyncSession, environment_id: Any) -> InferenceEnvironment:
    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise RuntimeChoiceError("There is no such cluster.")
    return env


def _offered(bundle: RuntimeBundleManifest, *, default: bool) -> dict[str, Any]:
    """A runtime, in the runtime list's words (``RuntimeBundleDTO``)."""
    return {
        "bundle_id": bundle.bundle_id,
        "image": bundle.container.image,
        "runtime_version": bundle.compatibility_matrix.ray_version,
        "vllm_version": bundle.compatibility_matrix.vllm_version,
        "certification_status": bundle.certification.status,
        "channel": bundle.certification.channel,
        # What a machine of its flavor gets when its cluster chose none.
        "default": default,
    }


async def environment_status(session: AsyncSession, environment_id: Any) -> dict[str, Any]:
    """What the machines run, whether they are there yet, and whether a newer stable runtime is out."""
    env = await _environment(session, environment_id)
    registry = default_bundle_registry
    started = started_runtimes(env)
    offered: dict[str, RuntimeBundleManifest] = {}
    defaults: set[str] = set()
    runs: set[str] = set()
    applied = True
    update: str | None = None
    for node in await EnvironmentNodeDAO(session).machines(env.id):
        fits = registry.runtimes_for(node)
        offered.update((b.bundle_id, b) for b in fits)
        default = registry.default_among(fits)
        target = registry.resolve_for_node(node, environment=env)
        if target is None:
            continue
        runs.add(target.bundle_id)
        # A machine with no record runs its default: a cluster formed before
        # the record was kept, or one that never started.
        applied &= target.bundle_id == started.get(str(node.id), getattr(default, "bundle_id", None))
        if default is not None:
            defaults.add(default.bundle_id)
            # On an older stable runtime than its default. A preview was
            # chosen on purpose, and is left to its operator.
            if target is not default and target.certification.channel != "preview":
                update = default.bundle_id
    return {
        "runs": sorted(runs),
        "applied": applied,
        # Not there yet, and the cluster is to run: it is restarting.
        "restarting": not applied and env.desired_state == EnvironmentDesiredState.RUNNING.value,
        "update": update,
        "runtimes": [
            _offered(bundle, default=bundle.bundle_id in defaults)
            for bundle in sorted(offered.values(), key=lambda b: b.bundle_id, reverse=True)
        ],
    }


async def switch(session: AsyncSession, environment_id: Any, runtime_id: str) -> dict[str, Any]:
    """Have the cluster run *runtime_id*; the next pass restarts it on that runtime."""
    from faden_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    env = await _environment(session, environment_id)
    registry = default_bundle_registry
    runtime = registry.get_bundle(runtime_id)
    if runtime is None:
        raise RuntimeChoiceError(f"This server ships no runtime {runtime_id}.")
    if runtime.certification.status == "failed":
        raise RuntimeChoiceError(f"{runtime_id} failed its certification; it cannot be chosen.")
    nodes = await EnvironmentNodeDAO(session).machines(env.id)
    if not any(runtime in registry.runtimes_for(node) for node in nodes):
        raise RuntimeChoiceError(f"No machine of this cluster can run {runtime_id}: it is built for other hardware.")

    if (env.config_json or {}).get(RUNTIME_KEY) == runtime_id:
        return await environment_status(session, env.id)
    env.config_json = {**(env.config_json or {}), RUNTIME_KEY: runtime_id}
    status = await environment_status(session, env.id)
    if not status["applied"]:
        env.generation += 1
        wake_reconciler_after_commit(session)
    await session.flush()
    log.info(
        "Cluster %s: runtime %s chosen%s", env.name, runtime_id,
        "; the cluster restarts on it" if not status["applied"] else "; its machines already run it",
    )
    return status
