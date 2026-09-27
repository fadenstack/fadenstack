"""Integrations: the providers, MCP servers and skills the platform is connected to.

A section is None when its module is off -- the widget then shows no tile
for it. A module that is on but cannot be read (its service down) is named
in ``unavailable`` instead of failing the rest. The MCP and Skills services
are local; they are asked with a short timeout and the answer goes through
the fact cache (served stale while it refreshes).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from faden_backend.db.models.llm import LLMProvider, LLMRuntime, ProviderTarget, RuntimeStatus
from faden_backend.services.dashboard.traffic import RESIDENCY_CLASSES
from faden_backend.services.mcp.client import MCPServiceClient
from faden_backend.services.skills.client import SkillsPrincipal, SkillsServiceClient
from faden_backend.settings import settings

if TYPE_CHECKING:
    from faden_backend.services.dashboard.facts import Facts

log = logging.getLogger(__name__)

SERVICE_TIMEOUT_S = 3.0
#: MCP server states that mean it is not serving its tools.
MCP_PROBLEM_STATES = ("degraded", "disconnected", "error")


@dataclass
class Integrations:
    """What the platform is connected to, per module (None: module off)."""

    providers: dict[str, Any]
    mcp: dict[str, Any] | None = None
    skills: dict[str, Any] | None = None
    #: MCP servers in a problem state, for the attention rules (not sent as is).
    mcp_problems: list[dict[str, Any]] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)


async def _providers(facts: Facts) -> dict[str, Any]:
    attribution = await facts.attribution()

    async def unreachable(session: Any) -> int:
        rows = (
            await session.execute(
                select(LLMRuntime.provider_id)
                .join(LLMProvider, LLMProvider.id == LLMRuntime.provider_id)
                .where(
                    LLMRuntime.status == RuntimeStatus.ERROR,
                    LLMRuntime.desired_state == "running",
                    LLMProvider.target == ProviderTarget.REMOTE_ENDPOINT,
                )
                .distinct()
            )
        ).all()
        return len(rows)

    by = Counter(attribution.residency_of(p.id) for p in attribution.providers)
    return {
        "total": len(attribution.providers),
        "by_residency": {cls: by.get(cls, 0) for cls in RESIDENCY_CLASSES},
        "unreachable": await facts.on_backend(unreachable),
    }


async def _mcp() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    client = MCPServiceClient(
        base_url=settings.mcp_service_url,
        service_token=settings.mcp_service_token,
        timeout_sec=SERVICE_TIMEOUT_S,
    )
    data = await client.list_servers()
    servers = data.get("servers", []) if isinstance(data, dict) else list(data or [])
    enabled = [s for s in servers if s.get("enabled")]
    problems = [s for s in enabled if str(s.get("status")) in MCP_PROBLEM_STATES]
    return (
        {
            "total": len(servers),
            "enabled": len(enabled),
            "healthy": sum(1 for s in enabled if s.get("status") == "active"),
            "problems": len(problems),
        },
        [
            {
                "id": str(s.get("id")),
                "name": s.get("name"),
                "status": s.get("status"),
                "message": s.get("last_error"),
            }
            for s in problems
        ],
    )


#: Who the dashboard asks the skills service as: a tenant admin, so the
#: counts are the tenant's (and the global skills), never one user's view --
#: the facts are cached per tenant and shown to every admin.
_DASHBOARD_PRINCIPAL = SkillsPrincipal(user_id="system:dashboard", caps=("admin",))


async def _skills() -> dict[str, Any]:
    client = SkillsServiceClient(
        base_url=settings.skills_service_url,
        service_token=settings.skills_service_token,
        timeout_sec=SERVICE_TIMEOUT_S,
    )

    async def count(*params: tuple[str, str]) -> int:
        # One row is enough: only the list's ``total`` is read.
        data = await client.get_json(
            "/api/skills", _DASHBOARD_PRINCIPAL, params=[("view", "all"), ("limit", "1"), *params]
        )
        return int(data.get("total", 0))

    every, archived, usable = await asyncio.gather(
        count(), count(("status", "archived")), count(("usable", "true"))
    )
    # "Active" is what a chat can be given: published, enabled, with a version.
    return {"total": every - archived, "active": usable}


async def load(facts: Facts) -> Integrations:
    """Every section at once; a failing module is named, not fatal."""
    out = Integrations(
        providers={"total": 0, "by_residency": dict.fromkeys(RESIDENCY_CLASSES, 0), "unreachable": 0}
    )
    tasks: dict[str, Any] = {"providers": _providers(facts)}
    if settings.mcp_enabled:
        tasks["mcp"] = _mcp()
    if settings.skills_enabled:
        tasks["skills"] = _skills()
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for name, result in zip(tasks, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("dashboard integrations: %s unavailable: %s", name, result)
            out.unavailable.append(name)
        elif name == "providers":
            out.providers = result
        elif name == "mcp":
            out.mcp, out.mcp_problems = result
        else:
            out.skills = result
    return out
