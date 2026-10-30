"""What the usage routes share: the gateway database, day ranges in the usage time zone, periods, refusals."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Hashable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, NoReturn
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.services.usage.people import PeopleRefusal
from faden_backend.services.usage.rollups import DayRange

#: The default range: the last 30 days, today included.
DEFAULT_DAYS = 30
#: The longest range of hours (the hourly rollups are kept 31 to 180 days; a month of hours is plenty).
MAX_HOUR_DAYS = 31
#: The longest range of days or months (the daily rollups are kept up to 60 months).
MAX_DAYS = 1830


async def gateway_session(request: Request) -> AsyncGenerator[AsyncSession]:
    """A session on the gateway's database, where the usage rollups are; 503 without one."""
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Gateway database not available",
        )
    async with factory() as session:
        yield session


def problems(*found: tuple[str, str]) -> NoReturn:
    """Refuse with 422 ``{"detail": {"problems": [{"field", "code"}]}}``."""
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"problems": [{"field": field, "code": code} for field, code in found]},
    )


def refuse(refusal: PeopleRefusal) -> NoReturn:
    """A refusal to show named people's usage, as ``{"detail": {"code": ...}}``."""
    raise HTTPException(status_code=refusal.status_code, detail={"code": refusal.code})


def _date(value: str | None, field: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        problems((field, "invalid"))


def day_range(first: str | None, last: str | None, zone: ZoneInfo, *, max_days: int = MAX_DAYS) -> DayRange:
    """``from``/``to`` (``YYYY-MM-DD`` in the usage time zone, both included); default the last 30 days."""
    to = _date(last, "to") or datetime.now(zone).date()
    frm = _date(first, "from") or to - timedelta(days=DEFAULT_DAYS - 1)
    if frm > to:
        problems(("from", "invalid"))
    if (to - frm).days + 1 > max_days:
        problems(("to", "out_of_range"))
    return DayRange(frm, to)


def _whole_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def utc_bounds(days: DayRange, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The range as whole UTC hours: from the first day's local midnight to the midnight after the last.

    The hourly rollups are whole UTC hours; a time zone half an hour off (Asia/Kolkata, +05:30) has its
    midnight in the middle of one. Both ends are floored to the hour, so each UTC hour belongs to the local
    day in which it ends (:func:`day_of_hour`), and the hours of consecutive days follow each other without a
    gap.
    """
    start = datetime.combine(days.first, time(), zone).astimezone(UTC)
    end = datetime.combine(days.last + timedelta(days=1), time(), zone).astimezone(UTC)
    return _whole_hour(start), _whole_hour(end)


def day_of_hour(hour: datetime, zone: ZoneInfo) -> date:
    """The local day a UTC hour counts toward: the day of its last instant (``utc_bounds``)."""
    return (hour + timedelta(hours=1) - timedelta(microseconds=1)).astimezone(zone).date()


def periods(days: DayRange, bucket: str, zone: ZoneInfo) -> list[Hashable]:
    """Every period of the range, in order: dates (day), first days of months (month), UTC hours (hour)."""
    found: list[Hashable] = []
    if bucket == "hour":
        start, end = utc_bounds(days, zone)
        while start < end:
            found.append(start)
            start += timedelta(hours=1)
    elif bucket == "month":
        current = days.first.replace(day=1)
        while current <= days.last:
            found.append(current)
            current = (current + timedelta(days=32)).replace(day=1)
    else:
        found = [days.first + timedelta(days=n) for n in range((days.last - days.first).days + 1)]
    return found


def period_text(period: Any) -> str:
    """A period as the API writes it: ``YYYY-MM-DD``, or an ISO-8601 UTC hour (``...T05:00:00Z``)."""
    if isinstance(period, datetime):
        return period.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return period.isoformat() if isinstance(period, date) else str(period)
