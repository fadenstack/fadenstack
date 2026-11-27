"""Engine metrics for a Mac: the standalone driver's targets, and the same ``targets.json`` Linux uses (CONTRACTS C4)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import DeploymentDesiredState, DeploymentPhase
from faden_backend.services.inference import reconciliation
from faden_backend.services.inference.observability import DeploymentMetrics, EnvironmentMetrics
from faden_backend.services.inference.registry import registry
from faden_backend.services.llm import monitoring
from faden_backend.services.llm.monitoring import MonitoringProvisioner
from tests.test_native_bundles import MAC
from tests.test_standalone_driver import (  # noqa: F401  (fixtures)
    _Ctx,
    _deployment,
    _environment,
    _member,
    _mac_runtime,
    _node,
)

pytestmark = pytest.mark.anyio()

@pytest.fixture(autouse=True)
def _managers_do_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the managers do is test_standalone_driver's business; here only the target sync after them."""
    from faden_backend.services.inference.drivers.standalone.deployment import StandaloneDeploymentManager
    from faden_backend.services.inference.drivers.standalone.environment import StandaloneEnvironmentManager

    async def nothing(self: Any, *_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(StandaloneEnvironmentManager, "reconcile_environment", nothing)
    monkeypatch.setattr(StandaloneDeploymentManager, "reconcile_deployment", nothing)


MAC_METRICS = dict(MAC, native_metrics={"port": 9468})


@pytest.fixture()
def prov(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MonitoringProvisioner:
    targets = tmp_path / "targets.json"
    targets.write_text("[]", encoding="utf-8")
    p = MonitoringProvisioner(targets_file=str(targets))
    monkeypatch.setattr(monitoring, "get_monitoring_provisioner", lambda: p)

    async def no_dashboard(**_: Any) -> None:
        return None

    monkeypatch.setattr(p, "provision_environment", no_dashboard)
    monkeypatch.setattr(p, "_schedule_reload", lambda: None)
    return p


def _entries(p: MonitoringProvisioner) -> list[dict[str, Any]]:
    return json.loads(Path(p._targets_file).read_text(encoding="utf-8"))  # noqa: SLF001


async def _mac_env(session: AsyncSession, caps: dict[str, Any] = MAC_METRICS):  # noqa: ANN202
    env = await _environment(session)
    node = await _node(session, caps, host="10.0.0.9")
    await _member(session, env, node)
    dep = await _deployment(session, env)
    return env, node, dep


async def test_the_driver_returns_the_machines_scrape_target(dbsession: AsyncSession) -> None:
    env, node, dep = await _mac_env(dbsession)
    dep.phase = DeploymentPhase.RUNNING.value
    await dbsession.flush()
    driver = registry.get("standalone")()

    metrics = await driver.deployment_metrics(dbsession, dep)
    assert isinstance(metrics, DeploymentMetrics)
    assert metrics.app_name == dep.name and metrics.replicas_ready == 1
    assert [(t.address, t.port, t.url) for t in metrics.scrape_targets] == [("10.0.0.9", 9468, "http://10.0.0.9:9468/metrics")]

    env_metrics = await driver.environment_metrics(dbsession, env)
    assert isinstance(env_metrics, EnvironmentMetrics)
    assert env_metrics.nodes_total == 1 and env_metrics.alive
    assert [t.port for t in env_metrics.scrape_targets] == [9468]


async def test_an_agent_without_the_metrics_port_says_so_instead_of_inventing_a_target(dbsession: AsyncSession) -> None:
    env, _, dep = await _mac_env(dbsession, MAC)
    dep.phase = DeploymentPhase.RUNNING.value
    metrics = await registry.get("standalone")().deployment_metrics(dbsession, dep)
    assert metrics.scrape_targets == []
    assert metrics.partials and "upgrade" in metrics.partials[0].reason


async def test_a_deployment_that_is_not_serving_has_no_target(dbsession: AsyncSession) -> None:
    _, _, dep = await _mac_env(dbsession)
    dep.phase = DeploymentPhase.PENDING.value
    metrics = await registry.get("standalone")().deployment_metrics(dbsession, dep)
    assert metrics.scrape_targets == []


async def test_a_serving_deployment_is_written_into_targets_json_with_the_dashboard_labels(
    dbsession: AsyncSession, prov: MonitoringProvisioner,
) -> None:
    env, _, dep = await _mac_env(dbsession)
    dep.phase = DeploymentPhase.RUNNING.value
    await dbsession.flush()

    await reconciliation.reconcile_environment(_Ctx(dbsession), env)

    (entry,) = _entries(prov)
    assert entry["targets"] == ["10.0.0.9:9468"]
    labels = entry["labels"]
    assert labels["runtime_name"] == env.name  # what the dashboard template and the stat cards select on
    assert labels["environment_id"] == str(env.id)
    assert labels["node_host"] == "10.0.0.9"
    assert labels["job"] == "native"


async def test_stop_and_delete_take_the_target_away_and_leave_other_environments(
    dbsession: AsyncSession, prov: MonitoringProvisioner,
) -> None:
    env, _, dep = await _mac_env(dbsession)
    other = await prov.sync_ray_targets(environment_id=uuid.uuid4(), environment_name="dgx", targets=[{"address": "192.0.2.1", "port": 1}])
    assert other == 1
    dep.phase = DeploymentPhase.RUNNING.value
    await dbsession.flush()
    await reconciliation.reconcile_environment(_Ctx(dbsession), env)
    assert len(_entries(prov)) == 2

    dep.desired_state = DeploymentDesiredState.STOPPED.value
    await dbsession.flush()
    await reconciliation.reconcile_environment(_Ctx(dbsession), env)
    assert [e["labels"]["runtime_name"] for e in _entries(prov)] == ["dgx"]

    dep.desired_state = DeploymentDesiredState.ACTIVE.value
    dep.phase = DeploymentPhase.RUNNING.value
    await dbsession.flush()
    await reconciliation.reconcile_environment(_Ctx(dbsession), env)
    assert len(_entries(prov)) == 2

    dep.desired_state = DeploymentDesiredState.DELETED.value
    dep.phase = DeploymentPhase.DELETED.value
    await dbsession.flush()
    await reconciliation.reconcile_environment(_Ctx(dbsession), env)
    assert [e["labels"]["runtime_name"] for e in _entries(prov)] == ["dgx"]


async def test_a_deployment_reconcile_refreshes_the_target_too(
    dbsession: AsyncSession, prov: MonitoringProvisioner,
) -> None:
    env, _, dep = await _mac_env(dbsession)
    dep.phase = DeploymentPhase.RUNNING.value
    await dbsession.flush()
    await reconciliation.reconcile_deployment(_Ctx(dbsession), dep)
    assert [e["targets"] for e in _entries(prov)] == [["10.0.0.9:9468"]]


async def test_an_old_agent_gets_no_target(dbsession: AsyncSession, prov: MonitoringProvisioner) -> None:
    env, _, dep = await _mac_env(dbsession, MAC)
    dep.phase = DeploymentPhase.RUNNING.value
    await dbsession.flush()
    await reconciliation.reconcile_environment(_Ctx(dbsession), env)
    assert _entries(prov) == []


def test_ray_targets_are_unchanged() -> None:
    import inspect

    assert inspect.signature(MonitoringProvisioner.sync_ray_targets).parameters["job"].default == "ray"


_TEMPLATE = Path(__file__).resolve().parents[2] / "shared/grafana/dashboards/templates/vllm-runtime.json"


@pytest.mark.skipif(not _TEMPLATE.exists(), reason="dashboard template not present")
def test_the_request_rate_panel_falls_back_to_vllm_when_there_is_no_ray_serve() -> None:
    template = json.loads(_TEMPLATE.read_text(encoding="utf-8"))
    exprs = [t["expr"] for p in template["panels"] for t in p.get("targets", []) if "ray_serve_num_http_requests_total" in t["expr"]]
    ok = [e for e in exprs if 'status_code=~"2.."' in e]
    assert len(ok) == 1
    left, _, right = ok[0].partition(" or sum(rate(vllm:request_success_total{")
    assert 'ray_serve_num_http_requests_total{route=~".*/v1/chat/completions"' in left, "the Ray expression is first, so Linux is unchanged"
    assert 'runtime_name="__RUNTIME_NAME__"' in right.split("}", 1)[0]


def test_nothing_new_reaches_a_linux_or_0_2_12_agent() -> None:
    """The backend only READS ``capabilities_json.native_metrics`` (sent by a Mac agent) and issues no command for metrics."""
    from types import SimpleNamespace

    from faden_backend.services.inference.drivers.standalone.metrics import metrics_port
    from tests.test_native_bundles import GB10

    assert metrics_port(SimpleNamespace(capabilities_json=GB10)) is None
    for junk in ("9468", [9468], {"port": "x"}, {"port": 0}, {"port": 70000}, {}, None):
        assert metrics_port(SimpleNamespace(capabilities_json={"native_metrics": junk})) is None
    assert metrics_port(SimpleNamespace(capabilities_json={"native_metrics": {"port": 9468}})) == 9468
