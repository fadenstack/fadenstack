"""Content-free binding for an explicit host command's two policy checks.

These are comparison stamps, not execution permits. The gateway never executes
the device's tool. The SDK checks again immediately before dispatch and still
enforces its own tool class, stages, approval and native authority.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stamp(value: Any) -> str:
    """Deterministic UTF-8 JSON digest; never persist it as a content audit record."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def principal(auth: Any, agent: Any) -> dict[str, str | None]:
    """Only fields derived from verified authentication and the authorized agent."""
    return {
        "tenant_id": auth.tenant_id,
        "user_id": auth.user_id,
        "device_id": agent.device_id,
        "agent_id": agent.id,
    }
