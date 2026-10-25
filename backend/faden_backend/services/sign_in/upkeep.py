"""The hourly upkeep of accounts that sign in with an outside provider.

- **Inactive** (CIS v8 5.3): an account linked to a provider whose policy has ``inactive_days``, and not
  used for that long, is suspended. Used means a sign-in with any of its providers, or one of its agents
  renewing its sign-in. Personal API tokens do not record their use, so they do not count.
- **Gone** (CIS v8 6.2, BSI ORP.4.A2): for a directory provider the module says whom it still has. The
  disabled and the missing are suspended; for the others the memberships the provider keeps follow the
  directory, as they would at a sign-in.
- Suspended means what an administrator does by hand (``PUT /api/admin/users/{id}/active``): the flag set,
  sign-ins ended, API tokens and agents refused; nothing is deleted, and the upkeep reactivates nobody.
- Superusers are never suspended by it: the local administrator must always get in (BSI ORP.4.A20); an
  administrator decides about another.

Every backend worker runs the loop (``web/lifespan.py``); a transaction-scoped advisory lock lets one act
at a time. A module that cannot be reached changes nothing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select, text

from faden_backend.db.models.agents import AgentRefreshToken
from faden_backend.db.models.oauth import OAuthAccount
from faden_backend.db.models.users import User, end_sign_ins
from faden_backend.services.sign_in import accounts, module

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from faden_backend.services.sign_in.contract import ProviderUpkeep

log = logging.getLogger(__name__)
LOCK = "faden_sign_in_upkeep"
#: People asked about in one call to the module.
BATCH = 500


@dataclass
class Report:
    """What one pass did."""

    suspended: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    groups_changed: int = 0
    errors: list[str] = field(default_factory=list)


async def _suspend(session: AsyncSession, user: User, reason: str, provider: str, report: Report) -> None:
    from faden_backend.db.models.containers import AuditResult  # noqa: PLC0415
    from faden_backend.services.audit import chain  # noqa: PLC0415

    user.is_active = False
    end_sign_ins(user)
    await session.flush()
    report.suspended.append((user.id, reason))
    log.info("Suspended %s: %s (provider %s)", user.id, reason, provider)
    await chain.append(
        chain.NewEvent(
            action="user.suspend", target_type="user", target_id=str(user.id), result=AuditResult.ALLOW,
            severity="high", source="system",
            metadata_json=json.dumps({
                "by": "sign_in_upkeep", "reason": reason, "provider": provider, "email": user.email,
            }),
        ),
        request_session=session,
    )


async def _inactive(session: AsyncSession, provider: ProviderUpkeep, report: Report) -> None:
    """Suspend the accounts linked to *provider* that nobody used for its ``inactive_days``."""
    cutoff = datetime.now(UTC) - timedelta(days=provider.inactive_days or 0)
    last_sign_in = (
        select(func.max(OAuthAccount.last_sign_in_at))
        .where(OAuthAccount.user_id == User.id)
        .scalar_subquery()
    )
    last_agent = (
        select(func.max(func.coalesce(AgentRefreshToken.last_used_at, AgentRefreshToken.created_at)))
        .where(AgentRefreshToken.user_id == User.id)
        .scalar_subquery()
    )
    # greatest() leaves out NULLs; an account with no recorded use at all is left alone.
    found = await session.scalars(
        select(User)
        .where(
            User.is_active.is_(True),
            User.is_superuser.is_(False),
            User.id.in_(select(OAuthAccount.user_id).where(OAuthAccount.oauth_name == provider.key)),
            func.greatest(last_sign_in, last_agent) < cutoff,
        ),
    )
    for user in found.all():
        await _suspend(session, user, f"inactive_{provider.inactive_days}_days", provider.key, report)


async def _directory(session: AsyncSession, provider: ProviderUpkeep, report: Report) -> None:
    """Suspend whom the directory no longer has, or has disabled; groups follow it for the others."""
    rows = (await session.execute(
        select(OAuthAccount.account_id, User)
        .join(User, User.id == OAuthAccount.user_id)
        .where(OAuthAccount.oauth_name == provider.key, User.is_active.is_(True)),
    )).all()
    people: dict[str, User] = dict(rows)  # type: ignore[arg-type]
    subjects = list(people)
    for start in range(0, len(subjects), BATCH):
        try:
            answer = await module.directory(provider.key, subjects[start:start + BATCH])
        except (module.ModuleUnavailableError, module.ModuleRefusedError) as exc:
            log.warning("Directory check of %s not done: %s", provider.key, exc)
            report.errors.append(f"{provider.key}: {exc}")
            return
        for person in answer.people:
            user = people.get(person.subject)
            if user is None:
                continue
            if person.status in ("disabled", "missing"):
                if user.is_superuser:
                    log.warning("The directory of %s says %s is %s; a superuser, left to an administrator",
                                provider.key, user.id, person.status)
                    continue
                await _suspend(session, user, f"directory_{person.status}", provider.key, report)
            elif person.status == "active" and answer.manage_groups:
                added, removed = await accounts.sync_groups(session, user, provider.key, person.group_ids)
                report.groups_changed += bool(added or removed)


async def upkeep_once(session: AsyncSession) -> Report | None:
    """One pass in *session*; ``None`` when there is no module, it cannot say, or another worker is at it."""
    if not module.enabled():
        return None
    try:
        providers = await module.upkeep()
    except (module.ModuleUnavailableError, module.ModuleRefusedError) as exc:
        log.warning("Sign-in upkeep not done: %s", exc)
        return None
    if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:lock))"), {"lock": LOCK}):
        return None
    report = Report()
    for provider in providers:
        if provider.inactive_days:
            await _inactive(session, provider, report)
        if provider.directory:
            await _directory(session, provider, report)
    return report


async def run_pass(session_factory: async_sessionmaker[AsyncSession]) -> Report | None:
    """One pass, committed."""
    async with session_factory() as session:
        report = await upkeep_once(session)
        await session.commit()
    return report
