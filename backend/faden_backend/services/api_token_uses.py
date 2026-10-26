"""Every use of an API token (``api_token_uses``, written by the gateway): what the console shows of them.

Each token's last use and how often it was used lately, one token's uses in turn, and their deletion after
``api_token_use_retention_days`` (GDPR Art. 5(1)(e)). A person's last token use also counts as activity for
the sign-in upkeep, so people who work only through tokens are not taken for gone.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import ScalarSelect, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.api_tokens import ApiTokenUse
from faden_backend.db.models.users import User

#: How far back "used lately" counts, in days.
RECENT_DAYS = 30


@dataclass(frozen=True)
class LastUse:
    """A token's latest use, and how many it had in the last :data:`RECENT_DAYS` days."""

    at: datetime
    address: str | None
    client: str | None
    route: str | None
    recent: int


async def last_uses(session: AsyncSession, token_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, LastUse]:
    """The latest use of each of *token_ids* that was used (within the kept period)."""
    if not token_ids:
        return {}
    latest = (await session.execute(
        select(ApiTokenUse)
        .where(ApiTokenUse.token_id.in_(token_ids))
        .order_by(ApiTokenUse.token_id, ApiTokenUse.at.desc(), ApiTokenUse.id.desc())
        .distinct(ApiTokenUse.token_id),
    )).scalars().all()
    since = datetime.now(UTC) - timedelta(days=RECENT_DAYS)
    counts = dict((await session.execute(
        select(ApiTokenUse.token_id, func.count())
        .where(ApiTokenUse.token_id.in_(token_ids), ApiTokenUse.at >= since)
        .group_by(ApiTokenUse.token_id),
    )).tuples().all())
    return {
        use.token_id: LastUse(
            at=use.at, address=use.address, client=use.client, route=use.route,
            recent=counts.get(use.token_id, 0),
        )
        for use in latest
    }


async def uses_of(session: AsyncSession, token_id: uuid.UUID, *, limit: int = 100) -> list[ApiTokenUse]:
    """The token's latest uses, newest first."""
    return list((await session.execute(
        select(ApiTokenUse)
        .where(ApiTokenUse.token_id == token_id)
        .order_by(ApiTokenUse.at.desc(), ApiTokenUse.id.desc())
        .limit(limit),
    )).scalars())


def last_token_use_of_user() -> ScalarSelect[datetime]:
    """The person's latest API token use, for a query over users (the upkeep's activity)."""
    return select(func.max(ApiTokenUse.at)).where(ApiTokenUse.user_id == User.id).scalar_subquery()


async def delete_older_than(session: AsyncSession, days: int) -> int:
    """Delete the uses older than *days*; how many."""
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(delete(ApiTokenUse).where(ApiTokenUse.at < cutoff))
    return int(result.rowcount or 0)
