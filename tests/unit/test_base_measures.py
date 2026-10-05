"""Shared base measurements on hand-built series (Multi-Strategy step 3; synthetic data)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from vcp_scanner.config.models import VCPThresholdsConfig
from vcp_scanner.features.base_measures import (
    base_extremes,
    max_weekly_close_change_pct,
    prior_advance,
    volume_dryup_ratio,
    weekly_bars,
    weekly_close_range_pct,
)
from vcp_scanner.patterns.vcp.segmentation import segment_base

MON = date(2026, 9, 7)  # a Monday


def _days(n: int, start: date = MON) -> list[date]:
    """``n`` weekdays from ``start`` (synthetic calendar, no holidays)."""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# -- weekly bars -------------------------------------------------------------------------------


def test_weekly_bars_aggregate_iso_weeks() -> None:
    dates = _days(8)  # Mon..Fri, Mon..Wed
    high = [10, 12, 11, 13, 12, 14, 15, 13]
    low = [9, 10, 9.5, 11, 10, 12, 13, 12]
    close = [9.5, 11, 10, 12, 11.5, 13, 14, 12.5]
    vol = [100, 200, 300, 400, 500, 10, 20, 30]
    opens = [9.2, 9.6, 11, 10, 12, 11.6, 13, 14]
    bars = weekly_bars(dates, high, low, close, vol, opens)
    assert len(bars) == 2
    w1, w2 = bars
    assert (w1.first_day, w1.week_end, w1.sessions) == (dates[0], dates[4], 5)
    assert (w1.open, w1.high, w1.low, w1.close, w1.volume) == (9.2, 13, 9, 11.5, 1500.0)
    assert not w1.partial
    # The as-of week stops at the as-of bar (Wednesday) and is partial.
    assert (w2.week_end, w2.sessions, w2.high, w2.low, w2.close, w2.volume) == (
        dates[7], 3, 15, 12, 12.5, 60.0)  # fmt: skip
    assert w2.partial


def test_friday_as_of_week_is_complete_and_holiday_weeks_are_short() -> None:
    dates = [MON, MON + timedelta(days=1), MON + timedelta(days=7), MON + timedelta(days=11)]
    bars = weekly_bars(dates, [1, 2, 3, 4], [1, 1, 1, 1], [1, 2, 3, 4])
    assert [b.sessions for b in bars] == [2, 2] and not bars[-1].partial
    assert bars[0].volume is None and bars[0].open is None  # not given


def test_missing_volume_makes_the_week_volume_missing() -> None:
    bars = weekly_bars(_days(5), [1] * 5, [1] * 5, [1] * 5, [1, 2, None, 4, 5])
    assert bars[0].volume is None


def test_weekly_bars_refuse_ragged_columns() -> None:
    with pytest.raises(ValueError):
        weekly_bars(_days(3), [1, 2], [1, 2, 3], [1, 2, 3])


# -- weekly close tightness --------------------------------------------------------------------


def test_weekly_close_range_and_largest_change() -> None:
    closes = [90.0, 100.0, 101.0, 100.5]
    assert weekly_close_range_pct(closes, 3) == pytest.approx(1.0)  # (101 - 100) / 100
    assert weekly_close_range_pct(closes, 4) == pytest.approx((101 - 90) / 90 * 100)
    assert max_weekly_close_change_pct(closes, 2) == pytest.approx(1.0)  # 100 -> 101
    assert max_weekly_close_change_pct(closes, 3) == pytest.approx(100 / 90 * 100 - 100)


@pytest.mark.parametrize(
    ("closes", "weeks"),
    [([100.0, 101.0], 3), ([100.0, math.nan, 101.0], 3), ([100.0, 0.0, 101.0], 3),
     ([100.0], 0)],
)  # fmt: skip
def test_weekly_tightness_is_none_when_it_cannot_be_measured(
    closes: list[float], weeks: int
) -> None:
    assert weekly_close_range_pct(closes, weeks) is None
    assert max_weekly_close_change_pct(closes, weeks) is None


def test_largest_change_needs_one_more_close_than_weeks() -> None:
    assert max_weekly_close_change_pct([100.0, 101.0, 100.5], 3) is None
    assert max_weekly_close_change_pct([99.0, 100.0, 101.0, 100.5], 3) == pytest.approx(
        1 / 99 * 100)  # fmt: skip


# -- prior advance -----------------------------------------------------------------------------


def test_prior_advance_from_the_lowest_low_of_the_window() -> None:
    high = [11, 10, 12, 15, 20, 24]
    low = [9, 8, 10, 8, 14, 18]  # two lows of 8: the earliest wins
    pa = prior_advance(high, low, end=5, lookback=6)
    assert pa is not None and pa.window_complete
    assert pa.pct == pytest.approx(200.0) and pa.low_index == 1
    short = prior_advance(high, low, end=5, lookback=10)
    assert short is not None and not short.window_complete and short.pct == pa.pct
    window = prior_advance(high, low, end=5, lookback=2)
    assert window is not None and window.low_index == 4
    assert window.pct == pytest.approx((24 / 14 - 1) * 100)


@pytest.mark.parametrize("end", [-1, 6])
def test_prior_advance_outside_the_series_is_none(end: int) -> None:
    assert prior_advance([1.0] * 6, [1.0] * 6, end=end, lookback=3) is None


def test_prior_advance_with_a_bad_low_is_none() -> None:
    assert prior_advance([2.0, 3.0], [0.0, 1.0], end=1, lookback=2) is None
    assert prior_advance([2.0, 3.0], [math.nan, 1.0], end=1, lookback=2) is None


def test_prior_advance_equals_vcp_segmentation() -> None:
    """The shared helper and VCP's base start give the same number (VCP keeps its own code)."""
    n = 160
    dates = _days(n)
    # Up from 50 to 100 over 100 bars, then a base with two pullbacks.
    close = [50 + 0.5 * i for i in range(100)]
    close += [100 - 0.8 * i for i in range(15)] + [88 + 0.6 * i for i in range(15)]
    close += [97 - 0.4 * i for i in range(10)] + [93 + 0.3 * i for i in range(20)]
    high = [c * 1.01 for c in close]
    low = [c * 0.99 for c in close]
    cfg = VCPThresholdsConfig()
    seg = segment_base(dates, high, low, as_of=dates[-1], config=cfg)
    assert seg.base is not None and seg.base.prior_advance_return_pct is not None
    b = seg.base.base_start_index
    pa = prior_advance(high, low, b, cfg.prior_advance.lookback_days)
    assert pa is not None and pa.pct == seg.base.prior_advance_return_pct
    assert dates[pa.low_index] == seg.base.prior_advance_low_date
    ext = base_extremes(dates, high, low, b)
    assert ext is not None and ext.low == seg.base.base_low and ext.high == seg.base.base_high
    assert ext.sessions == seg.base.base_duration_days


# -- base extremes -----------------------------------------------------------------------------


def test_base_extremes_depth_and_length() -> None:
    dates = _days(7)  # Mon..Fri, Mon, Tue
    high = [10, 12, 12, 11, 10.5, 11.5, 11]
    low = [9, 10, 9, 9.5, 9, 10, 10.5]
    ext = base_extremes(dates, high, low, start=1)
    assert ext is not None
    assert (ext.high, ext.high_index, ext.low, ext.low_index) == (12, 1, 9, 2)  # earliest ties
    assert ext.depth_pct == pytest.approx(25.0)
    assert (ext.sessions, ext.weeks) == (6, 2)
    part = base_extremes(dates, high, low, start=3, end=4)
    assert part is not None and (part.high, part.low, part.sessions, part.weeks) == (
        11, 9, 2, 1)  # fmt: skip


@pytest.mark.parametrize(("start", "end"), [(5, 3), (-1, None), (0, 7)])
def test_base_extremes_outside_the_series_is_none(start: int, end: int | None) -> None:
    assert base_extremes(_days(7), [1.0] * 7, [1.0] * 7, start, end) is None


# -- volume dry-up -----------------------------------------------------------------------------


def test_volume_dryup_ratio() -> None:
    vol: list[float | None] = [100.0] * 50 + [40.0] * 5
    assert volume_dryup_ratio(vol, recent=5) == pytest.approx(0.4)
    assert volume_dryup_ratio(vol, recent=5, base=10, end=54) == pytest.approx(0.4)
    assert volume_dryup_ratio(vol, recent=6) is None  # base window does not fit
    vol[10] = None
    assert volume_dryup_ratio(vol, recent=5) is None  # a missing volume is never a dry-up
    assert volume_dryup_ratio([0.0] * 55, recent=5) is None
