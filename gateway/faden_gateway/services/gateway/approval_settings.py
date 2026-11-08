"""The approval settings the gateway follows: Bypass, the longest grant, how long Bypass evidence is kept.

Whether Bypass may be chosen, the longest grant in the installation, and the retention of Bypass evidence
(sdk/38 §5; sdk/09 L-57, L-65; sdk/40 A3).

Core has one tenant per installation, so the tenant's settings are the backend's system settings
(``system_setting_value``, keys ``approvals.*``, ``{"value": ...}``). Each worker reads them at most every 30
seconds, on the paths that need them (a chat in Bypass, a Bypass grant, the profile) and at each run of the
rollup job. A worker that cannot read them keeps what it read last; before its first read it has the defaults,
and Bypass is off (ASVS 5.0 V8.3.1: fail closed). Without a backend database the defaults hold.

When a read finds Bypass off, the running Bypass grants end, recorded as ended by the kill switch (L-59): an
admin who turns Bypass off stops every Bypass session within half a minute, and no call is covered meanwhile
(``tool_classes.effective`` narrows a chosen Bypass to Auto as soon as this worker has read the switch).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine

from faden_gateway.services.gateway import grants, usage_settings

log = logging.getLogger(__name__)

PREFIX = "approvals."
TTL_SEC = 30.0
#: (default, smallest, largest), as the backend's registry allows them.
LONGEST_GRANT_MINUTES = (grants.DEFAULT_LONGEST_MINUTES, 15, 720)
BYPASS_RETENTION_DAYS = (365, 90, 1095)


@dataclass(frozen=True, slots=True)
class ApprovalSettings:
    """The approval settings as the gateway applies them."""

    #: Whether a person may choose Bypass at all (L-57); off by default.
    bypass_allowed: bool = False
    #: The longest grant in the installation; an agent's own may only be shorter (sdk/38 §5).
    longest_grant_minutes: int = grants.DEFAULT_LONGEST_MINUTES
    #: How long a Bypass grant and its calls are kept after it ended (L-65).
    bypass_retention_days: int = 365


def _bounded(value: Any, default: int, low: int, high: int) -> int:
    """*value* as a whole number within *low* and *high*; *default* when it is none."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        number = int(value)
    except ValueError:
        return default
    return min(max(number, low), high)


def _value(raw: Any) -> Any:
    """A ``value_json`` as the backend stores it: ``{"value": ...}``, maybe still a JSON string."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw.get("value") if isinstance(raw, dict) else raw


def from_values(values: dict[str, Any]) -> ApprovalSettings:
    """The settings from the backend's values, by key without the ``approvals.`` prefix.

    A value out of its range is taken as the nearest end; anything but ``true`` keeps Bypass off.
    """
    return ApprovalSettings(
        bypass_allowed=values.get("bypass_allowed") is True,
        longest_grant_minutes=_bounded(values.get("longest_grant_minutes"), *LONGEST_GRANT_MINUTES),
        bypass_retention_days=_bounded(values.get("bypass_retention_days"), *BYPASS_RETENTION_DAYS),
    )


async def read(dsn: str | None = None) -> ApprovalSettings | None:
    """The settings from the backend's database; ``None`` when it cannot be read, the defaults without one."""
    dsn = dsn or usage_settings.backend_dsn()
    if dsn is None:
        return ApprovalSettings()
    try:
        conn = await asyncpg.connect(dsn, timeout=5)
        try:
            rows = await conn.fetch(
                "SELECT key, value_json FROM system_setting_value WHERE key LIKE 'approvals.%'", timeout=5,
            )
        finally:
            await conn.close()
    except Exception as exc:  # the backend's database is away: keep what was read last
        log.warning("The approval settings could not be read from the backend's database: %s", exc)
        return None
    return from_values({row["key"][len(PREFIX):]: _value(row["value_json"]) for row in rows})


class _Cache:
    def __init__(self) -> None:
        self.found: ApprovalSettings | None = None
        self.until = 0.0
        self.lock = asyncio.Lock()


_cache = _Cache()


def remember(found: ApprovalSettings | None, ttl_sec: float = TTL_SEC) -> None:
    """Keep *found* as this worker's settings for *ttl_sec*; ``None`` (not read) keeps the last."""
    if found is not None:
        _cache.found = found
    _cache.until = time.monotonic() + ttl_sec


def forget() -> None:
    """Read again at the next use (tests; a worker that wants the newest)."""
    _cache.found = None
    _cache.until = 0.0


def last() -> ApprovalSettings:
    """What this worker read last; the defaults (Bypass off) before its first read."""
    return _cache.found or ApprovalSettings()


async def current(bind: AsyncEngine | None = None) -> ApprovalSettings:
    """The settings, read again when this worker's copy is older than 30 seconds.

    With *bind* (the gateway's database), a read that finds Bypass turned off since this worker's last read
    ends the running Bypass grants; the rollup job's :func:`housekeeping` ends any that a worker which never
    saw it on missed. Either way no call is covered once this worker has read the switch off.
    """
    if time.monotonic() < _cache.until:
        return last()
    async with _cache.lock:
        if time.monotonic() < _cache.until:
            return last()
        was_allowed = last().bypass_allowed
        found = await read()
        remember(found)
    if bind is not None and found is not None and was_allowed and not found.bypass_allowed:
        await grants.end_bypass(bind, by="kill_switch")
    return last()


async def housekeeping(bind: AsyncEngine) -> dict[str, int]:
    """At each run of the rollup job: the settings again, the kill switch, and Bypass evidence past its time.

    Nothing is deleted when the settings could not be read (the retention is the backend's word). Postgres
    only, as the rollup job.
    """
    if bind.dialect.name != "postgresql":
        return {}
    forget()
    found = await read()
    remember(found)
    ended = 0
    if found is not None and not found.bypass_allowed:
        ended = await grants.end_bypass(bind, by="kill_switch")
    purged = await grants.purge_bypass(bind, found.bypass_retention_days) if found is not None else 0
    return {"bypass_ended": ended, "bypass_purged": purged}
