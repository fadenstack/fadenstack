"""The dashboard's key metrics, one function each.

A KPI is computed on its own: one whose source is missing or failing is left
out of the answer (and named in ``unavailable``) instead of failing the rest.
Each carries the same measure over its comparison window, stated in the spec:
"today" against yesterday over the same wall-clock span, a month against the
previous month up to the same day, the last hour against the hour before.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from faden_backend.services.dashboard import attention as attention_rules
from faden_backend.services.dashboard import capacity as capacity_service
from faden_backend.services.dashboard import timewindows as tw
from faden_backend.services.dashboard import traffic
from faden_backend.services.dashboard.facts import Facts

log = logging.getLogger(__name__)

Kpi = dict[str, Any]
KpiFn = Callable[[Facts], Awaitable[Kpi]]


async def requests_today(facts: Facts) -> Kpi:
    """Requests since local midnight, and tokens."""
    current, previous = tw.today(facts.now, facts.tz)
    a, b = await asyncio.gather(facts.totals(current), facts.totals(previous))
    return {"value": a.requests, "previous": b.requests, "context": {"tokens": a.tokens}}


async def active_users_today(facts: Facts) -> Kpi:
    """Distinct users with a request since local midnight, and this month."""
    current, previous = tw.today(facts.now, facts.tz)
    month, _ = tw.month(facts.now, facts.tz)
    a, b, m = await asyncio.gather(facts.totals(current), facts.totals(previous), facts.totals(month))
    return {"value": a.users, "previous": b.users, "context": {"month": m.users}}


async def in_house_share_today(facts: Facts) -> Kpi:
    """Share of routed requests served on our machines or network (unknown counts against it)."""
    current, previous = tw.today(facts.now, facts.tz)
    attribution, rows_now, rows_before = await asyncio.gather(
        facts.attribution(), facts.instances(current), facts.instances(previous)
    )
    split = traffic.residency_split(rows_now, attribution)
    routed = sum(split.values())
    return {
        "value": traffic.in_house_share(split),
        "previous": traffic.in_house_share(traffic.residency_split(rows_before, attribution)),
        "context": {
            "external": split["external"] / routed if routed else None,
            "unknown": split["unknown"] / routed if routed else None,
            "routed": routed,
        },
    }


async def spend_month(facts: Facts) -> Kpi:
    """Estimated cost this month, per currency, and requests that should have had a price."""
    current, previous = tw.month(facts.now, facts.tz)
    attribution, now_rows, before_rows = await asyncio.gather(
        facts.attribution(), facts.spend(current), facts.spend(previous)
    )
    a = traffic.fold_spend(now_rows, attribution)
    b = traffic.fold_spend(before_rows, attribution)
    return {
        "value": [{"currency": c, "amount": v} for c, v in a.by_currency.items()],
        "previous": [{"currency": c, "amount": v} for c, v in b.by_currency.items()],
        "context": {"unpriced_requests": a.unpriced},
    }


async def error_rate_1h(facts: Facts) -> Kpi:
    """Share of requests answered with status >= 400 in the last hour, and P95 latency."""
    current, previous = tw.rolling(facts.now, timedelta(hours=1))
    a, b = await asyncio.gather(facts.totals(current, latency=True), facts.totals(previous))
    return {
        "value": a.error_rate,
        "previous": b.error_rate,
        "context": {"p95_ms": a.p95_ms, "requests": a.requests},
    }


async def attention_open(facts: Facts) -> Kpi:
    """Current problems, by severity. No trend: it is a state, not a flow."""
    items, _failed = await facts.attention()
    by = attention_rules.counts(items)
    return {"value": len(items), "previous": None, "context": by}


async def gpus_busy(facts: Facts) -> Kpi:
    """GPUs at or above 10 % utilization, of those reporting. A load figure, not health."""
    cap = await facts.capacity()
    busy, reporting = capacity_service.gpus_busy(cap.machines)
    online = sum(1 for m in cap.machines if m.health not in ("offline", "stale"))
    return {
        "value": busy / reporting if reporting else None,
        "previous": None,
        "context": {"busy": busy, "reporting": reporting, "machines": online},
    }


#: Every KPI this backend can compute, by id.
KPIS: dict[str, KpiFn] = {
    "requests_today": requests_today,
    "active_users_today": active_users_today,
    "in_house_share_today": in_house_share_today,
    "spend_month": spend_month,
    "error_rate_1h": error_rate_1h,
    "attention_open": attention_open,
    "gpus_busy": gpus_busy,
}


async def compute(ids: list[str], facts: Facts) -> tuple[list[Kpi], list[str]]:
    """The asked-for KPIs this backend knows, in order; and those that failed."""
    known = [i for i in dict.fromkeys(ids) if i in KPIS]
    results = await asyncio.gather(*(KPIS[i](facts) for i in known), return_exceptions=True)
    items: list[Kpi] = []
    unavailable: list[str] = []
    for kpi_id, result in zip(known, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("dashboard KPI %s unavailable: %s", kpi_id, result)
            unavailable.append(kpi_id)
        else:
            items.append({"id": kpi_id, **result})
    return items, unavailable
