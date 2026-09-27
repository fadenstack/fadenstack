"""Dashboard traffic: where each request was served, and how the figures add up.

A routed request is attributed through its provider instance: a cluster
deployment to its cluster, a found container to its provider, a runtime to its
provider -- this server for a local Docker one, the provider itself otherwise.
Its residency class comes from services/llm/residency.py. Unknown instances
(a provider since deleted) count as "unknown", never as ours.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.models.llm import ProviderTarget
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.services.dashboard import kpis as kpi_service
from llm_port_backend.services.dashboard import timewindows as tw
from llm_port_backend.services.dashboard import traffic
from llm_port_backend.services.dashboard.cache import FactCache
from llm_port_backend.services.dashboard.facts import Facts
from llm_port_backend.web.api.admin.dashboard.common import dashboard_now

pytestmark = pytest.mark.anyio()

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
BERLIN = tw.parse_tz("Europe/Berlin")

#: The gateway tables, as far as the dashboard reads them. They live in the
#: gateway's database, which the backend's test fixture does not create;
#: Postgres DDL is transactional, so they vanish with the test's rollback.
_GATEWAY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS llm_provider_instance (
        id           uuid PRIMARY KEY,
        type         varchar(64)  NOT NULL,
        base_url     text         NOT NULL,
        enabled      boolean      NOT NULL DEFAULT true,
        source_kind  varchar(64),
        source_id    varchar(128)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_gateway_request_log (
        id                   uuid PRIMARY KEY,
        request_id           varchar(128) NOT NULL,
        tenant_id            varchar(128) NOT NULL,
        user_id              varchar(128) NOT NULL,
        endpoint             varchar(128) NOT NULL,
        status_code          integer      NOT NULL,
        latency_ms           integer      NOT NULL DEFAULT 0,
        total_tokens         integer,
        estimated_total_cost numeric(18, 8),
        currency             varchar(3),
        provider_instance_id uuid,
        created_at           timestamptz  NOT NULL
    )
    """,
)


@pytest.fixture()
async def gateway(dbsession: AsyncSession) -> AsyncSession:
    for statement in _GATEWAY_SCHEMA:
        await dbsession.execute(text(statement))
    await dbsession.flush()
    return dbsession


async def _instance(gw: AsyncSession, *, source_kind: str | None = None, source_id: str | None = None) -> str:
    instance_id = uuid.uuid4()
    await gw.execute(
        text(
            "INSERT INTO llm_provider_instance (id, type, base_url, enabled, source_kind, source_id) "
            "VALUES (:id, 'vllm', 'http://x:8000', true, :kind, :sid)"
        ),
        {"id": instance_id, "kind": source_kind, "sid": source_id},
    )
    return str(instance_id)


async def _requests(
    gw: AsyncSession,
    instance_id: str | None,
    count: int,
    *,
    at: datetime = NOW - timedelta(minutes=5),
    user: str = "u1",
    status_code: int = 200,
    tokens: int = 100,
    cost: str | None = None,
    currency: str | None = None,
    latency_ms: int = 400,
) -> None:
    for _ in range(count):
        await gw.execute(
            text(
                "INSERT INTO llm_gateway_request_log (id, request_id, tenant_id, user_id, endpoint, status_code, "
                "latency_ms, total_tokens, estimated_total_cost, currency, provider_instance_id, created_at) "
                "VALUES (:id, :rid, 'default', :user, '/v1/chat/completions', :status, :lat, :tokens, :cost, "
                ":currency, :iid, :at)"
            ),
            {
                "id": uuid.uuid4(),
                "rid": uuid.uuid4().hex,
                "user": user,
                "status": status_code,
                "lat": latency_ms,
                "tokens": tokens,
                "cost": Decimal(cost) if cost else None,
                "currency": currency,
                "iid": uuid.UUID(instance_id) if instance_id else None,
                "at": at,
            },
        )


def _provider(name: str, target: ProviderTarget, **extra: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), name=name, target=target, source_kind=None, source_id=None, **extra
    )


class _Platform:
    """A cluster deployment, a local runtime, a cloud runtime, a LAN runtime and a found container."""

    def __init__(self) -> None:
        self.env_id = str(uuid.uuid4())
        self.deployment_id = str(uuid.uuid4())
        self.adoption_id = str(uuid.uuid4())
        self.cluster_provider = _provider("qwen on dgx", ProviderTarget.INFERENCE_CLUSTER)
        self.cluster_provider.source_kind = "inference_deployment"
        self.cluster_provider.source_id = self.deployment_id
        self.local = _provider("local llama", ProviderTarget.LOCAL_DOCKER)
        self.cloud = _provider("OpenAI", ProviderTarget.REMOTE_ENDPOINT)
        self.lan = _provider("lab vllm", ProviderTarget.REMOTE_ENDPOINT)
        self.found = _provider("found qwen", ProviderTarget.REMOTE_ENDPOINT)
        self.found.source_kind = "found_container"
        self.found.source_id = self.adoption_id
        self.local_runtime = SimpleNamespace(id=uuid.uuid4(), provider_id=self.local.id)
        self.cloud_runtime = SimpleNamespace(id=uuid.uuid4(), provider_id=self.cloud.id)
        self.lan_runtime = SimpleNamespace(id=uuid.uuid4(), provider_id=self.lan.id)

    def attribution(self) -> traffic.Attribution:
        return traffic.Attribution(
            providers=[self.cluster_provider, self.local, self.cloud, self.lan, self.found],
            runtimes=[self.local_runtime, self.cloud_runtime, self.lan_runtime],
            deployments={self.deployment_id: self.env_id},
            environments={self.env_id: "DGX Pair"},
            residency={
                str(self.cluster_provider.id): "machines",
                str(self.local.id): "machines",
                str(self.cloud.id): "external",
                str(self.lan.id): "private",
                str(self.found.id): "machines",
            },
        )


def test_each_kind_of_instance_is_attributed_to_its_destination() -> None:
    p = _Platform()
    a = p.attribution()

    cluster = a.destination("any", "inference_deployment", p.deployment_id)
    assert (cluster.kind, cluster.id, cluster.name, cluster.residency) == (
        "cluster",
        p.env_id,
        "DGX Pair",
        "machines",
    )
    assert a.destination(str(p.local_runtime.id), None, None) == traffic.Destination(
        "this_server", "this_server", None, "machines"
    )
    cloud = a.destination(str(p.cloud_runtime.id), None, None)
    assert (cloud.kind, cloud.name, cloud.residency) == ("provider", "OpenAI", "external")
    assert a.destination(str(p.lan_runtime.id), None, None).residency == "private"
    found = a.destination("any", "found_container", p.adoption_id)
    assert (found.kind, found.name) == ("found", "found qwen")
    gone = a.destination(str(uuid.uuid4()), None, None)
    assert (gone.kind, gone.residency) == ("unknown", "unknown")


def test_a_deployment_whose_provider_is_missing_is_still_ours() -> None:
    p = _Platform()
    a = traffic.Attribution(
        providers=[],
        runtimes=[],
        deployments={p.deployment_id: p.env_id},
        environments={p.env_id: "DGX Pair"},
        residency={},
    )
    assert a.destination("x", "inference_deployment", p.deployment_id).residency == "machines"


async def _seed(gw: AsyncSession, p: _Platform) -> dict[str, str]:
    ids = {
        "cluster": await _instance(gw, source_kind="inference_deployment", source_id=p.deployment_id),
        "local": str(p.local_runtime.id),
        "cloud": str(p.cloud_runtime.id),
        "lan": str(p.lan_runtime.id),
        "found": await _instance(gw, source_kind="found_container", source_id=p.adoption_id),
        "gone": str(uuid.uuid4()),
    }
    for key in ("local", "cloud", "lan", "gone"):
        await _instance_with_id(gw, ids[key])
    await _requests(gw, ids["cluster"], 5, user="alice")
    await _requests(gw, ids["local"], 3, user="bob")
    await _requests(gw, ids["cloud"], 2, user="alice", cost="0.25", currency="USD")
    await _requests(gw, ids["lan"], 1, user="carol")
    await _requests(gw, ids["found"], 1, user="carol")
    await _requests(gw, ids["gone"], 1, user="dave")  # external-or-unknown with no price
    await _requests(gw, None, 2, user="eve", status_code=401, tokens=0)  # rejected before routing
    # Outside the window: yesterday.
    await _requests(gw, ids["cluster"], 7, at=NOW - timedelta(days=1, minutes=5))
    return ids


async def _instance_with_id(gw: AsyncSession, instance_id: str) -> None:
    await gw.execute(
        text("INSERT INTO llm_provider_instance (id, type, base_url) VALUES (:id, 'vllm', 'http://x')"),
        {"id": uuid.UUID(instance_id)},
    )


async def test_totals_count_every_request_and_errors_like_observability(gateway: AsyncSession) -> None:
    await _seed(gateway, _Platform())
    window = tw.Window(NOW - timedelta(hours=1), NOW)

    t = await traffic.totals(gateway, window)

    assert (t.requests, t.routed, t.users, t.errors) == (15, 13, 5, 2)
    assert t.tokens == 13 * 100
    assert t.p50_ms == pytest.approx(400)


async def test_the_residency_split_and_the_top_destinations(gateway: AsyncSession) -> None:
    p = _Platform()
    await _seed(gateway, p)
    rows = await traffic.by_instance(gateway, tw.Window(NOW - timedelta(hours=1), NOW))
    a = p.attribution()

    split = traffic.residency_split(rows, a)
    assert split == {"machines": 9, "private": 1, "external": 2, "unknown": 1}
    assert traffic.in_house_share(split) == pytest.approx(10 / 13)

    top, other = traffic.destinations(rows, a, top=3)
    assert [(d.destination.kind, d.requests) for d in top] == [
        ("cluster", 5),
        ("this_server", 3),
        ("provider", 2),
    ]
    assert other == 3  # lan, found, gone


async def test_spend_is_per_currency_and_unpriced_counts_only_where_a_price_is_expected(
    gateway: AsyncSession,
) -> None:
    p = _Platform()
    ids = await _seed(gateway, p)
    await _requests(gateway, ids["cloud"], 1, cost="3", currency="EUR")

    s = traffic.fold_spend(
        await traffic.spend(gateway, tw.Window(NOW - timedelta(hours=1), NOW)), p.attribution()
    )

    assert s.by_currency == {"EUR": Decimal("3"), "USD": Decimal("0.5")}
    # The unknown instance's request had no price; the local ones are free, not unpriced.
    assert s.unpriced == 1


async def test_days_are_the_viewers_calendar_days(gateway: AsyncSession) -> None:
    p = _Platform()
    instance = await _instance(gateway, source_kind="inference_deployment", source_id=p.deployment_id)
    # 23:30 UTC on the 25th is 01:30 on the 26th in Berlin.
    await _requests(gateway, instance, 2, at=datetime(2026, 9, 25, 23, 30, tzinfo=UTC))
    await _requests(gateway, instance, 1, at=datetime(2026, 9, 25, 21, 30, tzinfo=UTC))

    span, _ = tw.day_range(NOW, BERLIN, 7)
    folded = traffic.fold_buckets(await traffic.day_buckets(gateway, span, BERLIN), p.attribution())

    assert folded["2026-09-26"]["machines"] == 2
    assert folded["2026-09-25"]["machines"] == 1


async def test_hours_are_grouped_by_utc_hour(gateway: AsyncSession) -> None:
    p = _Platform()
    instance = await _instance(gateway, source_kind="inference_deployment", source_id=p.deployment_id)
    await _requests(gateway, instance, 3, at=datetime(2026, 9, 26, 8, 10, tzinfo=UTC))

    rows = await traffic.hour_buckets(gateway, tw.Window(NOW - timedelta(hours=6), NOW))

    assert [(b, r.requests) for b, r in rows] == [(datetime(2026, 9, 26, 8, 0, tzinfo=UTC), 3)]


async def test_attribution_loads_on_an_empty_platform(dbsession: AsyncSession) -> None:
    a = await traffic.Attribution.load(dbsession)
    assert a.destination(str(uuid.uuid4()), None, None).residency == "unknown"


# ── KPIs and the HTTP path ──────────────────────────────────────────


def _serial_factory(session: AsyncSession):  # noqa: ANN202
    """Sessions for Facts that are all the test's one session, used one at a time.

    The test's data lives in its transaction, visible only on this session; the
    KPIs gather concurrently, so the uses are serialised.
    """
    lock = asyncio.Lock()

    @contextlib.asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        async with lock:
            yield session

    return factory


async def test_kpis_compare_today_with_yesterday_and_skip_what_they_cannot_compute(
    gateway: AsyncSession,
) -> None:
    p = _Platform()
    await _seed(gateway, p)
    facts = Facts(
        cache=FactCache(),
        backend_factory=_serial_factory(gateway),
        gateway_factory=_serial_factory(gateway),
        now=NOW,
        tz=BERLIN,
    )

    async def fixed_attribution() -> traffic.Attribution:
        return p.attribution()

    facts.attribution = fixed_attribution  # type: ignore[method-assign]
    items, unavailable = await kpi_service.compute(
        ["requests_today", "in_house_share_today", "spend_month", "no_such_kpi"], facts
    )

    by_id = {i["id"]: i for i in items}
    assert by_id["requests_today"]["value"] == 15
    assert by_id["requests_today"]["previous"] == 7  # yesterday, 00:00 -> 12:00 Berlin
    assert by_id["in_house_share_today"]["value"] == pytest.approx(10 / 13)
    assert by_id["spend_month"]["value"] == [{"currency": "USD", "amount": Decimal("0.5")}]
    assert "no_such_kpi" not in by_id
    assert unavailable == []


async def test_a_kpi_without_its_source_is_listed_as_unavailable(dbsession: AsyncSession) -> None:
    facts = Facts(
        cache=FactCache(),
        backend_factory=_serial_factory(dbsession),
        gateway_factory=None,
        now=NOW,
        tz=BERLIN,
    )
    items, unavailable = await kpi_service.compute(["requests_today"], facts)
    assert items == []
    assert unavailable == ["requests_today"]


@pytest.fixture
async def superuser(dbsession: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email=f"dash-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


async def test_the_traffic_endpoint_end_to_end(
    fastapi_app: FastAPI,
    client: AsyncClient,
    gateway: AsyncSession,
    superuser: User,
) -> None:
    await _seed(gateway, _Platform())
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    fastapi_app.dependency_overrides[dashboard_now] = lambda: NOW
    factory = _serial_factory(gateway)
    fastapi_app.state.db_session_factory = factory
    fastapi_app.state.llm_graph_trace_session_factory = factory

    resp = await client.get(
        fastapi_app.url_path_for("admin_dashboard_traffic"), params={"tz": "Europe/Berlin"}
    )

    assert resp.status_code == status.HTTP_200_OK, resp.text
    assert resp.headers["cache-control"] == "no-store, private"
    body = resp.json()
    assert (body["requests"], body["routed"], body["errors"]["count"]) == (15, 13, 2)
    # No providers in this backend. A deployment's or a found container's
    # requests ran on our machines whatever has been deleted since; a runtime
    # instance with no runtime left is honestly "unknown".
    split = {r["residency"]: r["requests"] for r in body["by_residency"]}
    assert (split["machines"], split["unknown"]) == (6, 7)


async def test_an_unknown_time_zone_is_422(
    fastapi_app: FastAPI, client: AsyncClient, superuser: User
) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get(
        fastapi_app.url_path_for("admin_dashboard_kpis"), params={"tz": "Mars/Olympus_Mons"}
    )
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_without_the_gateway_database_traffic_is_503(
    fastapi_app: FastAPI,
    client: AsyncClient,
    superuser: User,
) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    fastapi_app.state.llm_graph_trace_session_factory = None
    resp = await client.get(fastapi_app.url_path_for("admin_dashboard_traffic"))
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.parametrize(
    "route", ["admin_dashboard_kpis", "admin_dashboard_traffic", "admin_dashboard_traffic_series"]
)
async def test_non_superusers_are_refused_even_with_a_warm_cache(
    fastapi_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
    route: str,
) -> None:
    operator = User(
        id=uuid.uuid4(),
        email=f"op-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    dbsession.add(operator)
    await dbsession.flush()
    # Anything cached would be served only after the access check.
    fastapi_app.state.dashboard_fact_cache = FactCache()
    fastapi_app.dependency_overrides[current_active_user] = lambda: operator

    resp = await client.get(fastapi_app.url_path_for(route))

    assert resp.status_code == status.HTTP_403_FORBIDDEN
