"""The usage settings the gateway follows: the usage time zone, the retention periods, the minimum group size.

The backend keeps them in its system settings (``system_setting_value``, ``{"value": ...}``); the gateway
reads them from the backend's database at each run of the rollup job (``usage_rollup.py``), so a change
applies at the next run without a restart. Every worker reads them, and keeps the last read for the
tokens-per-day cap's calendar day (``ratelimit.py``), which needs the time zone on each request.

A key with no row has its default; the time zone's is this install's (``FADEN_GATEWAY_USAGE_TIME_ZONE``,
UTC unless set). A value out of its range is taken as the nearest end, and a time zone that is not an IANA
name as the default: a bad value never purges more than the ranges allow (M09).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import asyncpg

from faden_gateway.settings import settings

log = logging.getLogger(__name__)

PREFIX = "usage."

#: The retention settings: (default, smallest, largest).
RETENTION: dict[str, tuple[int, int, int]] = {
    "request_log_retention_days": (90, 7, 365),
    "person_retention_days": (90, 31, 400),
    "hourly_retention_days": (90, 31, 180),
    "daily_retention_months": (25, 13, 60),
}
#: The minimum group size k (M04): (default, smallest, largest), as the backend allows it.
MIN_GROUP_SIZE = (5, 5, 50)


@dataclass(frozen=True, slots=True)
class UsageSettings:
    """The usage settings as the gateway applies them."""

    time_zone: str = "UTC"
    request_log_retention_days: int = 90
    person_retention_days: int = 90
    hourly_retention_days: int = 90
    daily_retention_months: int = 25
    #: Beyond the per-person retention, a rollup cell of fewer than k people folds (``usage_rollup._fold``).
    min_group_size: int = 5

    @property
    def zone(self) -> tzinfo:
        """The usage time zone (``time_zone`` is always a name ``ZoneInfo`` knows)."""
        return ZoneInfo(self.time_zone)


def _zone_name(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        try:
            ZoneInfo(value.strip())
            return value.strip()
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("usage.time_zone %r is not a time zone name; %s applies", value, default)
    return default


def _within(name: str, value: Any) -> int:
    return _bounded(value, *RETENTION[name])


def _bounded(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        number = int(value)
    except ValueError:
        return default
    return min(max(number, low), high)


def defaults() -> UsageSettings:
    """The settings with no row in the backend: the install's time zone, the retention defaults."""
    return UsageSettings(time_zone=_zone_name(settings.usage_time_zone, "UTC"))


def from_values(values: dict[str, Any]) -> UsageSettings:
    """The settings from the backend's values, by key without the ``usage.`` prefix."""
    base = defaults()
    found: dict[str, Any] = {
        name: _within(name, values[name]) for name in RETENTION if values.get(name) is not None
    }
    if "time_zone" in values:
        found["time_zone"] = _zone_name(values["time_zone"], base.time_zone)
    if values.get("min_group_size") is not None:
        found["min_group_size"] = _bounded(values["min_group_size"], *MIN_GROUP_SIZE)
    return replace(base, **found)


_current: UsageSettings | None = None


def current() -> UsageSettings:
    """The settings this worker read last; the defaults before the first read."""
    return _current if _current is not None else defaults()


def remember(found: UsageSettings | None) -> None:
    """Keep *found* as this worker's settings; ``None`` (not read) keeps the last."""
    global _current  # noqa: PLW0603
    if found is not None:
        _current = found


def _value(raw: Any) -> Any:
    """A ``value_json`` as the backend stores it: ``{"value": ...}``, maybe still a JSON string."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw.get("value") if isinstance(raw, dict) else raw


def backend_dsn() -> str | None:
    """The backend database, on the gateway's own server and account; ``None`` without one."""
    if not settings.backend_db_base:
        return None
    return (
        f"postgresql://{settings.db_user}:{settings.db_pass}"
        f"@{settings.db_host}:{settings.db_port}/{settings.backend_db_base}"
    )


async def read(dsn: str | None = None) -> UsageSettings | None:
    """The usage settings from the backend's database, or ``None`` when it cannot be read.

    Without a backend database configured, the defaults: the gateway runs on its own.
    """
    dsn = dsn or backend_dsn()
    if dsn is None:
        return defaults()
    try:
        conn = await asyncpg.connect(dsn, timeout=5)
        try:
            rows = await conn.fetch(
                "SELECT key, value_json FROM system_setting_value WHERE key LIKE 'usage.%'", timeout=5,
            )
        finally:
            await conn.close()
    except Exception as exc:  # the backend's database is away: keep what was read last
        log.warning("The usage settings could not be read from the backend's database: %s", exc)
        return None
    return from_values({row["key"][len(PREFIX):]: _value(row["value_json"]) for row in rows})
