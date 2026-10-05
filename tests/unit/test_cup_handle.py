"""Cup and handle detector on synthetic bars (STRATEGY_SPECIFICATION 15.1; synthetic data)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from vcp_scanner.patterns.registry import load_runtime
from vcp_scanner.patterns.strategy_base import DailyBars, StrategyContext

ROOT = Path(__file__).resolve().parents[2]
RT = load_runtime(ROOT / "config", "cup_handle")
DET = RT.detector
assert DET is not None

L = 150  # index of the left lip in every series below


def _days(n: int) -> list[date]:
    out, d = [], date(2024, 1, 1)  # a Monday
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class Bars:
    """Mutable OHLCV lists (high/low = close +/- a spread unless set)."""

    def __init__(self) -> None:
        self.h: list[float] = []
        self.lo: list[float] = []
        self.c: list[float] = []
        self.v: list[float | None] = []

    def add(self, close: float, spread: float = 1.0, high: float | None = None,
            low: float | None = None, vol: float = 1000.0) -> None:  # fmt: skip
        self.c.append(close)
        self.h.append(close + spread if high is None else high)
        self.lo.append(close - spread if low is None else low)
        self.v.append(vol)

    def ctx(self, stage2: bool = True, breakouts: dict | None = None) -> StrategyContext:
        dates = _days(len(self.c))
        return StrategyContext("SYN", dates[-1],
                               DailyBars(dates, self.h, self.lo, self.c, self.v), True, stage2,
                               breakouts or {}, 1.5)  # fmt: skip


def worked_example(flat_prior: bool = False, v_shape: bool = False,
                   handle_low: float = 186.0) -> Bars:  # fmt: skip
    """Section 15.1 worked example: a rise into a left lip of 200, 6 weeks down to 160, 2 weeks
    at the bottom (160-170), 4 weeks up to a right lip of 196, 8 handle sessions down to 186 on
    0.7 x volume, last close 191.0 (2.7 % below the pivot)."""
    b = Bars()
    for i in range(L):  # prior advance about 40 % over the 120 bars into the lip
        b.add(195.0 if flat_prior else 130 + 0.45 * i)
    b.add(198.0, high=200.0, low=196.0)  # L
    if v_shape:  # down to 175, a 3-bar spike to 160 and back, up again: a V
        for k in range(28):
            b.add(198 - (k + 1) * 23 / 28, spread=2.0)
        b.add(168.0, spread=2.0, high=172.0)
        b.add(162.0, low=160.0, high=166.0)
        b.add(168.0, spread=2.0, high=172.0)
        for k in range(28):
            b.add(175 + k * 18 / 27, spread=2.0)
    else:
        for k in range(30):  # 6 weeks down to the bottom zone
            b.add(198 - (k + 1) * 1.0, spread=2.0)
        for k in range(10):  # 2 weeks between 160 and 170
            b.add(164.0, high=167.0, low=160.0 if k == 4 else 162.0)
        for k in range(19):  # 4 weeks up
            b.add(166 + (k + 1) * 1.45, spread=2.0)
    b.add(194.0, high=196.0, low=192.0)  # R
    handle = [193.0, 192.0, 190.5, 189.0, 188.0, 189.5, 190.0, 191.0]
    for k, c in enumerate(handle):
        b.add(c, high=c + 1.5, low=handle_low if k == 4 else c - 1.0, vol=700.0)
    return b


def test_worked_example_is_cup_handle_a_pivot_ready() -> None:
    b = worked_example()
    r = DET.detect(b.ctx())
    s = r.setup
    assert s is not None, r
    assert s.classification == "CUP_HANDLE_A" and s.grade == 3
    assert s.pivot_price == pytest.approx(196.196)
    assert s.stop_reference_price == 186.0
    assert s.status == "PIVOT_READY" and s.pivot_distance_pct == pytest.approx(2.72, abs=0.01)
    det = s.details
    assert det["cup_depth_pct"] == pytest.approx(20.0)
    assert det["right_lip_gap_pct"] == pytest.approx(2.0)
    assert det["handle_depth_pct"] == pytest.approx(10 / 196 * 100)
    assert det["handle_position"] == pytest.approx(0.65)
    assert det["handle_sessions"] == 9 and det["cup_sessions"] == 60
    assert det["rounded"] is True and det["bottom_sessions"] >= 10
    assert 0.15 <= det["bottom_share"] <= 0.40 and 0.4 <= det["bottom_position"] <= 0.6
    assert det["handle_dryup_ratio"] == pytest.approx((1000 + 8 * 700) / 9 / 1000)
    assert det["sma_close"] < 186.0
    dates = _days(len(b.c))
    assert s.base_start == dates[L] and s.pivot_date == dates[L + 60] and s.base_end is None
    assert det["bottom_date"] == dates[L + 35].isoformat()
    assert set(s.measures) == {"roundness", "handle_depth", "handle_position", "cup_depth",
                               "prior_advance"}  # fmt: skip
    assert s.prior_advance_pct is not None and s.prior_advance_pct >= 30


def test_full_volume_handle_is_grade_2() -> None:
    b = worked_example()
    b.v = [1000.0] * len(b.c)
    s = DET.detect(b.ctx()).setup
    assert s is not None and s.classification == "CUP_HANDLE" and s.grade == 2
    assert s.unmet_rules["CUP_HANDLE_A"] == ["max_handle_dryup_ratio"]


def test_v_cup_is_not_rounded() -> None:
    s = DET.detect(worked_example(v_shape=True).ctx()).setup
    assert s is not None and s.grade == 1 and s.classification == "CUP_HANDLE_LIKE"
    assert s.details["rounded"] is False and s.details["bottom_sessions"] < 5
    assert "rounded" in s.unmet_rules["CUP_HANDLE"]


def test_no_stage2_caps_at_grade_1() -> None:
    s = DET.detect(worked_example().ctx(stage2=False)).setup
    assert s is not None and s.grade == 1 and s.classification == "CUP_HANDLE_LIKE"
    assert s.unmet_rules["CUP_HANDLE"] == ["trend_template_and_stage2"]
    assert s.status == "FORMING"  # grade 1 is never PIVOT_READY


def test_insufficient_history() -> None:
    b = worked_example()
    short = Bars()
    short.h, short.lo, short.c, short.v = b.h[-30:], b.lo[-30:], b.c[-30:], b.v[-30:]
    assert DET.detect(short.ctx()).no_setup_reason == "INSUFFICIENT_HISTORY"


def test_no_handle_when_a_later_high_tops_the_right_lip() -> None:
    b = worked_example()
    b.h[-1], b.c[-1] = 197.0, 195.0  # above the handle start, close below pivot x 1.03
    assert DET.detect(b.ctx()).no_setup_reason == "NO_HANDLE"


def test_moved_above_base_without_breakout_volume() -> None:
    b = worked_example()
    b.h[-1], b.c[-1] = 204.0, 203.0  # > 196.196 x 1.03, ordinary volume
    assert DET.detect(b.ctx()).no_setup_reason == "MOVED_ABOVE_BASE"


def test_no_cup_when_a_high_inside_tops_the_rims() -> None:
    b = worked_example()
    b.h[L + 40] = 210.0  # inside the cup (last bottom bar), above 200 x 1.03
    assert DET.detect(b.ctx()).no_setup_reason == "NO_CUP"


def test_right_lip_well_above_the_left_is_no_cup() -> None:
    """1.1.0: a stock that rallied past its old high and paused is not in a cup."""
    b = worked_example()
    b.h[L + 60] = 204.0  # 2 % above the left lip: still a cup
    s = DET.detect(b.ctx()).setup
    assert s is not None and s.details["right_lip_gap_pct"] == pytest.approx(-2.0)
    b.h[L + 60] = 210.0  # 5 % above: no cup
    assert DET.detect(b.ctx()).no_setup_reason == "NO_CUP"


def test_no_prior_advance() -> None:
    r = DET.detect(worked_example(flat_prior=True).ctx())
    assert r.setup is None and r.no_setup_reason == "NO_PRIOR_ADVANCE"


def test_too_deep_handle() -> None:
    r = DET.detect(worked_example(handle_low=150.0).ctx())  # handle 23 % deep, below mid-cup
    assert r.setup is None and r.no_setup_reason == "TOO_DEEP"


def test_breakout_freezes_the_handle_then_fails() -> None:
    b = worked_example()
    b.add(199.0, high=200.0, low=193.0, vol=3000.0)  # close above the pivot on 3 x volume
    s = DET.detect(b.ctx()).setup
    assert s is not None and s.status == "BREAKOUT" and s.breakout is not None
    dates = _days(len(b.c))
    assert s.base_end == dates[-1] and s.details["handle_sessions"] == 9
    assert s.breakout.volume_ratio > 1.5
    b.add(194.0, high=196.0, low=192.0)  # back below the pivot: FAILED, the event reused
    s2 = DET.detect(b.ctx(breakouts={s.base_start: s.breakout})).setup
    assert s2 is not None and s2.status == "FAILED" and s2.breakout == s.breakout


def test_stale_data_without_an_as_of_bar() -> None:
    ctx = worked_example().ctx()
    r = DET.detect(replace(ctx, as_of_date=ctx.as_of_date + timedelta(days=1)))
    assert r.setup is None and r.data_state == "STALE_DATA"


def test_lookback_is_480_bars() -> None:
    assert DET.lookback_bars() == 480
