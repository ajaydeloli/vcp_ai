"""Three Weeks Tight detector on synthetic bars (STRATEGY_SPECIFICATION 14; synthetic data)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from vcp_scanner.patterns.registry import load_runtime
from vcp_scanner.patterns.strategy_base import DailyBars, StrategyContext

ROOT = Path(__file__).resolve().parents[2]
RT = load_runtime(ROOT / "config", "three_weeks_tight")
DET = RT.detector
assert DET is not None
START = date(2025, 6, 2)  # a Monday


def _bars(week_closes: list[float], extra_days: list[float], vol: float = 1000.0,
          spread: float = 0.8) -> DailyBars:  # fmt: skip
    """50 rising weeks (about 3 % a week, 26 -> ~118: never tight), then one week per
    ``week_closes`` entry (5 days, all
    closing at that value), then ``extra_days`` closes in the following week (partial when
    fewer than 5). High / low = close +/- ``spread``."""
    closes = [26.4 * 1.006**i for i in range(250)]
    for wc in week_closes:
        closes += [wc] * 5
    closes += extra_days
    dates, d = [], START
    while len(dates) < len(closes):
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return DailyBars(dates, [c + spread for c in closes], [c - spread for c in closes], closes,
                     [vol] * len(closes))  # fmt: skip


def _ctx(b: DailyBars, stage2: bool = True, breakouts: dict | None = None) -> StrategyContext:
    return StrategyContext("SYN", b.dates[-1], b, True, stage2, breakouts or {}, 1.5)


def test_three_weeks_tight_found_mid_week() -> None:
    b = _bars([120.0, 121.0, 120.5], [120.8, 121.2, 121.0])  # as-of: Wednesday
    s = DET.detect(_ctx(b)).setup
    assert s is not None
    assert s.classification == "THREE_WEEKS_TIGHT" and s.grade == 2  # no dry-up: not grade 3
    assert s.details["weekly_closes"] == [120.0, 121.0, 120.5]
    assert s.details["max_close_change_pct"] == pytest.approx(1 / 120 * 100)
    assert s.details["age_weeks"] == 0
    assert s.pivot_price == pytest.approx(121.8 * 1.001) and s.stop_reference_price == 119.2
    assert s.base_duration_days == 15 and s.status == "PIVOT_READY"
    assert s.base_start == b.dates[250]


def test_unfinished_week_is_left_out_but_a_friday_counts() -> None:
    # Weeks: tight 120 / 121 / 120.5, then a loose week (128) completing on Friday.
    friday = _bars([120.0, 121.0, 120.5], [124.0, 126.0, 127.0, 127.5, 128.0])
    r = DET.detect(_ctx(friday))
    # The Friday week counts: the latest 3 weeks are not tight, the window one week back is.
    assert r.setup is not None and r.setup.details["age_weeks"] == 1
    wed = _bars([120.0, 121.0, 120.5], [124.0, 126.0, 127.0])
    s = DET.detect(_ctx(wed)).setup
    assert s is not None and s.details["age_weeks"] == 0


def test_setup_expires_after_two_weeks() -> None:
    weeks = [120.0, 121.0, 120.5, 116.0, 123.0, 117.0]  # three loose weeks after the pattern
    r = DET.detect(_ctx(_bars(weeks, [120.0])))
    assert r.setup is None and r.no_setup_reason == "NOT_TIGHT"


def test_close_below_the_pattern_low_breaks_it() -> None:
    r = DET.detect(_ctx(_bars([120.0, 121.0, 120.5], [118.0])))  # low of the pattern 119.2
    assert r.setup is None and r.no_setup_reason == "BELOW_PATTERN"


def test_breakout_on_volume() -> None:
    b = _bars([120.0, 121.0, 120.5], [120.8, 124.0])
    vol = list(b.volume)
    vol[-1] = 2500.0
    b = DailyBars(b.dates, b.high, b.low, b.close, vol)
    s = DET.detect(_ctx(b)).setup
    assert s is not None and s.status == "BREAKOUT" and s.base_end == b.dates[-1]


def test_grade_3_needs_dryup_and_stage2_caps_grade() -> None:
    b = _bars([120.0, 120.6, 120.3], [120.4])
    vol = list(b.volume)
    for k in range(250, 265):
        vol[k] = 500.0
    b3 = DailyBars(b.dates, b.high, b.low, b.close, vol)
    s = DET.detect(_ctx(b3)).setup
    assert s is not None and s.classification == "THREE_WEEKS_TIGHT_A" and s.grade == 3
    s1 = DET.detect(_ctx(b3, stage2=False)).setup
    assert s1 is not None and s1.grade == 1


def test_loose_closes_and_short_history() -> None:
    r = DET.detect(_ctx(_bars([120.0, 124.0, 119.0], [120.0])))
    assert r.setup is None and r.no_setup_reason == "NOT_TIGHT"
    b = _bars([120.0, 121.0, 120.5], [121.0])
    short = DailyBars(b.dates[-12:], b.high[-12:], b.low[-12:], b.close[-12:], b.volume[-12:])
    assert DET.detect(_ctx(short)).no_setup_reason == "INSUFFICIENT_HISTORY"


def test_tight_weeks_counts_the_run() -> None:
    s = DET.detect(_ctx(_bars([120.0, 120.5, 121.0, 120.5], [121.0]))).setup
    assert s is not None and s.details["tight_weeks"] == 4


def test_tight_weeks_uses_the_grade_2_rule() -> None:
    # Changes of 1.2 % and 1.4 %: grade 2 (<= 1.5), not grade 3 (<= 1.0): still 3 tight weeks.
    s = DET.detect(_ctx(_bars([120.0, 121.44, 119.74], [120.0]))).setup
    assert s is not None and s.grade == 2 and s.details["tight_weeks"] == 3
