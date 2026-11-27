"""What a machine must be able to do before a standalone environment uses it.

Asked before anything is started -- when a machine is added to a standalone
environment, when a deployment is created on one, and on every reconcile pass --
so a machine that cannot serve is refused with a sentence, never left pending.
"""

from __future__ import annotations

from typing import Any

#: The agent implements the native command set (CONTRACTS § C1). Version 1.
NATIVE_RUNTIME_CAPABILITY = "native_runtime"
#: The agent's model-TLS proxy runs natively on this machine (B1).
MODEL_TLS_NATIVE_CAPABILITY = "model_tls_native"

#: Metal's recommended working set as a fraction of unified memory, used until
#: the agent can read the real value (it needs a staged runtime's MLX). Measured
#: in Gate 0d on an M5 Pro: 55 662 788 608 / 68 719 476 736 = 0.81.
ESTIMATED_WORKING_SET_FRACTION = 0.81

#: The internal port a native engine listens on, on 127.0.0.1: the public port
#: plus this. The machine's model-TLS proxy owns the public port.
INTERNAL_PORT_OFFSET = 20000


def node_refusal(node: Any) -> str | None:
    """Why *node* cannot run a standalone deployment, or ``None`` when it can."""
    caps = getattr(node, "capabilities_json", None) or {}
    name = getattr(node, "agent_id", None) or getattr(node, "host", None) or str(getattr(node, "id", "this machine"))
    missing = []
    if not _version(caps.get(NATIVE_RUNTIME_CAPABILITY)):
        missing.append("run a native model runtime")
    if not _version(caps.get(MODEL_TLS_NATIVE_CAPABILITY)):
        missing.append("put its model-TLS proxy in front of a native runtime")
    if not missing:
        return None
    return (
        f"{name} cannot serve models on its own yet: its agent does not "
        f"{' or '.join(missing)}. Update the agent on that machine, then try again."
    )


def _version(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def working_set_bytes(node: Any) -> tuple[int | None, bool]:
    """The machine's Metal working set in bytes, and whether it is estimated.

    The agent reports ``metal.recommended_max_working_set_bytes`` once a runtime
    is staged and only ``metal.unified_memory_bytes`` before that; the
    recommended set is then estimated from the unified memory.
    """
    metal = (getattr(node, "capabilities_json", None) or {}).get("metal") or {}
    if not isinstance(metal, dict):
        return None, False
    recommended = metal.get("recommended_max_working_set_bytes")
    if recommended:
        return int(recommended), False
    unified = metal.get("unified_memory_bytes")
    if unified:
        return int(int(unified) * ESTIMATED_WORKING_SET_FRACTION), True
    return None, False


def parse_bytes(text: str | None) -> int | None:
    """``'32Gi'``/``'32G'``/``'512Mi'``/plain bytes, as bytes; ``None`` when not given."""
    if not text:
        return None
    raw = str(text).strip()
    units = {"ki": 2**10, "mi": 2**20, "gi": 2**30, "ti": 2**40, "k": 10**3, "m": 10**6, "g": 10**9, "t": 10**12}
    lower = raw.lower().removesuffix("b")
    for suffix in sorted(units, key=len, reverse=True):
        if lower.endswith(suffix):
            return int(float(lower[: -len(suffix)]) * units[suffix])
    return int(float(lower))


#: The utilization handed to vLLM-Metal when a deployment names no memory, at most (the Mac's
#: own ceiling applies below it). A fraction of the recommended working set.
DEFAULT_GPU_MEMORY_UTILIZATION = 0.8


def memory_ceiling(node: Any) -> tuple[int | None, float | None]:
    """``(bytes, fraction of R)`` an engine may use on *node*, or ``(None, None)`` before it reports.

    The same function the plan uses (``fit.metal_ceiling_share``), so a plan
    that says it fits is never refused here.
    """
    from faden_backend.services.marketplace.fit import metal_ceiling, metal_ceiling_share  # noqa: PLC0415

    working_set, _ = working_set_bytes(node)
    if not working_set:
        return None, None
    metal = (getattr(node, "capabilities_json", None) or {}).get("metal") or {}
    unified = int(metal.get("unified_memory_bytes") or 0) if isinstance(metal, dict) else 0
    return metal_ceiling(working_set, unified), metal_ceiling_share(working_set, unified)


def memory_budget(node: Any, memory: str | None, utilization: Any = None) -> dict[str, Any]:
    """The ``gpu_memory_utilization`` for a deployment on *node*.

    vLLM-Metal sizes its cache as a fraction of Metal's recommended working set
    (Gate 0 semantics). The deployment may name the fraction itself (the Host
    Model form fills it from the Mac plan) or a budget in bytes
    (``resources.replica.memory``); either is held to the machine's ceiling
    (``min(0.95 R, R - reserve)``, CONTRACTS § C3). With neither, the default is
    0.8 or the ceiling, whichever is lower. Raises ``ValueError`` with a
    sentence when the budget cannot fit.
    """
    working_set, estimated = working_set_bytes(node)
    ceiling_bytes, ceiling_fraction = memory_ceiling(node)
    base = {"working_set_bytes": working_set, "estimated": estimated, "ceiling_bytes": ceiling_bytes}
    requested = parse_bytes(memory)
    if utilization is None and requested is None:
        default = DEFAULT_GPU_MEMORY_UTILIZATION if ceiling_fraction is None else min(
            DEFAULT_GPU_MEMORY_UTILIZATION, ceiling_fraction,
        )
        return {"gpu_memory_utilization": default, **base}
    if not working_set:
        raise ValueError(
            "This machine has not reported its memory yet, so a memory budget cannot be checked. "
            "Wait for it to report, or deploy without a memory setting."
        )
    fraction = float(utilization) if utilization is not None else requested / working_set
    if fraction <= 0:
        raise ValueError("The memory share must be more than zero.")
    if ceiling_fraction is not None and fraction > ceiling_fraction + 1e-9:
        asked = fraction * working_set
        raise ValueError(
            f"The model asks for {asked / 2**30:.1f} GiB but this Mac's GPU can give an engine at most about "
            f"{ceiling_bytes / 2**30:.1f} GiB{' (estimated)' if estimated else ''}, after keeping memory for the system."
        )
    return {"gpu_memory_utilization": round(max(fraction, 0.05), 4), **base}
