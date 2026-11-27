"""Bundle matching exactly as backend v0.7.2 shipped it, frozen for compatibility tests.

Copied from ``git show v0.7.2:backend/faden_backend/services/inference/bundles.py``
(file sha256 b0de96aa5569e5d2856d17c8fe843333287d72eac1650f508a1c2eeceef7dd96):
``RuntimeBundleRegistry.validate_node_compatibility`` and ``runtimes_for``, as
functions. Do not edit: it is the reference the current matcher is compared to.
"""

from __future__ import annotations

from typing import Any


def validate_node_compatibility(bundle: Any, node: Any) -> tuple[bool, str]:
    caps = node.capabilities_json or {}
    gpu_info = caps.get("gpu") or {}
    gpus = gpu_info.get("devices") or []

    # Check GPU vendor
    vendor = (gpu_info.get("vendor") or caps.get("gpu_vendor") or "nvidia").lower()
    if vendor != bundle.target_architecture.accelerator.vendor.lower():
        return False, (
            f"Incompatible GPU vendor: expected "
            f"{bundle.target_architecture.accelerator.vendor}, found {vendor}"
        )

    # Host CPU architecture, when the node reported it.
    machine = str(caps.get("machine") or "").lower()
    expected_cpu = bundle.target_architecture.cpu.lower()
    if machine and expected_cpu:
        aliases = {"aarch64": {"aarch64", "arm64"}, "x86_64": {"x86_64", "amd64"}}
        accepted = aliases.get(expected_cpu, {expected_cpu})
        if machine not in accepted:
            return False, (
                f"Incompatible CPU architecture: bundle targets {expected_cpu}, "
                f"node reports {machine}"
            )

    # If bundle requires specific compute capability, verify if present
    required_caps = set(bundle.target_architecture.accelerator.compute_capabilities)
    if required_caps and gpus:
        node_caps = {
            str(g.get("compute_capability")) for g in gpus if g.get("compute_capability")
        }
        if node_caps and not required_caps.intersection(node_caps):
            return False, (
                f"Compute capability mismatch: required {required_caps}, found {node_caps}"
            )

    return True, "Node is fully compatible with runtime bundle"


def runtimes_for(bundles: list[Any], node: Any, *, driver: str = "ray") -> list[Any]:
    if not str((node.capabilities_json or {}).get("machine") or "").strip():
        return []
    return [b for b in bundles if b.driver == driver and validate_node_compatibility(b, node)[0]]
