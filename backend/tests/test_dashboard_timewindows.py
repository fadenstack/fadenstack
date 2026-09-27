"""Dashboard windows follow the viewer's calendar, daylight-saving days included.

"Today" is compared with yesterday over the same wall-clock span, not with the
24 hours before: on 2026-10-25 Berlin's day has 25 hours and on 2026-03-08 New
York's has 23, and a 24-hour offset would compare 12:00 with 11:00 or 13:00.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from faden_backend.services.dashboard import timewindows as tw

BERLIN = tw.parse_tz("Europe/Berlin")
NEW_YORK = tw.parse_tz("America/New_York")


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_today_on_an_ordinary_day() -> None:
    now = _utc(2026, 9, 26, 8, 15)  # 10:15 in Berlin (CEST, +02:00)

    current, previous = tw.today(now, BERLIN)

    assert current == tw.Window(_utc(2026, 9, 25, 22, 0), now)
    assert previous == tw.Window(_utc(2026, 9, 24, 22, 0), _utc(2026, 9, 25, 8, 15))


def test_today_on_berlins_25_hour_day_compares_the_same_wall_clock_span() -> None:
    # 2026-10-25: clocks go back 03:00 CEST -> 02:00 CET. 12:00 CET is 11:00Z.
    now = _utc(2026, 10, 25, 11, 0)

    current, previous = tw.today(now, BERLIN)

    assert current.start == _utc(2026, 10, 24, 22, 0)  # midnight CEST
    assert current.end - current.start == timedelta(hours=13)  # 00:00 -> 12:00 with the extra hour
    # Yesterday 00:00 -> 12:00, both CEST.
    assert previous == tw.Window(_utc(2026, 10, 23, 22, 0), _utc(2026, 10, 24, 10, 0))


def test_today_on_new_yorks_23_hour_day() -> None:
    # 2026-03-08: clocks go forward 02:00 EST -> 03:00 EDT. 12:00 EDT is 16:00Z.
    now = _utc(2026, 3, 8, 16, 0)

    current, previous = tw.today(now, NEW_YORK)

    assert current.start == _utc(2026, 3, 8, 5, 0)  # midnight EST
    assert current.end - current.start == timedelta(hours=11)
    assert previous == tw.Window(_utc(2026, 3, 7, 5, 0), _utc(2026, 3, 7, 17, 0))


def test_a_wall_clock_time_that_did_not_exist_resolves_forward() -> None:
    # 02:30 on 2026-03-09 (EDT); yesterday at 02:30 fell in the skipped hour.
    now = _utc(2026, 3, 9, 6, 30)

    _, previous = tw.today(now, NEW_YORK)

    assert previous.end == _utc(2026, 3, 8, 7, 30)  # = 03:30 EDT, one hour past the gap


def test_a_repeated_wall_clock_time_takes_its_first_occurrence() -> None:
    assert tw.to_utc(datetime(2026, 10, 25, 2, 30), BERLIN) == _utc(2026, 10, 25, 0, 30)  # 02:30 CEST


def test_a_month_is_compared_with_the_previous_month_up_to_the_same_day() -> None:
    now = _utc(2026, 9, 26, 8, 0)

    current, previous = tw.month(now, BERLIN)

    assert current.start == _utc(2026, 8, 31, 22, 0)  # 1 Sep, 00:00 CEST
    assert previous == tw.Window(_utc(2026, 7, 31, 22, 0), _utc(2026, 8, 26, 8, 0))


def test_the_31st_is_compared_with_the_last_day_of_a_shorter_month() -> None:
    now = _utc(2026, 3, 31, 8, 0)  # 10:00 CEST

    _, previous = tw.month(now, BERLIN)

    assert previous.start == _utc(2026, 1, 31, 23, 0)  # 1 Feb, 00:00 CET
    assert previous.end == _utc(2026, 2, 28, 9, 0)  # 28 Feb, 10:00 CET


def test_january_is_compared_with_december_of_the_year_before() -> None:
    _, previous = tw.month(_utc(2026, 1, 15, 12, 0), tw.parse_tz("UTC"))

    assert previous == tw.Window(_utc(2025, 12, 1), _utc(2025, 12, 15, 12, 0))


def test_rolling_windows_compare_the_equal_span_before() -> None:
    now = _utc(2026, 9, 26, 12, 0)

    current, previous = tw.window("7d", now, BERLIN)

    assert current == tw.Window(_utc(2026, 9, 19, 12, 0), now)
    assert previous == tw.Window(_utc(2026, 9, 12, 12, 0), _utc(2026, 9, 19, 12, 0))


def test_a_day_range_ends_today_and_starts_at_local_midnight() -> None:
    now = _utc(2026, 9, 26, 8, 15)

    span, dates = tw.day_range(now, BERLIN, 7)

    assert dates == [date(2026, 9, 20) + timedelta(days=i) for i in range(7)]
    assert span == tw.Window(_utc(2026, 9, 19, 22, 0), now)


@pytest.mark.parametrize("name", ["Mars/Olympus_Mons", "../../etc/passwd", "/etc/localtime", "x" * 65])
def test_an_unknown_time_zone_is_refused(name: str) -> None:
    with pytest.raises(tw.InvalidTimeZoneError):
        tw.parse_tz(name)


def test_no_time_zone_means_utc() -> None:
    assert tw.parse_tz(None).key == "UTC"
