"""Top-level node agent service orchestration."""

from __future__ import annotations

import asyncio
import time
import logging
from pathlib import Path

from collections.abc import Callable
from typing import Any

import psutil
from httpx import HTTPStatusError
from websockets.exceptions import InvalidStatus

from faden_agent.backend_client import BackendClient
from faden_agent.config import AgentConfig
from faden_agent.container_log_forwarder import ContainerLogForwarder
from faden_agent.dispatcher import CommandDispatcher
from faden_agent.event_buffer import EventBuffer
from faden_agent.gpu import detect_gpu
from faden_agent.log_collector import LogCollector
from faden_agent.log_cursors import LogCursors
from faden_agent.loki_client import LokiClient
from faden_agent.ray_log_forwarder import RayServeLogForwarder
from faden_agent.image_loader import load_image_from_backend
from faden_agent.model_puller import pull_model
from faden_agent.model_tls import ModelTls
from faden_agent.policy_guard import PolicyGuard
from faden_agent.preflight import build_static_capabilities
from faden_agent.runtimes import detect_runtime
from faden_agent.runtime_manager import RuntimeManager
from faden_agent.server_trust import ServerTrust
from faden_agent.state_store import StateStore
from faden_agent.stream_client import StreamClient

log = logging.getLogger(__name__)

#: How often a connected agent asks which authority the backend's certificate
#: comes from: a new one has to be here before the backend switches to it.
TRUST_REFRESH_SEC = 60.0
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class NodeAgentService:
    """Bootstraps onboarding and runs persistent stream loop."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._state_store = StateStore(config.state_path)
        # Before the client: a move to HTTPS remembered from an earlier run
        # changes the address it is made for.
        self._trust = ServerTrust(config)
        self._client = BackendClient(config)
        self._stream: StreamClient | None = None
        self._loki: LokiClient | None = None
        # Held so the forwarders are never garbage-collected mid-run.
        self._log_tasks: list[asyncio.Task[None]] = []
        self._ray_manager: Any = None  # typed import is local (optional dep)
        self._model_tls: ModelTls | None = None

    async def run_forever(self) -> None:
        """Run agent forever with reconnect and re-enrollment handling."""
        runtime = detect_runtime(preferred=self._config.container_runtime)
        log.info("Detected container runtime: %s", runtime.name)

        gpu_collector = detect_gpu()
        log.info("Detected GPU collector: %s", gpu_collector.vendor)

        static_capabilities = await build_static_capabilities(
            runtime,
            gpu_collector,
            paths={
                "model_store": self._config.model_store_root,
                "ray_session": self._config.ray_session_dir,
            },
        )
        # This agent can put the model-TLS proxy in front of what it serves
        # (model_tls.py); the backend sends loopback binds only to agents that say so.
        static_capabilities["model_tls"] = 1
        # ... and run Ray with mutual TLS between the cluster's machines (ray/ray_tls.py).
        # 2: and say in each ENSURE_RAY_RUNTIME result what the container runs
        # (1 was a lab build that did not).
        static_capabilities["ray_tls"] = 2
        # ... and download a model from the Hub itself, and say whether it can
        # reach it (hf_download.py): a cluster's "machines download models".
        static_capabilities["model_download"] = 1
        # ... and keep a command running when its connection drops, deliver its
        # result on the next, and say on connecting what runs and what finished
        # (stream_client.py): the server then leaves them alone when it sees a
        # connection close, instead of failing them as lost.
        static_capabilities["command_continuity"] = 1
        # ... and drain a machine out of its Ray cluster, Serve moving its
        # copies first (ray/drain.py, serve_drain_patch.py).
        static_capabilities["ray_drain"] = 1
        # ... and change how many copies a model runs without restarting the
        # copies that run (ray/scale.py, SCALE_SERVE_APP).
        static_capabilities["serve_scale"] = 1
        log.info("Static capabilities: %s", static_capabilities)
        if not bool(static_capabilities.get("docker_available")):
            log.warning("Container runtime is not available; runtime commands will fail until daemon is reachable.")

        # Prime cpu_percent so first utilization report is non-zero
        psutil.cpu_percent()

        events = EventBuffer()

        async def _pull_model(
            *,
            model_sync: dict,
            emit_progress: Callable[[dict[str, Any]], Any] | None = None,
        ) -> None:
            """Pull model files from the backend using the node credential."""
            credential = self._state_store.state.credential
            if not credential:
                raise RuntimeError("No credential available for model pull.")
            await pull_model(
                client=self._client.http,
                credential=credential,
                model_sync=model_sync,
                model_store_root=self._config.model_store_root,
                emit_progress=emit_progress,
            )

        async def _download_model(
            *,
            model_sync: dict,
            emit_progress: Callable[[dict[str, Any]], Any] | None = None,
        ) -> dict[str, Any]:
            """Download a model from the Hub, with the server's token for a gated one."""
            from faden_agent import hf_download  # noqa: PLC0415

            credential = self._state_store.state.credential
            if not credential:
                raise RuntimeError("No credential available to ask the server for its Hugging Face token.")
            token = await hf_download.fetch_token(self._client.http, credential)
            repo = str(model_sync.get("hf_repo_id") or "")
            loop = asyncio.get_running_loop()
            last = [0.0]

            def _progress(done: int, total: int, _file: str) -> None:
                # Every ten seconds is plenty: each report is a row on the server.
                now = time.monotonic()
                if emit_progress is None or (now - last[0] < 10.0 and done < total):
                    return
                last[0] = now
                pct = round(done / total * 100, 1) if total else 0.0
                message = (
                    f"Downloading {repo} from Hugging Face: "
                    f"{done / 1e9:.1f} of {total / 1e9:.1f} GB ({pct}%)"
                )
                asyncio.run_coroutine_threadsafe(
                    emit_progress({"message": message, "progress_pct": pct, "bytes_done": done, "bytes_total": total}),
                    loop,
                )

            return await asyncio.to_thread(
                hf_download.download_model,
                hf_repo_id=repo,
                revision=model_sync.get("revision"),
                cache_dir=self._config.model_store_root,
                token=token,
                progress=_progress,
            )

        async def _load_image(
            *,
            image: str,
            emit_progress: Callable[[dict[str, Any]], Any] | None = None,
        ) -> None:
            """Stream image tarball from backend and load via runtime."""
            credential = self._state_store.state.credential
            if not credential:
                raise RuntimeError("No credential available for image transfer.")
            await load_image_from_backend(
                client=self._client.http,
                credential=credential,
                image=image,
                runtime=runtime,
                emit_progress=emit_progress,
            )

        async def _load_tls_image(*, image: str, expect_rootfs: str | None = None, **_: Any) -> None:
            credential = self._state_store.state.credential
            if not credential:
                raise RuntimeError("No credential available for image transfer.")
            await load_image_from_backend(
                client=self._client.http, credential=credential, image=image,
                runtime=runtime, expect_rootfs=expect_rootfs,
            )

        tls_dir = (
            Path(self._config.model_tls_dir)
            if self._config.model_tls_dir
            else Path(self._config.state_path).parent / "model-tls"
        )
        self._model_tls = ModelTls(
            runtime=runtime,
            directory=tls_dir,
            host_directory=self._config.model_tls_host_dir or None,
            image_loader=_load_tls_image,
        )

        async def _refresh_now() -> None:
            credential = self._state_store.state.credential
            if credential:
                await self._refresh_model_tls(credential)

        self._model_tls.refresher = _refresh_now

        from faden_agent.ray.manager import RayManager

        # Reuse the agent's configured backend HTTP client (TLS + base URL)
        # to fetch the decrypted Ray cluster token for each command.
        ray_manager = RayManager(
            state_store=self._state_store,
            events=events,
            http=self._client.http,
            image_download_dir=(
                Path(self._config.image_download_dir)
                if self._config.image_download_dir
                else Path(self._config.state_path).parent / "images"
            ),
        )
        self._ray_manager = ray_manager
        ray_manager.model_tls = self._model_tls
        from faden_agent.ray.ray_tls import RayTls  # noqa: PLC0415

        ray_manager.ray_tls = RayTls(
            base_dir=Path(self._config.state_path).parent / "ray-tls",
            http=self._client.http,
            credential=lambda: self._state_store.state.credential,
        )

        runtime_manager = RuntimeManager(
            runtime=runtime,
            state_store=self._state_store,
            events=events,
            advertise_host=self._config.advertise_host,
            advertise_scheme=self._config.advertise_scheme,
            model_store_root=self._config.model_store_root,
            model_puller=_pull_model,
            image_loader=_load_image,
            model_tls=self._model_tls,
            model_downloader=_download_model,
        )
        stream = StreamClient(
            config=self._config,
            runtime=runtime,
            state_store=self._state_store,
            dispatcher=None,  # type: ignore[arg-type]  # set below
            static_capabilities=static_capabilities,
            events=events,
            backend_client=self._client,
            gpu_collector=gpu_collector,
        )
        dispatcher = CommandDispatcher(
            state_store=self._state_store,
            runtime_manager=runtime_manager,
            policy_guard=PolicyGuard(image_allowlist=self._config.image_allowlist),
            events=events,
            on_refresh_inventory=stream.trigger_inventory,
            ray_manager=ray_manager,
        )
        stream._dispatcher = dispatcher
        self._stream = stream

        # Log forwarding to Loki. An explicit FADEN_NODE_AGENT_LOKI_URL wins;
        # otherwise the backend says where to send logs. The one-line install
        # writes no Loki address, so machines installed that way forwarded
        # nothing at all -- the logs page went silent for them without a word.
        if self._config.loki_url:
            self._start_log_forwarding(self._config.loki_url, runtime)
        else:
            self._log_tasks.append(
                asyncio.create_task(self._discover_log_sink(runtime), name="log_sink_discovery"),
            )

        self._log_tasks.append(asyncio.create_task(self._trust_loop(), name="server_trust"))

        backoff = self._config.reconnect_min_sec
        auth_fail_streak = 0
        _MAX_AUTH_RETRIES = 3
        # Backend restarts can take 10-30 s; use a generous floor so the
        # agent doesn't burn through all retries before the backend is back.
        _AUTH_RETRY_DELAY_SEC = 15.0
        while True:
            try:
                await self._ensure_credential(static_capabilities)
                credential = self._state_store.state.credential
                if not credential:
                    raise RuntimeError("Credential missing after enrollment flow.")
                await self._refresh_trust(credential)
                await stream.run(credential=credential)
                backoff = self._config.reconnect_min_sec
                auth_fail_streak = 0
            except InvalidStatus as exc:
                # 401/403 may be transient (e.g. backend restart).  Only
                # clear the credential after several consecutive failures
                # to avoid a single blip bricking the agent.
                status = exc.response.status_code
                if status in _REDIRECT_STATUSES and await self._follow_to_https():
                    backoff = self._config.reconnect_min_sec
                    continue
                if status in {401, 403}:
                    auth_fail_streak += 1
                    if auth_fail_streak >= _MAX_AUTH_RETRIES:
                        log.warning(
                            "Stream auth rejected (%s) %d times in a row — "
                            "clearing credential.",
                            status,
                            auth_fail_streak,
                        )
                        self._state_store.state.credential = None
                        self._state_store.save()
                        auth_fail_streak = 0
                    else:
                        delay = _AUTH_RETRY_DELAY_SEC * auth_fail_streak
                        log.warning(
                            "Stream auth rejected (%s), attempt %d/%d — "
                            "will retry with same credential in %.0f s.",
                            status,
                            auth_fail_streak,
                            _MAX_AUTH_RETRIES,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                else:
                    log.warning("Stream rejected with status=%s", status)
            except RuntimeError as exc:
                # Terminal errors (enrollment token consumed, no credential)
                # should not be silently retried forever.
                log.error("Fatal agent error: %s", exc)
                raise
            except Exception:
                log.exception("Node stream loop failed.")
                if await self._follow_to_https():
                    backoff = self._config.reconnect_min_sec
                    continue

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._config.reconnect_max_sec)

    async def _refresh_trust(self, credential: str) -> None:
        """Learn the backend's authority; never in the way of connecting."""
        try:
            await self._trust.refresh(self._client.http, credential)
        except Exception:  # noqa: BLE001 - an old backend, or a blip: the next round asks again
            log.debug("Could not ask the backend which authority to trust.", exc_info=True)
        await self._refresh_model_tls(credential)

    async def _refresh_model_tls(self, credential: str) -> None:
        """Keep the model-TLS certificate current, and the proxy with it."""
        if self._model_tls is None:
            return
        try:
            await self._model_tls.refresh(self._client.http, credential)
        except Exception as exc:  # noqa: BLE001 - the next round asks again
            self._model_tls.last_error = str(exc)[-500:]
            log.warning("Model TLS refresh failed: %s", exc)

    async def _trust_loop(self) -> None:
        while True:
            await asyncio.sleep(TRUST_REFRESH_SEC)
            credential = self._state_store.state.credential
            if credential:
                await self._refresh_trust(credential)

    async def _follow_to_https(self) -> bool:
        """Follow the backend to HTTPS when its plain-HTTP address redirects there."""
        target = await self._trust.follow_to_https()
        if target is None:
            return False
        # The same client object is shared (model pulls, image loads, Ray),
        # so moving its base URL moves all of them.
        self._client.http.base_url = target
        return True

    async def close(self) -> None:
        """Close outbound HTTP resources, SDK attach, and flush state."""
        self._state_store.flush_seq()
        if self._ray_manager is not None:
            try:
                await self._ray_manager.close()
            except Exception:  # pragma: no cover - best effort teardown
                log.debug("ray manager close failed", exc_info=True)
        await self._client.close()
        if self._loki:
            await self._loki.close()

    def _start_log_forwarding(self, loki_url: str, runtime: Any) -> None:
        """Push system, container and Ray Serve logs to *loki_url*."""
        # The ``host`` label has to be the identity the *backend* knows
        # this node by, because that is what the UI queries with.  It
        # records ``advertise_host`` (enrolment replaces a bare hostname
        # with the address the request came from), so labelling
        # with ``config.host`` -- the local hostname -- produced logs that
        # were shipped, stored, and impossible to find.
        # A path is the backend's own address for pushes (log-sink?via=backend):
        # they go over this agent's client, with its credential.
        via_backend = loki_url.startswith("/")
        loki_client = LokiClient(
            loki_url=loki_url,
            http=self._client.http if via_backend else None,
            credential=(lambda: self._state_store.state.credential) if via_backend else None,
            labels={
                "job": "node-agent",
                "host": self._config.advertise_host,
                # The operator's name for the machine, for hand-written
                # queries; the UI matches on ``host``.
                "agent_id": self._config.agent_id,
                "container": f"node-{self._config.agent_id}",
            },
            verify_tls=self._config.verify_tls,
            ca_bundle=self._config.tls_ca_bundle,
        )
        self._loki = loki_client
        collector = LogCollector(max_lines=self._config.log_batch_size)
        # Where each shipper has got to, kept across restarts (log_cursors).
        cursors = LogCursors(self._config.state_path.parent / "log-cursors.json")
        self._log_tasks.append(
            asyncio.create_task(self._log_push_loop(collector, loki_client, cursors), name="log_push"),
        )
        # Container log forwarding — tails docker logs for tracked workloads
        self._log_tasks.append(
            asyncio.create_task(
                ContainerLogForwarder(
                    runtime=runtime,
                    state_store=self._state_store,
                    loki=loki_client,
                    host=self._config.advertise_host,
                    interval_sec=self._config.log_flush_interval_sec,
                    cursors=cursors,
                ).run_forever(),
                name="container_log_forwarder",
            ),
        )
        # Ray Serve replica logs.  A separate forwarder because they are
        # files rather than a container's console -- the runtime container
        # idles on ``sleep infinity`` and every replica writes its own
        # file inside it, so ``docker logs`` on it is permanently empty.
        self._log_tasks.append(
            asyncio.create_task(
                RayServeLogForwarder(
                    loki=loki_client,
                    host=self._config.advertise_host,
                    session_dir=self._config.ray_session_dir,
                    interval_sec=self._config.log_flush_interval_sec,
                    cursors=cursors,
                ).run_forever(),
                name="ray_serve_log_forwarder",
            ),
        )
        log.info("System log collection enabled → %s", loki_url)

    async def _discover_log_sink(self, runtime: Any) -> None:
        """Ask the backend where to send logs, until it answers.

        Waits for a credential (a fresh install is still joining) and retries
        while the backend is unreachable, backing off to five minutes. A
        backend that offers no sink -- or predates the question -- ends it.
        """
        delay = 5.0
        while True:
            credential = self._state_store.state.credential
            if credential:
                try:
                    sink = await self._client.log_sink(credential=credential)
                except Exception:  # noqa: BLE001 - unreachable now is not "no sink"
                    log.debug("Could not ask the backend for a log sink yet.", exc_info=True)
                else:
                    if sink:
                        self._start_log_forwarding(sink, runtime)
                    else:
                        log.info("The backend offers no log sink — system log collection disabled.")
                    return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300.0)

    async def _log_push_loop(
        self,
        collector: LogCollector,
        loki: LokiClient,
        cursors: LogCursors,
    ) -> None:
        """Collect system logs and push to Loki on a timer.

        The journal position moves only once Loki took the entries; a refused
        push reads the same entries again next time.
        """
        interval = max(self._config.log_flush_interval_sec, 2)
        position = cursors.get("journal")
        while True:
            try:
                entries, proposed = await collector.collect(after=position)
                if await loki.push(entries) and proposed and proposed != position:
                    position = proposed
                    cursors.set("journal", position)
                    cursors.save()
            except Exception:
                log.warning("Log push cycle failed.", exc_info=True)
            await asyncio.sleep(interval)

    async def _ensure_credential(self, capabilities: dict) -> None:
        if self._state_store.state.credential:
            if self._config.enrollment_token:
                log.warning(
                    "Enrollment token is still configured after enrollment — consider removing it from env."
                )
            return
        token = self._config.enrollment_token
        if not token:
            raise RuntimeError("No credential in state and no enrollment token configured.")
        log.info("Enrolling node agent '%s' at backend.", self._config.agent_id)
        try:
            payload = await self._client.enroll(
                enrollment_token=token,
                agent_id=self._config.agent_id,
                host=self._config.host,
                capabilities=capabilities,
                version="0.1.0",
            )
        except HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise RuntimeError(
                    "Enrollment token rejected (401). The token was likely already "
                    "consumed or has expired. Generate a new enrollment token in "
                    "the admin UI and update FADEN_NODE_AGENT_ENROLLMENT_TOKEN."
                ) from exc
            raise
        credential = payload.get("credential")
        node_id = payload.get("node_id")
        if not isinstance(credential, str) or not credential:
            raise RuntimeError("Enrollment succeeded but credential is missing.")
        self._state_store.state.credential = credential
        if isinstance(node_id, str) and node_id:
            self._state_store.state.node_id = node_id
        self._state_store.save()
        log.info("Enrollment complete. node_id=%s", self._state_store.state.node_id)
