"""What each cluster has to run models on, from what its machines report.

Read from each member's latest utilization report: the accelerators' names,
their memory, and how much of it is free. A machine that has not reported is
listed with no accelerators rather than guessed at -- the fit check then says
"no accelerators", which is the truth about what the server knows.
"""

from __future__ import annotations

from typing import Any

from faden_backend.services.marketplace.fit import ClusterHardware, Gpu, Machine

MIB = 1024**2


def gpus_from_utilization(utilization: dict[str, Any] | None) -> list[Gpu]:
    """The accelerators in one machine's utilization report."""
    gpu = (utilization or {}).get("gpu") or {}
    devices = gpu.get("devices") if isinstance(gpu, dict) else None
    found: list[Gpu] = []
    for device in devices or []:
        if not isinstance(device, dict):
            continue
        total_mib = device.get("memory_total_mib")
        if not total_mib:
            continue
        used_mib = device.get("memory_used_mib")
        free = None if used_mib is None else max(0, int((float(total_mib) - float(used_mib)) * MIB))
        found.append(Gpu(
            name=str(device.get("name") or device.get("vendor") or "accelerator"),
            total_bytes=int(float(total_mib) * MIB),
            free_bytes=free,
        ))
    if found:
        return found
    # Older agents: only totals for the whole machine.
    total = gpu.get("total_vram_bytes") if isinstance(gpu, dict) else None
    count = int(gpu.get("count") or 1) if isinstance(gpu, dict) else 1
    if total:
        free = gpu.get("free_vram_bytes")
        return [
            Gpu(name="accelerator", total_bytes=int(total) // count,
                free_bytes=None if free is None else int(free) // count)
            for _ in range(count)
        ]
    return []


def metal_from_node(node: Any) -> dict[str, Any] | None:
    """A Mac's memory for :func:`fit.plan_metal`, from its ``metal`` capability (estimated until a runtime is staged)."""
    from faden_backend.services.inference.drivers.standalone.admission import working_set_bytes  # noqa: PLC0415

    working_set, estimated = working_set_bytes(node)
    if not working_set:
        return None
    metal = ((getattr(node, "capabilities_json", None) or {}).get("metal")) or {}
    unified = int(metal.get("unified_memory_bytes") or 0) if isinstance(metal, dict) else 0
    return {"working_set_bytes": working_set, "unified_bytes": unified, "estimated": estimated}


async def cluster_hardware(session: Any) -> list[ClusterHardware]:
    """Every cluster this server manages, with its machines' accelerators."""
    from sqlalchemy import select  # noqa: PLC0415

    from faden_backend.db.dao.node_control_dao import NodeControlDAO  # noqa: PLC0415
    from faden_backend.db.models.inference import (  # noqa: PLC0415
        InferenceEnvironment,
        InferenceEnvironmentNode,
    )
    from faden_backend.db.models.node_control import InfraNode  # noqa: PLC0415
    from faden_backend.services.inference.bundles import default_bundle_registry  # noqa: PLC0415

    environments = list((await session.execute(
        select(InferenceEnvironment).order_by(InferenceEnvironment.name),
    )).scalars())
    members = list((await session.execute(select(InferenceEnvironmentNode))).scalars())
    node_ids = {m.node_id for m in members}
    nodes = {n.id: n for n in (await session.execute(select(InfraNode))).scalars() if n.id in node_ids}
    snapshots = await NodeControlDAO(session).latest_inventory_snapshots(node_ids=list(node_ids)) if node_ids else {}

    from faden_backend.services.marketplace.compat import RuntimeSupport  # noqa: PLC0415

    from faden_backend.services.inference.driver_key import (  # noqa: PLC0415
        DEFAULT_DRIVER,
        driver_keys_by_control_plane,
    )

    drivers = await driver_keys_by_control_plane(session)

    clusters: list[ClusterHardware] = []
    for env in environments:
        driver = drivers.get(getattr(env, "control_plane_id", None), DEFAULT_DRIVER)
        machines: list[Machine] = []
        version: str | None = None
        support: RuntimeSupport | None = None
        metal: dict[str, Any] | None = None
        for member in (m for m in members if m.environment_id == env.id):
            node = nodes.get(member.node_id)
            if node is None:
                continue
            snapshot = snapshots.get(node.id)
            machines.append(Machine(
                node_id=str(node.id),
                name=node.agent_id or node.host,
                gpus=gpus_from_utilization(getattr(snapshot, "utilization_json", None)),
            ))
            if driver == "standalone" and metal is None:
                metal = metal_from_node(node) or {"working_set_bytes": 0, "estimated": True}
            if version is None:
                try:
                    bundle = default_bundle_registry.resolve_for_node(
                        node, driver=driver, environment=env,
                    )
                    version = bundle.compatibility_matrix.vllm_version if bundle is not None else None
                    if bundle is not None:
                        matrix = bundle.compatibility_matrix
                        support = RuntimeSupport(
                            vllm_version=matrix.vllm_version or None,
                            transformers_version=matrix.transformers_version,
                            model_types=frozenset(matrix.supported_model_types),
                            architectures=frozenset(matrix.supported_architectures),
                            pipelines=frozenset(matrix.supported_pipelines),
                            diffusers_version=matrix.diffusers_version,
                        )
                except Exception:  # noqa: BLE001 - the version is a nicety, never a blocker
                    version = None
        clusters.append(ClusterHardware(
            environment_id=str(env.id),
            name=env.name,
            status=env.status,
            machines=machines,
            vllm_version=version or None,
            runtime=support,
            metal=metal,
        ))
    return clusters
