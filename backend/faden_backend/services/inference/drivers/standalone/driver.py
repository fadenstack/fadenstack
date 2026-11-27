"""The standalone driver: what the registry constructs for ``driver="standalone"``."""

from __future__ import annotations

from typing import Any

from faden_backend.services.inference.capabilities import CapabilityDocument
from faden_backend.services.inference.drivers.standalone.deployment import StandaloneDeploymentManager
from faden_backend.services.inference.drivers.standalone.environment import StandaloneEnvironmentManager
from faden_backend.services.inference.observability import LogPage, LogSource, ObservabilityUnsupported


class StandaloneDriver:
    """One machine, a native engine per deployment, no Ray (``InferenceDriver``).

    Constructed with no arguments by the registry; the reconciler hands in the
    session and node control at call time.
    """

    key: str = "standalone"

    def __init__(self) -> None:
        self.environment_manager = StandaloneEnvironmentManager()
        self.deployment_manager = StandaloneDeploymentManager()

    async def probe(self, control_plane: Any) -> dict:
        """A standalone control plane has no service of its own to reach."""
        return {"driver": self.key, "reachable": True, "detail": "no control service: each machine is its own"}

    async def capabilities(self, environment: Any) -> CapabilityDocument:
        """What a standalone environment offers: one copy, on one machine, no Ray features."""
        return CapabilityDocument.from_dict({
            "driver": self.key,
            "deployment": {"fixed_replicas": True, "autoscaling": False},
            "topology": {"multi_node": False, "tensor_parallel": False, "pipeline_parallel": False},
            "routing": {"strategies": ["default"]},
            "artifacts": {"local_path": True, "faden_sync": True},
        })

    async def logs(
        self,
        session: Any,
        deployment: Any,
        *,
        source: LogSource,
        node_id: str | None = None,
        replica_id: str | None = None,
        tail: int = 200,
        since: str | None = None,
        cursor: str | None = None,
        node_control: object | None = None,
    ) -> LogPage:
        """The engine's log, read from its machine (``native_worker_logs``)."""
        from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO  # noqa: PLC0415
        from faden_backend.db.models.node_control import NodeCommandType  # noqa: PLC0415
        from faden_backend.services.inference.drivers.standalone.commands import NativeCommands  # noqa: PLC0415
        from faden_backend.services.inference.observability import LogLine  # noqa: PLC0415

        machines = await EnvironmentNodeDAO(session).machines(deployment.environment_id)
        if not machines or node_control is None:
            return LogPage(source=source, detail="No machine to read the engine's log from.")
        node = machines[0]
        answer = await NativeCommands(node_control, {}, owner=f"deployment:{deployment.id}").ask(
            node_id=node.id,
            command_type=NodeCommandType.NATIVE_WORKER_LOGS.value,
            payload={"deployment_id": str(deployment.id), "tail": int(tail), "cursor": cursor},
        )
        if answer is None:
            return LogPage(source=source, node_id=str(node.id), detail="The machine did not answer in time.")
        if answer.get("ok") is False:
            return LogPage(source=source, node_id=str(node.id),
                           detail=f"The machine could not read the engine's log: {answer.get('error') or 'unknown error'}")
        lines = [
            LogLine(message=str(line), host=node.host, source="engine")
            for line in (answer.get("lines") or [])
        ]
        return LogPage(source=source, node_id=str(node.id), lines=lines, next_cursor=answer.get("cursor"))

    async def deployment_metrics(self, session: Any, deployment: Any, **_: Any) -> Any:
        raise ObservabilityUnsupported(self.key, "deployment_metrics")

    async def environment_metrics(self, session: Any, environment: Any, **_: Any) -> Any:
        raise ObservabilityUnsupported(self.key, "environment_metrics")
