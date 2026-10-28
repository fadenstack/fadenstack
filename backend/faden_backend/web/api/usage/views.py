"""A person's own usage: ``GET /api/usage/me`` and its download (M03, M13, M14, S03).

Every signed-in person sees their own figures, by day or month, by model, agent and project; the instance's
limits and where it stands; whether person-level reporting is on and how often their figures were viewed. The
figures are their own only (``usage_daily_person`` with their id): nobody else's, not even in sums.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.extensions import usage_sections
from faden_backend.services.observability.service import _csv_cell
from faden_backend.services.usage import config as usage_config
from faden_backend.services.usage import labels, people, rollups
from faden_backend.services.usage.config import UsageSettings
from faden_backend.services.usage.measures import COUNTS, Measures
from faden_backend.web.api.usage.common import day_range, gateway_session, period_text, periods

router = APIRouter()

#: The most request-log rows a download carries (the log is kept up to 365 days).
EXPORT_REQUEST_ROWS = 100_000


async def _by(
    session: AsyncSession, gw: AsyncSession, user_id: str, days: rollups.DayRange, dimension: str,
) -> list[dict[str, Any]]:
    cells = await rollups.own_cells(gw, user_id, days, rollups.PERSON_KEYS[dimension])
    names = await labels.label(session, gw, dimension, cells.keys())
    ordered = sorted(cells.items(), key=lambda item: (-item[1].requests, str(item[0])))
    return [{"key": key, "label": names.get(key), **measures.as_json()} for key, measures in ordered]


async def _caps(gw: AsyncSession, config: UsageSettings, user_id: str) -> list[dict[str, Any]]:
    """The instance's limits, when each starts again, and how much of today's token limit is used.

    The per-minute counters are the gateway's (in memory, per tenant: S11), so their use is not known here.
    Today's tokens are the whole instance's; shown only when at least k people other than the caller used it
    today, else they and the caller's own figures would give away what the others used (M02).
    """
    limits = await rollups.read_limits(gw)
    now = datetime.now(UTC)
    next_minute = (now.replace(second=0, microsecond=0) + timedelta(minutes=1)).isoformat()
    caps: list[dict[str, Any]] = []
    for kind, name in (("rpm", "rpm_limit"), ("tpm", "tpm_limit")):
        if limits[name] is not None:
            caps.append({"kind": kind, "limit": limits[name], "used": None, "resets_at": next_minute})
    if limits["tokens_per_day"] is not None:
        today = now.astimezone(config.zone).date()
        tokens, people_today, mine = await rollups.tenant_day(gw, today, user_id)
        others = people_today - (1 if mine else 0)
        midnight = datetime.combine(today + timedelta(days=1), time(), config.zone).astimezone(UTC)
        caps.append({
            "kind": "tokens_per_day",
            "limit": limits["tokens_per_day"],
            "used": tokens if others >= config.k else None,
            "resets_at": midnight.isoformat(),
        })
    return caps


@router.get("/me", name="usage_me")
async def my_usage(
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
    gw: AsyncSession = Depends(gateway_session),
    first: str | None = Query(default=None, alias="from"),
    last: str | None = Query(default=None, alias="to"),
    bucket: Literal["day", "month"] = "day",
) -> dict[str, Any]:
    """The caller's own usage over the range (default the last 30 days), by day or month."""
    config = await usage_config.read(session)
    days = day_range(first, last, config.zone)
    user_id = str(user.id)
    total = (await rollups.own_cells(gw, user_id, days, None)).get(None, Measures())
    per_period = await rollups.own_cells(gw, user_id, days, rollups.PERIODS[bucket])
    return {
        "time_zone": config.time_zone,
        "from": days.first.isoformat(),
        "to": days.last.isoformat(),
        "bucket": bucket,
        "totals": total.as_json(),
        "series": [
            {"period": period_text(period), **per_period.get(period, Measures()).as_json()}
            for period in periods(days, bucket, config.zone)
        ],
        "by_model": await _by(session, gw, user_id, days, "model"),
        "by_agent": await _by(session, gw, user_id, days, "agent"),
        "by_project": await _by(session, gw, user_id, days, "project"),
        "caps": await _caps(gw, config, user_id),
        "sections": await usage_sections(session, user, "me"),
        "privacy": {
            "person_reporting": config.person_reporting,
            "granularity": config.person_granularity,
            "retention_days": config.person_retention_days,
            "views_of_my_data": len(await people.views_of(session, user.id)),
        },
    }


def _number(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


#: The columns of the CSV download; ``record`` says which kind of row it is (daily, request, view).
CSV_COLUMNS = (
    "record", "date", "time", "team_id", "team", "project_id", "agent_id", "agent", "model", "status",
    *COUNTS, "cost", "currency", "purpose",
)


async def download(session: AsyncSession, gw: AsyncSession, user: User) -> dict[str, Any]:
    """Everything kept about *user*'s usage: daily figures, request metadata, views of their figures.

    The person's own download, and the usage auditor's (``/api/admin/usage/people/{id}/export``).
    """
    config = await usage_config.read(session)
    user_id = str(user.id)
    daily = await rollups.own_rollup_rows(gw, user_id)
    requests = await rollups.own_requests(gw, user_id, EXPORT_REQUEST_ROWS + 1)
    truncated = len(requests) > EXPORT_REQUEST_ROWS
    requests = requests[:EXPORT_REQUEST_ROWS]
    teams = await labels.label(session, gw, "team", {row["group_id"] for row in daily})
    agents = await labels.label(
        session, gw, "agent", {row["agent_id"] for row in daily} | {row["agent_id"] for row in requests},
    )
    return {
        "person": {"id": user_id, "email": user.email},
        "generated_at": datetime.now(UTC).isoformat(),
        "time_zone": config.time_zone,
        "retention": {
            "request_log_days": config.request_log_retention_days,
            "per_person_days": config.person_retention_days,
        },
        "daily": [
            {
                "date": row["day"].isoformat(),
                "team_id": row["group_id"],
                "team": teams.get(row["group_id"]),
                "project_id": row["project_id"],
                "agent_id": row["agent_id"],
                "agent": agents.get(row["agent_id"]),
                "model": row["model_alias"],
                **{name: int(row[name] or 0) for name in COUNTS},
                "cost": _number(row["cost"]),
                "currency": row["currency"],
            }
            for row in daily
        ],
        "requests": [
            {
                "time": row["created_at"].astimezone(UTC).isoformat(),
                "model": row["model_alias"],
                "agent_id": row["agent_id"],
                "agent": agents.get(row["agent_id"]),
                "status": row["status_code"],
                **{name: row[name] for name in ("input_tokens", "cached_input_tokens", "output_tokens",
                                                "reasoning_tokens")},
                "cost": _number(row["cost"]),
                "currency": row["currency"],
            }
            for row in requests
        ],
        "requests_truncated": truncated,
        "views_of_my_data": await people.views_of(session, user.id),
    }


def as_csv(data: dict[str, Any]) -> str:
    """The download as one CSV table; ``record`` says what each row is."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    rows: list[dict[str, Any]] = [{"record": "daily", **row} for row in data["daily"]]
    rows += [
        {"record": "request", "date": row["time"][:10], **row} for row in data["requests"]
    ]
    rows += [
        {"record": "view", "date": row["at"][:10], "time": row["at"], "purpose": row["purpose"]}
        for row in data["views_of_my_data"]
    ]
    for row in rows:
        writer.writerow([_csv_cell(row.get(column)) for column in CSV_COLUMNS])
    return buf.getvalue()


@router.get("/me/export", name="usage_me_export")
async def my_usage_export(
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
    gw: AsyncSession = Depends(gateway_session),
    format: Literal["json", "csv"] = "json",
) -> Response:
    """A download of the caller's usage data (GDPR Art. 15): no content of requests, no device."""
    return file_response(await download(session, gw, user), format, "my-usage")


def file_response(data: dict[str, Any], format: str, name: str) -> Response:
    """*data* as a JSON or CSV download named ``<name>-<date>.<format>``."""
    stamp = datetime.now(UTC).date().isoformat()
    if format == "csv":
        body, media = as_csv(data), "text/csv; charset=utf-8"
    else:
        body, media = json.dumps(data, ensure_ascii=False, indent=2), "application/json"
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.{format}"'},
    )
