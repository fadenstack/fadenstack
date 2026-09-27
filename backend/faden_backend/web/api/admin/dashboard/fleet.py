"""Dashboard fleet: local AI capacity, and what needs attention.

Both read stored state only (see services/dashboard/capacity.py and
attention.py); neither calls Ray, an agent or a runtime.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from faden_backend.db.models.users import User
from faden_backend.services.dashboard import attention as attention_rules
from faden_backend.services.dashboard import capacity as capacity_service
from faden_backend.web.api.admin.dashboard.common import build_facts, dashboard_now
from faden_backend.web.api.admin.dashboard.schema import (
    AttentionDTO,
    AttentionItemDTO,
    CapacityDTO,
    CapacityGroupDTO,
    CapacityTotalsDTO,
    GpuDTO,
    IntegrationsDTO,
    LoadHistogramDTO,
    MachineDTO,
    MachineRefDTO,
    McpSummaryDTO,
    ProvidersSummaryDTO,
    SkillsSummaryDTO,
)
from faden_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()

_NOT_ONLINE = ("offline", "stale")


def _machine(m: capacity_service.Machine) -> MachineDTO:
    return MachineDTO(
        id=m.id,
        name=m.name,
        health=m.health,
        last_seen=m.last_seen,
        cpu_pct=m.cpu_pct,
        gpu_util_pct=m.gpu_util_pct,
        vram_allocated_pct=m.vram_pct,
        gpus=[
            GpuDTO(
                index=g.index, util_pct=g.util_pct, vram_allocated_pct=g.vram_allocated_pct, temp_c=g.temp_c
            )
            for g in m.gpus
        ],
    )


def _group_key(group: capacity_service.Group) -> str:
    return group.id or "unassigned"


def _online(machines: list[capacity_service.Machine]) -> int:
    return sum(1 for m in machines if m.health not in _NOT_ONLINE)


@router.get("/capacity", response_model=CapacityDTO, name="admin_dashboard_capacity")
async def get_capacity(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    now: Annotated[datetime, Depends(dashboard_now)],
    group: Annotated[str | None, Query(max_length=64, description="A cluster id, or 'unassigned'")] = None,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> CapacityDTO:
    """Clusters and machines: health (as reported), load (apart from health), and free GPUs.

    Small fleets get every machine; above ``MACHINE_LIST_LIMIT`` machines only
    the aggregates, and one group's machines when asked for (paged).
    """
    cap = await build_facts(request, now).capacity()
    include_all = len(cap.machines) <= capacity_service.MACHINE_LIST_LIMIT
    if group is not None and not any(_group_key(g) == group for g in cap.groups):
        raise HTTPException(status_code=404, detail="No such group")

    groups: list[CapacityGroupDTO] = []
    for g in cap.groups:
        dto = CapacityGroupDTO(
            kind=g.kind,
            id=g.id,
            name=g.name,
            status=g.status,
            observed_at=g.observed_at,
            stale=g.stale,
            machines_total=len(g.machines),
            machines_online=_online(g.machines),
            gpus_total=g.gpus_total,
            gpus_free=g.gpus_free,
            health_counts=capacity_service.health_counts(g.machines),
            load_histogram=LoadHistogramDTO(
                gpu_util=capacity_service.load_histogram([m.gpu_util_pct for m in g.machines]),
                vram_allocated=capacity_service.load_histogram([m.vram_pct for m in g.machines]),
            ),
            exceptions=[
                MachineRefDTO(id=m.id, name=m.name, health=m.health)
                for m in capacity_service.exceptions(g.machines)
            ],
        )
        if group == _group_key(g):
            page = g.machines[cursor : cursor + capacity_service.PAGE_SIZE]
            dto.machines = [_machine(m) for m in page]
            nxt = cursor + capacity_service.PAGE_SIZE
            dto.next_cursor = str(nxt) if nxt < len(g.machines) else None
        elif include_all and group is None:
            dto.machines = [_machine(m) for m in g.machines]
        groups.append(dto)

    known_free = [g.gpus_free for g in cap.groups if g.kind == "cluster" and g.gpus_free is not None]
    return CapacityDTO(
        generated_at=now,
        totals=CapacityTotalsDTO(
            clusters=cap.clusters,
            clusters_healthy=cap.clusters_healthy,
            machines=len(cap.machines),
            machines_online=_online(cap.machines),
            gpus=sum(len(m.gpus) for m in cap.machines),
            gpus_free=sum(known_free) if known_free else None,
            gpus_free_unknown_clusters=cap.gpus_free_unknown_clusters,
        ),
        groups=groups,
        machines_included=include_all and group is None,
    )


@router.get("/attention", response_model=AttentionDTO, name="admin_dashboard_attention")
async def get_attention(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    now: Annotated[datetime, Depends(dashboard_now)],
) -> AttentionDTO:
    """Current problems from stored state, most severe first; failing rules are listed, not fatal."""
    items, failed = await build_facts(request, now).attention()
    return AttentionDTO(
        generated_at=now,
        items=[
            AttentionItemDTO(
                id=i.id,
                code=i.code,
                severity=i.severity,
                category=i.category,
                params=i.params,
                since=i.since,
                link=i.link,
            )
            for i in items
        ],
        counts=attention_rules.counts(items),
        rule_errors=failed,
    )


@router.get("/integrations", response_model=IntegrationsDTO, name="admin_dashboard_integrations")
async def get_integrations(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)],
    now: Annotated[datetime, Depends(dashboard_now)],
) -> IntegrationsDTO:
    """Providers, MCP servers and skills; a module that is off has no section."""
    found = await build_facts(request, now).integrations()
    return IntegrationsDTO(
        generated_at=now,
        providers=ProvidersSummaryDTO(**found.providers),
        mcp=McpSummaryDTO(**found.mcp) if found.mcp is not None else None,
        skills=SkillsSummaryDTO(**found.skills) if found.skills is not None else None,
        unavailable=found.unavailable,
    )
