"""Double bottom detector on synthetic bars (STRATEGY_SPECIFICATION 15.2; synthetic data)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from tests.unit.test_cup_handle import Bars, _days
from vcp_scanner.patterns.registry import load_runtime

ROOT = Path(__file__).resolve().parents[2]
RT = load_runtime(ROOT / "config", "double_bottom")
DET = RT.detector
assert DET is not None

L = 150


def _line(b: Bars, start: float, stop: float, bars: int) -> None:
    """``bars`` closes moving linearly from just after ``start`` to ``stop``."""
    for k in range(bars):
        b.add(start + (stop - start) * (k + 1) / bars)


def worked_example(flat_prior: bool = False, second_low: float = 237.0,
                   last_vol: float = 1000.0) -> Bars:  # fmt: skip
    """Section 15.2 worked example: left high 300; first low 240 (20 bars after L); middle peak
    276; second low 237; a rally to a close of 270 with the last 10 bars within 262 .. 274."""
    b = Bars()
    for i in range(L):  # prior advance about 37 % over the 120 bars into the left high
        b.add(290.0 if flat_prior else 200 + 0.62 * i)
    b.add(297.0, high=300.0, low=295.0)  # L
    _line(b, 297.0, 245.0, 19)
    b.add(242.0, high=244.0, low=240.0)  # B1, L + 20
    _line(b, 242.0, 270.0, 14)
    b.add(274.0, high=276.0, low=272.0)  # M, L + 35
    _line(b, 274.0, 243.0, 14)
    b.add(240.0, high=242.0, low=second_low)  # B2, L + 50
    _line(b, 240.0, 263.0, 10)
    for c in (265.0, 268.0, 266.0, 269.0, 271.0, 268.0, 270.0, 272.0, 269.0, 270.0):
        b.add(c, high=min(c + 2, 274.0), low=max(c - 3, 262.0), vol=last_vol)
    b.h[-6], b.lo[-10] = 274.0, 262.0  # the right side spans exactly 262 .. 274
    return b


def test_worked_example_is_double_bottom_pivot_ready() -> None:
    b = worked_example()
    r = DET.detect(b.ctx())
    s = r.setup
    assert s is not None, r
    assert s.classification == "DOUBLE_BOTTOM" and s.grade == 2
    assert s.pivot_price == pytest.approx(276.276)
    assert s.stop_reference_price == 262.0
    assert s.status == "PIVOT_READY" and s.pivot_distance_pct == pytest.approx(2.32, abs=0.01)
    assert s.base_depth_pct == pytest.approx(21.0)
    det = s.details
    assert det["undercut_pct"] == pytest.approx(1.25)
    assert det["middle_bounce_pct"] == pytest.approx(15.0)
    assert det["middle_peak_position"] == pytest.approx(39 / 63)
    assert det["right_side_range_pct"] == pytest.approx(12 / 274 * 100)
    dates = _days(len(b.c))
    assert s.base_start == dates[L] and s.pivot_date == dates[L + 35]
    assert det["first_low_date"] == dates[L + 20].isoformat()
    assert det["second_low_date"] == dates[L + 50].isoformat()
    assert s.unmet_rules["DOUBLE_BOTTOM_A"] == ["max_dryup_ratio"]  # constant volume
    assert set(s.measures) == {"right_side", "depth", "undercut", "middle_peak",
                               "prior_advance"}  # fmt: skip


def test_dryup_makes_grade_3() -> None:
    s = DET.detect(worked_example(last_vol=600.0).ctx()).setup
    assert s is not None and s.classification == "DOUBLE_BOTTOM_A" and s.grade == 3
    assert s.dryup_volume_ratio == pytest.approx(0.6)


def test_equal_lows_are_not_a_true_undercut() -> None:
    s = DET.detect(worked_example(second_low=240.0, last_vol=600.0).ctx()).setup
    assert s is not None and s.grade == 2 and s.details["undercut_pct"] == 0.0
    assert s.details["second_low_date"] == _days(L + 71)[L + 50].isoformat()  # the later low
    assert s.unmet_rules["DOUBLE_BOTTOM_A"] == ["require_undercut"]


def test_no_stage2_caps_at_grade_1() -> None:
    s = DET.detect(worked_example().ctx(stage2=False)).setup
    assert s is not None and s.grade == 1 and s.classification == "DOUBLE_BOTTOM_LIKE"
    assert s.unmet_rules["DOUBLE_BOTTOM"] == ["trend_template_and_stage2"]


def test_insufficient_history() -> None:
    b = worked_example()
    short = Bars()
    short.h, short.lo, short.c, short.v = b.h[-30:], b.lo[-30:], b.c[-30:], b.v[-30:]
    assert DET.detect(short.ctx()).no_setup_reason == "INSUFFICIENT_HISTORY"


def test_no_w_after_a_straight_decline() -> None:
    b = Bars()
    for i in range(L):
        b.add(200 + 0.62 * i)
    b.add(297.0, high=300.0, low=295.0)
    _line(b, 297.0, 230.0, 60)
    assert DET.detect(b.ctx()).no_setup_reason == "NO_W"


def test_no_prior_advance() -> None:
    r = DET.detect(worked_example(flat_prior=True).ctx())
    assert r.setup is None and r.no_setup_reason == "NO_PRIOR_ADVANCE"


def test_too_deep() -> None:
    r = DET.detect(worked_example(second_low=140.0).ctx())  # 53 % deep, 42 % undercut
    assert r.setup is None and r.no_setup_reason == "TOO_DEEP"


def test_moved_above_base_without_breakout_volume() -> None:
    b = worked_example()
    b.add(290.0, high=291.0, low=284.0)  # > 276.276 x 1.03, ordinary volume
    assert DET.detect(b.ctx()).no_setup_reason == "MOVED_ABOVE_BASE"


def test_breakout_freezes_the_base_then_fails() -> None:
    b = worked_example()
    b.add(280.0, high=282.0, low=270.0, vol=3000.0)
    s = DET.detect(b.ctx()).setup
    assert s is not None and s.status == "BREAKOUT" and s.breakout is not None
    assert s.base_end == _days(len(b.c))[-1] and s.stop_reference_price == 262.0
    assert s.base_duration_days == 71  # measured up to the day before the breakout
    b.add(268.0, high=271.0, low=266.0)
    s2 = DET.detect(b.ctx(breakouts={s.base_start: s.breakout})).setup
    assert s2 is not None and s2.status == "FAILED" and s2.breakout == s.breakout
    assert s2.stop_reference_price == 262.0  # still the frozen right side


def test_old_breakout_is_no_longer_a_setup() -> None:
    b = worked_example()
    b.add(280.0, high=282.0, low=270.0, vol=3000.0)  # breakout bar k
    bo = DET.detect(b.ctx()).setup
    assert bo is not None and bo.breakout is not None
    for _ in range(10):  # 10 sessions after the breakout: still a setup
        b.add(283.0, high=284.0, low=281.0)
    s = DET.detect(b.ctx(breakouts={bo.base_start: bo.breakout})).setup
    assert s is not None and s.status == "BREAKOUT"
    b.add(283.0, high=284.0, low=281.0)  # the 11th: gone
    r = DET.detect(b.ctx(breakouts={bo.base_start: bo.breakout}))
    assert r.setup is None and r.no_setup_reason == "OLD_BREAKOUT"
    assert DET.detect(b.ctx()).no_setup_reason == "OLD_BREAKOUT"  # also when found, not recorded


def test_stale_data_without_an_as_of_bar() -> None:
    ctx = worked_example().ctx()
    r = DET.detect(replace(ctx, as_of_date=ctx.as_of_date + timedelta(days=1)))
    assert r.setup is None and r.data_state == "STALE_DATA"


def test_lookback_covers_the_longest_base() -> None:
    assert DET.lookback_bars() >= 325 + 120
