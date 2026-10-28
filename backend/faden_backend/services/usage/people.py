"""Who may see the usage of named people other than themselves, and the record of every such view.

Three conditions, all of them (M02, M06, M07; GDPR Art. 25(2), 32(4)):

1. person-level reporting is on (``usage.person_reporting``, changed only in Root Mode);
2. the caller holds ``usage.people:audit`` through a role (directly or through a team). Superuser status
   does not count: the grant is read from the roles, never through ``require_permission``, which lets
   superusers by;
3. a reason of 10 to 500 characters.

Every view is recorded in the audit trail as ``usage.people.view``, every download of one person's data as
``usage.people.export``: who looked, whose figures (the people's ids), which range, and the reason. Refusals
are recorded too. A person sees the dates and purposes of both kinds about their own figures, without the
viewer's name (S03; CJEU C-579/21).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.rbac_dao import USAGE_PEOPLE_AUDIT, RbacDAO
from faden_backend.db.models.containers import AuditEvent, AuditResult
from faden_backend.db.models.groups import GroupRole, UserGroup
from faden_backend.db.models.rbac import Permission, RolePermission, UserRole
from faden_backend.db.models.users import User
from faden_backend.services.usage.config import UsageSettings

VIEW_ACTION = "usage.people.view"
EXPORT_ACTION = "usage.people.export"
#: The records of someone looking at named people's usage: views and downloads.
LOOK_ACTIONS = (VIEW_ACTION, EXPORT_ACTION)
REASON_MIN = 10
REASON_MAX = 500


class PeopleRefusal(Exception):  # noqa: N818 - a refusal, not an error of the program
    """The caller may not see named people's usage now: an HTTP status and the code the console words."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


async def holds_audit_grant(session: AsyncSession, user: User) -> bool:
    """Whether a role of *user* (direct or through a team) grants ``usage.people:audit``; superuser aside."""
    return await RbacDAO(session).has_permission(user.id, *USAGE_PEOPLE_AUDIT)


def clean_reason(reason: str | None) -> str | None:
    """The reason, trimmed, if it is 10 to 500 characters long; else ``None``."""
    trimmed = (reason or "").strip()
    return trimmed if REASON_MIN <= len(trimmed) <= REASON_MAX else None


async def entitled(session: AsyncSession, user: User, config: UsageSettings, reason: str | None) -> str:
    """The cleaned reason if *user* may see named people's usage now; else raise ``PeopleRefusal``.

    Checked in this order: the switch (``person_reporting_off``), the grant (``not_usage_auditor``), the
    reason (``reason_required``, 422).
    """
    if not config.person_reporting:
        raise PeopleRefusal(status.HTTP_403_FORBIDDEN, "person_reporting_off")
    if not await holds_audit_grant(session, user):
        raise PeopleRefusal(status.HTTP_403_FORBIDDEN, "not_usage_auditor")
    cleaned = clean_reason(reason)
    if cleaned is None:
        raise PeopleRefusal(status.HTTP_422_UNPROCESSABLE_CONTENT, "reason_required")
    return cleaned


async def record_view(
    session: AsyncSession,
    actor: User,
    *,
    target_type: str,
    target_id: str,
    person_ids: Iterable[str],
    details: dict[str, Any],
    reason: str | None,
    refused: str | None = None,
    action: str = VIEW_ACTION,
) -> None:
    """Record a view (or with ``EXPORT_ACTION`` a download) of named people's usage, or its refusal."""
    metadata = {
        "person_ids": sorted({str(pid) for pid in person_ids}),
        **details,
        "reason": reason,
    }
    if refused is not None:
        metadata["refused"] = refused
    await AuditDAO(session).log(
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=AuditResult.DENY if refused else AuditResult.ALLOW,
        actor_id=actor.id,
        severity="high",
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )


async def auditors(session: AsyncSession) -> list[str]:
    """The addresses of the active accounts that hold ``usage.people:audit``, directly or through a team."""
    resource, action = USAGE_PEOPLE_AUDIT
    granting = (
        select(RolePermission.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Permission.resource == resource, Permission.action == action)
    )
    direct = select(UserRole.user_id).where(UserRole.role_id.in_(granting))
    through_team = (
        select(UserGroup.user_id)
        .join(GroupRole, GroupRole.group_id == UserGroup.group_id)
        .where(GroupRole.role_id.in_(granting))
    )
    rows = await session.execute(
        select(User.email)
        .where(User.is_active.is_(True), User.id.in_(direct.union(through_team)))
        .order_by(User.email),
    )
    return [str(email) for email in rows.scalars()]


async def count_views(session: AsyncSession, since: datetime) -> int:
    """How many views and downloads of named people's usage were recorded since *since*."""
    found = await session.execute(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.action.in_(LOOK_ACTIONS),
            AuditEvent.result == AuditResult.ALLOW,
            AuditEvent.time >= since,
        ),
    )
    return int(found.scalar_one())


async def views_of(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """The recorded views and downloads of *user_id*'s figures by others, newest first: when, and the purpose.

    Not who looked (S03). The audit trail keeps them as long as it keeps its events.
    """
    needle = str(user_id)
    rows = await session.execute(
        select(AuditEvent.time, AuditEvent.metadata_json, AuditEvent.actor_id)
        .where(
            AuditEvent.action.in_(LOOK_ACTIONS),
            AuditEvent.result == AuditResult.ALLOW,
            AuditEvent.metadata_json.contains(needle),
        )
        .order_by(AuditEvent.time.desc()),
    )
    found: list[dict[str, Any]] = []
    for at, metadata_json, actor_id in rows:
        try:
            details = json.loads(metadata_json or "{}")
        except ValueError:
            continue
        if needle not in (details.get("person_ids") or []) or actor_id == user_id:
            continue
        found.append({"at": at.isoformat(), "purpose": details.get("reason")})
    return found
