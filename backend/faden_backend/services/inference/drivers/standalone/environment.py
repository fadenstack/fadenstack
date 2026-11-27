"""Standalone environments: exactly one machine, its native runtime staged on it.

Ready means the machine can serve: it is the environment's only member, its
agent can run a native runtime behind its model-TLS proxy, a certified runtime
resolves for it, and that runtime is staged there (``stage_native_runtime``,
idempotent by layer digest). Engines belong to deployments, not to the
environment, so stopping an environment stops nothing here: its deployments
see it stopped and stop themselves.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO
from faden_backend.db.models.inference import EnvironmentStatus, InferenceEnvironment
from faden_backend.db.models.node_control import NodeCommandType, NodeHealthStatus
from faden_backend.services.inference.bundles import default_bundle_registry
from faden_backend.services.inference.drivers.standalone.admission import node_refusal
from faden_backend.services.inference.drivers.standalone.commands import NativeCommands

log = logging.getLogger(__name__)

DRIVER_KEY = "standalone"

#: Fetching a 2-3 GB runtime over a slow link, verifying and extracting it.
STAGE_TIMEOUT_SEC = 3600


class StandaloneEnvironmentManager:
    """``EnvironmentManager`` for the standalone driver."""

    async def reconcile_environment(
        self,
        session: Any,
        environment: InferenceEnvironment,
        *,
        node_control: Any = None,
    ) -> None:
        """Drive *environment* toward its desired state (no commit)."""
        if str(environment.desired_state) != "running":
            self._observe(environment, EnvironmentStatus.STOPPED, "stopped")
            return
        if node_control is None:
            self._observe(
                environment, EnvironmentStatus.PENDING,
                "node control service unavailable; no live action taken", mark_observed=False,
            )
            return

        members = await EnvironmentNodeDAO(session).list_for_environment(environment.id)
        machines = await EnvironmentNodeDAO(session).machines(environment.id)
        if not machines:
            self._observe(environment, EnvironmentStatus.PENDING, "Add the Mac this environment runs on.")
            return
        if len(machines) > 1:
            self._observe(
                environment, EnvironmentStatus.FAILED,
                "A standalone environment runs on exactly one machine; remove the others.",
            )
            return
        node = machines[0]
        name = node.agent_id or node.host
        if environment.head_node_id != node.id:
            environment.head_node_id = node.id
        problem = node_refusal(node)
        if problem:
            self._set_members(members, "unknown")
            self._observe(environment, EnvironmentStatus.FAILED, problem)
            return
        bundle = default_bundle_registry.resolve_for_node(node, driver=DRIVER_KEY, environment=environment)
        if bundle is None or bundle.native is None:
            caps = node.capabilities_json or {}
            self._observe(
                environment, EnvironmentStatus.FAILED,
                f"No certified native runtime for {name} ({caps.get('os') or 'unknown OS'}, "
                f"{caps.get('machine') or 'unknown CPU'}, GPU {caps.get('gpu_vendor') or 'unknown'}).",
            )
            return

        observed = dict(environment.observed_status_json or {})
        native = dict(observed.get("native") or {})
        native["commands"] = dict(native.get("commands") or {})
        commands = NativeCommands(node_control, native["commands"], owner=f"environment:{environment.id}")
        spec = bundle.native
        stage = await commands.step(
            "stage",
            node_id=node.id,
            command_type=NodeCommandType.STAGE_NATIVE_RUNTIME.value,
            payload={
                "bundle_id": bundle.bundle_id,
                "oci_image": spec.repository,
                "tag": spec.tag,
                "config_digest": spec.config_digest,
                "rootfs_digest": spec.rootfs_digest,
                "layer_digest": spec.layer_digest,
                "size_bytes": spec.size_bytes,
                "pyvenv_home_rewrite": spec.pyvenv_home_rewrite,
                "interpreter_path": spec.interpreter.path,
                # Only when the manifest records it: a payload for an older
                # manifest is exactly what it was.
                **({"unpacked_size_bytes": spec.unpacked_size_bytes} if spec.unpacked_size_bytes else {}),
            },
            timeout_sec=STAGE_TIMEOUT_SEC,
        )
        native["bundle_id"] = bundle.bundle_id
        if stage.failed:
            commands.forget("stage")  # the next pass asks again
            self._save(environment, native)
            self._set_members(members, "dead")
            self._observe(environment, EnvironmentStatus.FAILED, f"The runtime could not be staged on {name}: {stage.error}")
            return
        if not stage.done:
            self._save(environment, native)
            self._observe(
                environment, EnvironmentStatus.PREPARING,
                f"Fetching the runtime ({bundle.display_name}) to {name}.", mark_observed=False,
            )
            return
        native["staged"] = stage.result
        native["staged_at"] = native.get("staged_at") or datetime.now(tz=UTC).isoformat()
        self._save(environment, native)
        environment.runtime_version = bundle.compatibility_matrix.vllm_version or environment.runtime_version
        offline = str(getattr(node, "status", "") or "") == NodeHealthStatus.OFFLINE.value
        pending = await self._clean_up(environment, native, node, node_control, offline=offline)
        self._save(environment, native)
        note = (
            f" Cleanup pending on {name} for {pending} deleted deployment{'s' if pending != 1 else ''}."
            if pending else ""
        )
        if offline:
            # Staged, but the machine is not connected: what it serves cannot
            # be reached, and nothing sent to it would arrive.
            self._set_members(members, "dead")
            self._observe(
                environment, EnvironmentStatus.DEGRADED,
                f"{name} is offline: its models cannot be reached until it reconnects.{note}",
            )
            return
        self._set_members(members, "alive")
        self._observe(environment, EnvironmentStatus.READY, note.strip() or None, mark_observed=not pending)

    async def _clean_up(
        self, environment: InferenceEnvironment, native: dict[str, Any], node: Any, node_control: Any, *, offline: bool,
    ) -> int:
        """Take down what force-deleted deployments left on the machine; return how many are still pending.

        Each entry: route removed (when it had a port), then the engine
        stopped. Sent only while the machine is connected; an entry goes once
        both are done.
        """
        cleanup = dict(native.get("cleanup") or {})
        if offline or not cleanup:
            return len(cleanup)
        for deployment_id, entry in list(cleanup.items()):
            entry = dict(entry)
            entry["commands"] = dict(entry.get("commands") or {})
            commands = NativeCommands(node_control, entry["commands"], owner=f"cleanup:{deployment_id}")
            routed = True
            if entry.get("public_port"):
                unroute = await commands.step(
                    "unroute", node_id=node.id, command_type=NodeCommandType.MODEL_TLS_ROUTE.value,
                    payload={"action": "remove", "public_port": int(entry["public_port"]), "owner": deployment_id},
                    timeout_sec=180, wait_sec=2,
                )
                routed = not (unroute.done or unroute.failed)
                if unroute.failed:
                    log.warning("Cleanup of %s: route removal failed: %s", deployment_id, unroute.error)
            stopped = False
            if not routed:
                stop = await commands.step(
                    "stop", node_id=node.id, command_type=NodeCommandType.STOP_NATIVE_WORKER.value,
                    payload={"deployment_id": deployment_id, "grace_s": 30},
                    timeout_sec=120, wait_sec=2,
                )
                if stop.failed:
                    commands.forget("stop")  # asked again on the next pass
                stopped = stop.done
            if stopped:
                cleanup.pop(deployment_id)
            else:
                cleanup[deployment_id] = entry
        native["cleanup"] = cleanup
        return len(cleanup)

    @staticmethod
    def _set_members(members: list[Any], status: str) -> None:
        now = datetime.now(tz=UTC)
        for member in members:
            member.member_status = status
            if status == "alive" and member.joined_at is None:
                member.joined_at = now

    @staticmethod
    def _save(environment: InferenceEnvironment, native: dict[str, Any]) -> None:
        observed = dict(environment.observed_status_json or {})
        observed["native"] = native
        environment.observed_status_json = observed

    @staticmethod
    def _observe(
        environment: InferenceEnvironment,
        status: EnvironmentStatus,
        reason: str | None,
        *,
        mark_observed: bool = True,
    ) -> None:
        """Record the pass (attribute mutation only; the caller commits)."""
        environment.status = status
        if mark_observed:
            environment.observed_generation = environment.generation
        observed = dict(environment.observed_status_json or {})
        observed["observation"] = {
            "status": status.value,
            "reconciled": status is not EnvironmentStatus.PENDING,
            "reason": reason,
            "driver": DRIVER_KEY,
        }
        environment.observed_status_json = observed
        # READY may carry a note too (a clean-up still pending); STOPPED never does.
        environment.status_message = None if status is EnvironmentStatus.STOPPED else reason
