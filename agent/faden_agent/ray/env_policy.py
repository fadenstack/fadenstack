"""Environment variables the agent alone decides for Ray.

A cluster's settings, a runtime bundle and a deployment all reach Ray as
environment variables, and the agent starts Ray in two ways: as a process on the
host (``manager.start_env``) and inside the certified runtime container
(``container.ensure_container``). Both must refuse the same names, or a setting
one path strips is a way to switch security off through the other.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Ray's authentication and transport security, and the device selection the
#: scheduler hands out. None of these may come from a cluster's ``node_env_vars``
#: or a bundle: with ``RAY_AUTH_MODE`` unset the cluster stops asking for its
#: token, with ``RAY_USE_TLS`` off its nodes stop proving who they are, and
#: ``CUDA_VISIBLE_DEVICES`` reaches accelerators outside the allocation.
#:
#: The backend refuses the same names when a cluster is saved
#: (``faden_backend.services.inference.drivers.ray.schemas.RESERVED_ENV_KEYS``);
#: keep the two in step.
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


def without_reserved(env: Mapping[str, str] | None) -> dict[str, str]:
    """*env* minus the names the agent alone sets."""
    return {str(k): str(v) for k, v in (env or {}).items() if str(k) not in RESERVED_ENV_KEYS}
