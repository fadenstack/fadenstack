"""A startup check: does this service's database pool fit its Postgres.

Every uvicorn worker of every service keeps its own connection pool, and all
of them share one Postgres, which accepts ``max_connections`` (100 by default)
less the few it reserves for superusers. When the pools can add up to more than
that, nothing fails at startup: under load Postgres starts answering "too many
clients" to every service at once, and a pool that grew keeps its connections
afterwards. Pools used to be sized from the CPU count, and on a 64-thread
workstation one gateway worker could open 384 (2026-09-25).

So each service reads the limit once at startup and says plainly when its own
share cannot fit: an error when it alone could take everything, a warning when
it could take more than half, the numbers either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Budget:
    """The verdict: how loudly to say it, and what to say."""

    level: int
    message: str


def assess(
    *,
    service: str,
    env_prefix: str,
    workers: int,
    pool_size: int,
    max_overflow: int,
    max_connections: int,
    reserved: int,
) -> Budget:
    """Compare the most this service can open with what Postgres accepts."""
    usable = max(0, max_connections - reserved)
    most = max(1, workers) * (pool_size + max_overflow)
    sizing = f"{workers} worker(s) x ({pool_size} kept + {max_overflow} on demand)"
    limit = f"max_connections {max_connections}, {reserved} reserved"
    fix = (
        f"lower {env_prefix}WORKERS_COUNT, {env_prefix}DB_POOL_SIZE or "
        f"{env_prefix}DB_MAX_OVERFLOW, or raise max_connections in Postgres"
    )
    if most > usable:
        return Budget(
            logging.ERROR,
            f"Database connections: {service} can open up to {most} ({sizing}), but "
            f"this Postgres accepts only {usable} ({limit}). Under load it will refuse "
            f"new connections to every service. To fix: {fix}.",
        )
    if most > usable // 2:
        return Budget(
            logging.WARNING,
            f"Database connections: {service} can open up to {most} of the "
            f"{usable} this Postgres accepts ({sizing}; {limit}), leaving "
            f"{usable - most} for every other service on it. If they share it: "
            f"{fix}.",
        )
    return Budget(
        logging.INFO,
        f"Database connections: {service} can open up to {most} of {usable} "
        f"({sizing}).",
    )


async def check_connection_budget(
    engine: AsyncEngine,
    *,
    service: str,
    env_prefix: str,
    workers: int,
    pool_size: int,
    max_overflow: int,
) -> Budget | None:
    """Read Postgres' limit, log the verdict and return it.

    ``None`` when nothing was checked: not Postgres, or the limit unreadable.
    """
    if engine.dialect.name != "postgresql":
        return None
    try:
        async with engine.connect() as conn:
            limit = await conn.execute(text("SHOW max_connections"))
            max_connections = int(limit.scalar_one())
            kept = await conn.execute(text("SHOW superuser_reserved_connections"))
            reserved = int(kept.scalar_one())
    except Exception:
        log.warning(
            "Database connections: could not read Postgres' connection limit; "
            "the pool size of %s is not checked.",
            service,
            exc_info=True,
        )
        return None
    budget = assess(
        service=service,
        env_prefix=env_prefix,
        workers=workers,
        pool_size=pool_size,
        max_overflow=max_overflow,
        max_connections=max_connections,
        reserved=reserved,
    )
    log.log(budget.level, budget.message)
    return budget
