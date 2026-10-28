"""Usage for administrators: aggregates, the usage auditor's view, the settings, the limits, the statement.

- ``GET /api/admin/usage`` (``usage:read``): totals, a series by hour, day or month, and groups by team (a
  tree, ``services/usage/tree.py``), agent, model or provider, with no person in them. Every figure about
  fewer than k people is hidden, with complementary suppression (M04, M05; ``services/usage/suppression.py``);
  hours only where at least k people are (M10). Below k for the whole range the totals and the series are
  hidden too, and ``small_instance`` is set (S12); a usage auditor who gives a reason sees them while
  person-level reporting is on, recorded like a view of named people (M07).
- ``GET /api/admin/usage/people``: named people's figures, per day or month, sorted by name, never by usage
  (M11). Only with the switch on, the explicit grant, and a reason, and recorded (M02, M06, M07).
- ``GET``/``PUT /api/admin/usage/settings``: the ``usage.*`` settings; the switch needs Root Mode (M08: each
  change is recorded like every settings change).
- ``GET``/``PUT /api/admin/usage/limits``: the instance's caps in the gateway's ``tenant_llm_policy`` (S11).
- ``GET /api/admin/usage/statement``: the configuration statement in Markdown (S09).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Hashable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.root_session_dao import RootSessionDAO
from faden_backend.db.dao.system_settings_dao import SystemSettingsDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.api_tokens import ACCOUNT_TENANT
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services import teams
from faden_backend.services.extensions import usage_sections
from faden_backend.services.usage import config as usage_config
from faden_backend.services.usage import labels, people, rollups, statement, tree
from faden_backend.services.usage.measures import Measures, hidden
from faden_backend.services.usage.people import PeopleRefusal
from faden_backend.services.usage.suppression import Cell, hide
from faden_backend.web.api.rbac import require_permission
from faden_backend.web.api.usage import views as usage_views
from faden_backend.web.api.usage.common import (
    MAX_DAYS,
    MAX_HOUR_DAYS,
    day_range,
    gateway_session,
    period_text,
    periods,
    problems,
    refuse,
    utc_bounds,
)

router = APIRouter()

#: The largest value a cap column holds (Postgres ``integer``).
_INT_MAX = 2_147_483_647


async def root_mode_on(
    user: Annotated[User, Depends(current_active_user)],
    root_dao: RootSessionDAO = Depends(),
) -> bool:
    """Whether the caller is in Root Mode now (only superusers can be); never refuses by itself."""
    return bool(user.is_superuser and await root_dao.get_active(user.id) is not None)


# ── Aggregates ──────────────────────────────────────────────────────


async def _groups(
    session: AsyncSession, gw: AsyncSession, days: rollups.DayRange, group_by: rollups.GroupBy, k: int,
) -> list[dict[str, Any]]:
    """The groups at least k people strong, and one ``other`` row for the rest (hidden if it is under k)."""
    cells = await rollups.group_cells(gw, days, group_by)
    person_key = rollups.PERSON_KEYS.get(group_by)  # no provider in the per-person rollup
    if person_key is not None:
        exact = await rollups.people_per_key(gw, days, person_key)
        for key, cell in cells.items():
            cell.floor = max(cell.floor, exact.get(key, 0))

    async def together(group: list[Cell]) -> int:
        floor = max(cell.floor for cell in group)
        if person_key is not None:
            floor = max(floor, await rollups.people_in(gw, days, person_key, [cell.key for cell in group]))
        return floor

    hidden_keys, hidden_people = await hide(list(cells.values()), k, together)
    shown = [cell for key, cell in cells.items() if key not in hidden_keys]
    names = await labels.label(session, gw, group_by, [cell.key for cell in shown])
    rows = [
        {
            "key": cell.key,
            "label": names.get(cell.key),
            "other": False,
            "people": cell.floor,
            "suppressed": False,
            **cell.measures.as_json(),
        }
        for cell in sorted(shown, key=lambda cell: (cell.key is None, str(names.get(cell.key) or cell.key)))
    ]
    if hidden_keys:
        other = Measures()
        for key in hidden_keys:
            other.merge(cells[key].measures)
        figures = other.as_json() if hidden_people >= k else hidden()
        rows.append(
            {"key": None, "label": None, "other": True, "people": None, "suppressed": True, **figures},
        )
    return rows


async def _team_tree(
    session: AsyncSession, gw: AsyncSession, days: rollups.DayRange, structure: tree.Structure, k: int,
) -> list[dict[str, Any]]:
    """The teams as a tree, each with its sub-teams, k people per row at least (services/usage/tree.py)."""
    now_tree = await teams.load(session)
    built = tree.Built()
    for row in await rollups.team_cells(gw, days):
        built.add_cells(tree.path_of(row["group_id"], row["group_path"], structure, now_tree), row)
    for group_id, group_path, user_id in await rollups.team_people(gw, days):
        built.add_person(tree.path_of(group_id, group_path, structure, now_tree), user_id)
    keys = {key for path in built.subtree for key in path}
    names = await labels.label(session, gw, "team", keys)
    return tree.rows(built, k, names.get)


async def _series(
    gw: AsyncSession,
    days: rollups.DayRange,
    bucket: rollups.Bucket,
    config: usage_config.UsageSettings,
    *,
    unhidden: bool = False,
) -> list[dict[str, Any]]:
    """Every period of the range; one about fewer than k people (with its complement) carries no figures.

    *unhidden*: a usage auditor's look with a reason (person-level reporting on): nothing is hidden.
    """
    k = 0 if unhidden else config.k
    if bucket == "hour":
        start, end = utc_bounds(days, config.zone)
        cells = await rollups.hour_cells(gw, start, end)
        period_key: str | None = None  # no person hours: each hour's own count is all there is
    else:
        cells = await rollups.period_cells(gw, days, bucket)
        period_key = rollups.PERIODS[bucket]
        exact = await rollups.people_per_key(gw, days, period_key)
        for key, cell in cells.items():
            cell.floor = max(cell.floor, exact.get(key, 0))

    async def together(group: list[Cell]) -> int:
        floor = max(cell.floor for cell in group)
        if period_key is not None:
            floor = max(floor, await rollups.people_in(gw, days, period_key, [cell.key for cell in group]))
        return floor

    hidden_keys, _ = await hide(list(cells.values()), k, together)
    series: list[dict[str, Any]] = []
    for period in periods(days, bucket, config.zone):
        if period in hidden_keys:
            series.append({"period": period_text(period), "suppressed": True, **hidden()})
        else:
            found = cells.get(period)
            figures = found.measures if found is not None else Measures()
            series.append({"period": period_text(period), "suppressed": False, **figures.as_json()})
    return series


@router.get("", name="admin_usage")
async def admin_usage(
    user: Annotated[User, Depends(require_permission("usage", "read"))],
    session: AsyncSession = Depends(get_db_session),
    gw: AsyncSession = Depends(gateway_session),
    first: str | None = Query(default=None, alias="from"),
    last: str | None = Query(default=None, alias="to"),
    bucket: rollups.Bucket = "day",
    group_by: rollups.GroupBy = "team",
    structure: tree.Structure = "recorded",
    reason: str | None = Query(default=None, max_length=2000),
) -> dict[str, Any]:
    """Usage of teams, agents, models or providers over the range (default the last 30 days); no person.

    With a *reason*, a usage auditor (person-level reporting on) sees the totals and the series unhidden;
    refused like ``/people`` otherwise, and recorded either way.
    """
    config = await usage_config.read(session)
    k = config.k
    days = day_range(first, last, config.zone, max_days=MAX_HOUR_DAYS if bucket == "hour" else MAX_DAYS)
    total = await rollups.total_cell(gw, days)
    total_people = max(total.floor, await rollups.people_in(gw, days, None, []))
    small = total.measures.requests > 0 and total_people < k
    unhidden = reason is not None and await _auditor_look(session, gw, user, config, days, reason, small)
    shown_totals = not small or unhidden
    if group_by == "team":
        groups = await _team_tree(session, gw, days, structure, k)
    else:
        groups = await _groups(session, gw, days, group_by, k)
    return {
        "time_zone": config.time_zone,
        "from": days.first.isoformat(),
        "to": days.last.isoformat(),
        "bucket": bucket,
        "group_by": group_by,
        "structure": structure,
        "min_group_size": k,
        "small_instance": small,
        "totals": {
            **(total.measures.as_json() if shown_totals else hidden()),
            "people": total_people if total_people >= k or unhidden else None,
        },
        "series": await _series(gw, days, bucket, config, unhidden=unhidden),
        "groups": groups,
        "person_reporting": config.person_reporting,
        "can_view_people": config.person_reporting and await people.holds_audit_grant(session, user),
        "unhidden": unhidden,
        "sections": await usage_sections(session, user, "admin"),
    }


async def _auditor_look(
    session: AsyncSession,
    gw: AsyncSession,
    user: User,
    config: usage_config.UsageSettings,
    days: rollups.DayRange,
    reason: str,
    small: bool,
) -> bool:
    """A usage auditor's look at totals and series below k: allowed and recorded, or refused and recorded.

    The people of the range are named in the record when the range holds fewer than k of them: the look was at
    their figures, and each sees it in their own list (S03).
    """
    try:
        cleaned = await people.entitled(session, user, config, reason)
    except PeopleRefusal as refusal:
        await people.record_view(
            session, user, target_type="usage_aggregate", target_id="instance", person_ids=[],
            details={"from": days.first.isoformat(), "to": days.last.isoformat()},
            reason=reason.strip()[:500] or None, refused=refusal.code,
        )
        refuse(refusal)
    await people.record_view(
        session, user, target_type="usage_aggregate", target_id="instance",
        person_ids=await rollups.people_ids(gw, days) if small else [],
        details={"from": days.first.isoformat(), "to": days.last.isoformat()}, reason=cleaned,
    )
    return True


# ── Named people (the usage auditor's view) ─────────────────────────


def _one_target(team: str | None, person: str | None) -> tuple[str, str]:
    """``(kind, id)`` of the one team or person asked for; 422 unless exactly one valid id is given."""
    if (team is None) == (person is None):
        problems(("team", "invalid"), ("person", "invalid"))
    kind, value = ("team", team) if team is not None else ("person", person)
    try:
        return kind, str(uuid.UUID(str(value)))
    except ValueError:
        problems((kind, "invalid"))


@router.get("/people", name="admin_usage_people")
async def admin_usage_people(
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
    gw: AsyncSession = Depends(gateway_session),
    first: str | None = Query(default=None, alias="from"),
    last: str | None = Query(default=None, alias="to"),
    team: str | None = None,
    person: str | None = None,
    reason: str | None = Query(default=None, max_length=2000),
) -> dict[str, Any]:
    """One team's people, or one person: figures per person and day (or month), sorted by name."""
    config = await usage_config.read(session)
    kind, target = _one_target(team, person)
    target_type = f"usage_{kind}"
    try:
        cleaned = await people.entitled(session, user, config, reason)
    except PeopleRefusal as refusal:
        await people.record_view(
            session, user, target_type=target_type, target_id=target,
            person_ids=[target] if kind == "person" else [], details={"from": first, "to": last},
            reason=(reason or "").strip()[:500] or None, refused=refusal.code,
        )
        refuse(refusal)
    days = day_range(first, last, config.zone, max_days=config.person_retention_days)
    found = await rollups.person_rows(
        gw, days, rollups.PERIODS[config.person_granularity],
        team=target if kind == "team" else None, person=target if kind == "person" else None,
    )
    person_ids = {user_id for user_id, _ in found} | ({target} if kind == "person" else set())
    accounts = await _accounts(session, person_ids)

    def order(item: tuple[tuple[str, Hashable], Measures]) -> tuple[bool, str, str, str]:
        (user_id, period), _ = item
        email = accounts.get(user_id)
        return email is None, (email or "").lower(), user_id, period_text(period)

    rows = [
        {
            "person": {"id": user_id, "name": accounts.get(user_id), "email": accounts.get(user_id)},
            "period": period_text(period),
            **measures.as_json(),
        }
        for (user_id, period), measures in sorted(found.items(), key=order)
    ]
    await people.record_view(
        session, user, target_type=target_type, target_id=target, person_ids=person_ids,
        details={
            "from": days.first.isoformat(), "to": days.last.isoformat(),
            "granularity": config.person_granularity, "rows": len(rows),
        },
        reason=cleaned,
    )
    return {"granularity": config.person_granularity, "rows": rows}


@router.get("/people/{user_id}/export", name="admin_usage_person_export")
async def admin_usage_person_export(
    user_id: uuid.UUID,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
    gw: AsyncSession = Depends(gateway_session),
    reason: str | None = Query(default=None, max_length=2000),
    format: Literal["json", "csv"] = "json",
) -> Response:
    """One person's usage data as they download it themselves, for an access request (M13; GDPR Art. 15).

    The same gates as the per-person view; recorded as ``usage.people.export``.
    """
    config = await usage_config.read(session)
    target = str(user_id)
    try:
        cleaned = await people.entitled(session, user, config, reason)
    except PeopleRefusal as refusal:
        await people.record_view(
            session, user, target_type="usage_person", target_id=target, person_ids=[target],
            details={"format": format}, reason=(reason or "").strip()[:500] or None, refused=refusal.code,
            action=people.EXPORT_ACTION,
        )
        refuse(refusal)
    person = await session.get(User, user_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such account")
    data = await usage_views.download(session, gw, person)
    await people.record_view(
        session, user, target_type="usage_person", target_id=target, person_ids=[target],
        details={"format": format}, reason=cleaned, action=people.EXPORT_ACTION,
    )
    return usage_views.file_response(data, format, f"usage-{target}")


async def _accounts(session: AsyncSession, user_ids: set[str]) -> dict[str, str]:
    """Each id's e-mail address (core keeps no other name); an account since deleted is missing."""
    ids: list[uuid.UUID] = []
    for user_id in user_ids:
        try:
            ids.append(uuid.UUID(user_id))
        except ValueError:
            continue
    if not ids:
        return {}
    rows = await session.execute(select(User.id, User.email).where(User.id.in_(ids)))
    return {str(row_id): str(email) for row_id, email in rows}


# ── Settings ────────────────────────────────────────────────────────


@router.get("/settings", name="admin_usage_settings")
async def admin_usage_settings(
    _user: Annotated[User, Depends(require_permission("system.settings", "read"))],
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The ``usage.*`` settings in force, by their names without ``usage.``."""
    return (await usage_config.read(session)).as_dict()


@router.put("/settings", name="admin_usage_settings_update")
async def admin_usage_settings_update(
    user: Annotated[User, Depends(require_permission("system.settings", "update"))],
    body: Annotated[dict[str, Any], Body()],
    root_mode: bool = Depends(root_mode_on),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Change the settings in *body* (any of them; the others stay); all or none.

    422 with a problem per bad field; 403 ``root_mode`` when the switch would change outside Root Mode. Each
    setting that changes is recorded (``system.settings.update``) with its old and new value.
    """
    current = await usage_config.read(session)
    changes, found = usage_config.check_changes(body)
    if found:
        problems(*((problem["field"], problem["code"]) for problem in found))
    changed = {name: value for name, value in changes.items() if getattr(current, name) != value}
    if "person_reporting" in changed and not root_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "root_mode"})
    dao, audit = SystemSettingsDAO(session), AuditDAO(session)
    for name, value in changed.items():
        key = usage_config.PREFIX + name
        await dao.upsert_value(key, {"value": value}, user.id)
        await audit.log(
            action="system.settings.update",
            target_type="system_setting",
            target_id=key,
            result=AuditResult.ALLOW,
            actor_id=user.id,
            severity="high" if name == "person_reporting" or root_mode else "normal",
            metadata_json=json.dumps({"from": getattr(current, name), "to": value, "via": "usage"}),
        )
    await session.flush()
    return (await usage_config.read(session)).as_dict()


# ── The instance's limits ───────────────────────────────────────────


@router.get("/limits", name="admin_usage_limits")
async def admin_usage_limits(
    _user: Annotated[User, Depends(require_permission("usage", "read"))],
    gw: AsyncSession = Depends(gateway_session),
) -> dict[str, int | None]:
    """The instance's caps: requests and tokens per minute, tokens per day (``null``: no cap)."""
    return await rollups.read_limits(gw)


@router.put("/limits", name="admin_usage_limits_update")
async def admin_usage_limits_update(
    user: Annotated[User, Depends(require_permission("system.settings", "update"))],
    body: Annotated[dict[str, Any], Body()],
    session: AsyncSession = Depends(get_db_session),
    gw: AsyncSession = Depends(gateway_session),
) -> dict[str, int | None]:
    """Set the caps in *body* (positive whole numbers, ``null``: none); recorded ``usage.limits.update``."""
    changes: dict[str, int | None] = {}
    found: list[tuple[str, str]] = []
    for name, value in body.items():
        if name not in rollups.LIMITS or isinstance(value, bool) or not isinstance(value, int | None):
            found.append((name, "invalid"))
        elif value is not None and not 0 < value <= _INT_MAX:
            found.append((name, "out_of_range"))
        else:
            changes[name] = value
    if found:
        problems(*found)
    before = await rollups.read_limits(gw)
    if changes:
        await rollups.write_limits(gw, changes)
        await gw.commit()
    after = await rollups.read_limits(gw)
    await AuditDAO(session).log(
        action="usage.limits.update",
        target_type="tenant_llm_policy",
        target_id=ACCOUNT_TENANT,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        metadata_json=json.dumps({"before": before, "after": after}),
    )
    return after


# ── The configuration statement ─────────────────────────────────────


@router.get("/statement", name="admin_usage_statement")
async def admin_usage_statement(
    _user: Annotated[User, Depends(require_permission("usage", "read"))],
    session: AsyncSession = Depends(get_db_session),
    gw: AsyncSession = Depends(gateway_session),
    language: Literal["en", "de", "es", "fr", "zh"] = "en",
) -> Response:
    """The privacy configuration in force, as a Markdown download in *language*."""
    now = datetime.now(UTC)
    config = await usage_config.read(session)
    text = statement.render(
        config,
        await rollups.read_limits(gw),
        await people.auditors(session),
        await people.count_views(session, statement.year_before(now)),
        language,
        now,
    )
    stamp = now.astimezone(config.zone).date().isoformat()
    return Response(
        content=text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="usage-configuration-{stamp}-{language}.md"'},
    )
