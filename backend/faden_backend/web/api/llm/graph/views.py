"""LLM Graph endpoints for topology and live trace visualization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from faden_backend.db.models.users import User
from faden_backend.services.llm.calls_service import CALLS_MAX_LIMIT, CallFilters, LLMCallsService
from faden_backend.services.llm.graph_service import (
    TRACE_DEFAULT_LIMIT,
    TRACE_MAX_LIMIT,
    LLMGraphService,
)
from faden_backend.web.api.llm.dependencies import get_llm_calls_service, get_llm_graph_service
from faden_backend.web.api.llm.schema import (
    DataUsageSummaryDTO,
    LLMCallDetailDTO,
    LLMCallPageDTO,
    LLMCallStatsDTO,
    LLMRoutingDTO,
    LLMTurnPageDTO,
    TopologyResponseDTO,
    TraceSnapshotResponseDTO,
)
from faden_backend.web.api.rbac import require_permission

router = APIRouter()


@router.get("/topology", response_model=TopologyResponseDTO)
async def get_graph_topology(
    user: User = Depends(require_permission("llm.graph", "read")),
    graph_service: LLMGraphService = Depends(get_llm_graph_service),
) -> TopologyResponseDTO:
    """Return provider/runtime/model graph topology."""
    return await graph_service.get_topology()


@router.get("/traces", response_model=TraceSnapshotResponseDTO)
async def get_recent_traces(
    user: User = Depends(require_permission("llm.graph", "read")),
    graph_service: LLMGraphService = Depends(get_llm_graph_service),
    limit: int = Query(TRACE_DEFAULT_LIMIT, ge=1, le=TRACE_MAX_LIMIT),
    after_event_id: int | None = Query(None),
) -> TraceSnapshotResponseDTO:
    """Return initial or incremental graph trace events."""
    return await graph_service.list_recent_traces(limit=limit, after_event_id=after_event_id)


@router.get("/data-usage", response_model=DataUsageSummaryDTO)
async def get_data_usage(
    user: User = Depends(require_permission("llm.graph", "read")),
    graph_service: LLMGraphService = Depends(get_llm_graph_service),
) -> DataUsageSummaryDTO:
    """Aggregate token/request usage per provider instance from gateway logs."""
    return await graph_service.get_data_usage()


@router.get("/traces/stream")
async def stream_traces(
    request: Request,
    user: User = Depends(require_permission("llm.graph", "read")),
) -> StreamingResponse:
    """Stream trace events as Server-Sent Events.

    This is an Enterprise-only endpoint.  Returns ``402 Payment Required``
    unless the Observability Pro plugin is loaded (which shadows this route).
    """
    raise HTTPException(
        status_code=402,
        detail=(
            "Gateway trace SSE streaming requires the Observability Pro plugin. "
            "Install faden-ee to enable this endpoint."
        ),
    )


# ── LLM Calls explorer ────────────────────────────────────────────

CALLS_DEFAULT_WINDOW = timedelta(hours=24)
CALLS_MAX_RANGE = timedelta(days=90)


def _call_filters(
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    q: str | None = Query(None, max_length=256),
    model_alias: str | None = Query(None, max_length=256),
    outcome: Literal["ok", "error"] | None = Query(None),
    min_latency_ms: int | None = Query(None, ge=0),
) -> CallFilters:
    """The explorer's filters; none by agent, session or conversation (``services/llm/calls_service.py``)."""
    end = end or datetime.now(tz=UTC)
    start = start or end - CALLS_DEFAULT_WINDOW
    if start >= end:
        raise HTTPException(status_code=400, detail="start must be before end")
    if end - start > CALLS_MAX_RANGE:
        raise HTTPException(status_code=400, detail="The time range cannot exceed 90 days")
    return CallFilters(
        start=start, end=end, q=q, model_alias=model_alias, outcome=outcome, min_latency_ms=min_latency_ms,
    )


@router.get("/calls", response_model=LLMCallPageDTO)
async def list_llm_calls(
    user: User = Depends(require_permission("llm.graph", "read")),
    calls: LLMCallsService = Depends(get_llm_calls_service),
    filters: CallFilters = Depends(_call_filters),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=CALLS_MAX_LIMIT),
    sort: Literal["newest", "oldest", "slowest", "tokens"] = Query("newest"),
) -> LLMCallPageDTO:
    """A page of gateway calls in a window, filtered."""
    return LLMCallPageDTO(**await calls.list_calls(filters, page=page, limit=limit, sort=sort))


@router.get("/turns", response_model=LLMTurnPageDTO)
async def list_llm_turns(
    user: User = Depends(require_permission("llm.graph", "read")),
    calls: LLMCallsService = Depends(get_llm_calls_service),
    filters: CallFilters = Depends(_call_filters),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=CALLS_MAX_LIMIT),
    sort: Literal["newest", "oldest", "slowest", "tokens"] = Query("newest"),
) -> LLMTurnPageDTO:
    """A page of turns: the calls made for one user request, with the tools and approvals in between."""
    return LLMTurnPageDTO(**await calls.list_turns(filters, page=page, limit=limit, sort=sort))


@router.get("/calls/stats", response_model=LLMCallStatsDTO)
async def get_llm_call_stats(
    user: User = Depends(require_permission("llm.graph", "read")),
    calls: LLMCallsService = Depends(get_llm_calls_service),
    filters: CallFilters = Depends(_call_filters),
) -> LLMCallStatsDTO:
    """The filtered calls summed up: volume, errors, latency percentiles, tokens, cost."""
    return LLMCallStatsDTO(**await calls.stats(filters))


@router.get("/calls/{call_id}", response_model=LLMCallDetailDTO)
async def get_llm_call(
    call_id: str,
    user: User = Depends(require_permission("llm.graph", "read")),
    calls: LLMCallsService = Depends(get_llm_calls_service),
) -> LLMCallDetailDTO:
    """One call, by its log row's id: tool calls, policy outcomes and the calls of its conversation."""
    detail = await calls.detail(call_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return LLMCallDetailDTO(**detail)


@router.get("/routing", response_model=LLMRoutingDTO)
async def get_llm_routing(
    user: User = Depends(require_permission("llm.graph", "read")),
    calls: LLMCallsService = Depends(get_llm_calls_service),
    filters: CallFilters = Depends(_call_filters),
) -> LLMRoutingDTO:
    """Which model names went to which providers in the window, with volume, errors and p95 latency."""
    return LLMRoutingDTO(**await calls.routing(filters))
