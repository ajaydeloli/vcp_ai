"""Flat / tight base detector on synthetic bars (STRATEGY_SPECIFICATION 13; synthetic data)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from vcp_scanner.patterns.registry import load_runtime
from vcp_scanner.patterns.strategy_base import DailyBars, StrategyContext

ROOT = Path(__file__).resolve().parents[2]
RT = load_runtime(ROOT / "config", "flat_base")
DET = RT.detector
assert DET is not None


def _days(n: int) -> list[date]:
    out, d = [], date(2025, 6, 2)  # a Monday
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


Series = tuple[list[float], list[float], list[float], list[float | None]]


def _base(n_base: int = 34, swing: float = 3.0, low_dip: float | None = None,
          vol: float = 1000.0) -> Series:  # fmt: skip
    """150 rising bars (close 60 -> 119.6), a peak bar (close 120, high 121), then ``n_base``
    sideways bars with closes 115 +/- ``swing`` (high/low = close +/- 1)."""
    close = [60 + 0.4 * i for i in range(150)] + [120.0]
    high = [c * 1.005 for c in close[:150]] + [121.0]
    low = [c * 0.995 for c in close[:150]] + [119.0]
    for k in range(n_base):
        c = 115.0 + swing * (1 if k % 2 else -1) * (0.5 + (k % 3) / 4)
        close.append(c)
        high.append(c + 1)
        low.append(c - 1)
    if low_dip is not None:
        low[160] = low_dip
    return high, low, close, [vol] * len(close)


def _ctx(high: list[float], low: list[float], close: list[float], volume: list[float | None],
         stage2: bool = True, breakouts: dict | None = None) -> StrategyContext:  # fmt: skip
    dates = _days(len(close))
    return StrategyContext("SYN", dates[-1], DailyBars(dates, high, low, close, volume), True,
                           stage2, breakouts or {}, 1.5)  # fmt: skip


def test_flat_base_found_with_pivot_stop_and_grade() -> None:
    h, lo, c, v = _base()
    c[-1] = 118.5
    r = DET.detect(_ctx(h, lo, c, v))
    s = r.setup
    assert s is not None, r
    assert s.classification == "FLAT_BASE" and s.grade == 2
    assert s.base_high == 121.0 and s.pivot_price == pytest.approx(121.0 * 1.001)
    assert s.base_duration_days == 35 and s.base_end is None
    assert s.base_depth_pct == pytest.approx((121 - min(lo[150:])) / 121 * 100)
    assert s.stop_reference_price == min(lo[-10:])
    assert s.status == "PIVOT_READY"  # 118.5 is 2.2 % below the pivot
    assert s.prior_advance_pct is not None and s.prior_advance_pct > 20
    assert "TIGHT_FLAT_BASE" in s.unmet_rules  # constant volume: no dry-up
    assert set(s.measures) == {"depth", "weekly_tightness", "right_side", "length",
                               "prior_advance"}  # fmt: skip


def test_tight_flat_base_with_dryup() -> None:
    h, lo, c, v = _base(swing=1.0)
    v = [1000.0] * (len(c) - 10) + [500.0] * 10
    s = DET.detect(_ctx(h, lo, c, v)).setup
    assert s is not None and s.classification == "TIGHT_FLAT_BASE" and s.grade == 3


def test_no_stage2_caps_at_grade_1() -> None:
    h, lo, c, v = _base()
    s = DET.detect(_ctx(h, lo, c, v, stage2=False)).setup
    assert s is not None and s.grade == 1 and s.classification == "FLAT_BASE_LIKE"
    assert "trend_template_and_stage2" in s.unmet_rules["FLAT_BASE"]


def test_too_deep_is_no_base() -> None:
    h, lo, c, v = _base(low_dip=90.0)  # (121 - 90) / 121 = 25.6 %
    r = DET.detect(_ctx(h, lo, c, v))
    assert r.setup is None and r.no_setup_reason == "TOO_DEEP"


def test_leaving_the_range_without_volume_ends_the_base() -> None:
    h, lo, c, v = _base()
    c[-1], h[-1] = 126.0, 126.5  # > pivot x 1.03, ordinary volume
    r = DET.detect(_ctx(h, lo, c, v))
    assert r.setup is None and r.no_setup_reason == "MOVED_ABOVE_BASE"
    c[-1], h[-1] = 123.0, 123.5  # above the pivot by < 3 %: still a base, not a breakout
    s = DET.detect(_ctx(h, lo, c, v)).setup
    assert s is not None and s.breakout is None and s.status == "FORMING"


def test_breakout_on_volume_freezes_the_base() -> None:
    h, lo, c, v = _base()
    c[-1], h[-1], v[-1] = 126.0, 126.5, 3000.0
    s = DET.detect(_ctx(h, lo, c, v)).setup
    assert s is not None and s.status == "BREAKOUT" and s.breakout is not None
    dates = _days(len(c))
    assert s.base_end == dates[-1] and s.breakout.volume_ratio == pytest.approx(3.0)
    assert s.base_duration_days == 34  # measured up to the day before the breakout
    # The next day closes back below the pivot: FAILED (the recorded breakout is reused).
    h2, lo2, c2, v2 = h + [121.0], lo + [118.0], c + [119.0], v + [1000.0]
    s2 = DET.detect(_ctx(h2, lo2, c2, v2, breakouts={s.base_start: s.breakout})).setup
    assert s2 is not None and s2.status == "FAILED" and s2.breakout == s.breakout


def test_no_prior_advance_and_short_history() -> None:
    h, lo, c, v = _base()
    flat = [120.0] * 150
    r = DET.detect(_ctx(flat + h[150:], [118.0] * 150 + lo[150:], flat + c[150:], v))
    assert r.setup is None and r.no_setup_reason == "NO_PRIOR_ADVANCE"
    r = DET.detect(_ctx(h[-60:], lo[-60:], c[-60:], v[-60:]))
    assert r.no_setup_reason == "INSUFFICIENT_HISTORY"


def test_stale_data_without_an_as_of_bar() -> None:
    h, lo, c, v = _base()
    ctx = _ctx(h, lo, c, v)
    r = DET.detect(replace(ctx, as_of_date=ctx.as_of_date + timedelta(days=1)))
    assert r.setup is None and r.data_state == "STALE_DATA"


def test_lookback_covers_the_longest_window() -> None:
    assert DET.lookback_bars() >= 65 + 120
