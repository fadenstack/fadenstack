"""Read model for the LLM Calls explorer: one gateway request per row.

Everything here comes from what the gateway records about a call -- ids,
timings, tokens, outcome, tool and policy metadata. Prompts, completions and
a failed tool's text are not recorded (or are sealed) and are not read.

Nothing here names the person who made a call, and calls cannot be searched by person: ``user_id`` is
``None`` in every answer (GDPR Art. 25(2); M02 of the usage compliance report). The usage of named people is
seen only through ``/api/admin/usage/people``, behind its switch, grant and reason. Internally the person
still ties a call to its turn and its policy decisions.

Nor by what stands for a person (M02, M04): no answer carries a call's device, session, conversation or
project id, and calls cannot be narrowed to an agent, a session or a conversation -- each would single out
one person's calls. The ids still tie a call to its conversation's other calls and its app's events inside.
"""

from __future__ import annotations

import hashlib
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
TURN_CALLS_LIMIT = 200
#: How far around a call's own time its policy decisions are looked for.
_POLICY_SLACK = timedelta(minutes=5)
#: A turn: the calls an agent's app made for one user request (its ``turn_id``); else a call alone.
_TURN_KEY = "COALESCE(l.turn_id, CAST(l.id AS TEXT))"
#: How far outside the window a listed turn's other calls are still counted (approvals can take a while).
_TURN_REACH = timedelta(hours=6)
#: The app's clock and the gateway's differ a little; its events are matched to a turn with this slack.
_EVENT_SLACK = timedelta(seconds=3)
#: The fields of an app's tool and approval events that are shown: names, sources, outcomes and timings.
_EVENT_FIELDS = ("tool", "source", "server", "outcome", "duration_ms", "decision", "wait_ms", "scope")
#: (order of the matching turns, order of their figures) per sort.
_TURN_SORTS = {
    "newest": ("last_at DESC", "ended_at DESC"),
    "oldest": ("last_at ASC", "ended_at ASC"),
    "slowest": (
        "last_at - first_at DESC",
        "MAX(t.created_at) - MIN(t.created_at - t.latency_ms * interval '1 millisecond') DESC",
    ),
    "tokens": ("tokens DESC", "SUM(COALESCE(t.total_tokens, 0)) DESC"),
}


def _turn_of(event: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The turn an app's event happened in: same conversation and user, within the turn's time."""
    for turn in turns:
        if (
            turn["conversation_id"] == event["conversation_id"]
            and turn["_person"] == event["user_id"]
            and turn["started_at"] - _EVENT_SLACK <= event["at"] <= turn["ended_at"] + _EVENT_SLACK
        ):
            return turn
    return None

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
        if self.min_latency_ms is not None:
            clauses.append("l.latency_ms >= :min_latency_ms")
            params["min_latency_ms"] = self.min_latency_ms
        needle = (self.q or "").strip()
        if needle:
            # A call's own ids match exactly (they are indexed); names and error codes by part. Never an id
            # that stands for a person or picks one person's calls (module docstring).
            clauses.append(
                "(l.request_id = :q OR l.trace_id = :q"
                " OR l.model_alias ILIKE :q_like OR l.error_code ILIKE :q_like"
                " OR l.endpoint ILIKE :q_like OR l.finish_reason ILIKE :q_like)",
            )
            params["q"] = needle
            params["q_like"] = f"%{_escape_like(needle)}%"
        return "WHERE " + " AND ".join(clauses), params


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _turn_key(user_id: object, turn: object) -> str:
    """A turn's key in the answers: stable, and without the person's id in it."""
    return hashlib.sha256(f"{user_id}:{turn}".encode()).hexdigest()[:32]


def _num(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


#: Ids read for the links between calls, never answered (module docstring).
_UNSHOWN_IDS = ("device_id", "session_id", "conversation_id", "project_id")


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
        call["user_id"] = None  # nobody is named (module docstring)
        for key in _UNSHOWN_IDS:
            call.pop(key, None)
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

    # ── turns ─────────────────────────────────────────────────────

    async def list_turns(
        self, filters: CallFilters, *, page: int = 1, limit: int = 50, sort: str = "newest",
    ) -> dict[str, Any]:
        """A page of turns: the calls of one user request (an agent's ``turn_id``), each with all its calls.

        A call without a turn is a turn of its own. A turn is listed when any of its calls matches the
        filters; its figures add up all its calls, also those just outside the window.
        """
        limit = max(1, min(limit, CALLS_MAX_LIMIT))
        page = max(page, 1)
        if self._factory is None:
            return {"items": [], "total": 0, "page": page, "limit": limit}
        where, params = filters.where()
        match_order, turn_order = _TURN_SORTS.get(sort, _TURN_SORTS["newest"])
        matched = f"""
            SELECT l.user_id AS uid, {_TURN_KEY} AS tkey,
                   MAX(l.created_at) AS last_at,
                   MIN(l.created_at - l.latency_ms * interval '1 millisecond') AS first_at,
                   SUM(COALESCE(l.total_tokens, 0)) AS tokens
            {_FROM} {where}
            GROUP BY l.user_id, {_TURN_KEY}
        """
        page_params = {
            **params,
            "limit": limit,
            "offset": (page - 1) * limit,
            "wide_start": filters.start - _TURN_REACH,
            "wide_end": filters.end + _TURN_REACH,
        }
        async with self._factory() as session:
            # Only whitelisted fragments go into the SQL text (the sort comes from _TURN_SORTS).
            count_sql = text(f"SELECT COUNT(*) FROM ({matched}) m")  # noqa: S608
            total = (await session.execute(count_sql, params)).scalar_one()
            rows = (
                await session.execute(
                    text(f"""
                        WITH m AS ({matched} ORDER BY {match_order} LIMIT :limit OFFSET :offset),
                        t AS (
                            SELECT l.*, {_TURN_KEY} AS tkey,
                                   pi.source_kind AS pi_kind, CAST(pi.source_id AS TEXT) AS pi_source
                            FROM llm_gateway_request_log l
                            JOIN m ON m.uid = l.user_id AND m.tkey = {_TURN_KEY}
                            LEFT JOIN llm_provider_instance pi ON pi.id = l.provider_instance_id
                            WHERE l.created_at >= :wide_start AND l.created_at < :wide_end
                        )
                        SELECT
                            t.user_id, t.tkey,
                            MAX(t.turn_id) AS turn_id, MAX(t.conversation_id) AS conversation_id,
                            MAX(t.session_id) AS session_id, MAX(t.agent_id) AS agent_id,
                            MAX(t.device_id) AS device_id, MAX(t.tenant_id) AS tenant_id,
                            MIN(t.created_at - t.latency_ms * interval '1 millisecond') AS started_at,
                            MAX(t.created_at) AS ended_at,
                            COUNT(*) AS calls,
                            SUM(CASE WHEN t.finish_reason = 'tool_calls' THEN 1 ELSE 0 END) AS tool_rounds,
                            SUM(CASE WHEN t.status_code >= 400 OR t.error_code IS NOT NULL THEN 1 ELSE 0 END)
                                AS errors,
                            SUM(t.latency_ms) AS llm_ms,
                            SUM(t.prompt_tokens) AS prompt_tokens,
                            SUM(t.completion_tokens) AS completion_tokens,
                            SUM(t.total_tokens) AS total_tokens,
                            SUM(t.estimated_total_cost) AS estimated_total_cost,
                            MAX(t.currency) AS currency,
                            COALESCE(SUM(t.mcp_tool_call_count), 0) AS server_tools,
                            (array_agg(CAST(t.id AS TEXT) ORDER BY t.created_at DESC))[1] AS last_call_id,
                            (array_agg(t.model_alias ORDER BY t.created_at DESC))[1] AS model_alias,
                            (array_agg(t.endpoint ORDER BY t.created_at DESC))[1] AS endpoint,
                            (array_agg(t.finish_reason ORDER BY t.created_at DESC))[1] AS finish_reason,
                            (array_agg(t.status_code ORDER BY t.created_at DESC))[1] AS status_code,
                            (array_agg(t.status_code ORDER BY t.status_code DESC))[1] AS worst_status,
                            (array_agg(t.error_code ORDER BY t.created_at DESC)
                                FILTER (WHERE t.error_code IS NOT NULL))[1] AS error_code,
                            (array_agg(CAST(t.provider_instance_id AS TEXT) ORDER BY t.created_at DESC))[1]
                                AS provider_instance_id,
                            (array_agg(t.pi_kind ORDER BY t.created_at DESC))[1] AS source_kind,
                            (array_agg(t.pi_source ORDER BY t.created_at DESC))[1] AS source_id
                        FROM t
                        GROUP BY t.user_id, t.tkey
                        ORDER BY {turn_order}
                    """),  # noqa: S608
                    page_params,
                )
            ).mappings().all()
            turns = [self._turn(dict(r)) for r in rows]
            await self._count_client_events(session, turns)
        for turn in turns:
            turn.pop("_person", None)
            for key in _UNSHOWN_IDS:
                turn.pop(key, None)
        return {"items": turns, "total": int(total), "page": page, "limit": limit}

    def _turn(self, row: dict[str, Any]) -> dict[str, Any]:
        turn = dict(row)
        turn["_person"] = turn.pop("user_id")  # matches the app's events to the turn; dropped after
        turn["key"] = _turn_key(turn["_person"], turn.pop("tkey"))
        turn["user_id"] = None  # nobody is named (module docstring)
        turn["provider_id"], turn["provider_name"] = self._provider(row)
        turn.pop("source_kind", None)
        turn.pop("source_id", None)
        for field in ("calls", "tool_rounds", "errors", "llm_ms", "server_tools"):
            turn[field] = int(turn[field] or 0)
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            turn[field] = None if turn[field] is None else int(turn[field])
        turn["estimated_total_cost"] = _num(turn.get("estimated_total_cost"))
        turn["duration_ms"] = int((turn["ended_at"] - turn["started_at"]).total_seconds() * 1000)
        turn.update(client_tools=0, client_tool_errors=0, approvals=0, approvals_denied=0, approval_wait_ms=0)
        return turn

    async def _client_events(
        self,
        session: AsyncSession,
        conversations: list[str],
        since: datetime,
        until: datetime,
    ) -> list[dict[str, Any]]:
        """What the agents' apps reported doing in these conversations: tool runs and approvals.

        Only names, sources, outcomes and timings: the event's other fields are not read out.
        """
        if not conversations:
            return []
        rows = (
            await session.execute(
                text("""
                    SELECT type, conversation_id, user_id, agent_id, data,
                           COALESCE(occurred_at, created_at) AS at
                    FROM agent_client_event
                    WHERE conversation_id = ANY(:convs)
                      AND type IN ('tool.executed', 'approval.decided')
                      AND COALESCE(occurred_at, created_at) BETWEEN :since AND :until
                    ORDER BY COALESCE(occurred_at, created_at)
                    LIMIT 2000
                """),
                {"convs": conversations, "since": since - _EVENT_SLACK, "until": until + _EVENT_SLACK},
            )
        ).mappings().all()
        events = []
        for r in rows:
            data = r["data"] if isinstance(r["data"], dict) else {}
            events.append({
                "type": r["type"],
                "conversation_id": r["conversation_id"],
                "user_id": r["user_id"],
                "agent_id": r["agent_id"],
                "at": r["at"],
                **{k: data.get(k) for k in _EVENT_FIELDS},
            })
        return events

    async def _count_client_events(self, session: AsyncSession, turns: list[dict[str, Any]]) -> None:
        """Count per turn the tools its app ran and the approvals it waited for (conversation, time)."""
        with_conv = [t for t in turns if t.get("conversation_id")]
        if not with_conv:
            return
        events = await self._client_events(
            session,
            sorted({t["conversation_id"] for t in with_conv}),
            min(t["started_at"] for t in with_conv),
            max(t["ended_at"] for t in with_conv),
        )
        for event in events:
            turn = _turn_of(event, with_conv)
            if turn is None:
                continue
            if event["type"] == "tool.executed":
                turn["client_tools"] += 1
                if event.get("outcome") not in (None, "ok"):
                    turn["client_tool_errors"] += 1
            else:
                turn["approvals"] += 1
                if event.get("decision") == "denied":
                    turn["approvals_denied"] += 1
                turn["approval_wait_ms"] += int(event.get("wait_ms") or 0)

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
            person = row["user_id"]  # ties the call to its policy decisions and turn; never shown
            raw = dict(row)  # with the ids that tie it to its conversation; never shown
            call = self._call(raw)
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
                        "user": person,
                        "since": call["created_at"] - timedelta(milliseconds=call["latency_ms"] or 0)
                        - _POLICY_SLACK,
                        "until": call["created_at"] + _POLICY_SLACK,
                    },
                )
            ).mappings().all()
            related, group_by = await self._related(session, raw)
            turn_calls, client_events = await self._turn_detail(session, raw, person)
        return {
            "call": call,
            "tool_calls": [dict(t) for t in tools],
            "policy_decisions": [dict(p) for p in policies],
            "related": related,
            "related_by": group_by,
            "turn_calls": turn_calls,
            "client_events": client_events,
        }

    async def _turn_detail(
        self, session: AsyncSession, call: dict[str, Any], person: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """The calls of the call's turn (oldest first) and what its app reported doing meanwhile.

        *call* is the call's row with the ids that are not answered.
        """
        turn_calls = [self._call(call)]
        if call.get("turn_id"):
            rows = (
                await session.execute(
                    text(f"""
                        SELECT {_CALL_COLUMNS} {_FROM}
                        WHERE l.turn_id = :turn AND l.user_id = :user
                          AND l.created_at BETWEEN :since AND :until
                        ORDER BY l.created_at ASC, l.id ASC
                        LIMIT :limit
                    """),
                    {
                        "turn": call["turn_id"],
                        "user": person,
                        "since": call["created_at"] - _TURN_REACH,
                        "until": call["created_at"] + _TURN_REACH,
                        "limit": TURN_CALLS_LIMIT,
                    },
                )
            ).mappings().all()
            turn_calls = [self._call(dict(r)) for r in rows] or turn_calls
        if not call.get("conversation_id"):
            return turn_calls, []
        started = min(c["created_at"] - timedelta(milliseconds=c["latency_ms"] or 0) for c in turn_calls)
        ended = max(c["created_at"] for c in turn_calls)
        events = await self._client_events(session, [call["conversation_id"]], started, ended)
        events = [e for e in events if e["user_id"] == person]
        for e in events:
            for key in ("conversation_id", "user_id", "agent_id"):
                e.pop(key, None)
        return turn_calls, events

    async def _related(
        self, session: AsyncSession, call: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str | None]:
        """The other calls of the same conversation, session or trace, oldest first, and which of the three.

        *call* is the call's row with the ids that are not answered; the id itself is not answered either.
        """
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
                return [self._call(dict(r)) for r in rows], column
        return [], None

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
