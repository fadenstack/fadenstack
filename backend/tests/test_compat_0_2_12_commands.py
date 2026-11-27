"""Commands the new backend may send are ones agent 0.2.12 can answer, or go only to agents that ask for them.

COMPAT DESIGN rows 1-2. The frozen set below is ``NodeCommandType`` of ``node-agent-v0.2.12``
(``agent/faden_agent/models.py``). A command type added later fails
``test_a_new_command_type_is_a_compat_decision`` until it is listed here on purpose, with the
capability that gates it. The agent half (0.2.12 parsing the frames) is
``agent/tests/test_compat_0_2_12.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import EnvironmentStatus
from faden_backend.db.models.node_control import NodeCommandType
from faden_backend.services.inference import reconciliation
from tests.test_compat_0_2_12_nodes import NODES_0_2_12
from tests.test_standalone_driver import (  # noqa: F401  (fixtures)
    Script,
    _Ctx,
    _deployment,
    _environment,
    _mac_runtime,
    _member,
    _node,
    script,
)

AGENT_0_2_12_COMMANDS = {
    "apply_system_updates", "check_internet", "check_system_updates", "collect_diagnostics",
    "delete_serve_app", "deploy_workload", "describe_ray_cluster", "describe_ray_placement",
    "drain_node", "drain_ray_node", "enable_serve_drain", "ensure_ray_runtime", "ensure_runtime_image",
    "fetch_container_logs", "get_ray_serve_status", "get_ray_status", "host_op", "join_ray_cluster",
    "leave_ray_cluster", "model_tls_route", "refresh_inventory", "remove_workload", "restart_workload",
    "resume_node", "run_serve_app", "scale_serve_app", "serve_runtime_image", "set_maintenance_mode",
    "shutdown_serve", "start_ray_head", "start_workload", "stop_ray", "stop_serve_replicas",
    "stop_workload", "sync_model", "sync_node_profile", "update_workload", "validate_fabric_connect",
    "validate_fabric_listen",
}
#: Added since 0.2.12, each sent only to an agent reporting ``native_runtime`` (admission.node_refusal).
GATED_NEW_COMMANDS = {
    "stage_native_runtime", "native_runtime_status", "start_native_worker",
    "native_worker_status", "stop_native_worker", "native_worker_logs",
}


def test_a_new_command_type_is_a_compat_decision() -> None:
    backend = {c.value for c in NodeCommandType}
    assert AGENT_0_2_12_COMMANDS <= backend, "a command 0.2.12 agents rely on was removed"
    assert backend - AGENT_0_2_12_COMMANDS == GATED_NEW_COMMANDS, (
        "a new command type reaches old agents as 'Unsupported command type' unless it is capability-gated: "
        "gate it, then list it in GATED_NEW_COMMANDS"
    )


def test_the_native_commands_are_built_only_by_the_standalone_driver() -> None:
    root = Path(__file__).resolve().parents[1] / "faden_backend"
    pattern = re.compile(r"NodeCommandType\.(%s)\b" % "|".join(c.upper() for c in GATED_NEW_COMMANDS))
    users = {p.relative_to(root).as_posix() for p in root.rglob("*.py") if pattern.search(p.read_text())}
    outside = {u for u in users if u != "db/models/node_control.py" and not u.startswith("services/inference/drivers/standalone/")}
    assert not outside, outside


@pytest.mark.anyio()
@pytest.mark.parametrize("name", sorted(NODES_0_2_12))
async def test_no_native_command_reaches_a_0_2_12_machine(
    dbsession: AsyncSession, script: Script, name: str,  # noqa: F811
) -> None:
    """Even placed into a standalone environment by hand, an agent without ``native_runtime`` is asked nothing."""
    env = await _environment(dbsession)
    await _member(dbsession, env, await _node(dbsession, NODES_0_2_12[name]))
    ctx: Any = _Ctx(dbsession)
    await reconciliation.reconcile_environment(ctx, env)
    assert env.status == EnvironmentStatus.FAILED
    assert "cannot serve models on its own yet" in env.status_message
    dep = await _deployment(dbsession, env)
    await reconciliation.reconcile_deployment(ctx, dep)
    assert script.asked == []
