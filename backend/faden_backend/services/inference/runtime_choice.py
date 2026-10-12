"""Which runtime a cluster runs, and switching it to another.

This server ships up to three runtimes per flavor of machine
(``runtime-image/README.md``): the stable one clusters get, a newer one as a
preview, an older one to go back to. A cluster's machine runs the runtime the
cluster was switched to, else the one it was started with, else its flavor's
default (``RuntimeBundleRegistry.resolve_for_node``). So nothing moves a
cluster to another runtime by itself -- not a restart, not a recovery, not an
upgrade of this server that ships a newer one, nor one that no longer ships
the runtime a cluster runs: that cluster is shown on it, with the default
offered as the update.

The same goes for the settings a container is started with (where models and
caches are mounted): a container keeps the ones it was started with, whatever a
newer release would give it, and the models are loaded with paths that match
them (``bundles.running_mounts``). The cluster says the new ones are pending
(``settings_pending``) and applies them when the operator restarts it.

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
    mount_set,
    running_mounts,
    started_launches,
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


async def _mounts_differ(
    session: AsyncSession, env: InferenceEnvironment, node: Any, target: RuntimeBundleManifest
) -> bool:
    """Whether this machine's running container lacks a mount this release would give it.

    Known from what the backend started it with, else from what its agent
    reports; a machine whose container is not known is not said to differ.
    A mount the container has beyond the wanted ones (the token directory, the
    TLS material) is not a difference, so one that was started with this
    release's mounts exactly -- or a runtime other than the one that would run
    now, which a switch restarts -- never shows.
    """
    recorded = started_launches(env).get(str(node.id))
    if isinstance(recorded, dict) and recorded.get("bundle_id") not in (None, target.bundle_id):
        return False
    running = await running_mounts(session, env, node.id)
    if running is None:
        return False
    wanted = default_bundle_registry.mounts_for_node(target, node)
    return not mount_set(wanted) <= mount_set(running)


async def environment_status(session: AsyncSession, environment_id: Any) -> dict[str, Any]:
    """What the machines run, whether they are there yet, and whether a newer stable runtime is out."""
    env = await _environment(session, environment_id)
    registry = default_bundle_registry
    started = started_runtimes(env)
    # The cluster's own choice, when this server still ships it.
    chosen = registry.get_bundle((env.config_json or {}).get(RUNTIME_KEY) or "")
    offered: dict[str, RuntimeBundleManifest] = {}
    defaults: set[str] = set()
    runs: set[str] = set()
    withdrawn: set[str] = set()
    applied = True
    update: str | None = None
    settings_pending: list[str] = []
    for node in await EnvironmentNodeDAO(session).machines(env.id):
        fits = registry.runtimes_for(node)
        offered.update((b.bundle_id, b) for b in fits)
        default = registry.default_among(fits)
        target = registry.resolve_for_node(node, environment=env)
        if target is None:
            continue
        recorded = started.get(str(node.id))
        if recorded and registry.get_bundle(recorded) is None and (chosen is None or chosen not in fits):
            # A runtime this server no longer ships (withdrawn from the catalogue): the machine runs it until
            # its cluster restarts, and nothing moves a cluster by itself. It is shown as what runs, with the
            # move to the default offered; switching to it restarts the cluster.
            runs.add(recorded)
            withdrawn.add(recorded)
            if default is not None:
                defaults.add(default.bundle_id)
                update = default.bundle_id
            continue
        runs.add(target.bundle_id)
        # A machine with no record runs its default: a cluster formed before
        # the record was kept, or one that never started.
        applied &= target.bundle_id == started.get(str(node.id), getattr(default, "bundle_id", None))
        if "mounts" not in settings_pending and await _mounts_differ(session, env, node, target):
            settings_pending.append("mounts")
        if default is not None:
            defaults.add(default.bundle_id)
            # On an older stable runtime than its default. A preview was
            # chosen on purpose, and is left to its operator.
            if target is not default and target.certification.channel != "preview":
                update = default.bundle_id
    # Only for a cluster that is to run and is not restarting anyway: a pending runtime
    # switch makes the containers again with this release's settings.
    pending = bool(settings_pending) and applied and env.desired_state == EnvironmentDesiredState.RUNNING.value
    return {
        "runs": sorted(runs),
        "applied": applied,
        # The machines' containers were started with other launch settings than this release
        # gives them (``reasons``); they apply at the next restart, which nothing does by itself.
        "settings_pending": pending,
        "settings_reasons": settings_pending if pending else [],
        # The restart that applies them is under way.
        "settings_applying": pending and env.observed_generation < env.generation,
        # Not there yet, and the cluster is to run: it is restarting.
        "restarting": env.restarting(applied=applied),
        "update": update,
        # Runtimes the machines still run that this server no longer ships.
        "withdrawn": sorted(withdrawn),
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


async def apply_settings(session: AsyncSession, environment_id: Any) -> dict[str, Any]:
    """Restart the cluster so its machines' containers start with this release's settings.

    The operator's decision, like a switch (see the module docstring): after an
    upgrade that changes where the containers mount models and caches, the
    running ones keep the old mounts and serve as they did. Nothing to apply
    (``settings_pending`` is false) restarts nothing.
    """
    from faden_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    env = await _environment(session, environment_id)
    status = await environment_status(session, env.id)
    if not status["settings_pending"] or status["settings_applying"]:
        return status
    env.generation += 1
    wake_reconciler_after_commit(session)
    await session.flush()
    log.info("Cluster %s: restarts to apply this release's launch settings (%s)", env.name, ", ".join(status["settings_reasons"]))
    return await environment_status(session, env.id)
