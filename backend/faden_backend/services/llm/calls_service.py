"""Read model for the LLM Calls explorer: one gateway request per row.

Everything here comes from what the gateway records about a call -- ids,
timings, tokens, outcome, tool and policy metadata. Prompts, completions and
a failed tool's text are not recorded (or are sealed) and are not read.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    # Typing only: graph_service imports the API package, whose views import this module.
    from faden_backend.services.llm.graph_service import ProviderOwners

log = logging.getLogger(__name__)

CALLS_MAX_LIMIT = 200
RELATED_LIMIT = 100
#: How far around a call's own time its policy decisions are looked for.
_POLICY_SLACK = timedelta(minutes=5)

#: A call failed when the upstream or the gateway said so.
_IS_ERROR = "(l.status_code >= 400 OR l.error_code IS NOT NULL)"

_CALL_COLUMNS = """
    CAST(l.id AS TEXT)                      AS id,
    l.request_id, l.trace_id, l.tenant_id, l.user_id,
    l.model_alias,
    CAST(l.provider_instance_id AS TEXT)    AS provider_instance_id,
    pi.source_kind                          AS source_kind,
    CAST(pi.source_id AS TEXT)              AS source_id,
    l.endpoint, l.status_code, l.latency_ms, l.ttft_ms,
    l.prompt_tokens, l.completion_tokens, l.total_tokens, l.cached_tokens,
    l.error_code, l.estimated_total_cost, l.currency, l.stream,
    l.session_id, l.agent_id, l.device_id, l.conversation_id, l.turn_id,
    l.group_id, l.project_id,
    l.finish_reason, l.retry_count, l.skills_used, l.rag_context,
    l.mcp_tool_call_count, l.mcp_tool_loop_iterations,
    l.created_at
"""

_FROM = """
    FROM llm_gateway_request_log l
    LEFT JOIN llm_provider_instance pi ON pi.id = l.provider_instance_id
"""

_SORTS = {
    "newest": "l.created_at DESC, l.id DESC",
    "oldest": "l.created_at ASC, l.id ASC",
    "slowest": "l.latency_ms DESC, l.created_at DESC",
    "tokens": "COALESCE(l.total_tokens, 0) DESC, l.created_at DESC",
}


@dataclass(frozen=True)
class CallFilters:
    """What the explorer narrows calls to."""

    start: datetime
    end: datetime
    q: str | None = None
    model_alias: str | None = None
    outcome: str | None = None  # "ok" or "error"
    user_id: str | None = None
    agent_id: str | None = None
    session: str | None = None  # a session or conversation id
    min_latency_ms: int | None = None

    def where(self) -> tuple[str, dict[str, Any]]:
        """The SQL ``WHERE`` clause for these filters and its bound parameters."""
        clauses = ["l.created_at >= :start", "l.created_at < :end"]
        params: dict[str, Any] = {"start": self.start, "end": self.end}
        if self.model_alias:
            clauses.append("l.model_alias = :model_alias")
            params["model_alias"] = self.model_alias
        if self.outcome == "error":
            clauses.append(_IS_ERROR)
        elif self.outcome == "ok":
            clauses.append(f"NOT {_IS_ERROR}")
        if self.user_id:
            clauses.append("l.user_id = :user_id")
            params["user_id"] = self.user_id
        if self.agent_id:
            clauses.append("l.agent_id = :agent_id")
            params["agent_id"] = self.agent_id
        if self.session:
            clauses.append("(l.session_id = :session OR l.conversation_id = :session)")
            params["session"] = self.session
        if self.min_latency_ms is not None:
            clauses.append("l.latency_ms >= :min_latency_ms")
            params["min_latency_ms"] = self.min_latency_ms
        needle = (self.q or "").strip()
        if needle:
            # Ids match exactly (they are indexed); names and error codes by part.
            clauses.append(
                "(l.request_id = :q OR l.trace_id = :q OR l.user_id = :q"
                " OR l.session_id = :q OR l.conversation_id = :q OR l.agent_id = :q"
                " OR l.model_alias ILIKE :q_like OR l.error_code ILIKE :q_like"
                " OR l.endpoint ILIKE :q_like OR l.finish_reason ILIKE :q_like)",
            )
            params["q"] = needle
            params["q_like"] = f"%{_escape_like(needle)}%"
        return "WHERE " + " AND ".join(clauses), params


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _num(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


class LLMCallsService:
    """Lists, sums up and explains gateway calls."""

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] | None,
        *,
        owners: ProviderOwners | None = None,
        provider_names: dict[str, str] | None = None,
    ) -> None:
        self._factory = session_factory
        self._owners = owners
        self._names = provider_names or {}

    # ── helpers ───────────────────────────────────────────────────

    def _provider(self, row: dict[str, Any]) -> tuple[str | None, str | None]:
        instance = row.get("provider_instance_id")
        if not instance or self._owners is None:
            return None, None
        provider_id = self._owners.provider_for(instance, row.get("source_kind"), row.get("source_id"))
        return provider_id, (self._names.get(provider_id) if provider_id else None)

    def _call(self, row: dict[str, Any]) -> dict[str, Any]:
        call = dict(row)
        call.pop("source_kind", None)
        call.pop("source_id", None)
        call["estimated_total_cost"] = _num(call.get("estimated_total_cost"))
        call["provider_id"], call["provider_name"] = self._provider(row)
        return call

    # ── list ──────────────────────────────────────────────────────

    async def list_calls(
        self, filters: CallFilters, *, page: int = 1, limit: int = 50, sort: str = "newest",
    ) -> dict[str, Any]:
        """A page of the calls the filters select, and how many there are."""
        limit = max(1, min(limit, CALLS_MAX_LIMIT))
        page = max(page, 1)
        if self._factory is None:
            return {"items": [], "total": 0, "page": page, "limit": limit}
        where, params = filters.where()
        order = _SORTS.get(sort, _SORTS["newest"])
        async with self._factory() as session:
            total = (await session.execute(text(f"SELECT COUNT(*) {_FROM} {where}"), params)).scalar_one()
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_CALL_COLUMNS} {_FROM} {where}"
                        f" ORDER BY {order} LIMIT :limit OFFSET :offset",
                    ),
                    {**params, "limit": limit, "offset": (page - 1) * limit},
                )
            ).mappings().all()
        items = [self._call(dict(r)) for r in rows]
        return {"items": items, "total": int(total), "page": page, "limit": limit}

    # ── stats ─────────────────────────────────────────────────────

    async def stats(self, filters: CallFilters) -> dict[str, Any]:
        """Volume, errors, latency percentiles, tokens and cost of the selected calls."""
        empty: dict[str, Any] = {
            "total": 0, "errors": 0, "error_rate": None,
            "p50_latency_ms": None, "p95_latency_ms": None, "p95_ttft_ms": None,
            "prompt_tokens": 0, "completion_tokens": 0, "estimated_total_cost": None,
            "models": [],
        }
        if self._factory is None:
            return empty
        where, params = filters.where()
        # The model list ignores the model filter itself, so it stays a list to choose from.
        model_where, model_params = CallFilters(
            start=filters.start, end=filters.end, q=filters.q, outcome=filters.outcome,
            user_id=filters.user_id, agent_id=filters.agent_id, session=filters.session,
            min_latency_ms=filters.min_latency_ms,
        ).where()
        async with self._factory() as session:
            row = (
                await session.execute(
                    text(f"""
                        SELECT
                            COUNT(*)                                                    AS total,
                            COALESCE(SUM(CASE WHEN {_IS_ERROR} THEN 1 ELSE 0 END), 0)  AS errors,
                            PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY l.latency_ms) AS p50_latency_ms,
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY l.latency_ms) AS p95_latency_ms,
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY l.ttft_ms)    AS p95_ttft_ms,
                            COALESCE(SUM(l.prompt_tokens), 0)                          AS prompt_tokens,
                            COALESCE(SUM(l.completion_tokens), 0)                      AS completion_tokens,
                            SUM(l.estimated_total_cost)                                AS estimated_total_cost
                        {_FROM} {where}
                    """),
                    params,
                )
            ).mappings().one()
            models = (
                await session.execute(
                    text(f"""
                        SELECT l.model_alias AS model_alias, COUNT(*) AS calls
                        {_FROM} {model_where} AND l.model_alias IS NOT NULL
                        GROUP BY l.model_alias ORDER BY calls DESC LIMIT 100
                    """),
                    model_params,
                )
            ).mappings().all()
        total = int(row["total"])
        errors = int(row["errors"])
        return {
            "total": total,
            "errors": errors,
            "error_rate": (errors / total) if total else None,
            "p50_latency_ms": _num(row["p50_latency_ms"]),
            "p95_latency_ms": _num(row["p95_latency_ms"]),
            "p95_ttft_ms": _num(row["p95_ttft_ms"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "estimated_total_cost": _num(row["estimated_total_cost"]),
            "models": [{"model_alias": m["model_alias"], "calls": int(m["calls"])} for m in models],
        }

    # ── detail ────────────────────────────────────────────────────

    async def detail(self, call_id: str) -> dict[str, Any] | None:
        """One call (by its log row's id) with its tool calls, policy outcomes and related calls.

        Not by ``request_id``: a caller chooses that (``X-Request-ID``), so
        several calls, of several users, can share one.
        """
        if self._factory is None:
            return None
        try:
            row_id = uuid.UUID(call_id)
        except ValueError:
            return None
        async with self._factory() as session:
            row = (
                await session.execute(
                    text(f"SELECT {_CALL_COLUMNS} {_FROM} WHERE l.id = :id"), {"id": row_id},
                )
            ).mappings().first()
            if row is None:
                return None
            call = self._call(dict(row))
            tools = (
                await session.execute(
                    text("""
                        SELECT CAST(id AS TEXT) AS id, iteration, tool_name, mcp_server,
                               latency_ms, is_error, error_kind, created_at
                        FROM llm_tool_call_log
                        WHERE request_log_id = :id
                        ORDER BY iteration, created_at
                    """),
                    {"id": row_id},
                )
            ).mappings().all()
            # What a policy decided, never the refused text, its hash or the policy's name. Decisions
            # have no link to the log row: the same request id, user and tenant, around the call's time.
            policies = (
                await session.execute(
                    text("""
                        SELECT stage, outcome, category, "check" AS check_name, tool_id, created_at
                        FROM llm_gateway_policy_decision
                        WHERE request_id = :rid AND tenant_id = :tenant AND user_id = :user
                          AND created_at BETWEEN :since AND :until
                        ORDER BY created_at
                    """),
                    {
                        "rid": call["request_id"],
                        "tenant": call["tenant_id"],
                        "user": call["user_id"],
                        "since": call["created_at"] - timedelta(milliseconds=call["latency_ms"] or 0)
                        - _POLICY_SLACK,
                        "until": call["created_at"] + _POLICY_SLACK,
                    },
                )
            ).mappings().all()
            related, group_by, group_value = await self._related(session, call)
        return {
            "call": call,
            "tool_calls": [dict(t) for t in tools],
            "policy_decisions": [dict(p) for p in policies],
            "related": related,
            "related_by": group_by,
            "related_key": group_value,
        }

    async def _related(
        self, session: AsyncSession, call: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None, str | None]:
        """The other calls of the same conversation, session or trace, oldest first."""
        for column in ("conversation_id", "session_id", "trace_id"):
            value = call.get(column)
            if not value or (column == "trace_id" and value == call["request_id"]):
                continue
            rows = (
                await session.execute(
                    text(f"""
                        SELECT {_CALL_COLUMNS} {_FROM}
                        WHERE l.{column} = :value
                        ORDER BY l.created_at ASC, l.id ASC
                        LIMIT :limit
                    """),
                    {"value": value, "limit": RELATED_LIMIT},
                )
            ).mappings().all()
            if len(rows) > 1:
                return [self._call(dict(r)) for r in rows], column, str(value)
        return [], None, None

    # ── routing ───────────────────────────────────────────────────

    async def routing(self, filters: CallFilters) -> dict[str, Any]:
        """Which model names went to which providers in the window, and how that went."""
        if self._factory is None:
            return {"routes": []}
        where, params = filters.where()
        async with self._factory() as session:
            rows = (
                await session.execute(
                    text(f"""
                        SELECT
                            COALESCE(l.model_alias, '')               AS model_alias,
                            CAST(l.provider_instance_id AS TEXT)      AS provider_instance_id,
                            MAX(pi.source_kind)                                        AS source_kind,
                            MAX(CAST(pi.source_id AS TEXT))                            AS source_id,
                            COUNT(*)                                                   AS calls,
                            COALESCE(SUM(CASE WHEN {_IS_ERROR} THEN 1 ELSE 0 END), 0)  AS errors,
                            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY l.latency_ms) AS p95_latency_ms,
                            COALESCE(SUM(l.total_tokens), 0)                           AS tokens,
                            MAX(l.created_at)                                          AS last_call_at
                        {_FROM} {where}
                        GROUP BY COALESCE(l.model_alias, ''), l.provider_instance_id
                        ORDER BY calls DESC
                        LIMIT 500
                    """),
                    params,
                )
            ).mappings().all()
        routes = []
        for r in rows:
            row = dict(r)
            provider_id, provider_name = self._provider(row)
            routes.append({
                "model_alias": row["model_alias"] or None,
                "provider_instance_id": row["provider_instance_id"],
                "provider_id": provider_id,
                "provider_name": provider_name,
                "calls": int(row["calls"]),
                "errors": int(row["errors"]),
                "p95_latency_ms": _num(row["p95_latency_ms"]),
                "tokens": int(row["tokens"]),
                "last_call_at": row["last_call_at"],
            })
        return {"routes": routes}
