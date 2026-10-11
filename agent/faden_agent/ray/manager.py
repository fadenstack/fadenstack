"""High-level coordinator for Ray lifecycle on a physical node (facade).

Spec section 2 target layout, implemented:

* :class:`~faden_agent.ray.runtime.RayRuntime` — bootstrap/process,
  the ONLY CLI layer (``ray start`` / ``ray stop``).
* :class:`~faden_agent.ray.core.RayCoreClient` — live cluster state
  via Ray Core Python APIs (GCS-direct; no Dashboard).
* :class:`~faden_agent.ray.serve.RayServeManager` — Serve
  lifecycle/status via the ``ray.serve`` Python API.
* :class:`~faden_agent.ray.metrics.RayMetricsDiscovery` — Tier C
  Prometheus scrape targets discovered from ``ray.nodes()``.
* :class:`~faden_agent.ray.state.RayStateDiagnostics` — Tier B
  optional ``ray.util.state`` (Dashboard-dependent; never gates health).

``RayManager`` keeps the exact same public surface the dispatcher already
calls (``ensure_runtime`` / ``start_head`` / ``join_cluster`` /
``leave_cluster`` / ``stop_ray`` / ``get_status``), plus ``get_serve_status``.
``get_status`` answers ``GET_RAY_STATUS`` with the enriched
:class:`~faden_agent.ray.models.RayEnvironmentStatus` (flat fields
preserved for the existing backend parser; tiers additive and non-gating).
"""

import asyncio
import logging
import os
import stat
from pathlib import Path
from typing import Any

import httpx

from faden_agent.event_buffer import EventBuffer
from faden_agent.ray import errors
from faden_agent.ray.container import (
    RayContainerRuntime,
    RuntimeBundleSpec,
)
from faden_agent.ray.core import RayCoreClient
from faden_agent.ray.metrics import RayMetricsDiscovery
from faden_agent.ray.runtime import RayRuntime
from faden_agent.ray.schemas import (
    DeleteServeAppPayload,
    EnsureRayRuntimePayload,
    JoinRayClusterPayload,
    RunServeAppPayload,
    StartRayHeadPayload,
    StopRayPayload,
    GetRayServeStatusPayload,
    GetRayStatusPayload,
)
from faden_agent.ray.serve import RayServeManager
from faden_agent.runtimes import ContainerRuntimeError
from faden_agent.ray.state import RayStateDiagnostics
from faden_agent.state_store import StateStore

log = logging.getLogger(__name__)

_SECRET_ENDPOINT = "/api/admin/system/nodes/secrets/"
_DEFAULT_VERSION = "2.58.0"


def _writable_token_dir(preferred: Path) -> Path:
    """The first directory this agent can actually write its cluster token in.

    The fallback used to be a fixed ``/tmp/faden/ray``, shared by every
    user on the machine, and "usable" meant ``mkdir(exist_ok=True)`` did not
    raise -- which it does not for a directory that already exists, whoever
    owns it. On a DGX node that directory had been created by Docker, as
    root, during an earlier run; an agent running as a user service picked
    it, and ``start_ray_head`` then failed on ``Permission denied`` writing
    the token. So: check the directory is writable, and fall back only to
    places that belong to this user.
    """
    candidates = [preferred]
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        candidates.append(Path(runtime_dir) / "faden-agent" / "ray")
    candidates.append(Path.home() / ".local" / "share" / "faden-agent" / "ray")
    uid = os.getuid() if hasattr(os, "getuid") else 0
    candidates.append(Path(f"/tmp/faden-{uid}/ray"))

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(candidate, os.W_OK):
            return candidate
    return candidates[-1]


class RayManager:
    """Coordinates Ray lifecycle commands from the backend."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        events: EventBuffer,
        ray_base_path: str = "/opt/faden/ray",
        token_dir: Path | str = "/var/run/faden/ray",
        backend_url: str = "http://127.0.0.1:8000",
        token_ttl_sec: int = 300,
        http: httpx.AsyncClient | None = None,
        image_download_dir: Path | str | None = None,
    ) -> None:
        self._state = state_store
        self._image_dir = Path(image_download_dir) if image_download_dir else None
        self._events = events
        # Process/bootstrap layer.  Aliased as ``_process`` (the historical
        # attribute name) so existing code/tests that swap ``manager._process``
        # with a mock keep working; it is now a :class:`RayRuntime`.
        self._process = RayRuntime(ray_base_path=ray_base_path)
        self._runtime = self._process
        token_env = os.getenv("FADEN_NODE_AGENT_RAY_TOKEN_DIR")
        self._token_dir = Path(token_env) if token_env else _writable_token_dir(Path(token_dir))
        # Claim the directory now, whichever branch chose it.  The runtime
        # container bind-mounts this path, and Docker creates a missing
        # bind-mount source as **root** -- which happens during
        # ENSURE_RUNTIME_IMAGE, long before the first token write.  An agent
        # running as an unprivileged user could then never write its own
        # cluster token, and `ray start` failed inside the container with an
        # opaque "token file cannot be opened or is empty".
        try:
            self._token_dir.mkdir(parents=True, exist_ok=True)
        except OSError:  # noqa: BLE001 - a read-only path is reported at write time
            pass
        self._token_file = self._token_dir / "cluster.token"
        self._container = RayContainerRuntime(token_path=str(self._token_file))
        # The agent's own SDK attach must present the cluster token.  Ray
        # resolves its auth mode in native code when the SDK is first loaded,
        # so setting these just before ``ray.init`` is ignored (verified live:
        # InvalidAuthToken) — they must be in the process env before anything
        # imports ``ray``, i.e. now.  The file itself only has to exist by
        # attach time (START_RAY_HEAD / JOIN_RAY_CLUSTER write it).
        os.environ.setdefault("RAY_AUTH_MODE", "token")
        os.environ.setdefault("RAY_AUTH_TOKEN_PATH", str(self._token_file))
        self._core = RayCoreClient(token_path=self._token_file)
        self._serve = RayServeManager(core=self._core)
        #: The machine's model-TLS proxy (``model_tls.ModelTls``), set by the service.
        self.model_tls: Any = None
        #: Issues this machine's Ray TLS certificates (``ray.ray_tls.RayTls``), set by the service.
        self.ray_tls: Any = None
        self._metrics = RayMetricsDiscovery(core=self._core)
        self._state_diag = RayStateDiagnostics(core=self._core)
        self._backend_url = backend_url.rstrip("/")
        self._token_ttl_sec = token_ttl_sec
        # Owned http client (created lazily) vs injected (caller owns close).
        self._http_injected = http
        self._http: httpx.AsyncClient | None = http
        self._token_ref: str | None = None
        self._token_written_at: float = 0.0

    async def close(self) -> None:
        """Release the owned HTTP client, if any."""
        if self._http is not None and self._http_injected is None:
            await self._http.aclose()
            self._http = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self._backend_url, timeout=30.0)
        return self._http

    # ------------------------------------------------------------------
    # Token delivery
    # ------------------------------------------------------------------

    def _token_is_fresh(self, credential_ref: str) -> bool:
        import time

        if self._token_ref != credential_ref:
            return False
        try:
            if not self._token_file.exists() or not self._token_file.read_bytes().strip():
                return False
        except OSError:
            return False
        return (time.monotonic() - self._token_written_at) < self._token_ttl_sec

    async def _write_token_securely(self, payload: dict[str, Any]) -> None:
        """Fetch the decrypted cluster token from the backend and write it to a 0600 file.

        Raises:
            ValueError: payload lacks a ``credential_ref``.
            RuntimeError: no node credential recorded, or the backend
                refused to serve the token.
        """
        import time

        credential_ref = payload.get("credential_ref")
        if not credential_ref:
            raise ValueError("Missing credential_ref in payload")

        credential_ref = str(credential_ref)
        if self._token_is_fresh(credential_ref):
            return

        credential = self._state.state.credential
        if not credential:
            raise RuntimeError("Node credential not recorded; cannot fetch cluster token")

        client = self._http_client()
        try:
            response = await client.get(
                _SECRET_ENDPOINT + credential_ref,
                headers={"Authorization": f"Bearer {credential}"},
            )
        except Exception as exc:
            raise RuntimeError(f"Cluster token fetch failed: {exc}") from exc
        if response.status_code != 200:
            raise RuntimeError(f"Cluster token fetch failed (HTTP {response.status_code})")
        data = response.json()
        token = (data or {}).get("token")
        if not token:
            raise RuntimeError("Cluster token fetch returned an empty token")

        self._token_dir.mkdir(parents=True, exist_ok=True)
        # The file is the whole cluster's auth token: owner read/write only.
        fd = os.open(
            str(self._token_file),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            os.write(fd, token.encode("utf-8"))
        finally:
            os.close(fd)
        self._token_ref = credential_ref
        self._token_written_at = time.monotonic()

    # Auth is the agent's to decide; a bundle or environment must not be able
    # to point ``ray start`` at a different token or turn auth off.
    _RESERVED_ENV_KEYS = frozenset({"RAY_AUTH_MODE", "RAY_AUTH_TOKEN_PATH", "CUDA_VISIBLE_DEVICES"})
    _ALLOWED_ENV_PREFIXES = ("VLLM_", "HF_", "NCCL_", "CUDA_", "RAY_", "UCX_")

    def start_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        """Env for ``ray start``: token auth on, token supplied via file path.

        ``RAY_*`` is allowed through (minus the two auth keys) because the
        bundle's certified platform tuning lives there — the GB10 workaround is
        ``RAY_memory_monitor_refresh_ms=0``, and filtering the whole prefix out
        meant the one setting certification proved necessary never reached
        ``ray start``.
        """
        env = {"RAY_AUTH_MODE": "token"}
        env["RAY_AUTH_TOKEN_PATH"] = str(self._token_file)
        if extra_env:
            for k, v in extra_env.items():
                if k.startswith(self._ALLOWED_ENV_PREFIXES) and k not in self._RESERVED_ENV_KEYS:
                    env[k] = str(v)
        return env

    async def _use_container(self, payload: dict[str, Any]) -> bool:
        """Should this command be served from the runtime container?

        The backend decides, by putting ``runtime_bundle`` on the command; the
        running-container check is only a sanity assertion on top of that.
        Inferring the mode from local state instead would let a leftover
        container from a previous environment hijack a host-based one, and
        would silently fall back to the host SDK - which on a certified node
        has no Ray at all - whenever the container happened to be restarting.
        """
        bundle = self._bundle_spec(payload)
        if bundle is None:
            return False
        if await self._container.is_running(bundle.name):
            return True
        raise RuntimeError(
            f"Runtime container {bundle.name!r} is not running; "
            "cannot serve this command from the pinned runtime."
        )

    @staticmethod
    def _bundle_spec(payload: dict[str, Any]) -> RuntimeBundleSpec | None:
        """Parse the optional runtime bundle carried by a lifecycle command.

        Its presence is what selects the containerized bootstrap path; without
        it the manager keeps using the host Ray distribution (the Phase 2/3
        behaviour, still valid on nodes that have one).
        """
        raw = payload.get("runtime_bundle")
        if not isinstance(raw, dict) or not raw:
            return None
        return RuntimeBundleSpec.from_payload(raw)

    async def ensure_runtime_image(self, payload: dict[str, Any], emit_progress: Any = None) -> dict[str, Any]:
        """``ENSURE_RUNTIME_IMAGE``: make the pinned OCI image present, verified.

        Never reaches a public registry: the image is either already on the
        node under its pinned identity, or it is streamed from the backend's
        own image endpoint and loaded locally.
        """
        spec = self._bundle_spec(payload)
        if spec is None:
            raise RuntimeError("ensure_runtime_image requires a runtime_bundle payload")
        # Where a missing image comes from: the backend by default, a cluster
        # peer when the backend names one, or nowhere at all when it only
        # wants to know whether the image is here (``fetch: false``) -- which
        # is how it finds out which machines can seed the others.
        source = payload.get("source") if isinstance(payload.get("source"), dict) else None
        if payload.get("fetch") is False:
            loader = None
        elif source and source.get("peer_url"):
            loader = self._peer_loader(source, emit_progress)
        else:
            loader = self._image_loader(emit_progress)
        result = await self._container.ensure_image(
            spec, loader=loader, emit_progress=emit_progress,
        )
        if payload.get("ensure_container"):
            result.update(await self._container.ensure_container(spec, ray_tls=self._ray_tls_for(payload)))
        return result

    def _ray_tls_for(self, payload: dict[str, Any]) -> Any:
        """``None`` (not said), ``False`` (off) or the material to run Ray with mutual TLS."""
        if "ray_tls" not in payload:
            return None
        wanted = payload.get("ray_tls")
        if not wanted:
            return False
        if self.ray_tls is None:
            raise ContainerRuntimeError("this agent cannot run Ray with TLS")
        return self.ray_tls.material(str(wanted["environment_id"]))

    def _image_loader(self, emit_progress: Any = None) -> Any:
        """Loader that fetches the pinned image from the backend (air-gap path).

        Passes the pin, so a server holding a different build refuses before
        sending anything, and the progress callback, which it used to drop --
        the transfer reported progress to nobody, and the cluster page said
        "Preparing 1 machine" for eleven minutes.
        """

        async def _load(spec: RuntimeBundleSpec) -> None:
            from faden_agent.image_loader import load_image_from_backend
            from faden_agent.runtimes import detect_runtime

            credential = self._state.state.credential
            if not credential:
                raise RuntimeError(
                    "Node credential not recorded; cannot stream the runtime image"
                )
            await load_image_from_backend(
                client=self._http_client(),
                credential=credential,
                image=spec.image,
                runtime=detect_runtime(preferred=spec.runtime_handler),
                emit_progress=emit_progress,
                expect_id=spec.digest,
                expect_rootfs=spec.rootfs_digest,
                download_dir=self._image_download_dir(),
            )

        return _load

    def _peer_loader(self, source: dict[str, Any], emit_progress: Any = None) -> Any:
        """Loader that fetches the pinned image from a cluster peer."""

        async def _load(spec: RuntimeBundleSpec) -> None:
            from faden_agent.image_seed import fetch_from_peer
            from faden_agent.runtimes import detect_runtime

            await fetch_from_peer(
                url=str(source["peer_url"]),
                token=str(source.get("token") or ""),
                image=spec.image,
                runtime=detect_runtime(preferred=spec.runtime_handler),
                download_dir=self._image_dir or Path("/tmp/faden-agent-images"),
                emit_progress=emit_progress,
            )

        return _load

    async def serve_runtime_image(
        self, payload: dict[str, Any], emit_progress: Any = None
    ) -> dict[str, Any]:
        """``SERVE_RUNTIME_IMAGE``: hand this node's runtime image to its peers.

        Serves only an image that matches the pin: verified here first, with
        no loader, so a node that does not hold the right build refuses
        instead of passing a wrong one along.
        """
        from faden_agent.image_seed import serve_image

        spec = self._bundle_spec(payload)
        if spec is None:
            raise RuntimeError("serve_runtime_image requires a runtime_bundle payload")
        await self._container.ensure_image(spec, loader=None)
        return await serve_image(
            image=spec.image,
            bind_ip=str(payload["bind_ip"]),
            port=int(payload["port"]),
            token=str(payload["token"]),
            expected_peers=int(payload.get("expected_peers") or 1),
            timeout_sec=float(payload.get("timeout_sec") or 3600),
            work_dir=self._image_dir or Path("/tmp/faden-agent-images"),
            emit_progress=emit_progress,
        )

    def _image_download_dir(self) -> Path | None:
        """Where image tars land before loading. ``None`` streams, unresumable."""
        return self._image_dir

    async def ensure_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Check that a usable Ray runtime is available for the requested version.

        With a runtime bundle the check runs against the **container**: the
        image must be present under its pinned digest and its ``ray`` CLI must
        answer.  That is the Phase 4B contract — a node with no host Ray
        package still reports ``installed=True``.

        Without a bundle this falls back to the host CLI: the agent packages
        the matching SDK, so the interesting facts are the CLI location plus
        the packaged SDK version (same wheel, parity by construction).  A
        missing CLI is reported as ``installed=False``: bootstrap must use a
        verified binary, never whatever ``ray`` happens to be on PATH first.
        """
        bundle = self._bundle_spec(payload)
        if bundle is not None:
            return await self._ensure_runtime_container(payload, bundle)

        spec = EnsureRayRuntimePayload.model_validate(payload)
        ray_bin = self._runtime.ray_binary_path(spec.version)
        installed = ray_bin.exists()
        # Detect the Serve-LLM stack from installed distribution metadata
        # instead of importing it: importing vLLM/torch in the agent process
        # takes seconds on every ENSURE (blocking the command loop) and loads
        # CUDA libraries the agent never uses.
        vllm_version = _dist_version("vllm")
        torch_version = _dist_version("torch")
        serve_llm_available = bool(vllm_version and _dist_version("pyarrow"))

        return {
            "installed": installed,
            "version": spec.version,
            "path": str(ray_bin.parent.parent),
            "cli_path": str(ray_bin),
            "sdk_version": _sdk_version(),
            "serve_llm_available": serve_llm_available,
            "vllm_version": vllm_version,
            "torch_version": torch_version,
        }

    async def _ensure_runtime_container(
        self, payload: dict[str, Any], bundle: RuntimeBundleSpec
    ) -> dict[str, Any]:
        """Satisfy ``ENSURE_RAY_RUNTIME`` from the pinned container."""
        spec = EnsureRayRuntimePayload.model_validate(
            {k: v for k, v in payload.items() if k in {"version"}}
        )
        try:
            image = await self._container.ensure_image(bundle, loader=self._image_loader())
            ensured = await self._container.ensure_container(bundle, ray_tls=self._ray_tls_for(payload))
            version = await self._container.installed_version(bundle)
        except (ContainerRuntimeError, ValueError) as exc:
            return {
                "installed": False,
                "version": spec.version,
                "runtime": "container",
                "container": bundle.name,
                "image": bundle.image,
                "error": str(exc),
            }
        return {
            "installed": version is not None,
            "version": version or spec.version,
            "requested_version": spec.version,
            "version_match": version == spec.version if version else False,
            "runtime": "container",
            "container": bundle.name,
            "image": bundle.image,
            "image_id": image.get("image_id"),
            "digest_verified": bool(image.get("verified")),
            # The certified image ships the full Serve-LLM stack; that is the
            # bundle's contract, not something to re-derive from host metadata.
            "serve_llm_available": True,
            **({"ray_tls": ensured["ray_tls"]} if "ray_tls" in (ensured or {}) else {}),
        }

    async def start_head(self, payload: dict[str, Any], emit_progress: Any) -> dict[str, Any]:
        """Start a Ray head node (CLI bootstrap).

        The Dashboard is optional (payload ``include_dashboard``): when
        False the node boots GCS + raylet only, which is all the SDK status
        path needs.
        """
        spec = StartRayHeadPayload.model_validate(payload)
        await emit_progress(
            {"phase": "starting_head", "message": f"Starting Ray head on port {spec.port}"}
        )

        # Ensure a live, 0600 cluster token file exists (fetch + write).
        await self._write_token_securely(payload)

        bundle = self._bundle_spec(payload)
        if bundle is not None:
            await self._container.ensure_image(bundle, loader=self._image_loader())
            await self._container.ensure_container(bundle, ray_tls=self._ray_tls_for(payload))
            try:
                result = await self._container.start_head(
                    bundle,
                    port=spec.port,
                    dashboard_port=spec.dashboard_port,
                    dashboard_host=spec.dashboard_host,
                    node_ip_address=spec.node_ip_address,
                    num_cpus=spec.num_cpus,
                    num_gpus=spec.num_gpus,
                    include_dashboard=spec.include_dashboard,
                    env=self.start_env(spec.env),
                )
            except ContainerRuntimeError as exc:
                raise RuntimeError(f"Ray head start failed in container: {exc}") from None
            # The Serve drain patch in place before Serve starts on this head,
            # so its controller can move copies off draining machines from
            # the start. Never worth failing the start over.
            try:
                from faden_agent.ray import drain  # noqa: PLC0415

                await self._container.run_python(
                    drain.enable_script(wait_sec=0, restart=False), bundle.name, timeout_sec=60,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not put the Serve drain patch in place: %s", exc)
            return {
                "cluster_address": result.get("cluster_address"),
                "head_address": result.get("head_address"),
                "dashboard_url": result.get("dashboard_url"),
                "runtime": "container",
                "container": bundle.name,
            }

        try:
            result = await self._runtime.start_head(
                version=spec.version,
                port=spec.port,
                dashboard_port=spec.dashboard_port,
                dashboard_host=spec.dashboard_host,
                node_ip_address=spec.node_ip_address,
                num_cpus=spec.num_cpus,
                num_gpus=spec.num_gpus,
                include_dashboard=spec.include_dashboard,
                env=self.start_env(spec.env),
            )
        except errors.RayRuntimeError:
            last = self._runtime.last_error
            raise RuntimeError(
                f"Ray head start failed (version {spec.version}): "
                f"{last.stderr if last else 'launch error'}"
            ) from None

        resolved_host = spec.node_ip_address or spec.dashboard_host
        return {
            "cluster_address": result.get("cluster_address", f"{resolved_host}:{spec.port}"),
            "head_address": result.get("head_address", f"{resolved_host}:{spec.port}"),
            "dashboard_url": result.get("dashboard_url"),
        }

    async def join_cluster(self, payload: dict[str, Any], emit_progress: Any) -> dict[str, Any]:
        """Join a Ray cluster as a worker (CLI bootstrap)."""
        spec = JoinRayClusterPayload.model_validate(payload)
        await emit_progress(
            {"phase": "joining_cluster", "message": f"Joining Ray cluster at {spec.head_address}"}
        )

        # Workers must hold the same token so the head's GCS accepts them.
        await self._write_token_securely(payload)

        bundle = self._bundle_spec(payload)
        if bundle is not None:
            await self._container.ensure_image(bundle, loader=self._image_loader())
            await self._container.ensure_container(bundle, ray_tls=self._ray_tls_for(payload))
            try:
                await self._container.join_cluster(
                    bundle,
                    head_address=spec.head_address,
                    node_ip_address=spec.node_ip_address,
                    num_cpus=spec.num_cpus,
                    num_gpus=spec.num_gpus,
                    env=self.start_env(spec.env),
                )
            except ContainerRuntimeError as exc:
                raise RuntimeError(f"Ray join failed in container: {exc}") from None
            return {
                "joined": True,
                "head_address": spec.head_address,
                "runtime": "container",
                "container": bundle.name,
            }

        try:
            result = await self._runtime.join_cluster(
                version=spec.version,
                head_address=spec.head_address,
                node_ip_address=spec.node_ip_address,
                num_cpus=spec.num_cpus,
                num_gpus=spec.num_gpus,
                env=self.start_env(spec.env),
            )
        except errors.RayRuntimeError:
            last = self._runtime.last_error
            raise RuntimeError(
                f"Ray join failed (version {spec.version}): "
                f"{last.stderr if last else 'launch error'}"
            ) from None

        return {"joined": True, "head_address": spec.head_address}

    async def leave_cluster(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stop Ray processes on this node (leave the cluster)."""
        version = payload.get("version") or _DEFAULT_VERSION
        try:
            self._core.disconnect()  # a stale attach must not outlive the cluster
        except Exception:  # pragma: no cover - best effort
            pass
        bundle = self._bundle_spec(payload)
        if bundle is not None:
            routes = await self._serve_routes(bundle)
            result = await self._container.stop(bundle, remove=True)
            await self._drop_serve_routes(routes)
            return {"left": True, "runtime": "container", **result, **({"model_tls_removed": routes} if routes else {})}
        await self._runtime.stop(version=version)
        return {"left": True}

    async def stop_ray(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Force stop Ray processes."""
        spec = StopRayPayload.model_validate(payload)
        try:
            self._core.disconnect()
        except Exception:  # pragma: no cover - best effort
            pass
        bundle = self._bundle_spec(payload)
        if bundle is not None:
            routes = await self._serve_routes(bundle)
            result = await self._container.stop(bundle, force=spec.force, remove=True)
            await self._drop_serve_routes(routes)
        else:
            result = await self._runtime.stop(version=spec.version, force=spec.force)

        if self._token_file.exists():
            self._token_file.unlink()
        self._token_ref = None
        self._token_written_at = 0.0

        return {"stopped": result.get("stopped", False) or result.get("best_effort", False)}

    # ------------------------------------------------------------------
    # Status (SDK-first, Dashboard-independent)
    # ------------------------------------------------------------------

    async def get_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Cluster status via the Ray Core Python APIs (GCS-direct).

        Works with the Dashboard component disabled.  The result is the
        enriched :class:`RayEnvironmentStatus` dict: flat fields preserved
        for the existing backend parser, with ``serve`` / ``metrics`` /
        ``state`` tiers additive and non-gating (an unhealthy tier never
        flips overall cluster health).
        """
        spec = GetRayStatusPayload.model_validate(payload)
        if spec.credential_ref:
            try:
                await self._ensure_token({"credential_ref": spec.credential_ref})
            except Exception as e:
                log.warning("Could not ensure token for get_status: %s", e)

        # A pinned runtime that is not running here is an answer to "is Ray
        # up?": it is not. Raising instead made the backend's health check
        # read "no answer" and never re-form the cluster, so a removed
        # container -- an agent moved to a new name, a `docker rm` -- left the
        # cluster down until someone restarted it by hand. Still no fallback
        # to the host SDK: the reply says why, not "dead cluster".
        bundle = self._bundle_spec(payload)
        if bundle is not None and not await self._container.is_running(bundle.name):
            return {"alive": False, "version": None, "num_nodes": 0, "nodes": [],
                    "runtime_running": False, "runtime_container": bundle.name}

        # Containerized runtime: every Ray-aware call goes through the
        # in-container helper, with the same tiers the host path reports.
        if await self._use_container(payload):
            bundle = self._bundle_spec(payload)
            return await self._container.get_cluster_status(
                bundle.name,
                include_serve=spec.include_serve,
                include_metrics=spec.include_metrics,
                include_state=spec.include_state,
                expected_version=spec.expected_version,
            )

        # Attach + probe are short synchronous GCS round-trips; run them off
        # the event loop so a stuck GCS connection cannot wedge the agent.
        status = await asyncio.to_thread(
            self._core.probe, expected_version=spec.expected_version
        )

        # Additive Serve tier (never fails overall health).
        if spec.include_serve:
            serve = await asyncio.to_thread(self._serve.status)
            status.serve = serve
            status.capabilities.serve = serve and serve.available

        # Additive Tier C metrics targets (discovered from ``ray.nodes()``).
        if spec.include_metrics:
            status.metrics = await asyncio.to_thread(self._metrics.discover)
            status.capabilities.metrics = status.metrics.enabled

        # Optional Tier B state diagnostics (Dashboard-dependent).
        if spec.include_state:
            status.state = await asyncio.to_thread(self._state_diag.availability)
            status.capabilities.state = status.state is not None and status.state.available

        return status.model_dump(mode="json")

    async def run_serve_app(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deploy (or update) a named LLM Serve application (Phase 3).

        The head node builds the ingress application *in-process* with
        ``ray.serve.llm.build_openai_app`` and runs it under an explicit
        application name — Ray's per-application update path.  The deploy is
        non-blocking: convergence is observed through the serve status tier.
        """
        spec = RunServeAppPayload.model_validate(payload)
        if await self._use_container(payload):
            bundle = self._bundle_spec(payload)
            http_options = dict(spec.http_options or {})
            tls = await self._model_tls_for_serve(bundle.name, spec.model_tls, http_options)
            result = await self._container.run_serve_app(
                bundle.name,
                spec.app_name,
                spec.llm_serving_args,
                http_options=http_options,
                # One prefix per application, matching the host path.  The
                # container path defaulted to "/", so the address the backend
                # published (which assumes "/<app>") 404'd, and a second
                # deployment on the same cluster would have collided with the
                # first over the root prefix.
                route_prefix=f"/{spec.app_name}",
            )
            if tls is not None:
                result = {**result, **tls}
            return result

        # build_openai_app + serve.run are driver-side Python API calls that
        # do GCS round-trips; run off the event loop.
        return await asyncio.to_thread(
            self._serve.run_app,
            spec.app_name,
            spec.llm_serving_args,
            proxy_location=spec.proxy_location,
            http_options=spec.http_options,
        )

    async def scale_serve_app(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Change how many copies an app's model runs, the running ones left alone (``ray/scale.py``).

        Answers ``scaled: false`` with a ``reason`` when the app cannot be
        scaled in place (deployed without Ray's external scaler, or gone): the
        server deploys it again instead.
        """
        app_name = str(payload.get("app_name") or "")
        replicas = payload.get("replicas")
        if not app_name or not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 0:
            raise ValueError("scale_serve_app needs app_name and a replica count")
        if not await self._use_container(payload):
            return {"scaled": False, "app_name": app_name, "replicas": replicas, "reason": "no_container",
                    "error": "scaling in place needs the runtime container"}
        bundle = self._bundle_spec(payload)
        result = await self._container.scale_serve_app(bundle.name, app_name, replicas)
        if result.get("scaled"):
            self._events.add(event_type="ray.serve_app_scaled", payload={"app": app_name, "replicas": replicas})
        return result

    async def _serve_routes(self, bundle: Any) -> list[int]:
        """The model-TLS proxy's public ports in front of this cluster's Serve, read while it still runs.

        Stopping Ray stopped Serve but left the proxy serving its port: on the
        DGX pair, nginx on port 8000 for a cluster deleted hours before,
        answering 502 and holding the port. Read before the stop, because
        Serve's address goes with it; a route in front of anything else (a
        vLLM found running here) is not Serve's and stays.
        """
        if self.model_tls is None:
            return []
        try:
            if not await self._container.is_running(bundle.name):
                return []
            current = await self._serve_http(bundle.name)
        except ContainerRuntimeError as exc:
            log.warning("Model TLS: could not read where Serve listens before stopping (%s)", exc)
            return []
        from faden_agent.ray.serve_http import LOOPBACK  # noqa: PLC0415

        if not current.get("running") or str(current.get("host") or "") not in LOOPBACK:
            return []
        port = int(current.get("port") or 0)
        return sorted(public for public, internal in self.model_tls.routes().items() if internal == port)

    async def _drop_serve_routes(self, publics: list[int]) -> None:
        """Take the stopped Serve's routes off the proxy; with none left, the proxy goes."""
        for public in publics:
            try:
                await self.model_tls.remove_route(public)
                log.info("Model TLS: port %s no longer served, its cluster stopped here", public)
            except Exception:  # noqa: BLE001 - the cluster is stopped either way
                log.exception("Model TLS: could not remove the route for port %s", public)

    async def _serve_http(self, container: str) -> dict[str, Any]:
        """What Serve's proxy binds on this cluster: ``{"running": bool, "host", "port"}``."""
        from faden_agent.ray import serve_http  # noqa: PLC0415

        code, out, err = await self._container.run_python(serve_http.READ_SCRIPT, container, timeout_sec=90)
        doc = serve_http.parse(out)
        if doc is None or not doc.get("attached") or doc.get("error"):
            detail = (doc or {}).get("error") or (err or out or "").strip()[-400:]
            raise ContainerRuntimeError(f"could not read Serve's HTTP options (exit {code}): {detail}")
        return doc

    async def _model_tls_for_serve(
        self, container: str, request: dict[str, Any] | None, http_options: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Put the model-TLS proxy in front of Serve, when the backend asked and Serve allows.

        Serve binds once. Not running: it will start on the loopback address
        the backend asked for, behind the proxy. Already on that address: the
        proxy is (re)confirmed. Already listening on the network (a cluster
        from before model TLS): it keeps doing so -- the application is
        deployed on it as before and the result says so, for the backend to
        publish ``http://`` and offer the move (``shutdown_serve``).
        """
        if not request:
            return None
        public = int(request["public_port"])
        internal = int(request["internal_port"])
        if self.model_tls is None:
            raise ContainerRuntimeError("this agent has no model-TLS proxy configured")
        current = await self._serve_http(container)
        host = str(current.get("host") or "")
        from faden_agent.ray.serve_http import LOOPBACK  # noqa: PLC0415

        if current.get("running") and not (host in LOOPBACK and int(current.get("port") or 0) == internal):
            # Keep what runs; say where, so the backend publishes the truth.
            http_options["host"] = host
            http_options["port"] = int(current.get("port") or public)
            return {
                "model_tls": {"protected": False, "reason": "serve_running", "public_port": public},
                "serve_http": {"host": host, "port": int(current.get("port") or 0)},
            }
        route = await self.model_tls.ensure_route(public, internal)
        return {"model_tls": route, "serve_http": {"host": "127.0.0.1", "port": internal}}

    async def shutdown_serve(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stop Serve on this cluster: every application and the proxy.

        For moving a cluster behind the model-TLS proxy: the backend applies
        its applications again afterwards, and the first starts Serve anew
        with the loopback options. Requires the runtime container.
        """
        from faden_agent.ray import serve_http  # noqa: PLC0415

        bundle = self._bundle_spec(payload)
        name = bundle.name if bundle is not None else None
        facts = await (self._container.facts(name) if name else self._container.facts())
        if facts is None or not facts.get("running"):
            raise ContainerRuntimeError("shutdown_serve needs the runtime container, and it is not running here")
        name = name or str(facts.get("name") or "") or None
        if name:
            code, out, err = await self._container.run_python(serve_http.SHUTDOWN_SCRIPT, name, timeout_sec=180)
        else:
            code, out, err = await self._container.run_python(serve_http.SHUTDOWN_SCRIPT, timeout_sec=180)
        doc = serve_http.parse(out) or {}
        if not doc.get("shut_down"):
            detail = doc.get("error") or (err or out or "").strip()[-400:]
            raise ContainerRuntimeError(f"serve.shutdown failed (exit {code}): {detail}")
        self._events.add(event_type="ray.serve_shut_down", payload={"container": name})
        return {"shut_down": True}

    async def delete_serve_app(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Delete a named Serve application (Phase 3)."""
        spec = DeleteServeAppPayload.model_validate(payload)
        if await self._use_container(payload):
            bundle = self._bundle_spec(payload)
            return await self._container.delete_serve_app(
                bundle.name,
                spec.app_name,
            )
        return await asyncio.to_thread(self._serve.delete_app, spec.app_name)

    async def describe_cluster(self, payload: dict[str, Any]) -> dict[str, Any]:
        """What the cluster this machine runs is serving, read from Ray itself.

        For a server taking the cluster over after losing its own record of
        it (``ray/inspect.py``). ``verify`` carries the configurations that
        server would deploy, to be checked against what runs.
        ``hand_over_token`` adds the cluster's token -- which the running
        cluster keeps and the new server needs to let machines join it; the
        backend encrypts it the moment the result arrives.
        """
        from faden_agent.ray import inspect  # noqa: PLC0415

        facts = await self._container.facts()
        if facts is None or not facts.get("running"):
            return {"running": False, "container": facts}
        code, out, err = await self._container.run_python(inspect.script(payload.get("verify")))
        doc = inspect.parse(out)
        if doc is None:
            detail = (err or out or "").strip()[-800:]
            return {"running": True, "container": facts, "attached": False,
                    "error": f"inspection produced no result (exit {code}): {detail}"}
        result: dict[str, Any] = {"running": True, "container": facts, **doc}
        if payload.get("hand_over_token") and doc.get("attached"):
            try:
                token = self._token_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                result["token_error"] = f"cluster token unreadable: {exc}"
            else:
                if token:
                    result["cluster_token"] = token
                else:
                    result["token_error"] = "cluster token file is empty"
        return result

    async def _drain_step(self, script: str, *, what: str, timeout_sec: float = 90) -> dict[str, Any]:
        """Run one of ``ray/drain.py``'s scripts in the runtime container; its document, or raise."""
        from faden_agent.ray import drain  # noqa: PLC0415

        facts = await self._container.facts()
        if facts is None or not facts.get("running"):
            raise ContainerRuntimeError(f"{what} needs the runtime container, and it is not running here")
        code, out, err = await self._container.run_python(script, timeout_sec=timeout_sec)
        doc = drain.parse(out)
        if doc is None:
            detail = (err or out or "").strip()[-600:]
            raise ContainerRuntimeError(f"{what} produced no result (exit {code}): {detail}")
        if doc.get("attached") is False:
            raise ContainerRuntimeError(f"{what}: {doc.get('error') or 'could not attach to the cluster'}")
        return doc

    async def enable_serve_drain(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Let Serve move copies off draining machines (on the head; ``ray/drain.py``).

        Restarts Serve's controller once if it runs without the patch; the
        copies keep serving meanwhile.
        """
        from faden_agent.ray import drain  # noqa: PLC0415

        wait = int(payload.get("wait_sec") or 90)
        doc = await self._drain_step(drain.enable_script(wait_sec=wait), what="enable_serve_drain", timeout_sec=wait + 60)
        if not doc.get("installed") or doc.get("active") is False:
            raise ContainerRuntimeError(doc.get("error") or "the Serve drain patch did not take")
        if doc.get("restarted"):
            self._events.add(event_type="ray.serve_controller_restarted", payload={"reason": "drain patch"})
        return doc

    async def drain_ray_node(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Drain this machine in Ray: no new work here; Serve moves its copies away."""
        from faden_agent.ray import drain  # noqa: PLC0415

        doc = await self._drain_step(drain.drain_script(), what="drain_ray_node")
        if not doc.get("accepted"):
            raise ContainerRuntimeError(doc.get("error") or "Ray did not accept the drain")
        self._events.add(event_type="ray.node_drained", payload={"node_id": doc.get("node_id")})
        return doc

    async def describe_placement(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Machines and Serve copies, with where each copy's resources are held (on the head)."""
        from faden_agent.ray import drain  # noqa: PLC0415

        return await self._drain_step(drain.placement_script(), what="describe_ray_placement")

    async def stop_serve_replicas(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stop the Serve copies named in ``actor_names`` (on the head)."""
        from faden_agent.ray import drain  # noqa: PLC0415

        names = [str(n) for n in (payload.get("actor_names") or []) if n]
        if not names:
            return {"stopped": [], "missing": []}
        doc = await self._drain_step(drain.stop_script(names), what="stop_serve_replicas")
        self._events.add(event_type="ray.serve_replicas_stopped", payload={"stopped": doc.get("stopped")})
        return doc

    async def get_serve_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Serve-tier status command (additive; Dashboard-independent).

        The Serve control plane is a regular Ray job in the cluster — its
        status comes from the Python API, so this works with the dashboard
        disabled too.
        """
        spec = GetRayServeStatusPayload.model_validate(payload)
        if spec.credential_ref:
            try:
                await self._ensure_token({"credential_ref": spec.credential_ref})
            except Exception as e:
                log.warning("Could not ensure token for get_serve_status: %s", e)

        if await self._use_container(payload):
            bundle = self._bundle_spec(payload)
            serve_data = await self._container.get_serve_status(
                bundle.name, app_name=spec.app_name,
            )
            # The helper reports Serve's own ``applications`` key; the wire
            # contract for this command is ``apps``.
            apps = serve_data.get("apps") or serve_data.get("applications") or {}
            if spec.app_name is not None:
                apps = {spec.app_name: apps[spec.app_name]} if spec.app_name in apps else {}
            return {
                "available": bool(serve_data.get("available", False)),
                "apps": apps,
                "detail": serve_data.get("error") or serve_data.get("detail"),
            }

        serve = await asyncio.to_thread(self._serve.status)
        apps: dict[str, Any] = {}
        if serve is not None and serve.available:
            for k, v in (serve.apps or {}).items():
                if hasattr(v, "model_dump"):
                    apps[k] = v.model_dump(mode="json")
                else:
                    apps[k] = dict(v) if isinstance(v, dict) else v
        if spec.app_name is not None:
            apps = {spec.app_name: apps[spec.app_name]} if spec.app_name in apps else {}
        dump = serve.model_dump(mode="json") if serve is not None else {}
        dump["apps"] = apps
        return {
            "alive": bool(serve is not None and serve.available),
            "serve": dump,
        }


def _dist_version(name: str) -> str | None:
    """Installed version of distribution *name* (no import), else ``None``."""
    from importlib import metadata  # noqa: PLC0415

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _sdk_version() -> str | None:
    """Version of the Ray SDK the agent packages (``ray.__version__``)."""
    try:
        import ray  # noqa: PLC0415  - packaged with the agent

        return getattr(ray, "__version__", None)
    except Exception:
        return None

