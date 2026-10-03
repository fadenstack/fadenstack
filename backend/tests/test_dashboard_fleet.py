"""Dashboard fleet: machine health apart from load, clusters from stored snapshots, attention rules.

Health is what a machine reports, or "stale" when it has gone quiet; a GPU
at 100 % on a healthy machine is healthy. Clusters' free GPUs come from the
reconciler's stored observation, never from a live Ray call; a cluster never
observed has them unknown, not 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.node_control import InfraNode, InfraNodeInventorySnapshot
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.dashboard import attention as attention_rules
from faden_backend.services.dashboard import capacity as cap
from faden_backend.web.api.admin.dashboard.common import dashboard_now

pytestmark = pytest.mark.anyio()

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def _node(**overrides: object) -> SimpleNamespace:
    base = {
        "status": "healthy",
        "maintenance_mode": False,
        "draining": False,
        "last_seen": NOW - timedelta(seconds=10),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# ── health is reported state, not load ──────────────────────────────


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "ok"),
        ({"last_seen": NOW - timedelta(minutes=2)}, "stale"),
        ({"last_seen": None}, "stale"),
        ({"status": "offline"}, "offline"),
        ({"status": "offline", "last_seen": NOW - timedelta(days=3)}, "offline"),
        ({"maintenance_mode": True}, "maintenance"),
        ({"status": "draining"}, "maintenance"),
        ({"status": "degraded"}, "degraded"),
        ({"status": "error"}, "error"),
    ],
)
def test_machine_health_is_what_the_machine_reports(overrides: dict[str, object], expected: str) -> None:
    assert cap.machine_health(_node(**overrides), NOW) == expected


def test_a_gpu_at_full_load_on_a_healthy_machine_is_healthy() -> None:
    gpus, _ = cap.gpus_from(
        {
            "gpu": {
                "devices": [
                    {"index": 0, "utilization_pct": 100, "memory_used_mib": 23000, "memory_total_mib": 24000}
                ]
            }
        }
    )
    machine = cap.Machine("m", "m", cap.machine_health(_node(), NOW), NOW, 12.0, gpus)

    assert machine.health == "ok"
    assert machine.gpu_util_pct == 100
    assert machine.vram_pct == pytest.approx(95.8, rel=0.01)


def test_unified_memory_gpus_report_no_vram_share_of_their_own() -> None:
    gpus, machine_vram = cap.gpus_from(
        {
            "gpu": {
                "devices": [
                    {"index": 0, "utilization_pct": 40, "memory_used_mib": 1000, "memory_total_mib": None}
                ],
                "total_vram_bytes": 0,
            }
        }
    )
    assert gpus[0].vram_allocated_pct is None
    assert machine_vram is None


def test_histograms_exceptions_and_busy_gpus() -> None:
    def m(name: str, health: str, util: float | None) -> cap.Machine:
        return cap.Machine(
            name, name, health, NOW, None, [cap.Gpu(0, util, None, None)] if util is not None else []
        )

    machines = [
        m("a", "ok", 5),
        m("b", "ok", 99),
        m("c", "offline", None),
        m("d", "degraded", 45),
        m("e", "error", 100),
    ]

    assert cap.load_histogram([x.gpu_util_pct for x in machines]) == [1, 0, 1, 0, 2]
    assert [x.name for x in cap.exceptions(machines)] == ["e", "c", "d"]  # error, offline, degraded
    assert cap.gpus_busy(machines) == (3, 4)


# ── clusters from stored observations ───────────────────────────────


async def _seed_fleet(dbsession: AsyncSession) -> dict[str, InfraNode]:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    observed = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"dgx-{uuid.uuid4().hex[:6]}",
        status="ready",
        observed_status_json={
            "cluster": {"alive": True, "total_gpus": 2, "available_gpus": 1},
            "cluster_observed_at": (NOW - timedelta(seconds=40)).isoformat(),
        },
    )
    never = InferenceEnvironment(control_plane_id=cp.id, name=f"new-{uuid.uuid4().hex[:6]}", status="pending")
    dbsession.add_all([observed, never])
    nodes = {
        "a": InfraNode(
            agent_id=f"a-{uuid.uuid4().hex[:6]}", host="10.0.0.1", status="healthy", last_seen=NOW
        ),
        "b": InfraNode(
            agent_id=f"b-{uuid.uuid4().hex[:6]}",
            host="10.0.0.2",
            status="healthy",
            last_seen=NOW - timedelta(minutes=5),
        ),
        "c": InfraNode(
            agent_id=f"c-{uuid.uuid4().hex[:6]}",
            host="10.0.0.3",
            status="offline",
            last_seen=NOW - timedelta(days=1),
        ),
    }
    dbsession.add_all(nodes.values())
    await dbsession.flush()
    dbsession.add_all(
        [
            InferenceEnvironmentNode(environment_id=observed.id, node_id=nodes["a"].id, role="head"),
            InferenceEnvironmentNode(environment_id=observed.id, node_id=nodes["b"].id, role="worker"),
            InfraNodeInventorySnapshot(
                node_id=nodes["a"].id,
                inventory_json={},
                utilization_json={
                    "cpu_percent": 12,
                    "gpu": {"devices": [{"index": 0, "utilization_pct": 97, "temperature_c": 88}]},
                },
            ),
        ]
    )
    await dbsession.flush()
    return nodes


async def test_capacity_reads_clusters_from_their_stored_snapshot(dbsession: AsyncSession) -> None:
    nodes = await _seed_fleet(dbsession)

    fleet = await cap.load(dbsession, NOW)

    clusters = [g for g in fleet.groups if g.kind == "cluster"]
    observed = next(g for g in clusters if g.gpus_free is not None)
    assert (observed.gpus_total, observed.gpus_free, observed.stale) == (2, 1.0, False)
    assert [m.health for m in observed.machines] == ["ok", "stale"]
    never = next(g for g in clusters if g.gpus_free is None)
    assert never.observed_at is None
    assert fleet.gpus_free_unknown_clusters == 1
    unassigned = next(g for g in fleet.groups if g.kind == "unassigned")
    assert [m.id for m in unassigned.machines] == [str(nodes["c"].id)]


async def test_a_snapshot_older_than_three_reconcile_passes_is_stale(dbsession: AsyncSession) -> None:
    await _seed_fleet(dbsession)
    fleet = await cap.load(dbsession, NOW + timedelta(minutes=5))
    observed = next(g for g in fleet.groups if g.kind == "cluster" and g.gpus_free is not None)
    assert observed.stale


# ── attention ──────────────────────────────────────────────────────


class _FakeFacts:
    def __init__(self, fleet: cap.Capacity) -> None:
        self.now = NOW
        self.has_gateway = False
        self._fleet = fleet

    async def capacity(self) -> cap.Capacity:
        return self._fleet


async def test_machine_rules_report_state_and_heat_not_load(dbsession: AsyncSession) -> None:
    await _seed_fleet(dbsession)
    facts = _FakeFacts(await cap.load(dbsession, NOW))

    items = await attention_rules.machines(facts)  # type: ignore[arg-type]

    codes = sorted((i.code, i.severity) for i in items)
    # a: 97 % GPU (no item for load) at 88 degrees C; b: silent; c: offline.
    assert codes == [
        ("gpu_temperature_high", "warning"),
        ("node_offline", "critical"),
        ("node_telemetry_stale", "warning"),
    ]


async def test_a_failing_rule_is_named_and_the_others_still_answer() -> None:
    async def fine(_facts: object) -> list[attention_rules.Item]:
        return [
            attention_rules.Item("i1", "node_join_pending", "info", "infrastructure"),
            attention_rules.Item("c1", "node_offline", "critical", "infrastructure", since=NOW),
        ]

    async def broken(_facts: object) -> list[attention_rules.Item]:
        raise RuntimeError("db gone")

    items, failed = await attention_rules.evaluate(_FakeFacts(None), [fine, broken])  # type: ignore[arg-type, list-item]

    assert [i.code for i in items] == ["node_offline", "node_join_pending"]  # most severe first
    assert failed == ["broken"]
    assert attention_rules.counts(items) == {"critical": 1, "warning": 0, "info": 1}


# ── the HTTP path ───────────────────────────────────────────────────


def _serial_factory(session: AsyncSession):  # noqa: ANN202
    lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        async with lock:
            yield session

    return factory


async def _user(dbsession: AsyncSession, *, superuser: bool) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"fleet-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=superuser,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


async def test_the_capacity_endpoint_end_to_end(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession
) -> None:
    nodes = await _seed_fleet(dbsession)
    admin = await _user(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    fastapi_app.dependency_overrides[dashboard_now] = lambda: NOW
    fastapi_app.state.db_session_factory = _serial_factory(dbsession)

    resp = await client.get(fastapi_app.url_path_for("admin_dashboard_capacity"))

    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.headers["cache-control"] == "no-store, private"
    body = resp.json()
    assert body["machines_included"] is True
    assert body["totals"]["gpus_free_unknown_clusters"] == 1
    head = next(m for g in body["groups"] for m in (g["machines"] or []) if m["id"] == str(nodes["a"].id))
    assert (head["health"], head["gpu_util_pct"]) == ("ok", 97)

    one = await client.get(
        fastapi_app.url_path_for("admin_dashboard_capacity"), params={"group": "unassigned"}
    )
    groups = one.json()["groups"]
    assert [g["kind"] for g in groups if g["machines"] is not None] == ["unassigned"]

    missing = await client.get(fastapi_app.url_path_for("admin_dashboard_capacity"), params={"group": "nope"})
    assert missing.status_code == status.HTTP_404_NOT_FOUND


async def test_the_attention_endpoint_lists_failed_rules(
    fastapi_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def offline(_facts: object) -> list[attention_rules.Item]:
        return [
            attention_rules.Item(
                "node_offline:x",
                "node_offline",
                "critical",
                "infrastructure",
                {"node": "spark-1"},
                NOW,
                "/admin/nodes/x",
            )
        ]

    async def broken(_facts: object) -> list[attention_rules.Item]:
        raise RuntimeError("boom")

    monkeypatch.setattr(attention_rules, "RULES", [offline, broken])
    admin = await _user(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin

    body = (await client.get(fastapi_app.url_path_for("admin_dashboard_attention"))).json()

    assert [(i["code"], i["params"]) for i in body["items"]] == [("node_offline", {"node": "spark-1"})]
    assert body["counts"]["critical"] == 1
    assert body["rule_errors"] == ["broken"]


@pytest.mark.parametrize("route", ["admin_dashboard_capacity", "admin_dashboard_attention"])
async def test_non_superusers_are_refused(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, route: str
) -> None:
    operator = await _user(dbsession, superuser=False)
    fastapi_app.dependency_overrides[current_active_user] = lambda: operator
    assert (await client.get(fastapi_app.url_path_for(route))).status_code == status.HTTP_403_FORBIDDEN


async def test_capacity_and_attention_never_ask_ray_or_probe_a_runtime(
    fastapi_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live Ray status takes 10-25 s here; the dashboard reads what the reconciler stored."""
    from faden_backend.services.inference.drivers.ray import client as ray_client
    from faden_backend.services.llm import remote_health

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the dashboard must not contact Ray or a runtime")

    monkeypatch.setattr(ray_client.RayClusterClient, "__init__", forbidden)
    monkeypatch.setattr(remote_health, "probe", forbidden)
    await _seed_fleet(dbsession)
    admin = await _user(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    fastapi_app.dependency_overrides[dashboard_now] = lambda: NOW
    fastapi_app.state.db_session_factory = _serial_factory(dbsession)

    capacity = await client.get(fastapi_app.url_path_for("admin_dashboard_capacity"))
    attention = await client.get(fastapi_app.url_path_for("admin_dashboard_attention"))

    assert capacity.status_code == status.HTTP_200_OK, capacity.text
    assert attention.status_code == status.HTTP_200_OK, attention.text
    failed = attention.json()["rule_errors"]
    assert not {"machines", "clusters_and_deployments", "runtimes"} & set(failed), failed


# ── muting what someone has seen ────────────────────────────────────


def _offline_item(severity: str = "critical") -> attention_rules.Item:
    return attention_rules.Item(
        "node_offline:x", "node_offline", severity, "infrastructure", {"node": "spark-1"}, NOW, "/admin/nodes/x"
    )


def _disk_item(severity: str = "warning") -> attention_rules.Item:
    return attention_rules.Item("disk_low", "disk_low", severity, "platform", {"free": 8.0})


async def _attention(fastapi_app: FastAPI, client: AsyncClient, *items: attention_rules.Item, broken: bool = False):  # noqa: ANN202
    """Read the attention list as if the rules found *items* (and one rule failed, with *broken*)."""

    async def found(_facts: object) -> list[attention_rules.Item]:
        return list(items)

    async def failing(_facts: object) -> list[attention_rules.Item]:
        raise RuntimeError("boom")

    attention_rules.RULES[:] = [found, failing] if broken else [found]
    fastapi_app.state.dashboard_fact_cache = None  # evaluated afresh, not from the last read
    response = await client.get(fastapi_app.url_path_for("admin_dashboard_attention"))
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


async def _mute(fastapi_app: FastAPI, client: AsyncClient, item_id: str, severity: str):  # noqa: ANN202
    return await client.put(
        fastapi_app.url_path_for("admin_dashboard_attention_acknowledge", item_id=item_id),
        json={"severity": severity},
    )


@pytest.fixture()
async def admin(fastapi_app: FastAPI, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> User:
    monkeypatch.setattr(attention_rules, "RULES", [])
    user = await _user(dbsession, superuser=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    return user


async def test_a_muted_item_leaves_the_list_and_the_counts_for_everyone(
    fastapi_app: FastAPI, client: AsyncClient, admin: User
) -> None:
    assert (await _mute(fastapi_app, client, "node_offline:x", "critical")).json() == {
        "item_id": "node_offline:x", "muted": True,
    }
    body = await _attention(fastapi_app, client, _offline_item(), _disk_item())

    assert [i["id"] for i in body["items"]] == ["disk_low"]
    assert body["counts"] == {"critical": 0, "warning": 1, "info": 0}
    assert [(m["id"], m["acknowledged_by"]) for m in body["muted"]] == [("node_offline:x", admin.email)]


async def test_a_muted_item_that_gets_worse_is_shown_again(
    fastapi_app: FastAPI, client: AsyncClient, admin: User
) -> None:
    await _mute(fastapi_app, client, "disk_low", "warning")
    assert (await _attention(fastapi_app, client, _disk_item("warning")))["items"] == []

    worse = await _attention(fastapi_app, client, _disk_item("critical"))
    assert [i["id"] for i in worse["items"]] == ["disk_low"] and worse["muted"] == []
    # And it takes a new look to mute it: back to a warning, it is not muted by the old one.
    assert [i["id"] for i in (await _attention(fastapi_app, client, _disk_item("warning")))["items"]] == ["disk_low"]


async def test_a_problem_that_clears_and_comes_back_is_new(
    fastapi_app: FastAPI, client: AsyncClient, admin: User
) -> None:
    await _mute(fastapi_app, client, "node_offline:x", "critical")
    # Unknown while its rule fails: still muted.
    assert (await _attention(fastapi_app, client, broken=True))["muted"] == []
    assert (await _attention(fastapi_app, client, _offline_item(), broken=True))["items"] == []

    assert (await _attention(fastapi_app, client))["items"] == []  # cleared
    back = await _attention(fastapi_app, client, _offline_item())
    assert [i["id"] for i in back["items"]] == ["node_offline:x"]


async def test_a_muted_item_can_be_shown_again(
    fastapi_app: FastAPI, client: AsyncClient, admin: User
) -> None:
    await _mute(fastapi_app, client, "node_offline:x", "critical")
    response = await client.delete(
        fastapi_app.url_path_for("admin_dashboard_attention_unacknowledge", item_id="node_offline:x")
    )
    assert response.json() == {"item_id": "node_offline:x", "muted": False}
    assert [i["id"] for i in (await _attention(fastapi_app, client, _offline_item()))["items"]] == ["node_offline:x"]


async def test_muting_needs_a_known_severity_and_a_superuser(
    fastapi_app: FastAPI, client: AsyncClient, dbsession: AsyncSession, admin: User
) -> None:
    assert (await _mute(fastapi_app, client, "disk_low", "whatever")).status_code == 422
    operator = await _user(dbsession, superuser=False)
    fastapi_app.dependency_overrides[current_active_user] = lambda: operator
    assert (await _mute(fastapi_app, client, "disk_low", "warning")).status_code == status.HTTP_403_FORBIDDEN
