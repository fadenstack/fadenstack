"""Needs attention: the platform's current problems, derived from stored state.

Each rule reads what is already recorded -- node reports, cluster and
deployment status, runtime status (kept by the remote-runtime follower), job
rows, the request log -- and probes nothing but the local dependencies the
health strip probes anyway (through the fact cache). A rule runs on its own
with a time limit; one that fails is named in ``rule_errors`` and the others
still answer. Items carry a code and parameters, never English: the UI
renders ``dashboard.attention.<code>`` in the viewer's language.

An item clears when its cause clears. An administrator can mute one they
have seen (``acknowledge``): it leaves the list and the counts for every
administrator until it clears -- the same problem coming back is new -- or
gets worse than it was when muted.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError

from faden_backend.db.models.agents import Agent, AgentStatus, AgentVersion
from faden_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EnvironmentDesiredState,
    EnvironmentStatus,
    InferenceDeployment,
    InferenceEnvironment,
)
from faden_backend.db.models.llm import (
    DownloadJob,
    DownloadJobStatus,
    LLMProvider,
    LLMRuntime,
    ProviderTarget,
    RuntimeStatus,
)
from faden_backend.db.models.node_control import InfraNodeJoinRequest, JoinRequestStatus
from faden_backend.settings import settings

if TYPE_CHECKING:
    from faden_backend.services.dashboard.facts import Facts

log = logging.getLogger(__name__)

RULE_TIMEOUT_S = 5.0
GPU_TEMP_WARNING_C = 85.0
GPU_TEMP_CRITICAL_C = 92.0
DISK_WARNING_PCT = 10.0
DISK_CRITICAL_PCT = 5.0
#: Fewer requests than this in the window say nothing about an error rate.
GATEWAY_MIN_REQUESTS = 10
RECENT = timedelta(hours=24)

_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Item:
    """One current problem."""

    id: str
    code: str
    severity: str  # critical | warning | info
    category: str  # infrastructure | models | traffic | platform | knowledge | integrations
    params: dict[str, Any] = field(default_factory=dict)
    since: datetime | None = None
    link: str | None = None


Rule = Callable[["Facts"], Awaitable[list[Item]]]


async def machines(facts: Facts) -> list[Item]:
    """Offline, unhealthy or silent machines, and hot GPUs."""
    items: list[Item] = []
    for m in (await facts.capacity()).machines:
        link = f"/admin/nodes/{m.id}"
        if m.health == "offline":
            items.append(
                Item(
                    f"node_offline:{m.id}",
                    "node_offline",
                    "critical",
                    "infrastructure",
                    {"node": m.name},
                    m.last_seen,
                    link,
                )
            )
        elif m.health in ("error", "degraded"):
            severity = "critical" if m.health == "error" else "warning"
            items.append(
                Item(
                    f"node_unhealthy:{m.id}",
                    "node_unhealthy",
                    severity,
                    "infrastructure",
                    {"node": m.name, "state": m.health},
                    None,
                    link,
                )
            )
        elif m.health == "stale":
            items.append(
                Item(
                    f"node_telemetry_stale:{m.id}",
                    "node_telemetry_stale",
                    "warning",
                    "infrastructure",
                    {"node": m.name},
                    m.last_seen,
                    link,
                )
            )
        temps = [(g.temp_c, g.index) for g in m.gpus if g.temp_c is not None]
        if temps and m.health not in ("offline", "stale"):
            temp, index = max(temps)
            if temp >= GPU_TEMP_WARNING_C:
                items.append(
                    Item(
                        f"gpu_temperature_high:{m.id}",
                        "gpu_temperature_high",
                        "critical" if temp >= GPU_TEMP_CRITICAL_C else "warning",
                        "infrastructure",
                        {"node": m.name, "gpu": index, "temp": round(temp)},
                        None,
                        link,
                    )
                )
    return items


async def join_requests(facts: Facts) -> list[Item]:
    """Machines asking to join."""

    async def query(session: Any) -> list[Item]:
        count, oldest = (
            await session.execute(
                select(func.count(), func.min(InfraNodeJoinRequest.created_at)).where(
                    InfraNodeJoinRequest.status == JoinRequestStatus.PENDING,
                    InfraNodeJoinRequest.expires_at > facts.now,
                )
            )
        ).one()
        if not count:
            return []
        return [
            Item(
                "node_join_pending",
                "node_join_pending",
                "info",
                "infrastructure",
                {"count": int(count)},
                oldest,
                "/admin/nodes",
            )
        ]

    return await facts.on_backend(query)


async def clusters_and_deployments(facts: Facts) -> list[Item]:
    """Clusters and deployments that failed or run degraded while meant to run."""

    async def query(session: Any) -> list[Item]:
        items: list[Item] = []
        envs = (
            await session.execute(
                select(InferenceEnvironment).where(
                    InferenceEnvironment.desired_state == EnvironmentDesiredState.RUNNING.value,
                    InferenceEnvironment.status.in_(
                        [EnvironmentStatus.FAILED.value, EnvironmentStatus.DEGRADED.value]
                    ),
                )
            )
        ).scalars()
        for env in envs:
            items.append(
                Item(
                    f"cluster_degraded:{env.id}",
                    "cluster_degraded",
                    "critical" if env.status == EnvironmentStatus.FAILED.value else "warning",
                    "infrastructure",
                    {"cluster": env.name, "state": env.status, "message": env.status_message},
                    env.updated_at,
                    f"/admin/clusters/{env.id}",
                )
            )
        deployments = (
            await session.execute(
                select(InferenceDeployment).where(
                    InferenceDeployment.desired_state == DeploymentDesiredState.ACTIVE.value,
                    InferenceDeployment.phase.in_(
                        [DeploymentPhase.FAILED.value, DeploymentPhase.DEGRADED.value]
                    ),
                )
            )
        ).scalars()
        for dep in deployments:
            items.append(
                Item(
                    f"deployment_failed:{dep.id}",
                    "deployment_failed",
                    "critical" if dep.phase == DeploymentPhase.FAILED.value else "warning",
                    "models",
                    {"deployment": dep.name, "state": dep.phase, "message": dep.phase_message},
                    dep.updated_at,
                    f"/admin/clusters/{dep.environment_id}",
                )
            )
        return items

    return await facts.on_backend(query)


async def runtimes(facts: Facts) -> list[Item]:
    """Runtimes meant to run that are in error: a local one failed, or a remote endpoint stopped answering."""

    async def query(session: Any) -> list[Item]:
        rows = (
            await session.execute(
                select(LLMRuntime, LLMProvider)
                .join(LLMProvider, LLMProvider.id == LLMRuntime.provider_id)
                .where(LLMRuntime.status == RuntimeStatus.ERROR, LLMRuntime.desired_state == "running")
            )
        ).all()
        items: list[Item] = []
        for runtime, provider in rows:
            remote = provider.target == ProviderTarget.REMOTE_ENDPOINT
            code = "provider_unreachable" if remote else "runtime_unhealthy"
            items.append(
                Item(
                    f"{code}:{runtime.id}",
                    code,
                    "warning",
                    "models",
                    {"name": runtime.name, "provider": provider.name, "message": runtime.status_message},
                    runtime.updated_at,
                    "/admin/llm/providers" if remote else f"/admin/llm/runtimes/{runtime.id}",
                )
            )
        return items

    return await facts.on_backend(query)


async def model_downloads(facts: Facts) -> list[Item]:
    """Model downloads that failed in the last day."""

    async def query(session: Any) -> list[Item]:
        count, latest = (
            await session.execute(
                select(func.count(), func.max(DownloadJob.updated_at)).where(
                    DownloadJob.status == DownloadJobStatus.FAILED,
                    DownloadJob.updated_at >= facts.now - RECENT,
                )
            )
        ).one()
        if not count:
            return []
        return [
            Item(
                "model_job_failed",
                "model_job_failed",
                "warning",
                "models",
                {"count": int(count)},
                latest,
                "/admin/marketplace",
            )
        ]

    return await facts.on_backend(query)


async def gateway_errors(facts: Facts) -> list[Item]:
    """A 5xx rate over the gateway alert monitor's window and threshold."""
    if not facts.has_gateway:
        return []
    minutes = settings.mailer_alert_5xx_window_minutes
    threshold = float(settings.mailer_alert_5xx_threshold_percent)

    async def query(session: Any) -> list[Item]:
        total, errors = (
            await session.execute(
                text(
                    "SELECT count(*), count(*) FILTER (WHERE status_code >= 500) "
                    "FROM llm_gateway_request_log WHERE created_at >= :start AND created_at < :end"
                ),
                {"start": facts.now - timedelta(minutes=minutes), "end": facts.now},
            )
        ).one()
        if total < GATEWAY_MIN_REQUESTS:
            return []
        pct = errors / total * 100.0
        if pct < threshold:
            return []
        return [
            Item(
                "gateway_error_spike",
                "gateway_error_spike",
                "warning",
                "traffic",
                {"rate": round(pct, 1), "minutes": minutes, "errors": int(errors)},
                None,
                "/admin/observability",
            )
        ]

    return await facts.on_gateway(query)


async def dependencies(facts: Facts) -> list[Item]:
    """Postgres, RabbitMQ or Grafana down."""
    return [
        Item(
            f"dependency_down:{d.name}",
            "dependency_down",
            "critical",
            "platform",
            {"name": d.name, "message": d.detail},
        )
        for d in await facts.dependencies()
        if d.status == "down"
    ]


async def host(facts: Facts) -> list[Item]:
    """Low disk on this server, and restarting containers."""
    free, restarting = await facts.host()
    items: list[Item] = []
    if free is not None and free < DISK_WARNING_PCT:
        severity = "critical" if free < DISK_CRITICAL_PCT else "warning"
        items.append(Item("disk_low", "disk_low", severity, "platform", {"free": round(free, 1)}))
    if restarting:
        items.append(
            Item(
                "container_restarting",
                "container_restarting",
                "warning",
                "platform",
                {"count": restarting},
                None,
                "/admin/containers",
            )
        )
    return items


async def rag_jobs(facts: Facts) -> list[Item]:
    """RAG Lite ingestion jobs that failed in the last day."""
    if not settings.rag_lite_enabled:
        return []
    from faden_backend.db.models.rag_lite import RagLiteIngestJob, RagLiteJobStatus  # noqa: PLC0415

    async def query(session: Any) -> list[Item]:
        count, latest = (
            await session.execute(
                select(func.count(), func.max(RagLiteIngestJob.created_at)).where(
                    RagLiteIngestJob.status == RagLiteJobStatus.FAILED,
                    RagLiteIngestJob.created_at >= facts.now - RECENT,
                )
            )
        ).one()
        if not count:
            return []
        return [
            Item(
                "rag_job_failed",
                "rag_job_failed",
                "warning",
                "knowledge",
                {"count": int(count)},
                latest,
                "/admin/rag/documents",
            )
        ]

    return await facts.on_backend(query)


async def mcp_servers(facts: Facts) -> list[Item]:
    """Enabled MCP servers that are degraded, disconnected or in error."""
    if not settings.mcp_enabled:
        return []
    found = await facts.integrations()
    if "mcp" in found.unavailable:
        raise RuntimeError("MCP service unavailable")
    return [
        Item(
            f"mcp_server_error:{s['id']}",
            "mcp_server_error",
            "warning",
            "integrations",
            {"name": s["name"], "state": s["status"], "message": s["message"]},
            None,
            "/admin/mcp/servers",
        )
        for s in found.mcp_problems
    ]


async def agent_apps(facts: Facts) -> list[Item]:
    """Agents whose apps bring tools that are not live: held back until a version with them is published.

    Counted against the published version, so an accepted change left as a draft still asks.
    """
    if not facts.has_gateway:
        return []
    from faden_backend.services.agents import app_review  # noqa: PLC0415

    async def manifests(session: Any) -> list[Any]:
        since = facts.now - timedelta(days=app_review.CURRENT_DAYS)
        try:
            found = await session.execute(text(app_review.MANIFEST_SQL), {"agent": None, "since": since})
        except ProgrammingError:  # a gateway not upgraded yet
            return []
        return list(found.mappings().all())

    by_agent: dict[str, list[app_review.ManifestRow]] = {}
    for row in await facts.on_gateway(manifests):
        by_agent.setdefault(row["agent_id"], []).append(app_review.manifest_row(row))
    if not by_agent:
        return []

    async def agents(session: Any) -> list[Item]:
        ids = [uuid.UUID(a) for a in by_agent if _is_uuid(a)]
        found = (await session.execute(
            select(Agent).where(Agent.id.in_(ids), Agent.status != AgentStatus.RETIRED),
        )).scalars().all()
        versions: dict[uuid.UUID, list[AgentVersion]] = {}
        for version in (await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id.in_([a.id for a in found])),
        )).scalars():
            versions.setdefault(version.agent_id, []).append(version)
        items = []
        for agent in found:
            live = _live(agent, versions.get(agent.id, []))
            review = app_review.review(by_agent[str(agent.id)], live, now=facts.now)
            if review.pending:
                items.append(_apps_item(agent, review))
        return items

    return await facts.on_backend(agents)


def _live(agent: Agent, versions: list[AgentVersion]) -> AgentVersion | None:
    """The version the agent's users get: the published one, or a draft agent's newest (its maintainers')."""
    published = next((v for v in versions if v.id == agent.published_version_id), None)
    return published or max(versions, key=lambda v: v.version, default=None)


def _apps_item(agent: Agent, review: Any) -> Item:
    return Item(
        f"agent_apps_pending:{agent.id}",
        "agent_apps_pending",
        "warning",
        "integrations",
        {"name": agent.name, "count": review.pending},
        max((t.last_seen_at for t in review.tools if t.last_seen_at), default=None),
        f"/admin/agents/{agent.slug}?tab=apps",
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


#: Warned about this long before a certificate runs out; less when it renews by itself
#: (faden tls renew runs daily and acts 30 days before), which only a failing renewal reaches.
CERT_WARNING_DAYS = 30
CERT_WARNING_DAYS_RENEWING = 21
CERT_CRITICAL_DAYS = 7


async def edge_certificate(facts: Facts) -> list[Item]:
    """The certificate HTTPS is served with: close to its end, or past it."""
    from faden_backend.services import edge_trust  # noqa: PLC0415

    until = edge_trust.served_until()
    if until is None:
        return []
    days = (until - facts.now).total_seconds() / 86400
    renews = (edge_trust.edge_info() or {}).get("renews") == "daily"
    params = {"days": max(int(days), 0), "until": until.date().isoformat(), "renews": renews}
    if days <= 0:
        return [Item("tls_certificate_expired", "tls_certificate_expired", "critical", "platform", params, until)]
    if days < CERT_CRITICAL_DAYS:
        return [Item("tls_certificate_expiring", "tls_certificate_expiring", "critical", "platform", params)]
    if days < (CERT_WARNING_DAYS_RENEWING if renews else CERT_WARNING_DAYS):
        return [Item("tls_certificate_expiring", "tls_certificate_expiring", "warning", "platform", params)]
    return []


#: Model-TLS certificates renew ten days before their end; this close means renewal is failing.
MACHINE_CERT_WARNING_DAYS = 5
MACHINE_CERT_CRITICAL_DAYS = 2


async def model_traffic(facts: Facts) -> list[Item]:
    """Models reached over plain HTTP where TLS is possible, and model-TLS certificates not renewing."""

    async def query(session: Any) -> list[Item]:
        from faden_backend.db.models.inference import InferenceEndpoint  # noqa: PLC0415
        from faden_backend.db.models.machine_tls import MachineCertificate, MachineTlsClient  # noqa: PLC0415
        from faden_backend.db.models.node_control import InfraNode  # noqa: PLC0415

        items: list[Item] = []
        if not settings.machine_tls_enabled:
            return items
        pending = (
            await session.execute(
                select(InferenceEnvironment.id, InferenceEnvironment.name, InferenceEndpoint.published_json)
                .join(InferenceDeployment, InferenceDeployment.environment_id == InferenceEnvironment.id)
                .join(InferenceEndpoint, InferenceEndpoint.deployment_id == InferenceDeployment.id)
                .where(InferenceDeployment.desired_state == DeploymentDesiredState.ACTIVE.value)
            )
        ).all()
        seen: set[Any] = set()
        for env_id, env_name, published in pending:
            if env_id in seen or not (published or {}).get("model_tls_pending"):
                continue
            seen.add(env_id)
            items.append(Item(
                f"model_traffic_unencrypted:{env_id}", "model_traffic_unencrypted", "warning", "models",
                {"cluster": env_name}, None, f"/admin/clusters/{env_id}",
            ))
        rows = (
            await session.execute(
                select(MachineCertificate, InfraNode.host).join(InfraNode, InfraNode.id == MachineCertificate.node_id)
            )
        ).all()
        for cert, host in rows:
            days = (cert.not_after - facts.now).total_seconds() / 86400
            if days < MACHINE_CERT_WARNING_DAYS:
                items.append(Item(
                    f"machine_certificate_expiring:{cert.node_id}", "machine_certificate_expiring",
                    "critical" if days < MACHINE_CERT_CRITICAL_DAYS else "warning", "infrastructure",
                    {"machine": host, "days": max(int(days), 0), "until": cert.not_after.date().isoformat()},
                    None, "/admin/nodes",
                ))
        gateway = await session.get(MachineTlsClient, "fadenstack-gateway")
        if gateway is not None:
            days = (gateway.not_after - facts.now).total_seconds() / 86400
            if days < MACHINE_CERT_WARNING_DAYS:
                items.append(Item(
                    "gateway_machine_certificate_expiring", "gateway_machine_certificate_expiring",
                    "critical" if days < MACHINE_CERT_CRITICAL_DAYS else "warning", "platform",
                    {"days": max(int(days), 0), "until": gateway.not_after.date().isoformat()},
                ))
        return items

    return await facts.on_backend(query)


RULES: list[Rule] = [
    machines,
    join_requests,
    clusters_and_deployments,
    runtimes,
    model_downloads,
    gateway_errors,
    dependencies,
    host,
    rag_jobs,
    mcp_servers,
    edge_certificate,
    model_traffic,
    agent_apps,
]


async def evaluate(facts: Facts, rules: list[Rule] | None = None) -> tuple[list[Item], list[str]]:
    """Every rule's items, most severe first (newest first within a severity); and the rules that failed."""
    chosen = rules if rules is not None else RULES
    results = await asyncio.gather(
        *(asyncio.wait_for(rule(facts), RULE_TIMEOUT_S) for rule in chosen),
        return_exceptions=True,
    )
    items: list[Item] = []
    failed: list[str] = []
    for rule, result in zip(chosen, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("attention rule %s failed: %s", rule.__name__, result)
            failed.append(rule.__name__)
        else:
            items.extend(result)
    items.sort(key=lambda i: (_ORDER.get(i.severity, 9), -(i.since.timestamp() if i.since else 0)))
    return items, failed


@dataclass
class Muted:
    """An item someone muted: still a problem, no longer shouted about."""

    item: Item
    acknowledged_at: datetime
    #: Who muted it (their email); ``None`` once that user is gone.
    acknowledged_by: str | None


async def apply_acknowledgements(
    session: Any, items: list[Item], *, complete: bool,
) -> tuple[list[Item], list[Muted]]:
    """Split *items* into those to show and those muted; drop mutes that no longer hold.

    A mute goes when its item got worse than it was when muted (shown again,
    to be muted anew) and, when every rule ran (*complete*), when its item is
    gone: cleared, so if it comes back it is a new problem. With a rule that
    failed, its items are merely unknown this time, and their mutes stay.
    """
    from faden_backend.db.models.dashboard import AttentionAcknowledgement  # noqa: PLC0415
    from faden_backend.db.models.users import User  # noqa: PLC0415

    rows = (
        await session.execute(
            select(AttentionAcknowledgement, User.email).outerjoin(
                User, User.id == AttentionAcknowledgement.acknowledged_by
            )
        )
    ).all()
    acks = {ack.item_id: (ack, email) for ack, email in rows}
    shown: list[Item] = []
    muted: list[Muted] = []
    for item in items:
        found = acks.pop(item.id, None)
        if found is None:
            shown.append(item)
            continue
        ack, email = found
        if _ORDER.get(item.severity, 9) < _ORDER.get(ack.severity, 9):
            await session.delete(ack)
            shown.append(item)
            continue
        muted.append(Muted(item, ack.acknowledged_at, email))
    if complete:
        for ack, _email in acks.values():
            await session.delete(ack)
    return shown, muted


async def acknowledge(session: Any, item_id: str, severity: str, user_id: Any) -> None:
    """Mute *item_id* at *severity*, for every administrator (muting it again renews it)."""
    from faden_backend.db.models.dashboard import AttentionAcknowledgement  # noqa: PLC0415

    if severity not in _ORDER:
        raise ValueError(f"unknown severity {severity!r}")
    ack = await session.get(AttentionAcknowledgement, item_id)
    if ack is None:
        session.add(AttentionAcknowledgement(item_id=item_id, severity=severity, acknowledged_by=user_id))
    else:
        ack.severity = severity
        ack.acknowledged_by = user_id
        ack.acknowledged_at = func.now()
    await session.flush()


async def unacknowledge(session: Any, item_id: str) -> None:
    """Show *item_id* again."""
    from faden_backend.db.models.dashboard import AttentionAcknowledgement  # noqa: PLC0415

    ack = await session.get(AttentionAcknowledgement, item_id)
    if ack is not None:
        await session.delete(ack)
        await session.flush()


def counts(items: list[Item]) -> dict[str, int]:
    """Items per severity (every severity present)."""
    out = dict.fromkeys(_ORDER, 0)
    for item in items:
        out[item.severity] = out.get(item.severity, 0) + 1
    return out
