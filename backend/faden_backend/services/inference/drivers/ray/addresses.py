"""The addresses a Ray cluster's own machines have, for checking a pinned host against."""

from __future__ import annotations

from typing import Any

from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO
from faden_backend.db.models.inference import InferenceEnvironment


async def machine_addresses(session: Any, environment: InferenceEnvironment) -> list[str]:
    """Every address one of *environment*'s machines is known by.

    The address the backend reaches it by, and the one it has on the cluster's
    own network when the cluster runs on a separate fabric.
    """
    bindings = ((environment.observed_status_json or {}).get("resolved_fabric") or {}).get("node_bindings") or {}
    found: list[str] = []
    for node in await EnvironmentNodeDAO(session).machines(environment.id):
        host = str(node.host or "").strip().removeprefix("host=").strip()
        if host:
            found.append(host)
        fabric = str((bindings.get(str(node.id)) or {}).get("ip") or "").strip()
        if fabric:
            found.append(fabric)
    return found
