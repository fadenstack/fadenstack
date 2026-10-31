"""What administrators should know about usage now: the first purge of old request records, ahead of time.

The request log had no retention before the usage round; on upgrade the gateway first rolls the whole log up
(the backfill), then waits a week before it deletes what is older than ``usage.request_log_retention_days``
(``usage_rollup_state.purge_from``). Until then administrators see ``usage_purge_pending`` with the date and
the days kept, so they can lengthen the retention before anything goes (M09; the console words it).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.services.extensions import AdminNotice
from faden_backend.services.usage import config as usage_config


async def purge_pending(
    session: AsyncSession,
    gateway_sessions: Callable[[], AsyncSession] | None,
    *,
    now: datetime | None = None,
) -> list[AdminNotice]:
    """``usage_purge_pending`` while the gateway's first purge is still ahead; nothing once it may run."""
    if gateway_sessions is None:
        return []
    async with gateway_sessions() as gw:
        purge_from = (await gw.execute(text("SELECT MAX(purge_from) FROM usage_rollup_state"))).scalar_one()
    if purge_from is None or (now or datetime.now(UTC)) >= purge_from:
        return []
    config = await usage_config.read(session)
    return [
        AdminNotice(
            code="usage_purge_pending",
            severity="warning",
            params={
                "date": purge_from.astimezone(config.zone).date().isoformat(),
                "days": config.request_log_retention_days,
            },
        ),
    ]
