"""Machines joining and leaving a running cluster, and draining one.

Draining a machine moves its models' copies to the cluster's other machines
and keeps new ones from landing on it -- before maintenance, or before it
leaves the cluster. The work is split between Ray and Fadenstack:

- Ray: a machine drained in GCS gets no new work, and Serve (with the patch
  the agent installs, ``faden_agent/ray/serve_drain_patch.py``) moves each
  copy by starting its replacement elsewhere, waiting until it serves, and
  only then stopping the old one. Nothing is stopped early.
- Fadenstack: say beforehand which copies can move and which cannot
  (:func:`plan_drain`: a copy needs room for all of its GPU bundles on the
  other machines, and a copy spread over two machines needs room for both
  halves), ask before anything would stop, stop the copies that cannot move
  when the operator agreed, and follow the drain until the machine is empty
  -- then, for a removal, take it out of Ray and out of the cluster.

The steps run in the cluster's health check (``RayEnvironmentManager._check``),
from the record this module keeps in the cluster's observed state:
``observed_status_json["drains"][<machine id>]``. A drain cannot be undone in
Ray; stopping one has the machine leave Ray and join again.

Adding a machine to a running cluster gives it a binding on the cluster's
network (checked by a TCP challenge to the head), and the next pass of the
reconciler brings it up the way the cluster came up: runtime image (from a
peer), container, ``ray start``. The models' files follow from the deployment
reconciler. Copies already running stay as they are -- a running engine never
grows -- so the machine serves new copies and new models.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.inference_dao import EnvironmentNodeDAO
from faden_backend.db.models.inference import (
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.node_control import InfraNode

log = logging.getLogger(__name__)

#: The agent can drain its machine out of a cluster (agent 0.2.6).
CAPABILITY = "ray_drain"
#: Apps Fadenstack deploys are named ``faden-<deployment id>``.
APP_PREFIX = "faden-"
#: Statuses in which the cluster runs on its machines.
_UP = frozenset({"ready", "degraded"})
#: Ray states of a machine that can take a copy.
_SCHEDULABLE = frozenset({"RUNNING", "IDLE"})
#: After this long a copy that should have moved is reported as stuck.
_STUCK_AFTER = timedelta(minutes=30)
_EPS = 1e-6


class MembershipError(Exception):
    """A membership change cannot be made now; the message says why."""

    def __init__(self, message: str, *, status_code: int = 409, plan: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.plan = plan


def supported(capabilities: dict | None) -> bool:
    """Whether a machine's agent can drain it out of a cluster."""
    try:
        return int((capabilities or {}).get(CAPABILITY) or 0) >= 1
    except (TypeError, ValueError):
        return False


def _name(node: InfraNode | None, fallback: Any = None) -> str:
    if node is None:
        return str(fallback)
    return str(node.agent_id or node.host or node.id)


def _now() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Which copies can move
# ---------------------------------------------------------------------------


def _demand(resources: dict[str, Any] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in (resources or {}).items():
        try:
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            out[str(key)] = amount
    return out


def _fits(free: dict[str, float], need: dict[str, float]) -> bool:
    return all(free.get(k, 0.0) + _EPS >= v for k, v in need.items())


def _take(free: dict[str, float], need: dict[str, float]) -> None:
    for key, value in need.items():
        free[key] = free.get(key, 0.0) - value


def _fit(bundles: list[dict[str, float]], free: dict[str, dict[str, float]], strategy: str | None) -> dict[int, str] | None:
    """Where *bundles* would go on the machines in *free* (mutated on success), or ``None``.

    Close to how Ray places a placement group: PACK (Serve LLM's default)
    tries one machine for everything and spills over; STRICT_PACK needs one
    machine; SPREAD prefers distinct machines; STRICT_SPREAD needs them.
    """
    order = sorted(free, key=lambda n: (-free[n].get("GPU", 0.0), n))
    strategy = (strategy or "PACK").upper()
    if strategy in ("PACK", "STRICT_PACK"):
        total: dict[str, float] = {}
        for need in bundles:
            for key, value in need.items():
                total[key] = total.get(key, 0.0) + value
        for node in order:
            if _fits(free[node], total):
                _take(free[node], total)
                return {i: node for i in range(len(bundles))}
        if strategy == "STRICT_PACK":
            return None
    trial = {node: dict(available) for node, available in free.items()}
    placed: dict[int, str] = {}
    used: set[str] = set()
    for index, need in sorted(enumerate(bundles), key=lambda item: -item[1].get("GPU", 0.0)):
        fitting = [n for n in order if _fits(trial[n], need)]
        if strategy in ("SPREAD", "STRICT_SPREAD"):
            fresh = [n for n in fitting if n not in used]
            if strategy == "STRICT_SPREAD" or fresh:
                fitting = fresh
        if not fitting:
            return None
        node = fitting[0]
        _take(trial[node], need)
        placed[index] = node
        used.add(node)
    free.clear()
    free.update(trial)
    return placed


def _gpus(text: float) -> str:
    return f"{text:g}"


def plan_drain(
    placement: dict[str, Any],
    target: str,
    *,
    deployments: dict[str, dict[str, Any]] | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Which copies on Ray node *target* can move to the other machines, from DESCRIBE_RAY_PLACEMENT.

    *deployments* -- by Serve app name -- gives each copy its deployment's id
    and name, and optionally ``ready_nodes``: the Ray nodes holding its
    model's files (a copy moved anywhere else could not load). *names* gives
    Ray node ids readable names.
    """
    deployments = deployments or {}
    names = names or {}
    free = {
        str(n.get("node_id")): {k: float(v) for k, v in (n.get("available") or {}).items()}
        for n in placement.get("nodes") or []
        if n.get("node_id") and n.get("node_id") != target and n.get("status") in _SCHEDULABLE
    }
    affected = []
    others = 0
    for replica in placement.get("replicas") or []:
        bundles = [b for b in replica.get("bundles") or [] if isinstance(b, dict)]
        where = {str(b.get("node_id")) for b in bundles if b.get("node_id")}
        if replica.get("node_id"):
            where.add(str(replica["node_id"]))
        if target not in where:
            continue
        needs = [_demand(b.get("resources")) for b in bundles]
        gpus = sum(n.get("GPU", 0.0) for n in needs)
        if gpus <= 0:
            # The router and other small parts: Serve moves them too, and
            # they need no GPU, so there is always room.
            others += 1
            continue
        affected.append((replica, needs, gpus, where))

    copies = []
    for replica, needs, gpus, where in sorted(affected, key=lambda item: (-item[2], str(item[0].get("app")))):
        app = str(replica.get("app") or "")
        info = deployments.get(app) or {}
        ready = info.get("ready_nodes")
        pool = free if ready is None else {n: a for n, a in free.items() if n in ready}
        placed = _fit(needs, pool, replica.get("strategy")) if pool else None
        if placed is not None and pool is not free:
            for node, available in pool.items():  # what the copy took, taken from everyone's view
                free[node] = available
        entry: dict[str, Any] = {
            "app": app,
            "deployment_id": info.get("deployment_id"),
            "name": info.get("name") or app,
            "replica_id": replica.get("replica_id"),
            "actor_name": replica.get("actor_name"),
            "state": replica.get("state"),
            "gpus": round(gpus, 4),
            "spans": sorted(names.get(n, n) for n in where),
            "moves": placed is not None,
            "to": sorted({names.get(n, n) for n in (placed or {}).values()}),
            "reason": None,
        }
        if placed is None:
            if not free:
                entry["reason"] = "The cluster has no other machine that can take it."
            elif ready is not None and not pool:
                entry["reason"] = "No other machine holds this model's files yet."
            else:
                most = max((a.get("GPU", 0.0) for a in (pool or free).values()), default=0.0)
                entry["reason"] = (
                    f"It needs {_gpus(gpus)} GPU; the most another machine has free is {_gpus(round(most, 4))} GPU."
                )
        copies.append(entry)
    return {
        "copies": copies,
        "moving": sum(1 for c in copies if c["moves"]),
        "stopping": sum(1 for c in copies if not c["moves"]),
        "other_parts": others,
    }


def copies_on(placement: dict[str, Any], target: str) -> list[dict[str, Any]]:
    """The GPU copies that still hold resources on Ray node *target*."""
    out = []
    for replica in placement.get("replicas") or []:
        bundles = [b for b in replica.get("bundles") or [] if isinstance(b, dict)]
        gpus = sum(_demand(b.get("resources")).get("GPU", 0.0) for b in bundles)
        if gpus <= 0:
            continue
        where = {str(b.get("node_id")) for b in bundles if b.get("node_id")}
        if replica.get("node_id"):
            where.add(str(replica["node_id"]))
        if target in where:
            out.append(replica)
    return out


# ---------------------------------------------------------------------------
# Looking things up
# ---------------------------------------------------------------------------


def _fabric(environment: InferenceEnvironment) -> dict[str, Any]:
    return dict((environment.observed_status_json or {}).get("resolved_fabric") or {})


def _addresses(environment: InferenceEnvironment, node: InfraNode) -> set[str]:
    """The addresses Ray may know *node* by in *environment*: its binding, its host."""
    out = set()
    binding = (_fabric(environment).get("node_bindings") or {}).get(str(node.id)) or {}
    if binding.get("ip"):
        out.add(str(binding["ip"]).strip())
    host = str(node.host or "").strip()
    if host.startswith("host="):
        host = host[len("host="):]
    if host:
        out.add(host)
    return out


def ray_node_of(placement: dict[str, Any], addresses: set[str]) -> str | None:
    """The Ray node id of the machine known by *addresses*, among the live ones."""
    for node in placement.get("nodes") or []:
        if str(node.get("ip") or "").strip() in addresses and node.get("status") != "DEAD":
            return str(node.get("node_id"))
    return None


async def _cluster_of(session: AsyncSession, node_id: uuid.UUID) -> tuple[InferenceEnvironment, InferenceEnvironmentNode] | None:
    """The cluster *node_id* belongs to: the one meant to run, if several."""
    rows = list(
        (await session.execute(
            select(InferenceEnvironmentNode, InferenceEnvironment)
            .join(InferenceEnvironment, InferenceEnvironment.id == InferenceEnvironmentNode.environment_id)
            .where(InferenceEnvironmentNode.node_id == node_id)
        )).all()
    )
    if not rows:
        return None
    rows.sort(key=lambda r: r[1].desired_state != "running")
    member, environment = rows[0]
    return environment, member


async def _deployments_by_app(session: AsyncSession, environment: InferenceEnvironment) -> dict[str, dict[str, Any]]:
    from faden_backend.db.models.inference import ModelAvailability, ModelAvailabilityStatus  # noqa: PLC0415

    rows = list(
        (await session.execute(
            select(InferenceDeployment).where(InferenceDeployment.environment_id == environment.id)
        )).scalars().all()
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[f"{APP_PREFIX}{row.id}"] = {"deployment_id": str(row.id), "name": row.name, "model_id": row.model_id}
    # Which machines hold each model: a copy moved anywhere else could not
    # load (the runtime is offline). Only for models with availability
    # records -- one loading from the machines' own cache has none.
    model_ids = {info["model_id"] for info in out.values() if info.get("model_id")}
    if model_ids:
        ready = (
            await session.execute(
                select(ModelAvailability.model_id, ModelAvailability.node_id, ModelAvailability.status)
                .where(ModelAvailability.model_id.in_(model_ids))
            )
        ).all()
        by_model: dict[Any, dict[Any, str]] = {}
        for model_id, node_id, status in ready:
            by_model.setdefault(model_id, {})[node_id] = str(status)
        for info in out.values():
            records = by_model.get(info.get("model_id"))
            if records:
                info["ready_node_uuids"] = {
                    node for node, status in records.items() if status == ModelAvailabilityStatus.READY.value
                }
    return out


async def _ray_names(session: AsyncSession, environment: InferenceEnvironment, placement: dict[str, Any]) -> tuple[dict[str, str], dict[Any, str]]:
    """Ray node id -> machine name, and machine id -> Ray node id, for the cluster's members."""
    machines = await EnvironmentNodeDAO(session).machines(environment.id)
    names: dict[str, str] = {}
    ray_of: dict[Any, str] = {}
    for machine in machines:
        ray_id = ray_node_of(placement, _addresses(environment, machine))
        if ray_id:
            names[ray_id] = _name(machine)
            ray_of[machine.id] = ray_id
    return names, ray_of


def _client(gateway: Any) -> Any:
    from faden_backend.services.inference.drivers.ray.client import RayClusterClient  # noqa: PLC0415

    return RayClusterClient(gateway)


async def _placement(gateway: Any, environment: InferenceEnvironment, *, budget_sec: float = 90) -> dict[str, Any]:
    doc = await _client(gateway).describe_placement(head_node_id=environment.head_node_id, budget_sec=budget_sec)
    if doc is None:
        raise MembershipError(
            "The cluster's head did not say where the copies run in time. Try again in a minute.",
            status_code=503,
        )
    return doc


# ---------------------------------------------------------------------------
# Draining
# ---------------------------------------------------------------------------


async def drain_plan(session: AsyncSession, gateway: Any, node_id: uuid.UUID) -> dict[str, Any]:
    """What draining *node_id* would do: which copies move where, which would stop, and why."""
    node = await session.get(InfraNode, node_id)
    if node is None:
        raise MembershipError("There is no such machine.", status_code=404)
    found = await _cluster_of(session, node_id)
    base: dict[str, Any] = {
        "node_id": str(node.id),
        "machine": _name(node),
        "draining": bool(node.draining),
        "in_cluster": found is not None,
        "copies": [],
        "moving": 0,
        "stopping": 0,
        "other_parts": 0,
        "can_drain": True,
        "reason": None,
    }
    if found is None:
        return base
    environment, _member = found
    base.update({"environment_id": str(environment.id), "environment_name": environment.name})
    drains = (environment.observed_status_json or {}).get("drains") or {}
    if str(node.id) in drains:
        base["drain"] = drains[str(node.id)]
    running = environment.desired_state == "running" and str(environment.status) in _UP
    base["cluster_running"] = running
    if environment.head_node_id == node.id and running:
        base.update(can_drain=False, reason=(
            f"{_name(node)} is the head of {environment.name}: the cluster cannot run without it. "
            "Stop the cluster instead, or drain the other machines."
        ))
        return base
    if not running:
        # Nothing runs on it through this cluster: draining only keeps new
        # work off it.
        return base
    head = await session.get(InfraNode, environment.head_node_id)
    old = [_name(n) for n in (node, head) if n is not None and not supported(n.capabilities_json)]
    if old:
        base.update(can_drain=False, reason=(
            "Draining needs agent 0.2.6 or later on this machine and on the cluster's head; update "
            + ", ".join(sorted(set(old))) + " (re-run the install line on it)."
        ))
        return base
    placement = await _placement(gateway, environment)
    names, ray_of = await _ray_names(session, environment, placement)
    target = ray_of.get(node.id)
    if target is None:
        # Not in Ray at all (not joined, or dropped out): nothing to move.
        base["ray_node_id"] = None
        return base
    deployments = await _deployments_by_app(session, environment)
    for info in deployments.values():
        uuids = info.pop("ready_node_uuids", None)
        if uuids is not None:
            info["ready_nodes"] = {ray_of[n] for n in uuids if n in ray_of}
    base.update(plan_drain(placement, target, deployments=deployments, names=names))
    base["ray_node_id"] = target
    if not placement.get("drain_patch_active") and placement.get("serve_running"):
        base["note"] = "Serve on this cluster restarts its controller once to follow drains; the models keep serving."
    return base


def _record(environment: InferenceEnvironment, node_id: Any, entry: dict[str, Any] | None) -> None:
    observed = dict(environment.observed_status_json or {})
    drains = dict(observed.get("drains") or {})
    if entry is None:
        drains.pop(str(node_id), None)
    else:
        drains[str(node_id)] = entry
    if drains:
        observed["drains"] = drains
    else:
        observed.pop("drains", None)
    environment.observed_status_json = observed


def _queue(session: AsyncSession, environment: InferenceEnvironment) -> None:
    from faden_backend.services.inference.service import _queue_for_reconcile  # noqa: PLC0415
    from faden_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    _queue_for_reconcile(environment)
    wake_reconciler_after_commit(session)


async def start_drain(
    session: AsyncSession,
    gateway: Any,
    node_id: uuid.UUID,
    *,
    stop_unmovable: bool = False,
    remove: bool = False,
) -> dict[str, Any]:
    """Drain *node_id*: no new work on it, its copies moved; with *remove*, out of the cluster after.

    Refused while a copy would stop and *stop_unmovable* was not given: the
    operator saw the plan and agreed first.
    """
    plan = await drain_plan(session, gateway, node_id)
    if not plan["can_drain"]:
        raise MembershipError(plan["reason"], plan=plan)
    if plan["stopping"] and not stop_unmovable:
        raise MembershipError(
            f"{plan['stopping']} model {'copy' if plan['stopping'] == 1 else 'copies'} on "
            f"{plan['machine']} cannot move and would stop. Confirm to drain anyway.",
            plan=plan,
        )
    node = await session.get(InfraNode, node_id)
    from faden_backend.db.dao.node_control_dao import NodeControlDAO  # noqa: PLC0415

    await NodeControlDAO(session).set_node_draining(node=node, enabled=True)
    if plan.get("cluster_running") and plan.get("ray_node_id"):
        environment = await session.get(InferenceEnvironment, uuid.UUID(plan["environment_id"]))
        _record(environment, node_id, {
            "state": "starting",
            "ray_node_id": plan["ray_node_id"],
            "remove": remove,
            "stop_unmovable": stop_unmovable,
            "started_at": _now().isoformat(),
            "copies": plan["copies"],
            "left": len(plan["copies"]),
            "message": "Starting the drain.",
        })
        _queue(session, environment)
    elif remove and plan.get("in_cluster"):
        # Not in Ray (cluster stopped, or the machine never joined): leaving is
        # just the record.
        await _forget_member(session, uuid.UUID(plan["environment_id"]), node_id)
        await NodeControlDAO(session).set_node_draining(node=node, enabled=False)
    await session.flush()
    return await drain_plan(session, gateway, node_id) if not remove else plan


async def stop_drain(session: AsyncSession, node_id: uuid.UUID) -> dict[str, Any]:
    """Stop draining *node_id*. In a running cluster it leaves Ray and joins again (Ray has no undo)."""
    node = await session.get(InfraNode, node_id)
    if node is None:
        raise MembershipError("There is no such machine.", status_code=404)
    from faden_backend.db.dao.node_control_dao import NodeControlDAO  # noqa: PLC0415

    await NodeControlDAO(session).set_node_draining(node=node, enabled=False)
    found = await _cluster_of(session, node_id)
    resuming = False
    if found is not None:
        environment, _member = found
        entry = ((environment.observed_status_json or {}).get("drains") or {}).get(str(node_id))
        if entry is not None:
            if not entry.get("ray_drained"):
                # Never drained in Ray (not started yet, or it failed before):
                # nothing to undo, and rejoining would restart its copies.
                _record(environment, node_id, None)
            else:
                _record(environment, node_id, {**entry, "state": "resuming", "remove": False,
                                               "message": "Rejoining the cluster."})
                resuming = True
            _queue(session, environment)
    await session.flush()
    return {"node_id": str(node_id), "draining": False, "resuming": resuming}


async def advance(session: AsyncSession, gateway: Any, environment: InferenceEnvironment, *, manager: Any, ctx: dict[str, Any]) -> None:
    """Take every drain on *environment* one step further (from the cluster's health check).

    *manager* leaves and rejoins members (``RayEnvironmentManager``); *ctx*
    is what it needs for that (config, credential, head address).
    """
    drains = dict((environment.observed_status_json or {}).get("drains") or {})
    if not drains:
        return
    client = _client(gateway)
    from faden_backend.services.inference.drivers.ray.client import RayCommandError  # noqa: PLC0415

    placement: dict[str, Any] | None = None
    for node_key, entry in drains.items():
        entry = dict(entry)
        node_id = uuid.UUID(node_key)
        state = entry.get("state")
        tag = str(entry.get("started_at") or "")
        try:
            if state == "starting":
                await client.enable_serve_drain(
                    head_node_id=environment.head_node_id, key=f"membership:{environment.id}:{node_key}:{tag}:enable",
                )
                await client.drain_node(node_id=node_id, key=f"membership:{environment.id}:{node_key}:{tag}:drain")
                # From here Ray has drained it: only leaving and joining again undoes that.
                entry.update(state="moving", ray_drained=True, moving_since=_now().isoformat(),
                             message="Moving the copies to the other machines.")
                state = "moving"
            if state == "moving":
                if placement is None:
                    placement = await client.describe_placement(head_node_id=environment.head_node_id, budget_sec=90)
                if placement is None:
                    _record(environment, node_id, entry)
                    continue
                left = copies_on(placement, str(entry.get("ray_node_id")))
                stuck_names = {c["actor_name"] for c in entry.get("copies") or [] if not c.get("moves")}
                to_stop = [r["actor_name"] for r in left if r.get("actor_name") in stuck_names]
                if to_stop and entry.get("stop_unmovable"):
                    await client.stop_replicas(
                        head_node_id=environment.head_node_id, actor_names=to_stop,
                        key=f"membership:{environment.id}:{node_key}:{tag}:stop:{len(to_stop)}",
                    )
                    left = [r for r in left if r.get("actor_name") not in set(to_stop)]
                entry["left"] = len(left)
                if not left:
                    entry.update(state="leaving" if entry.get("remove") else "drained", drained_at=_now().isoformat(),
                                 message="No copies left on this machine.")
                    state = entry["state"]
                else:
                    since = datetime.fromisoformat(entry.get("moving_since") or _now().isoformat())
                    waiting = ", ".join(sorted({str(r.get("app")) for r in left}))
                    entry["message"] = (
                        f"Still moving {len(left)} {'copy' if len(left) == 1 else 'copies'}."
                        if _now() - since < _STUCK_AFTER
                        else f"{len(left)} {'copy has' if len(left) == 1 else 'copies have'} not moved after "
                        f"{int(_STUCK_AFTER.total_seconds() // 60)} minutes: the replacement did not start "
                        f"({waiting}). See the deployment's errors, or stop the drain."
                    )
            if state == "leaving":
                await manager.leave_member(session, environment, node_id, gateway=gateway, tag=tag)
                await _forget_member(session, environment.id, node_id)
                node = await session.get(InfraNode, node_id)
                if node is not None:
                    from faden_backend.db.dao.node_control_dao import NodeControlDAO  # noqa: PLC0415

                    await NodeControlDAO(session).set_node_draining(node=node, enabled=False)
                entry = None
            elif state == "resuming":
                member = (await EnvironmentNodeDAO(session).list_for_node(node_id))
                member = next((m for m in member if m.environment_id == environment.id), None)
                if member is not None:
                    await manager.rejoin_member(session, environment, member, gateway=gateway, tag=tag, **ctx)
                entry = None
        except RayCommandError as exc:
            entry.update(state="failed" if state == "starting" else state, message=exc.detail)
        except Exception as exc:  # noqa: BLE001 - one drain must not stop the others or the health check
            log.warning("Drain of %s on %s: %s", node_key, environment.id, exc, exc_info=True)
            entry["message"] = str(exc)
        _record(environment, node_id, entry)


async def _forget_member(session: AsyncSession, environment_id: uuid.UUID, node_id: uuid.UUID) -> None:
    """Remove *node_id* from the cluster's record: its membership and its network binding."""
    await EnvironmentNodeDAO(session).remove_node(environment_id, node_id)
    environment = await session.get(InferenceEnvironment, environment_id)
    if environment is None:
        return
    observed = dict(environment.observed_status_json or {})
    for key in ("resolved_fabric", "network"):
        section = dict(observed.get(key) or {})
        bindings = dict(section.get("node_bindings") or {})
        if bindings.pop(str(node_id), None) is not None:
            section["node_bindings"] = bindings
            observed[key] = section
    environment.observed_status_json = observed


# ---------------------------------------------------------------------------
# Adding and removing machines
# ---------------------------------------------------------------------------


def _fabric_entry(node: InfraNode, cidr: str | None) -> dict[str, Any] | None:
    """*node*'s usable address on network *cidr*, from its inventory."""
    from faden_backend.services.inference.planner import _fabric_is_usable  # noqa: PLC0415

    if not cidr:
        return None
    for fabric in ((node.capabilities_json or {}).get("network") or {}).get("fabrics") or []:
        if fabric.get("cidr") == cidr and fabric.get("ip") and _fabric_is_usable(fabric)[0]:
            return fabric
    return None


async def candidates(session: AsyncSession, environment_id: uuid.UUID) -> dict[str, Any]:
    """The machines that could join this cluster, and why the others cannot."""
    environment = await session.get(InferenceEnvironment, environment_id)
    if environment is None:
        raise MembershipError("There is no such cluster.", status_code=404)
    member_ids = {m.node_id for m in await EnvironmentNodeDAO(session).list_for_environment(environment.id)}
    other = {
        row.node_id: name
        for row, name in (
            await session.execute(
                select(InferenceEnvironmentNode, InferenceEnvironment.name)
                .join(InferenceEnvironment, InferenceEnvironment.id == InferenceEnvironmentNode.environment_id)
                .where(InferenceEnvironmentNode.environment_id != environment.id)
            )
        ).all()
    }
    cidr = _fabric(environment).get("cidr")
    from faden_backend.services.inference import ray_tls  # noqa: PLC0415
    from faden_backend.services.inference.drivers.ray.schemas import RayEnvironmentConfig  # noqa: PLC0415

    tls = RayEnvironmentConfig.model_validate(environment.config_json or {}).ray_tls
    head = await session.get(InfraNode, environment.head_node_id) if environment.head_node_id else None
    out = []
    for node in (await session.execute(select(InfraNode).order_by(InfraNode.agent_id))).scalars():
        if node.id in member_ids:
            continue
        reasons: list[str] = []
        warnings: list[str] = []
        if str(node.status or "").lower() == "offline":
            reasons.append("It is offline.")
        if node.maintenance_mode:
            reasons.append("It is in maintenance.")
        if node.draining:
            reasons.append("It is being drained.")
        if node.id in other:
            reasons.append(f"It belongs to the cluster {other[node.id]}; remove it there first.")
        if cidr and _fabric_entry(node, cidr) is None:
            reasons.append(f"It has no address on the cluster's network ({cidr}).")
        if tls and not ray_tls.supported(node.capabilities_json):
            reasons.append("The cluster runs Ray with TLS, which needs agent 0.1.18 or later on it.")
        arch = (node.capabilities_json or {}).get("machine")
        head_arch = (head.capabilities_json or {}).get("machine") if head else None
        if arch and head_arch and arch != head_arch:
            warnings.append(
                f"It is {arch}, the head is {head_arch}: it runs another runtime image, so no copy of a model "
                "may span both kinds of machine."
            )
        try:
            gpus = int((node.capabilities_json or {}).get("gpu_count") or 0)
        except (TypeError, ValueError):
            gpus = 0
        out.append({
            "node_id": str(node.id),
            "machine": _name(node),
            "host": node.host,
            "gpus": gpus,
            "can_add": not reasons,
            "reasons": reasons,
            "warnings": warnings,
        })
    running = environment.desired_state == "running" and str(environment.status) in _UP
    return {"environment_id": str(environment.id), "cluster_running": running, "network": cidr, "machines": out}


async def add_members(session: AsyncSession, gateway: Any, environment_id: uuid.UUID, node_ids: list[uuid.UUID]) -> dict[str, Any]:
    """Add machines to a running cluster: checked, bound to its network, then joined by the reconciler."""
    from faden_backend.services.inference.planner import (  # noqa: PLC0415
        FabricCandidate,
        MultiNodeFabricPlanner,
        NodeFabricBinding,
    )
    from faden_backend.services.inference.service import EnvironmentService  # noqa: PLC0415

    environment = await session.get(InferenceEnvironment, environment_id)
    if environment is None:
        raise MembershipError("There is no such cluster.", status_code=404)
    if not node_ids:
        raise MembershipError("Pick at least one machine.", status_code=422)
    listed = {m["node_id"]: m for m in (await candidates(session, environment_id))["machines"]}
    refused = [
        f"{listed[str(n)]['machine'] if str(n) in listed else n}: {' '.join(listed[str(n)]['reasons']) if str(n) in listed else 'already a member, or unknown.'}"
        for n in node_ids
        if str(n) not in listed or not listed[str(n)]["can_add"]
    ]
    if refused:
        raise MembershipError("These machines cannot join: " + " ".join(refused))

    fabric = _fabric(environment)
    bindings = dict(fabric.get("node_bindings") or {})
    new_bindings: dict[str, dict[str, Any]] = {}
    if fabric.get("cidr"):
        head_binding = bindings.get(str(environment.head_node_id))
        for node_id in node_ids:
            node = await session.get(InfraNode, node_id)
            entry = _fabric_entry(node, fabric["cidr"])
            binding = NodeFabricBinding(
                node_id=str(node.id),
                interface=entry.get("interface", ""),
                ip=entry.get("ip", ""),
                netmask=entry.get("netmask"),
                speed_gbps=float(entry.get("speed_gbps") or 1.0),
                speed_mbps=entry.get("speed_mbps"),
                link_type=str(entry.get("link_type") or "ethernet").lower(),
                rdma_device=entry.get("rdma_device"),
                pci_address=entry.get("pci_address"),
                mtu=int(entry.get("mtu") or 1500),
                is_up=bool(entry.get("is_up", True)),
            )
            if head_binding and gateway is not None:
                # The same challenge the cluster passed when it was planned:
                # the new machine must reach the head on this network.
                probe = await MultiNodeFabricPlanner(session, gateway=gateway)._probe_candidate(FabricCandidate(
                    candidate_id=f"join-{environment.id}-{node.id}",
                    fabric_type=str(fabric.get("fabric_type") or "ethernet"),
                    cidr=str(fabric["cidr"]),
                    speed_gbps=float(fabric.get("speed_gbps") or 1.0),
                    mtu=int(fabric.get("mtu") or 1500),
                    is_management=bool(fabric.get("is_management")),
                    score=0,
                    node_bindings={
                        str(environment.head_node_id): NodeFabricBinding(**head_binding),
                        str(node.id): binding,
                    },
                ))
                if probe.reachable is not True:
                    raise MembershipError(
                        f"{_name(node)} could not reach the cluster's head on {fabric['cidr']}: {probe.detail}"
                    )
            new_bindings[str(node.id)] = binding.model_dump()

    service = EnvironmentService(session)
    for node_id in node_ids:
        await service.add_node(environment.id, node_id, "worker")
    if new_bindings:
        observed = dict(environment.observed_status_json or {})
        for key in ("resolved_fabric", "network"):
            section = dict(observed.get(key) or {})
            section["node_bindings"] = {**dict(section.get("node_bindings") or {}), **new_bindings}
            observed[key] = section
        environment.observed_status_json = observed
    # A new generation: the reconciler runs the start sequence again, which
    # leaves every member already up as it is and brings the new ones up.
    environment.generation += 1
    from faden_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    wake_reconciler_after_commit(session)
    await session.flush()
    return {"environment_id": str(environment.id), "added": [str(n) for n in node_ids]}


async def removal_plan(session: AsyncSession, gateway: Any, environment_id: uuid.UUID, node_id: uuid.UUID) -> dict[str, Any]:
    """What removing *node_id* from the cluster would do: the drain plan, for this cluster."""
    members = await EnvironmentNodeDAO(session).list_for_environment(environment_id)
    if not any(m.node_id == node_id for m in members):
        raise MembershipError("That machine is not in this cluster.", status_code=404)
    plan = await drain_plan(session, gateway, node_id)
    if plan.get("environment_id") != str(environment_id):
        raise MembershipError("That machine is in another cluster that is meant to run.", status_code=409)
    environment = await session.get(InferenceEnvironment, environment_id)
    if environment.head_node_id == node_id:
        plan.update(can_drain=False, reason=(
            f"{plan['machine']} is the cluster's head. Stop the cluster first to remove it."
            if plan.get("cluster_running") else plan.get("reason")
        ))
        if not plan.get("cluster_running"):
            plan["can_drain"] = True
    return plan


async def remove_member(
    session: AsyncSession, gateway: Any, environment_id: uuid.UUID, node_id: uuid.UUID, *, stop_unmovable: bool = False,
) -> dict[str, Any]:
    """Take *node_id* out of the cluster: drained first if the cluster runs, then out of Ray, then off the record."""
    plan = await removal_plan(session, gateway, environment_id, node_id)
    if not plan["can_drain"]:
        raise MembershipError(plan["reason"], plan=plan)
    environment = await session.get(InferenceEnvironment, environment_id)
    if not plan.get("cluster_running"):
        if environment.head_node_id == node_id:
            environment.head_node_id = None
        await _forget_member(session, environment_id, node_id)
        await session.flush()
        return {"removed": True, "pending": False}
    await start_drain(session, gateway, node_id, stop_unmovable=stop_unmovable, remove=True)
    return {"removed": False, "pending": True}
