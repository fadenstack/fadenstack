"""Traffic figures for the dashboard: how many requests, and where they were served.

Every routed request in the gateway's log names the provider instance that
served it. The instance leads, through its source, to what the backend knows:
a cluster deployment (``inference_deployment``), a found vLLM container
(``found_container``), or a runtime whose provider is on this server or at an
address. That gives each request a *destination* and a *residency class* --
our machines, our private network, external, or unknown (see
``services/llm/residency.py``) -- without the gateway knowing either.

The log lives in the gateway's database and the providers in the backend's,
so the join is made here: the log is grouped by instance in SQL (a handful of
rows), and each instance is then attributed in Python. Every query is bounded
by ``created_at``, which is indexed.

Errors are counted as the Observability page counts them: HTTP status >= 400.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import InferenceDeployment, InferenceEnvironment
from llm_port_backend.db.models.llm import LLMProvider, LLMRuntime, ProviderTarget
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.dashboard.timewindows import Window
from llm_port_backend.services.llm import residency as residency_mod
from llm_port_backend.settings import settings

RESIDENCY_CLASSES = (
    residency_mod.MACHINES,
    residency_mod.PRIVATE,
    residency_mod.EXTERNAL,
    residency_mod.UNKNOWN,
)
IN_HOUSE = (residency_mod.MACHINES, residency_mod.PRIVATE)
#: Classes whose requests are expected to carry a price.
PRICED = (residency_mod.EXTERNAL, residency_mod.UNKNOWN)

DEPLOYMENT_SOURCE = "inference_deployment"
FOUND_SOURCE = "found_container"


# ── Attribution ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Destination:
    """Where a request was served, as the dashboard names it."""

    #: cluster | this_server | found | provider | unknown
    kind: str
    id: str
    name: str | None
    residency: str


class Attribution:
    """Maps a gateway provider instance to its destination and residency class."""

    def __init__(
        self,
        *,
        providers: list[Any],
        runtimes: list[Any],
        deployments: dict[str, str],
        environments: dict[str, str],
        residency: dict[str, str],
    ) -> None:
        self._providers = {str(p.id): p for p in providers}
        self._by_source = {
            (str(p.source_kind), str(p.source_id)): p
            for p in providers
            if getattr(p, "source_kind", None) and getattr(p, "source_id", None)
        }
        self._runtime_provider = {str(rt.id): str(rt.provider_id) for rt in runtimes}
        #: deployment id -> environment id
        self._deployments = deployments
        #: environment id -> name
        self._environments = environments
        #: provider id -> residency class
        self._residency = residency

    @classmethod
    async def load(cls, session: AsyncSession) -> Attribution:
        """Read what attribution needs from the backend DB, and classify providers."""
        providers = list((await session.execute(select(LLMProvider))).scalars().all())
        runtimes = list((await session.execute(select(LLMRuntime))).scalars().all())
        deployments = {
            str(d_id): str(env_id)
            for d_id, env_id in (
                await session.execute(select(InferenceDeployment.id, InferenceDeployment.environment_id))
            ).all()
        }
        environments = {
            str(e_id): name
            for e_id, name in (
                await session.execute(select(InferenceEnvironment.id, InferenceEnvironment.name))
            ).all()
        }
        nodes = list((await session.execute(select(InfraNode))).scalars().all())
        ctx = residency_mod.Context(
            machine_addresses=residency_mod.machine_addresses(nodes),
            internal_networks=residency_mod.parse_networks(settings.residency_internal_networks),
        )
        found = await residency_mod.classify_all(providers, ctx)
        return cls(
            providers=providers,
            runtimes=runtimes,
            deployments=deployments,
            environments=environments,
            residency={pid: r.kind for pid, r in found.items()},
        )

    def _provider_residency(self, provider: Any | None, default: str) -> str:
        if provider is None:
            return default
        return self._residency.get(str(provider.id), default)

    def destination(self, instance_id: str, source_kind: str | None, source_id: str | None) -> Destination:
        """The destination of an instance; ``unknown`` for one whose provider is gone."""
        if source_kind == DEPLOYMENT_SOURCE and source_id:
            env_id = self._deployments.get(source_id)
            provider = self._by_source.get((DEPLOYMENT_SOURCE, source_id))
            # A deployment on one of our clusters is ours by construction.
            residency = self._provider_residency(provider, residency_mod.MACHINES)
            if env_id is not None:
                return Destination("cluster", env_id, self._environments.get(env_id), residency)
            return Destination("cluster", source_id, getattr(provider, "name", None), residency)
        if source_kind == FOUND_SOURCE and source_id:
            provider = self._by_source.get((FOUND_SOURCE, source_id))
            residency = self._provider_residency(provider, residency_mod.MACHINES)
            return Destination(
                "found", str(getattr(provider, "id", source_id)), getattr(provider, "name", None), residency
            )
        provider_id = self._runtime_provider.get(instance_id)
        provider = self._providers.get(provider_id) if provider_id else None
        if provider is None:
            return Destination("unknown", instance_id, None, residency_mod.UNKNOWN)
        residency = self._provider_residency(provider, residency_mod.UNKNOWN)
        if provider.target == ProviderTarget.LOCAL_DOCKER:
            return Destination("this_server", "this_server", None, residency)
        return Destination("provider", str(provider.id), provider.name, residency)


# ── Queries ─────────────────────────────────────────────────────────


@dataclass
class Totals:
    """Counts over a window of the request log."""

    requests: int = 0
    routed: int = 0
    tokens: int = 0
    users: int = 0
    errors: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None

    @property
    def error_rate(self) -> float | None:
        """Errors per request; None without requests."""
        return self.errors / self.requests if self.requests else None


@dataclass
class InstanceRow:
    """Requests (and tokens) served by one provider instance."""

    instance_id: str | None
    source_kind: str | None
    source_id: str | None
    requests: int = 0
    tokens: int = 0


@dataclass
class SpendRow:
    """Cost of one instance's requests in one currency, and those without a price."""

    instance_id: str | None
    source_kind: str | None
    source_id: str | None
    currency: str | None
    amount: Decimal | None
    unpriced: int


async def totals(gw: AsyncSession, window: Window, *, latency: bool = True) -> Totals:
    """Request, token, user and error counts for a window; latency percentiles optional."""
    latency_sql = (
        """,
            PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms"""
        if latency
        else ""
    )
    row = (
        (
            await gw.execute(
                text(f"""
            SELECT
                COUNT(*)                                                    AS requests,
                COUNT(provider_instance_id)                                 AS routed,
                COALESCE(SUM(total_tokens), 0)                              AS tokens,
                COUNT(DISTINCT user_id)                                     AS users,
                COALESCE(SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END), 0) AS errors{latency_sql}
            FROM llm_gateway_request_log
            WHERE created_at >= :start AND created_at < :end
        """),  # noqa: S608 - only the fixed latency clause is interpolated
                {"start": window.start, "end": window.end},
            )
        )
        .mappings()
        .one()
    )
    return Totals(
        requests=int(row["requests"]),
        routed=int(row["routed"]),
        tokens=int(row["tokens"]),
        users=int(row["users"]),
        errors=int(row["errors"]),
        p50_ms=_float(row.get("p50_ms")),
        p95_ms=_float(row.get("p95_ms")),
    )


async def by_instance(gw: AsyncSession, window: Window) -> list[InstanceRow]:
    """Routed requests and tokens per provider instance, with the instance's source."""
    rows = (
        await gw.execute(
            text("""
            SELECT
                CAST(l.provider_instance_id AS TEXT)   AS instance_id,
                MAX(pi.source_kind)                    AS source_kind,
                MAX(CAST(pi.source_id AS TEXT))        AS source_id,
                COUNT(*)                               AS requests,
                COALESCE(SUM(l.total_tokens), 0)       AS tokens
            FROM llm_gateway_request_log l
            LEFT JOIN llm_provider_instance pi ON pi.id = l.provider_instance_id
            WHERE l.created_at >= :start AND l.created_at < :end
              AND l.provider_instance_id IS NOT NULL
            GROUP BY l.provider_instance_id
        """),
            {"start": window.start, "end": window.end},
        )
    ).mappings()
    return [
        InstanceRow(r["instance_id"], r["source_kind"], r["source_id"], int(r["requests"]), int(r["tokens"]))
        for r in rows
    ]


async def spend(gw: AsyncSession, window: Window) -> list[SpendRow]:
    """Estimated cost per instance and currency, and requests without a price."""
    rows = (
        await gw.execute(
            text("""
            SELECT
                CAST(l.provider_instance_id AS TEXT)   AS instance_id,
                MAX(pi.source_kind)                    AS source_kind,
                MAX(CAST(pi.source_id AS TEXT))        AS source_id,
                l.currency                             AS currency,
                SUM(l.estimated_total_cost)            AS amount,
                SUM(CASE WHEN l.estimated_total_cost IS NULL THEN 1 ELSE 0 END) AS unpriced
            FROM llm_gateway_request_log l
            LEFT JOIN llm_provider_instance pi ON pi.id = l.provider_instance_id
            WHERE l.created_at >= :start AND l.created_at < :end
              AND l.provider_instance_id IS NOT NULL
            GROUP BY l.provider_instance_id, l.currency
        """),
            {"start": window.start, "end": window.end},
        )
    ).mappings()
    return [
        SpendRow(
            r["instance_id"],
            r["source_kind"],
            r["source_id"],
            r["currency"],
            Decimal(str(r["amount"])) if r["amount"] is not None else None,
            int(r["unpriced"]),
        )
        for r in rows
    ]


async def day_buckets(gw: AsyncSession, window: Window, tz: ZoneInfo) -> list[tuple[str, InstanceRow]]:
    """Routed requests per local calendar day and instance."""
    rows = (
        await gw.execute(
            text("""
            SELECT
                to_char(date_trunc('day', l.created_at AT TIME ZONE :tz), 'YYYY-MM-DD') AS bucket,
                CAST(l.provider_instance_id AS TEXT)   AS instance_id,
                MAX(pi.source_kind)                    AS source_kind,
                MAX(CAST(pi.source_id AS TEXT))        AS source_id,
                COUNT(*)                               AS requests
            FROM llm_gateway_request_log l
            LEFT JOIN llm_provider_instance pi ON pi.id = l.provider_instance_id
            WHERE l.created_at >= :start AND l.created_at < :end
              AND l.provider_instance_id IS NOT NULL
            GROUP BY 1, l.provider_instance_id
        """),
            {"start": window.start, "end": window.end, "tz": tz.key},
        )
    ).mappings()
    return [
        (r["bucket"], InstanceRow(r["instance_id"], r["source_kind"], r["source_id"], int(r["requests"])))
        for r in rows
    ]


async def hour_buckets(gw: AsyncSession, window: Window) -> list[tuple[datetime, InstanceRow]]:
    """Routed requests per UTC hour and instance (labelled in local time by the caller).

    Grouping by the UTC hour keeps the hour repeated when clocks go back as two
    buckets, where grouping by local wall-clock time would merge them.
    """
    rows = (
        await gw.execute(
            text("""
            SELECT
                date_trunc('hour', l.created_at)       AS bucket,
                CAST(l.provider_instance_id AS TEXT)   AS instance_id,
                MAX(pi.source_kind)                    AS source_kind,
                MAX(CAST(pi.source_id AS TEXT))        AS source_id,
                COUNT(*)                               AS requests
            FROM llm_gateway_request_log l
            LEFT JOIN llm_provider_instance pi ON pi.id = l.provider_instance_id
            WHERE l.created_at >= :start AND l.created_at < :end
              AND l.provider_instance_id IS NOT NULL
            GROUP BY 1, l.provider_instance_id
        """),
            {"start": window.start, "end": window.end},
        )
    ).mappings()
    return [
        (r["bucket"], InstanceRow(r["instance_id"], r["source_kind"], r["source_id"], int(r["requests"])))
        for r in rows
    ]


# ── Folding ─────────────────────────────────────────────────────────


def residency_split(rows: list[InstanceRow], attribution: Attribution) -> dict[str, int]:
    """Routed requests per residency class (every class present, possibly 0)."""
    split = dict.fromkeys(RESIDENCY_CLASSES, 0)
    for row in rows:
        dest = attribution.destination(row.instance_id or "", row.source_kind, row.source_id)
        split[dest.residency] = split.get(dest.residency, 0) + row.requests
    return split


def in_house_share(split: dict[str, int]) -> float | None:
    """(machines + private) / routed; None without routed requests."""
    routed = sum(split.values())
    return sum(split[k] for k in IN_HOUSE) / routed if routed else None


@dataclass
class DestinationTotal:
    """Requests and tokens of one destination."""

    destination: Destination
    requests: int = 0
    tokens: int = 0


def destinations(
    rows: list[InstanceRow], attribution: Attribution, *, top: int = 5
) -> tuple[list[DestinationTotal], int]:
    """The *top* destinations by requests, and the requests of all the others."""
    totals_by: dict[tuple[str, str], DestinationTotal] = {}
    for row in rows:
        dest = attribution.destination(row.instance_id or "", row.source_kind, row.source_id)
        entry = totals_by.setdefault((dest.kind, dest.id), DestinationTotal(dest))
        entry.requests += row.requests
        entry.tokens += row.tokens
    ranked = sorted(totals_by.values(), key=lambda d: d.requests, reverse=True)
    other = sum(d.requests for d in ranked[top:])
    return ranked[:top], other


@dataclass
class Spend:
    """Estimated cost of a window, kept apart per currency."""

    #: currency -> amount; never summed across currencies.
    by_currency: dict[str, Decimal] = field(default_factory=dict)
    #: Requests to external or unknown destinations that carried no price.
    unpriced: int = 0


def fold_spend(rows: list[SpendRow], attribution: Attribution) -> Spend:
    """Sum cost per currency; count unpriced requests only where a price was expected."""
    out = Spend()
    amounts: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        if row.amount is not None and row.currency:
            amounts[row.currency] += row.amount
        if row.unpriced:
            dest = attribution.destination(row.instance_id or "", row.source_kind, row.source_id)
            if dest.residency in PRICED:
                out.unpriced += row.unpriced
    out.by_currency = dict(sorted(amounts.items()))
    return out


def fold_buckets(
    rows: list[tuple[Any, InstanceRow]],
    attribution: Attribution,
) -> dict[Any, dict[str, int]]:
    """Per bucket, routed requests per residency class."""
    out: dict[Any, dict[str, int]] = {}
    for bucket, row in rows:
        dest = attribution.destination(row.instance_id or "", row.source_kind, row.source_id)
        split = out.setdefault(bucket, dict.fromkeys(RESIDENCY_CLASSES, 0))
        split[dest.residency] += row.requests
    return out


def _float(value: object) -> float | None:
    return float(value) if value is not None else None  # type: ignore[arg-type]
