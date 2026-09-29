"""Persistent websocket stream client for backend node control."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

import websockets
from websockets.exceptions import ConnectionClosed

from faden_agent import __version__
from faden_agent.collectors import collect_gpu_snapshot, collect_inventory, collect_utilization
from faden_agent.command_verifier import (
    SIGNING_HEADER,
    SIGNING_SCHEME,
    derive_signing_key,
    validate_command_age,
    verify_command_signature,
)
from faden_agent.backend_client import BackendClient
from faden_agent.config import AgentConfig
from faden_agent.dispatcher import CommandDispatcher
from faden_agent.event_buffer import EventBuffer
from faden_agent.gpu import GpuCollector, NullCollector
from faden_agent.health_supervisor import HealthSupervisor
from faden_agent.runtimes import ContainerRuntime
from faden_agent.state_store import StateStore
from faden_agent.tls import websockets_ssl

log = logging.getLogger(__name__)

#: Header the agent connects with: the commands it is still executing (backend
#: ``NodeControlService.RUNNING_COMMANDS_HEADER``).
RUNNING_COMMANDS_HEADER = "X-Faden-Running-Commands"

#: How often the control channel checks the backend is still there.
_PING_INTERVAL_SEC = 20

#: How long a pong may take before the connection is considered dead.
#:
#: This was also 20s, which the agent's own work then broke. Pulling a
#: runtime image streams roughly 15GB down the same link the control channel
#: uses; on a 1Gb/s connection that saturates it, a pong queues behind the
#: transfer, and the socket is closed with "keepalive ping timeout" -- killing
#: the command that started the transfer. The node reconnects seconds later
#: and looks fine, while the image is half-loaded, the command is stuck in
#: ``running`` and the cluster sits at "preparing" with nothing to read.
#:
#: The control channel must not be collateral damage of the data path it
#: coordinates. Two minutes still detects a genuinely dead peer quickly.
_PING_TIMEOUT_SEC = 120


class StreamClient:
    """Manage one outbound stream session lifecycle."""

    def __init__(
        self,
        *,
        config: AgentConfig,
        runtime: ContainerRuntime,
        state_store: StateStore,
        dispatcher: CommandDispatcher,
        static_capabilities: dict[str, Any],
        events: EventBuffer,
        backend_client: BackendClient | None = None,
        gpu_collector: GpuCollector | None = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._state = state_store
        self._dispatcher = dispatcher
        self._static_capabilities = static_capabilities
        self._events = events
        self._backend_client = backend_client
        self._gpu_collector: GpuCollector = gpu_collector or NullCollector()
        self._send_lock = asyncio.Lock()
        self._inventory_trigger = asyncio.Event()
        self._health_supervisor = HealthSupervisor(
            runtime=runtime,
            state_store=state_store,
            events=events,
            advertise_host=config.advertise_host or "127.0.0.1",
            advertise_scheme=getattr(config, "advertise_scheme", "http") or "http",
        )

    async def run(self, *, credential: str) -> None:
        """Open stream and process commands until disconnected."""
        # The key for this connection: the backend signs with the credential
        # the stream was opened with, for as long as it stays open.
        self._signing_key = derive_signing_key(credential)
        ws_url = self._ws_url()
        headers = {
            "Authorization": f"Bearer {credential}",
            SIGNING_HEADER: SIGNING_SCHEME,
            RUNNING_COMMANDS_HEADER: self._running_commands_header(),
        }
        log.info("Connecting node stream to %s", ws_url)
        async with websockets.connect(
            ws_url,
            additional_headers=headers,
            open_timeout=self._config.request_timeout_sec,
            ping_interval=_PING_INTERVAL_SEC,
            ping_timeout=_PING_TIMEOUT_SEC,
            close_timeout=10,
            max_size=2**22,
            ssl=websockets_ssl(self._config) if ws_url.startswith("wss://") else None,
        ) as ws:
            tasks = {
                asyncio.create_task(self._receive_loop(ws), name="receive"),
                asyncio.create_task(self._heartbeat_loop(ws), name="heartbeat"),
                asyncio.create_task(self._inventory_loop(ws), name="inventory"),
                asyncio.create_task(self._event_flush_loop(ws), name="event_flush"),
                asyncio.create_task(self._seq_flush_loop(), name="seq_flush"),
                asyncio.create_task(self._health_supervisor.run_forever(), name="health"),
                asyncio.create_task(self._credential_rotation_loop(), name="cred_rotate"),
            }
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc

    def _running_commands_header(self) -> str:
        """``v1:`` and the ids of the commands still executing here, for the backend to keep.

        Everything else it had sent to this agent died with the last
        connection -- which the backend may not have seen close, when it is
        itself the one that restarted.
        """
        listing = getattr(self._dispatcher, "running_command_ids", None)
        running = listing() if callable(listing) else []
        return "v1:" + ",".join(running[:100])

    async def _receive_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            try:
                raw = await ws.recv()
            except ConnectionClosed:
                return
            payload = self._parse_message(raw)
            if payload is None:
                continue
            msg_type = str(payload.get("type") or "").strip().lower()
            if msg_type == "hello_ack" and payload.get("signs_commands") is True and not self._state.state.commands_signed:
                self._state.state.commands_signed = True
                self._state.save()
                log.info("The backend signs its commands; unsigned ones are refused from now on.")
            if msg_type in {"hello_ack", "commands"}:
                commands = payload.get("commands") if msg_type == "hello_ack" else payload.get("items")
                if isinstance(commands, list):
                    for item in commands:
                        if isinstance(item, dict):
                            await self._handle_command(ws, item)
                node_id = payload.get("node_id")
                if isinstance(node_id, str) and node_id:
                    self._state.state.node_id = node_id
                    self._state.save()
                # Sync profile from hello_ack
                if msg_type == "hello_ack":
                    profile = payload.get("profile")
                    if isinstance(profile, dict) or profile is None:
                        self._state.state.profile = profile
                        self._state.save()
            elif msg_type == "command":
                command = payload.get("command")
                if isinstance(command, dict):
                    await self._handle_command(ws, command)

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        interval = max(self._config.heartbeat_interval_sec, 5)
        while True:
            await asyncio.sleep(interval)
            status = "healthy"
            if self._state.state.maintenance_mode:
                status = "maintenance"
            elif self._state.state.draining:
                status = "draining"
            await self._send_json(
                ws,
                {
                    "type": "heartbeat",
                    "status": status,
                    "version": __version__,
                    "capabilities": self._static_capabilities,
                    "advertise_host": self._config.advertise_host,
                },
            )

    async def _inventory_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        interval = max(self._config.inventory_interval_sec, 15)
        while True:
            gpu_snapshot = await collect_gpu_snapshot(self._gpu_collector)
            inventory = await collect_inventory(self._static_capabilities, gpu_snapshot=gpu_snapshot)
            inventory["vllm_containers"] = await self._find_vllm()
            inventory["ray_runtime"] = await self._ray_runtime()
            utilization = await collect_utilization(gpu_snapshot=gpu_snapshot)
            await self._send_json(
                ws,
                {
                    "type": "inventory",
                    "inventory": inventory,
                    "utilization": utilization,
                },
            )
            # Wait for trigger or timeout
            try:
                await asyncio.wait_for(self._inventory_trigger.wait(), timeout=interval)
                self._inventory_trigger.clear()
            except TimeoutError:
                pass

    async def _find_vllm(self) -> list[dict[str, Any]]:
        """vLLM containers this machine runs that Fadenstack did not start.

        Bounded, and never able to hold up the inventory it rides along with.
        """
        from faden_agent.vllm_discovery import discover_vllm  # noqa: PLC0415

        try:
            return await asyncio.wait_for(discover_vllm(self._runtime), timeout=30)
        except Exception as exc:  # noqa: BLE001 - an inventory without this is still an inventory
            log.debug("vLLM discovery skipped: %s", exc)
            return []

    async def _ray_runtime(self) -> dict[str, Any] | None:
        """Whether this machine runs Fadenstack's Ray runtime -- what a new server looks for.

        Cheap (one ``inspect``), bounded, and never able to hold up the
        inventory: ``None`` when there is no runtime container or no answer.
        """
        from faden_agent.ray.container import DEFAULT_CONTAINER_NAME  # noqa: PLC0415
        from faden_agent.ray.inspect import runtime_summary  # noqa: PLC0415

        try:
            return await asyncio.wait_for(runtime_summary(self._runtime, DEFAULT_CONTAINER_NAME), timeout=10)
        except Exception as exc:  # noqa: BLE001 - an inventory without this is still an inventory
            log.debug("Ray runtime summary skipped: %s", exc)
            return None

    async def _event_flush_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while True:
            await asyncio.sleep(5)
            batch = self._events.drain(max_items=100)
            if not batch:
                continue
            await self._send_json(ws, {"type": "event_batch", "events": batch})

    async def _handle_command(
        self,
        ws: websockets.WebSocketClientProtocol,
        command: dict[str, Any],
    ) -> None:
        command_id = str(command.get("id") or "").strip()
        if not command_id:
            return
        command_type = str(command.get("command_type") or "").strip().lower()
        correlation_id = str(command.get("correlation_id") or command_id)

        # A signature is checked whenever there is one, and required once the
        # backend has said it signs (or the operator requires it).
        unsigned = "signature" not in command
        if (unsigned and self._requires_signature()) or (
            not unsigned and not verify_command_signature(command, self._signing_key)
        ):
            reason = "missing" if unsigned else "invalid"
            log.warning("Rejected command %s (%s): signature %s.", command_id, command_type, reason)
            await self._send_json(
                ws,
                {
                    "type": "command_result",
                    "command_id": command_id,
                    "correlation_id": correlation_id,
                    "success": False,
                    "error_code": f"signature_{reason}",
                    "error_message": f"Command signature {reason}: the agent refused to run it.",
                },
            )
            return

        # Reject expired fresh commands.  Server-driven re-dispatch of an
        # in-flight command (flagged by the backend) is exempt: it is by
        # definition older than the freshness bound (that's why it was
        # re-sent), the HMAC check above already authenticated it, and the
        # dispatcher deduplicates an execution that already finished.
        if command.get("redispatch") is not True and not validate_command_age(command):
            log.warning("Rejected command %s: expired issued_at.", command_id)
            await self._send_json(
                ws,
                {
                    "type": "command_result",
                    "command_id": command_id,
                    "correlation_id": correlation_id,
                    "success": False,
                    "error_code": "command_expired",
                    "error_message": "Command issued_at is too old.",
                },
            )
            return

        await self._send_json(
            ws,
            {
                "type": "command_ack",
                "command_id": command_id,
                "message": f"Accepted {command_type}",
                "correlation_id": correlation_id,
            },
        )

        async def emit_progress(progress_payload: dict[str, Any]) -> None:
            await self._send_json(
                ws,
                {
                    "type": "command_progress",
                    "command_id": command_id,
                    "correlation_id": correlation_id,
                    **progress_payload,
                },
            )

        result = await self._dispatcher.handle(command, emit_progress)
        await self._send_json(
            ws,
            {
                "type": "command_result",
                "command_id": command_id,
                "correlation_id": correlation_id,
                **result,
            },
        )

    async def _send_json(self, ws: websockets.WebSocketClientProtocol, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            envelope = dict(payload)
            envelope["seq"] = self._state.next_seq()
            await ws.send(json.dumps(envelope))

    async def _seq_flush_loop(self) -> None:
        """Periodically persist tx_seq to disk instead of per-message."""
        while True:
            await asyncio.sleep(30)
            self._state.flush_seq()

    def trigger_inventory(self) -> None:
        """Signal the inventory loop to run immediately."""
        self._inventory_trigger.set()

    async def _credential_rotation_loop(self) -> None:
        """Rotate credential periodically (every 12 hours)."""
        interval = 12 * 3600
        while True:
            await asyncio.sleep(interval)
            if not self._backend_client:
                continue
            credential = self._state.state.credential
            if not credential:
                continue
            try:
                result = await self._backend_client.rotate_credential(credential=credential)
                new_credential = result.get("credential")
                if isinstance(new_credential, str) and new_credential:
                    self._state.state.credential = new_credential
                    self._state.save()
                    # Not the signing key: the backend keeps signing this
                    # connection with the credential it was opened with. The
                    # next connection uses the new one, on both sides.
                    log.info("Credential rotated successfully.")
                else:
                    log.warning("Credential rotation response missing new credential.")
            except Exception:
                log.warning("Credential rotation failed; continuing with current credential.", exc_info=True)

    def _requires_signature(self) -> bool:
        """Whether a command without a signature is refused."""
        mode = getattr(self._config, "require_signed_commands", "auto")
        if mode in {"true", "1", "yes", "on"}:
            return True
        if mode in {"false", "0", "no", "off"}:
            return False
        return bool(self._state.state.commands_signed)

    @staticmethod
    def _parse_message(raw: str | bytes) -> dict[str, Any] | None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _ws_url(self) -> str:
        parsed = urlparse(self._config.backend_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        host = parsed.netloc
        base_path = parsed.path.rstrip("/")
        return f"{scheme}://{host}{base_path}/api/admin/system/nodes/stream"
