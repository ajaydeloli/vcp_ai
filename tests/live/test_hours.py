"""NSE market hours: 09:15 to 15:30 IST, Monday to Friday, not on holidays."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from vcp_scanner.data.providers._time import IST
from vcp_scanner.live.hours import MarketCalendar


def at(d: str, hh: int, mm: int, ss: int = 0) -> datetime:
    y, mo, da = (int(x) for x in d.split("-"))
    return datetime(y, mo, da, hh, mm, ss, tzinfo=IST)


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (at("2026-10-06", 9, 14, 59), False),  # Tuesday, one second before the open
        (at("2026-10-06", 9, 15), True),
        (at("2026-10-06", 15, 29, 59), True),
        (at("2026-10-06", 15, 30), False),
        (at("2026-10-10", 11, 0), False),  # Saturday
        (at("2026-10-11", 11, 0), False),  # Sunday
    ],
)
def test_open_hours_on_weekdays(when: datetime, expected: bool) -> None:
    assert MarketCalendar().is_open(when) is expected


def test_a_holiday_is_closed_once_the_list_is_loaded() -> None:
    cal = MarketCalendar(lambda: {date(2026, 10, 6)}, background=False)
    assert cal.is_open(at("2026-10-06", 11, 0))  # list not loaded yet: weekday rule
    assert cal.holidays_known is False
    cal.refresh(at("2026-10-06", 8, 0))
    assert cal.holidays_known is True
    assert not cal.is_open(at("2026-10-06", 11, 0))
    assert cal.last_closed_session(at("2026-10-06", 16, 0)) == date(2026, 10, 5)


def test_a_failing_holiday_list_keeps_the_weekday_rule_and_retries_in_an_hour() -> None:
    calls: list[int] = []

    def boom() -> set[date]:
        calls.append(1)
        raise RuntimeError("NSE is down")

    cal = MarketCalendar(boom, background=False)
    cal.refresh(at("2026-10-06", 8, 0))
    assert cal.holidays_known is False and cal.is_open(at("2026-10-06", 11, 0))
    cal.refresh(at("2026-10-06", 8, 30))  # too soon
    assert len(calls) == 1
    cal.refresh(at("2026-10-06", 9, 1))
    assert len(calls) == 2


def test_pre_open_window() -> None:
    cal = MarketCalendar()
    assert cal.is_pre_open(at("2026-10-06", 9, 0))
    assert cal.is_pre_open(at("2026-10-06", 9, 14))
    assert not cal.is_pre_open(at("2026-10-06", 9, 15))
    assert not cal.is_pre_open(at("2026-10-10", 9, 5))  # Saturday


def test_last_closed_session() -> None:
    cal = MarketCalendar()
    assert cal.last_closed_session(at("2026-10-06", 11, 0)) == date(2026, 10, 5)  # mid-session
    assert cal.last_closed_session(at("2026-10-06", 15, 30)) == date(2026, 10, 6)
    assert cal.last_closed_session(at("2026-10-10", 12, 0)) == date(2026, 10, 9)  # Saturday
    assert cal.last_closed_session(at("2026-10-12", 8, 0)) == date(2026, 10, 9)  # Monday early
