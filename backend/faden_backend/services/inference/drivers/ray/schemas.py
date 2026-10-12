"""Backend-side schemas for Ray driver config and status."""

import ipaddress
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel


#: Ports a new cluster is created with, stored in its config.
#:
#: Ray's own defaults collide with Fadenstack on the machine that runs the
#: server: 6379 is Redis (published on the host by the compose file) and 8000
#: is the backend in a dev install. A machine that is both server and GPU node
#: -- the first install most people make -- could not start its cluster: Ray's
#: GCS failed on "port 6379 ... Address already in use", and was retried on
#: the same port every minute. The class defaults below stay Ray's, because
#: clusters created before this run on them.
NEW_CLUSTER_PORTS: dict[str, int] = {"head_port": 6390, "serve_http_port": 8010}


class RayEnvironmentConfig(BaseModel):
    """Configuration stored in InferenceEnvironment.config_json."""
    ray_version: str = "2.58.0"
    head_port: int = 6379
    dashboard_port: int = 8265
    #: Where Ray's dashboard (and its job API) listens. Loopback, or the address
    #: of one of the cluster's machines -- see ``check_environment_config``.
    dashboard_host: str = "127.0.0.1"
    object_store_memory: int | None = None
    #: (``extra_ray_start_args`` was here: nothing ever read it. A cluster that
    #: still stores it loads, the field is ignored.)
    node_env_vars: dict[str, str] = {}
    serve_proxy_location: str = "HeadOnly"
    #: Pins the interface Serve binds, turns model mTLS off for the cluster and
    #: becomes the address every chat for its models is sent to. Loopback, or
    #: the address of one of the cluster's machines -- see
    #: ``check_environment_config``.
    serve_http_host: str | None = None
    serve_http_port: int = 8000
    #: Ray's own mutual TLS between the cluster's machines (``RAY_USE_TLS``),
    #: with the cluster's CA (services/machine_tls/ray_ca.py). Switching it
    #: restarts the cluster: Ray reads its certificates only when it starts.
    #:
    #: Off here, and for a new cluster too, on purpose: switching it on is a
    #: decision about the machines, and a cluster that has none yet cannot be
    #: asked. Starting a cluster with TLS on refuses an agent older than 0.1.18
    #: (``_require_ray_tls``), but a machine that runs Ray on the host instead of
    #: in the certified runtime container takes the setting without applying it.
    ray_tls: bool = False


#: Environment variables a cluster's ``node_env_vars`` may not set: Ray's
#: authentication and TLS, and the device selection the scheduler hands out.
#: The agent refuses the same names on the machine
#: (``faden_agent.ray.env_policy.RESERVED_ENV_KEYS``); refusing them here says so
#: when the cluster is saved instead of ignoring them later. Keep the two in step.
RESERVED_ENV_KEYS = frozenset({
    "RAY_AUTH_MODE",
    "RAY_AUTH_TOKEN",
    "RAY_AUTH_TOKEN_PATH",
    "RAY_USE_TLS",
    "RAY_TLS_SERVER_CERT",
    "RAY_TLS_SERVER_KEY",
    "RAY_TLS_CA_CERT",
    "CUDA_VISIBLE_DEVICES",
})


class EnvironmentConfigRefused(ValueError):
    """A cluster setting that is not allowed; the message says which and why."""


def _same_address(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return a == b


def _is_loopback(value: str) -> bool:
    text = value.strip().lower()
    if text == "localhost":
        return True
    try:
        return ipaddress.ip_address(text.strip("[]")).is_loopback
    except ValueError:
        return False


def check_environment_config(
    config: dict[str, Any] | None,
    *,
    machine_addresses: Iterable[str],
    previous: dict[str, Any] | None = None,
) -> None:
    """Refuse cluster settings that send traffic or code somewhere they should not go.

    * ``node_env_vars`` may not name ``RESERVED_ENV_KEYS``.
    * ``serve_http_host`` and ``dashboard_host`` must be loopback or the address
      of one of the cluster's machines (*machine_addresses*). A Serve host other
      than the machine's own is the address every chat for the cluster's models
      is sent to, and pinning it also switches model mTLS off; a wildcard would
      publish the dashboard's job API to the whole network.

    A value the cluster already has (*previous*) is left alone, so saving other
    settings of a cluster set up before this check does not fail on it.

    Raises:
        EnvironmentConfigRefused: naming the setting and the rule.
    """
    config = config or {}
    previous = previous or {}
    env = config.get("node_env_vars")
    if env is not None:
        if not isinstance(env, dict):
            raise EnvironmentConfigRefused("node_env_vars must be an object of names and values.")
        reserved = sorted(str(k) for k in env if str(k) in RESERVED_ENV_KEYS)
        if reserved:
            raise EnvironmentConfigRefused(
                f"node_env_vars may not set {', '.join(reserved)}: Ray's authentication, its TLS and the "
                "devices a machine offers are the agent's to decide, not a cluster setting."
            )
    addresses = [str(a) for a in machine_addresses if a]
    for key in ("serve_http_host", "dashboard_host"):
        value = config.get(key)
        if value is None or value == previous.get(key):
            continue
        if not isinstance(value, str) or not value.strip():
            raise EnvironmentConfigRefused(f"{key} must be an address.")
        if _is_loopback(value) or any(_same_address(value, a) for a in addresses):
            continue
        raise EnvironmentConfigRefused(
            f"{key} {value!r} is neither loopback nor the address of one of this cluster's machines. "
            + (
                "Every chat for the cluster's models would be sent to it."
                if key == "serve_http_host"
                else "Ray's dashboard and job API would listen there."
            )
        )


class RayProbeResult(BaseModel):
    """Structured probe output."""
    alive: bool
    version: str | None = None
    num_nodes: int = 0
    total_gpus: float = 0
    available_gpus: float = 0


class RayClusterStatus(BaseModel):
    """Parsed cluster state from GET_RAY_STATUS command.

    The agent answers with the enriched ``RayEnvironmentStatus`` (SDK-first,
    Dashboard-independent): the flat tier (Tier A) is what maps environment
    health; ``serve`` / ``metrics`` / ``state`` / ``capabilities`` are
    additive tiers — an unhealthy tier never flips cluster health.  New
    fields default so a result from an older agent still parses.
    """
    alive: bool
    #: Whether the node answered at all.
    #:
    #: ``alive=False`` is ambiguous on its own: it is both "the cluster told
    #: us it is down" and "we never heard back".  Only the first is a fact,
    #: and reporting the second as one produces a screen full of zeros that
    #: look measured.
    observed: bool = True
    version: str | None = None
    num_nodes: int = 0
    nodes: list[dict[str, Any]] = []
    total_gpus: float = 0
    available_gpus: float = 0
    cluster_address: str | None = None
    # Enriched flat tier (still Tier A — part of cluster health).
    total_cpus: float = 0
    head_address: str | None = None
    # Additive tiers (non-gating; best-effort parse).
    capabilities: dict[str, Any] = {}
    serve: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    state: dict[str, Any] | None = None

    @staticmethod
    def _record_is_alive(node: dict[str, Any]) -> bool:
        """Is one ``ray.nodes()`` record a live cluster member?"""
        if "alive" in node and not node["alive"]:
            return False
        if "state" in node and str(node["state"]).upper() not in {"ALIVE", "UP", "ACTIVE"}:
            return False
        return True

    @property
    def alive_node_records(self) -> list[dict[str, Any]]:
        """The subset of ``nodes`` that are live members right now.

        Ray never removes dead node records from the GCS: a worker restart
        leaves its old record behind forever (and a rejoin adds a *third*
        record with the same IP).  Membership therefore has to be counted over
        live records only — the raw list is history, not state.
        """
        return [n for n in self.nodes if self._record_is_alive(n)]

    @property
    def alive_nodes(self) -> int:
        """Number of live cluster members.

        Falls back to ``num_nodes`` when the probe carried no per-node records
        (older agents), where the raw count is the only signal available.
        """
        if not self.nodes:
            return self.num_nodes
        return len(self.alive_node_records)

    def healthy_for(self, expected_nodes: int) -> bool:
        """Is the cluster healthy for an environment expecting *expected_nodes* members?

        Dead records are ignored entirely; a member is only missing when the
        live count falls below what the environment expects.
        """
        if not self.alive:
            return False
        live = self.alive_nodes
        if live <= 0:
            return False
        if expected_nodes > 0:
            return live >= expected_nodes
        return True

    @property
    def all_healthy(self) -> bool:
        """Healthy without a membership expectation (at least one live node)."""
        return self.healthy_for(0)

    @property
    def serve_available(self) -> bool:
        """Whether the Serve control plane reported itself available."""
        return bool((self.serve or {}).get("available", False))

    @property
    def metrics_enabled(self) -> bool:
        """Whether Prometheus scrape targets were discovered (Tier C)."""
        return bool((self.metrics or {}).get("enabled", False))

