"""The time windows dashboard figures are counted over, in the viewer's time zone.

"Today" is the viewer's today: from their local midnight until now. Its
comparison is yesterday over the same *wall-clock* span -- 00:00 to the same
local time -- not the 24 hours before, because a day with a daylight-saving
change has 23 or 25 of them. A month is compared with the previous month up to
the same day of the month, clamped to that month's last day (31 March is
compared with 28 February).

Windows are computed on local wall-clock times and converted to UTC instants,
which is what ``created_at`` holds and what its index serves. A wall-clock time
that does not exist (the hour skipped in spring) resolves forward; one that
happens twice (the hour repeated in autumn) takes its first occurrence. Both
fall out of ``fold=0``: zoneinfo reads a skipped time with the offset in force
before the change, which lands the same distance past it.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Rolling windows the traffic widget offers, besides the calendar "today".
ROLLING = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


class InvalidTimeZoneError(ValueError):
    """The client sent a time zone name zoneinfo does not know."""


@dataclass(frozen=True)
class Window:
    """A half-open span ``[start, end)`` of UTC instants."""

    start: datetime
    end: datetime


def parse_tz(name: str | None) -> ZoneInfo:
    """The zone for an IANA name; UTC when none is given."""
    if not name:
        return ZoneInfo("UTC")
    # zoneinfo reads names as paths under the tz database; refuse anything
    # that is not shaped like a zone name before it gets there.
    if len(name) > 64 or ".." in name or name.startswith(("/", "\\")):
        raise InvalidTimeZoneError(name)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimeZoneError(name) from exc


def to_utc(local: datetime, tz: ZoneInfo) -> datetime:
    """A naive local wall-clock time in *tz*, as a UTC instant (``fold=0``)."""
    return local.replace(tzinfo=tz, fold=0).astimezone(UTC)


def _local_now(now: datetime, tz: ZoneInfo) -> datetime:
    return now.astimezone(tz).replace(tzinfo=None)


def today(now: datetime, tz: ZoneInfo) -> tuple[Window, Window]:
    """Local midnight -> now, and yesterday's midnight -> the same local time."""
    local = _local_now(now, tz)
    midnight = datetime.combine(local.date(), time.min)
    current = Window(to_utc(midnight, tz), now)
    previous = Window(
        to_utc(midnight - timedelta(days=1), tz),
        to_utc(local - timedelta(days=1), tz),
    )
    return current, previous


def _clamped(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def month(now: datetime, tz: ZoneInfo) -> tuple[Window, Window]:
    """The 1st of the local month -> now, and the previous month up to the same day."""
    local = _local_now(now, tz)
    first = datetime.combine(local.date().replace(day=1), time.min)
    prev_year, prev_month = (local.year - 1, 12) if local.month == 1 else (local.year, local.month - 1)
    prev_first = datetime(prev_year, prev_month, 1)
    prev_same = datetime.combine(_clamped(prev_year, prev_month, local.day), local.time())
    current = Window(to_utc(first, tz), now)
    previous = Window(to_utc(prev_first, tz), to_utc(prev_same, tz))
    return current, previous


def rolling(now: datetime, span: timedelta) -> tuple[Window, Window]:
    """The last *span* up to now, and the equal span just before it."""
    current = Window(now - span, now)
    return current, Window(now - 2 * span, now - span)


def window(name: str, now: datetime, tz: ZoneInfo) -> tuple[Window, Window]:
    """The current and previous window for a traffic window name."""
    if name == "today":
        return today(now, tz)
    if name == "month":
        return month(now, tz)
    span = ROLLING.get(name)
    if span is None:
        raise ValueError(f"unknown window {name!r}")
    return rolling(now, span)


def day_range(now: datetime, tz: ZoneInfo, days: int) -> tuple[Window, list[date]]:
    """The last *days* local calendar days, today included, and their dates in order."""
    local_today = _local_now(now, tz).date()
    dates = [local_today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    start = to_utc(datetime.combine(dates[0], time.min), tz)
    return Window(start, now), dates
