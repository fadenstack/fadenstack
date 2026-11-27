"""Deployments on a standalone environment: one native engine per deployment.

The sequence (CONTRACTS.md § C1, agreed with B1 and C2):

start   ``start_native_worker`` on 127.0.0.1:P+20000, then ``native_worker_status``
        until ready, then ``model_tls_route`` ensure P -> P+20000, then publish
        ``https://<machine>:P/v1``.
stop    ``model_tls_route`` remove, then ``stop_native_worker``, then STOPPED
        (or DELETED, which this manager sets itself: the reconciler purges the
        row only once the phase says the engine is gone).

Every command is carried across passes (``commands.NativeCommands``); a pass
never waits out a cold start.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from faden_backend.db.dao.inference_dao import EndpointDAO, EnvironmentNodeDAO
from faden_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    EnvironmentStatus,
    InferenceDeployment,
    InferenceEnvironment,
)
from faden_backend.db.models.llm import LLMModel
from faden_backend.db.models.node_control import NodeCommandType
from faden_backend.services import machine_tls
from faden_backend.services.inference.bundles import default_bundle_registry
from faden_backend.services.inference.drivers.standalone.admission import memory_budget, node_refusal
from faden_backend.services.inference.drivers.standalone.commands import NativeCommands

log = logging.getLogger(__name__)

DRIVER_KEY = "standalone"

#: Public ports handed out when a deployment names none. Below 12768 so that
#: the internal port (+20000) stays under the ephemeral range.
PUBLIC_PORT_RANGE = range(8100, 9000)

#: Restarts of an engine that died, per generation, before giving up; the
#: wait before each doubles from this (30 s, 60 s, 120 s).
MAX_RESTARTS = 3
RESTART_BACKOFF_SEC = 30

#: How long a delete waits for its machine before an operator may force it.
FORCE_DELETE_AFTER_SEC = 300

#: How long a machine may take to fetch, verify and start (cold start ~100 s, Gate 0d).
START_TIMEOUT_SEC = 600
STOP_TIMEOUT_SEC = 120
ROUTE_TIMEOUT_SEC = 180


def _native(deployment: InferenceDeployment) -> dict[str, Any]:
    """The driver's own record on the row, copied so a write is a real change."""
    observed = dict(deployment.observed_status_json or {})
    native = dict(observed.get("native") or {})
    native["commands"] = dict(native.get("commands") or {})
    return native


def _save_native(deployment: InferenceDeployment, native: dict[str, Any]) -> None:
    observed = dict(deployment.observed_status_json or {})
    observed["native"] = native
    deployment.observed_status_json = observed


class StandaloneDeploymentManager:
    """``DeploymentOrchestrator`` for the standalone driver."""

    async def reconcile_deployment(
        self,
        session: Any,
        deployment: InferenceDeployment,
        *,
        node_control: Any = None,
    ) -> None:
        """Drive *deployment* one step toward its desired state (no commit)."""
        if node_control is None:
            self._observe(
                deployment, DeploymentPhase.PENDING,
                "node control service unavailable; no live action taken",
                observed={"reason": "no node control"}, mark_observed=False,
            )
            return

        native = _native(deployment)
        commands = NativeCommands(node_control, native["commands"], owner=f"deployment:{deployment.id}")
        try:
            if deployment.desired_state in (DeploymentDesiredState.STOPPED.value, DeploymentDesiredState.DELETED.value):
                await self._reconcile_absent(session, deployment, native, commands)
            else:
                await self._reconcile_active(session, deployment, native, commands)
        finally:
            _save_native(deployment, native)

    # ------------------------------------------------------------------
    # Active
    # ------------------------------------------------------------------

    async def _reconcile_active(
        self, session: Any, deployment: InferenceDeployment, native: dict[str, Any], commands: NativeCommands,
    ) -> None:
        environment = await session.get(InferenceEnvironment, deployment.environment_id)
        if environment is None:
            self._fail(deployment, "environment not found")
            return
        machines = await EnvironmentNodeDAO(session).machines(environment.id)
        if len(machines) != 1:
            self._observe(
                deployment, DeploymentPhase.PENDING,
                f"Waiting for {environment.name} to have its one machine.",
                observed={"reason": "no machine"}, mark_observed=False,
            )
            return
        node = machines[0]
        problem = node_refusal(node)
        if problem:
            self._fail(deployment, problem)
            return
        spec = dict(deployment.spec_json or {})
        try:
            budget = memory_budget(node, (((spec.get("resources") or {}).get("replica") or {}).get("memory")))
        except ValueError as exc:
            self._fail(deployment, str(exc))
            return
        bundle = default_bundle_registry.resolve_for_node(node, driver=DRIVER_KEY, environment=environment)
        if bundle is None or bundle.native is None:
            self._fail(deployment, f"No certified native runtime for {node.agent_id or node.host}.")
            return
        if str(environment.desired_state) == "running":
            native.pop("parked", None)  # it may start again
        else:
            # Its environment was stopped: the engine goes too (route first,
            # as on a stop), and the model waits to start again with it.
            await self._reconcile_absent(
                session, deployment, native, commands,
                done=(
                    DeploymentPhase.PENDING,
                    f"Waiting for its environment: {environment.name} is stopped. The model starts again with it.",
                ),
            )
            return
        env_status = str(getattr(environment.status, "value", environment.status))
        if env_status == EnvironmentStatus.DEGRADED.value and deployment.phase in (
            DeploymentPhase.RUNNING.value, DeploymentPhase.DEGRADED.value,
        ):
            # Its machine dropped off: it may well still be serving, but nothing
            # can be checked or changed until it is back.
            self._observe(
                deployment, DeploymentPhase.DEGRADED,
                environment.status_message or f"{environment.name} is degraded.",
                observed={"reason": "environment degraded"}, mark_observed=False,
            )
            return
        if env_status != EnvironmentStatus.READY.value:
            self._observe(
                deployment, DeploymentPhase.PENDING,
                f"Waiting for {environment.name}: its runtime is not ready on {node.agent_id or node.host}.",
                observed={"reason": "environment not ready"}, mark_observed=False,
            )
            return

        model = await session.get(LLMModel, deployment.model_id)
        if model is None:
            self._fail(deployment, "model not found")
            return
        snapshot = await self._snapshot_path(session, deployment, environment, node, model, commands, spec)
        if snapshot is None:
            return

        public_port = native.get("public_port") or await self._public_port(session, deployment, spec)
        native["public_port"] = public_port
        internal_port = machine_tls.internal_port(public_port)
        served_name = self._served_name(model, spec)
        start_payload = {
            "deployment_id": str(deployment.id),
            "generation": int(deployment.generation or 0),
            "bundle_id": bundle.bundle_id,
            "layer_digest": bundle.native.layer_digest,
            "model": {
                "id": str(model.id),
                "name": served_name,
                "hf_repo_id": getattr(model, "hf_repo_id", None),
                "revision": getattr(model, "hf_revision", None),
                "snapshot_path": snapshot,
            },
            "served_model_name": served_name,
            "host": "127.0.0.1",
            "port": internal_port,
            "gpu_memory_utilization": budget["gpu_memory_utilization"],
            "max_model_len": ((spec.get("engine") or {}).get("max_model_len")),
            "extra_args": list(((spec.get("extensions") or {}).get("standalone") or {}).get("extra_args") or []),
            "env": dict(((spec.get("extensions") or {}).get("standalone") or {}).get("env") or {}),
        }
        native["memory"] = budget

        if self._held_back(deployment, native):
            return
        start = await commands.step(
            "start", node_id=node.id, command_type=NodeCommandType.START_NATIVE_WORKER.value,
            payload=start_payload, timeout_sec=START_TIMEOUT_SEC,
        )
        if start.failed:
            await self._engine_down(deployment, native, commands, node, f"did not start: {start.error}")
            return
        if not start.done:
            self._observe(
                deployment, DeploymentPhase.APPLYING, f"Starting the engine on {node.agent_id or node.host}.",
                observed={"reason": "starting"}, mark_observed=False, total=1,
            )
            return

        status = await commands.ask(
            node_id=node.id, command_type=NodeCommandType.NATIVE_WORKER_STATUS.value,
            payload={"deployment_id": str(deployment.id)},
        )
        state = str((status or {}).get("state") or "unknown")
        native["worker"] = status
        if state in ("failed", "stopped"):
            # Gone: started again after a back-off, a few times at most.
            await self._engine_down(
                deployment, native, commands, node, f"{state}: {(status or {}).get('last_error') or 'no reason given'}",
            )
            return
        if state != "ready":
            phase = DeploymentPhase.DEGRADED if deployment.phase == DeploymentPhase.RUNNING.value else DeploymentPhase.APPLYING
            self._observe(
                deployment, phase,
                "Loading the model." if state == "starting" else f"The machine did not say how the engine is ({state}).",
                observed={"reason": state}, mark_observed=False, total=1,
            )
            return

        route = await commands.step(
            "route", node_id=node.id, command_type=NodeCommandType.MODEL_TLS_ROUTE.value,
            payload={
                "action": "ensure", "public_port": public_port, "internal_port": internal_port,
                "owner": str(deployment.id), "generation": int(deployment.generation or 0),
            },
            timeout_sec=ROUTE_TIMEOUT_SEC,
        )
        if route.failed:
            commands.forget("route")
            self._fail(deployment, f"The machine's model-TLS proxy did not take port {public_port}: {route.error}")
            return
        if not route.done:
            self._observe(
                deployment, DeploymentPhase.APPLYING, f"Opening port {public_port} on {node.agent_id or node.host}.",
                observed={"reason": "routing"}, mark_observed=False, total=1,
            )
            return

        native.pop("restarts", None)  # it serves: the count starts over
        base = f"https://{node.host}:{public_port}"
        await self._publish(session, deployment, base, spec, served_name)
        self._observe(
            deployment, DeploymentPhase.RUNNING, f"Serving on {base}/v1",
            observed={"reason": "ready", "base_url": f"{base}/v1"}, ready=1, total=1,
        )

    # ------------------------------------------------------------------
    # Restarts: back off, then give up until someone asks again
    # ------------------------------------------------------------------

    async def _engine_down(
        self, deployment: InferenceDeployment, native: dict[str, Any], commands: NativeCommands, node: Any, reason: str,
    ) -> None:
        """The engine is gone: schedule a restart after a back-off, or give up after the last attempt."""
        commands.forget("start")
        commands.forget("route")
        generation = int(deployment.generation or 0)
        restarts = dict(native.get("restarts") or {})
        if restarts.get("generation") != generation:
            restarts = {"generation": generation, "count": 0}
        restarts["count"] = int(restarts.get("count") or 0) + 1
        restarts["last_error"] = reason
        if restarts["count"] > MAX_RESTARTS:
            restarts["gave_up"] = True
            restarts.pop("next_at", None)
            native["restarts"] = restarts
            # A start that timed out may still come up later: take down whatever
            # the machine has for it, so nothing is left running unrouted.
            await self._send_teardown(deployment, native, commands, node)
            self._fail(deployment, self._gave_up_message(restarts), mark_observed=True)
            return
        delay = RESTART_BACKOFF_SEC * 2 ** (restarts["count"] - 1)
        restarts["next_at"] = (datetime.now(tz=UTC) + timedelta(seconds=delay)).isoformat()
        native["restarts"] = restarts
        # DEGRADED keeps it in the reconciler's queue; FAILED would park it.
        self._observe(
            deployment, DeploymentPhase.DEGRADED,
            f"The engine {reason}. Starting it again in {delay} s "
            f"(attempt {restarts['count'] + 1} of {MAX_RESTARTS + 1}).",
            observed={"reason": "restarting", "restarts": restarts}, mark_observed=False, ready=0, total=1,
        )

    async def _send_teardown(
        self, deployment: InferenceDeployment, native: dict[str, Any], commands: NativeCommands, node: Any,
    ) -> None:
        """Send route remove and stop once, without waiting: best effort on giving up."""
        public_port = native.get("public_port")
        if public_port:
            await commands.step(
                "giveup_unroute", node_id=node.id, command_type=NodeCommandType.MODEL_TLS_ROUTE.value,
                payload={
                    "action": "remove", "public_port": int(public_port),
                    "owner": str(deployment.id), "generation": int(deployment.generation or 0),
                },
                timeout_sec=ROUTE_TIMEOUT_SEC, wait_sec=0,
            )
        await commands.step(
            "giveup_stop", node_id=node.id, command_type=NodeCommandType.STOP_NATIVE_WORKER.value,
            payload={"deployment_id": str(deployment.id), "grace_s": 30},
            timeout_sec=STOP_TIMEOUT_SEC, wait_sec=0,
        )

    def _held_back(self, deployment: InferenceDeployment, native: dict[str, Any]) -> bool:
        """Whether this pass must not start the engine: waiting out a back-off, or given up."""
        restarts = native.get("restarts") or {}
        if restarts.get("generation") != int(deployment.generation or 0):
            native.pop("restarts", None)  # a new generation: a fresh start
            native["commands"].pop("giveup_unroute", None)
            native["commands"].pop("giveup_stop", None)
            return False
        if restarts.get("gave_up"):
            if (deployment.observed_generation or 0) < (deployment.generation or 0):
                # Giving up stamped the generation observed; it lags again only
                # because an operator asked for another try (request_reconcile).
                native.pop("restarts", None)
                native["commands"].pop("giveup_unroute", None)
                native["commands"].pop("giveup_stop", None)
                return False
            self._fail(deployment, self._gave_up_message(restarts), mark_observed=True)
            return True
        next_at = restarts.get("next_at")
        if next_at and datetime.now(tz=UTC) < datetime.fromisoformat(next_at):
            left = int((datetime.fromisoformat(next_at) - datetime.now(tz=UTC)).total_seconds()) + 1
            self._observe(
                deployment, DeploymentPhase.DEGRADED,
                f"The engine {restarts.get('last_error')}. Starting it again in {left} s "
                f"(attempt {int(restarts.get('count') or 0) + 1} of {MAX_RESTARTS + 1}).",
                observed={"reason": "restarting", "restarts": restarts}, mark_observed=False, ready=0, total=1,
            )
            return True
        return False

    @staticmethod
    def _gave_up_message(restarts: dict[str, Any]) -> str:
        return (
            f"The engine stopped {restarts.get('count')} times; the last time it "
            f"{restarts.get('last_error')}. Not starting it again: fix the cause, then reconcile "
            "or change the deployment."
        )

    async def _snapshot_path(
        self, session: Any, deployment: InferenceDeployment, environment: Any, node: Any,
        model: Any, commands: NativeCommands, spec: dict[str, Any],
    ) -> str | None:
        """Where the model's files are on the machine, or ``None`` after recording why not yet."""
        artifacts = spec.get("artifacts") or {}
        if artifacts.get("source") == "local_path" and artifacts.get("root_path"):
            return str(artifacts["root_path"])
        from faden_backend.services.inference.artifacts import ModelArtifactCoordinator  # noqa: PLC0415

        coordinator = ModelArtifactCoordinator(session, gateway=commands._gateway)  # noqa: SLF001
        readiness = await coordinator.evaluate(model=model, environment=environment)
        if not readiness.all_ready:
            readiness = await coordinator.ensure(model=model, environment=environment, fetch_to_server=True)
        if readiness.all_ready and readiness.root_paths.get(str(node.id)):
            return readiness.root_paths[str(node.id)]
        if readiness.blockers:
            self._fail(deployment, "The model cannot be copied to the machine: " + "; ".join(readiness.blockers))
            return None
        self._observe(
            deployment, DeploymentPhase.PREPARING,
            readiness.waiting_on or f"Copying the model to {node.agent_id or node.host}.",
            observed={"reason": "preparing_artifacts"}, mark_observed=False,
        )
        return None

    async def _public_port(self, session: Any, deployment: InferenceDeployment, spec: dict[str, Any]) -> int:
        """The deployment's own port, else the first one no other deployment of its environment holds."""
        wanted = (spec.get("service") or {}).get("port")
        if wanted:
            machine_tls.internal_port(int(wanted))  # refuses a port too high to proxy
            return int(wanted)
        rows = await session.execute(
            select(InferenceDeployment).where(
                InferenceDeployment.environment_id == deployment.environment_id,
                InferenceDeployment.id != deployment.id,
            )
        )
        taken: set[int] = set()
        for other in rows.scalars():
            port = ((other.observed_status_json or {}).get("native") or {}).get("public_port")
            port = port or ((other.spec_json or {}).get("service") or {}).get("port")
            if port:
                taken.add(int(port))
        return next(p for p in PUBLIC_PORT_RANGE if p not in taken)

    @staticmethod
    def _served_name(model: Any, spec: dict[str, Any]) -> str:
        from faden_backend.services.inference.drivers.ray.compiler import _sanitize_model_id  # noqa: PLC0415

        return _sanitize_model_id(getattr(model, "display_name", None), getattr(model, "hf_repo_id", None))

    async def _publish(
        self, session: Any, deployment: InferenceDeployment, base: str, spec: dict[str, Any], served_name: str,
    ) -> None:
        path = str((spec.get("service") or {}).get("path") or "/v1")
        dao = EndpointDAO(session)
        existing = next((e for e in await dao.list_for_deployment(deployment.id) if e.name == "openai"), None)
        if existing is None:
            await dao.create(deployment.id, name="openai", address=base, path=path, status=EndpointStatus.PUBLISHED)
            existing = next(e for e in await dao.list_for_deployment(deployment.id) if e.name == "openai")
        await dao.update(
            existing.id,
            address=base,
            status=EndpointStatus.PUBLISHED,
            status_message="running natively (1 ready)",
            published={
                "driver": DRIVER_KEY,
                "model": served_name,
                "ready_replicas": 1,
                "path": path,
                "base_url": f"{base}{path}",
                "machine_tls": True,
                "model_tls_pending": False,
                "watermark": None,
            },
        )

    # ------------------------------------------------------------------
    # Absent (stop / delete)
    # ------------------------------------------------------------------

    async def _reconcile_absent(
        self, session: Any, deployment: InferenceDeployment, native: dict[str, Any], commands: NativeCommands,
        *, done: tuple[DeploymentPhase, str] | None = None,
    ) -> None:
        """Take the engine down: route removed, then the engine stopped, then *done* (default: STOPPED/DELETED)."""
        deleting = done is None and deployment.desired_state == DeploymentDesiredState.DELETED.value
        done_phase, done_message = done or (
            (DeploymentPhase.DELETED, "Deleted.") if deleting else (DeploymentPhase.STOPPED, "Stopped.")
        )
        machines = await EnvironmentNodeDAO(session).machines(deployment.environment_id)
        if deleting:
            native.setdefault("deleting_since", datetime.now(tz=UTC).isoformat())
            if native.get("force_delete") and machines:
                # The operator finished a delete its machine could not answer:
                # the row goes now, and the machine is cleaned up when it is
                # back (StandaloneEnvironmentManager, ``cleanup``).
                await self._leave_cleanup(session, deployment, native, machines[0])
                await self._retire(session, deployment.id)
                self._observe(
                    deployment, DeploymentPhase.DELETED,
                    f"Deleted. Cleanup pending on {machines[0].agent_id or machines[0].host}.",
                    observed={"reason": "force deleted"}, ready=0,
                )
                return
        # ``parked``: taken down for a stopped environment, port kept for the
        # restart. Nothing runs, so nothing is sent again pass after pass.
        started = not native.get("parked") and (
            bool(native["commands"].get("start")) or bool(native.get("public_port"))
        )
        if not machines or not started:
            # Nothing was started (or the machine left): nothing to take down.
            await self._retire(session, deployment.id)
            message = done_message if done else "Nothing runs for it."
            self._observe(deployment, done_phase, message, observed={"reason": "absent"}, ready=0)
            return
        node = machines[0]
        public_port = native.get("public_port")
        if public_port:
            route = await commands.step(
                "unroute", node_id=node.id, command_type=NodeCommandType.MODEL_TLS_ROUTE.value,
                payload={
                    "action": "remove", "public_port": int(public_port),
                    "owner": str(deployment.id), "generation": int(deployment.generation or 0),
                },
                timeout_sec=ROUTE_TIMEOUT_SEC,
            )
            if not route.done and not route.failed:
                self._observe(
                    deployment, DeploymentPhase.STOPPING, f"Closing port {public_port}.",
                    observed={"reason": "unrouting"}, mark_observed=False,
                )
                return
            if route.failed:
                log.warning("Deployment %s: removing route %s failed: %s", deployment.id, public_port, route.error)
        stop = await commands.step(
            "stop", node_id=node.id, command_type=NodeCommandType.STOP_NATIVE_WORKER.value,
            payload={"deployment_id": str(deployment.id), "grace_s": 30},
            timeout_sec=STOP_TIMEOUT_SEC,
        )
        if stop.failed:
            commands.forget("stop")
            self._observe(
                deployment, DeploymentPhase.STOPPING, f"The engine did not stop: {stop.error}. Trying again.",
                observed={"reason": "stop failed"}, mark_observed=False,
            )
            return
        if not stop.done:
            self._observe(
                deployment, DeploymentPhase.STOPPING, "Stopping the engine.",
                observed={"reason": "stopping"}, mark_observed=False,
            )
            return
        await self._retire(session, deployment.id)
        for step in ("start", "route", "unroute", "stop"):
            commands.forget(step)
        if not deleting and done is None:
            native.pop("public_port", None)
        if done is not None:
            native["parked"] = True
        self._observe(
            deployment, done_phase, done_message,
            observed={"reason": done_phase.value}, ready=0,
        )

    async def _leave_cleanup(
        self, session: Any, deployment: InferenceDeployment, native: dict[str, Any], node: Any,
    ) -> None:
        """Record on the environment what its machine must still take down for *deployment*."""
        environment = await session.get(InferenceEnvironment, deployment.environment_id)
        if environment is None:
            return
        observed = dict(environment.observed_status_json or {})
        env_native = dict(observed.get("native") or {})
        cleanup = dict(env_native.get("cleanup") or {})
        cleanup[str(deployment.id)] = {
            "node_id": str(node.id),
            "deployment_name": deployment.name,
            "public_port": native.get("public_port"),
            "since": datetime.now(tz=UTC).isoformat(),
            "commands": {},
        }
        env_native["cleanup"] = cleanup
        observed["native"] = env_native
        environment.observed_status_json = observed
        # The environment's next pass does the clean-up (or says it waits).
        if (environment.observed_generation or 0) >= (environment.generation or 0):
            environment.observed_generation = (environment.generation or 0) - 1

    async def _retire(self, session: Any, deployment_id: uuid.UUID) -> None:
        dao = EndpointDAO(session)
        for endpoint in await dao.list_for_deployment(deployment_id):
            await dao.update(
                endpoint.id, status=EndpointStatus.RETIRED, status_message="deployment converged to deleted/stopped",
            )

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def _fail(self, deployment: InferenceDeployment, message: str, *, mark_observed: bool = True) -> None:
        self._observe(deployment, DeploymentPhase.FAILED, message, observed={"reason": message}, mark_observed=mark_observed)

    @staticmethod
    def _observe(
        deployment: InferenceDeployment,
        phase: DeploymentPhase,
        message: str,
        *,
        observed: dict[str, Any] | None = None,
        mark_observed: bool = True,
        ready: int | None = None,
        total: int | None = None,
    ) -> None:
        """Record the pass on the row (attribute mutation only; the caller commits)."""
        status = dict(deployment.observed_status_json or {})
        payload = dict(observed or {})
        payload.setdefault("driver", DRIVER_KEY)
        payload.setdefault("reconciled", phase not in (DeploymentPhase.PENDING,))
        status["observation"] = payload
        deployment.observed_status_json = status
        if mark_observed:
            deployment.observed_generation = deployment.generation
        deployment.phase = phase.value
        deployment.phase_message = message
        if ready is not None:
            deployment.ready_replicas = ready
        if total is not None:
            deployment.total_replicas = total
