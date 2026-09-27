"""Environment-driven runtime configuration."""

from __future__ import annotations

import logging
import os
import socket
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _default_state_path() -> str:
    """Return a platform-appropriate default path for agent state."""
    if sys.platform == "win32":
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return str(Path(base) / "faden-agent" / "state.json")
    # Prefer system path when running as root, user-local otherwise.
    uid = getattr(os, "getuid", lambda: -1)()
    if uid == 0:
        return "/var/lib/faden-agent/state.json"
    return str(Path.home() / ".local" / "share" / "faden-agent" / "state.json")


def _default_model_store_root() -> str:
    """Default model store: the standard HuggingFace hub cache location.

    ``%LOCALAPPDATA%\\huggingface`` on Windows when available,
    ``~/.cache/huggingface`` elsewhere — the same spot ``huggingface_hub``
    uses, so a plain user install owns its model store without root.
    Override with ``FADEN_NODE_AGENT_MODEL_STORE``.
    """
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return str(Path(local) / "huggingface")
        return str(Path.home() / "AppData" / "Local" / "huggingface")
    return str(Path.home() / ".cache" / "huggingface")


def _detect_routable_ip(backend_url: str) -> str | None:
    """Determine the local IP that routes to the backend (no traffic sent)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(backend_url)
        target_host = parsed.hostname or "127.0.0.1"
        target_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target_host, target_port))
            return s.getsockname()[0]
    except Exception:
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(slots=True)
class AgentConfig:
    """Static process configuration resolved from env vars."""

    backend_url: str
    agent_id: str
    host: str
    advertise_host: str
    advertise_scheme: str
    enrollment_token: str | None
    state_path: Path
    heartbeat_interval_sec: int
    inventory_interval_sec: int
    reconnect_min_sec: float
    reconnect_max_sec: float
    request_timeout_sec: float
    verify_tls: bool
    tls_ca_bundle: str | None
    tls_client_cert: str | None
    tls_client_key: str | None
    log_level: str
    image_allowlist: list[str]
    model_store_root: str
    loki_url: str | None
    log_batch_size: int
    log_flush_interval_sec: int
    #: Host path Ray's session directory is bind-mounted to.
    #:
    #: The certified bundle mounts ``/tmp/ray`` here so the agent can read
    #: replica log files directly instead of shelling into the container. A
    #: node whose bundle predates the mount simply has no directory, and the
    #: forwarder finds nothing rather than failing.
    ray_session_dir: str
    container_runtime: str
    #: Where a runtime image lands before it is loaded, so a dropped transfer
    #: resumes instead of starting over. Empty means beside the state file.
    #: Needs room for one image tar -- about 12GB for the DGX runtime -- and
    #: falls back to streaming, which cannot resume, when there is not.
    image_download_dir: str = ""
    #: Built from the fields above plus the backend's authority the agent
    #: learnt (server_trust.build_context); shared by every connection to the
    #: backend. ``None`` while verification is off.
    ssl_context: Any = None
    #: ``auto``: refuse unsigned commands once the backend has said it signs
    #: them; ``true``: always; ``false``: never (a backend rolled back to one
    #: that does not sign).
    require_signed_commands: str = "auto"
    #: The machine's model-TLS proxy: key, certificate, trust and nginx's
    #: configuration (model_tls.py). Empty means ``model-tls`` beside the state.
    model_tls_dir: str = ""
    #: The same directory as the container runtime's host sees it, when the
    #: agent itself runs in a container (Docker-out-of-Docker). Empty: the same.
    model_tls_host_dir: str = ""

    @classmethod
    def from_env(cls) -> AgentConfig:
        """Load config using FADEN_NODE_AGENT_* environment vars."""
        hostname = socket.gethostname()
        backend_url = os.getenv("FADEN_NODE_AGENT_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
        routable_ip = _detect_routable_ip(backend_url)
        host = os.getenv("FADEN_NODE_AGENT_HOST", routable_ip or hostname)
        # advertise_host is the address the backend uses to reach workload
        # containers on this node.  It MUST be a routable IP, not an alias
        # like "workstation".  Default to the auto-detected IP even when
        # HOST is set to a friendly name.
        advertise_host = os.getenv("FADEN_NODE_AGENT_ADVERTISE_HOST", "").strip()
        if not advertise_host:
            advertise_host = routable_ip or host
        advertise_scheme = os.getenv("FADEN_NODE_AGENT_ADVERTISE_SCHEME", "http").strip().lower() or "http"
        if advertise_scheme not in {"http", "https"}:
            advertise_scheme = "http"
        instance = cls(
            backend_url=os.getenv("FADEN_NODE_AGENT_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/"),
            agent_id=os.getenv("FADEN_NODE_AGENT_AGENT_ID", hostname),
            host=host,
            advertise_host=advertise_host,
            advertise_scheme=advertise_scheme,
            enrollment_token=os.getenv("FADEN_NODE_AGENT_ENROLLMENT_TOKEN"),
            state_path=Path(
                os.getenv(
                    "FADEN_NODE_AGENT_STATE_PATH",
                    _default_state_path(),
                ),
            ),
            heartbeat_interval_sec=int(os.getenv("FADEN_NODE_AGENT_HEARTBEAT_INTERVAL_SEC", "15")),
            inventory_interval_sec=int(os.getenv("FADEN_NODE_AGENT_INVENTORY_INTERVAL_SEC", "60")),
            reconnect_min_sec=float(os.getenv("FADEN_NODE_AGENT_RECONNECT_MIN_SEC", "2")),
            reconnect_max_sec=float(os.getenv("FADEN_NODE_AGENT_RECONNECT_MAX_SEC", "30")),
            request_timeout_sec=float(os.getenv("FADEN_NODE_AGENT_REQUEST_TIMEOUT_SEC", "20")),
            verify_tls=_env_bool("FADEN_NODE_AGENT_VERIFY_TLS", True),
            tls_ca_bundle=os.getenv("FADEN_NODE_AGENT_TLS_CA_BUNDLE") or None,
            tls_client_cert=os.getenv("FADEN_NODE_AGENT_TLS_CLIENT_CERT") or None,
            tls_client_key=os.getenv("FADEN_NODE_AGENT_TLS_CLIENT_KEY") or None,
            log_level=os.getenv("FADEN_NODE_AGENT_LOG_LEVEL", "INFO").upper(),
            image_allowlist=[
                prefix.strip()
                for prefix in os.getenv("FADEN_NODE_AGENT_IMAGE_ALLOWLIST", "").split(",")
                if prefix.strip()
            ],
            model_store_root=os.getenv(
                "FADEN_NODE_AGENT_MODEL_STORE",
                _default_model_store_root(),
            ),
            loki_url=os.getenv("FADEN_NODE_AGENT_LOKI_URL") or None,
            log_batch_size=int(os.getenv("FADEN_NODE_AGENT_LOG_BATCH_SIZE", "100")),
            log_flush_interval_sec=int(os.getenv("FADEN_NODE_AGENT_LOG_FLUSH_INTERVAL_SEC", "5")),
            ray_session_dir=os.getenv(
                "FADEN_NODE_AGENT_RAY_SESSION_DIR", "/var/lib/faden/ray"
            ),
            container_runtime=os.getenv("FADEN_NODE_AGENT_CONTAINER_RUNTIME", "auto").strip().lower() or "auto",
            image_download_dir=os.getenv("FADEN_NODE_AGENT_IMAGE_DOWNLOAD_DIR", ""),
            require_signed_commands=(
                os.getenv("FADEN_NODE_AGENT_REQUIRE_SIGNED_COMMANDS", "auto").strip().lower() or "auto"
            ),
            model_tls_dir=os.getenv("FADEN_NODE_AGENT_MODEL_TLS_DIR", "").strip(),
            model_tls_host_dir=os.getenv("FADEN_NODE_AGENT_MODEL_TLS_HOST_DIR", "").strip(),
        )
        if not instance.verify_tls:
            log.warning("TLS verification is disabled — connections are vulnerable to MITM.")
        from faden_agent.server_trust import build_context  # noqa: PLC0415

        try:
            instance.ssl_context = build_context(instance)
        except (OSError, ssl.SSLError) as exc:
            # A bundle that cannot be read would otherwise stop the agent before
            # it says anything; connections then fail verification, with a reason.
            log.error("TLS settings cannot be loaded (%s); only the public roots are trusted.", exc)
            instance.ssl_context = ssl.create_default_context()
        return instance
