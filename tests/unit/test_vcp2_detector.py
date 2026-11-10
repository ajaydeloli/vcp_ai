"""VCP detector v2 on synthetic bars (claude/vcp_detector_v2_spec_draft.md, sections 3 and 6)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.patterns.vcp2.config import (
    BaseStartConfig,
    ContractionConfig,
    VCP2Config,
    load_vcp2_config,
    vcp2_config_hash,
)
from vcp_scanner.patterns.vcp2.detector import (
    BROKEN_OUT,
    NO_RUNUP,
    Bars,
    base_start,
    detect,
    swing_highs,
)

CFG = VCP2Config()
SPREAD = 0.002  # high = close x 1.002, low = close x 0.998


def path(start: float, legs: list[tuple[int, float]]) -> list[float]:
    """Closes moving in straight lines: from ``start``, ``bars`` sessions to each target."""
    closes = [start]
    for bars, target in legs:
        a = closes[-1]
        closes += [a + (target - a) * k / bars for k in range(1, bars + 1)]
    return closes


def bars_of(closes: list[float], volume: float = 1000.0) -> Bars:
    d0 = date(2020, 1, 1)
    return Bars(
        dates=[d0 + timedelta(days=i) for i in range(len(closes))],
        high=[c * (1 + SPREAD) for c in closes],
        low=[c * (1 - SPREAD) for c in closes],
        close=closes,
        volume=[volume] * len(closes),
    )


RUNUP = [(100, 500.0)]  # 300 -> 500 over 100 sessions: a +67% advance into the base


def staircase() -> list[tuple[int, float]]:
    """T1 is one falling leg 500 -> 455, bounce 480, -> 440, bounce 465, -> 420 (16%)."""
    return [(10, 455.0), (8, 480.0), (10, 440.0), (8, 465.0), (10, 420.0)]


# ---- config ---------------------------------------------------------------------------------


def test_defaults_are_the_owners_values() -> None:
    bs, ct = CFG.base_start, CFG.contraction
    assert (bs.window_bars, bs.left_edge_share, bs.extend_bars, bs.max_window_bars) == (
        130, 0.25, 30, 325)  # fmt: skip
    assert (ct.level_tolerance_pct, ct.break_on, ct.shakeout_tolerance_pct) == (1.5, "close", 0)


def test_the_repo_file_loads_and_equals_the_defaults() -> None:
    root = Path(__file__).resolve().parents[2]
    assert load_vcp2_config(root / "config") == CFG


def test_invalid_settings_are_refused(tmp_path: Path) -> None:
    for body in ("vcp2:\n  base_start:\n    window_bars: 130\n    max_window_bars: 100\n",
                 "vcp2:\n  base_start:\n    left_edge_share: 1.5\n",
                 "vcp2:\n  contraction:\n    level_tolerance_pct: -1\n",
                 "vcp2:\n  candidate:\n    min_depth_pct: 60\n",
                 "vcp2:\n  unknown_section: {}\n"):  # fmt: skip
        (tmp_path / "vcp2.yaml").write_text(body)
        with pytest.raises(ConfigError):
            load_vcp2_config(tmp_path)


def test_a_changed_setting_is_a_new_configuration() -> None:
    other = VCP2Config(contraction=ContractionConfig(level_tolerance_pct=2.0))
    assert vcp2_config_hash(other) != vcp2_config_hash(CFG)
    assert vcp2_config_hash(VCP2Config()) == vcp2_config_hash(CFG)


# ---- base start (3.1) -----------------------------------------------------------------------


def _edge_case(older: float, n_tail: int = 120) -> list[float]:
    """Peak A (``older``) 140 sessions back, outside the 130 window; peak B (205) 125 back, in
    the window's oldest quarter; then a slow decline to the scan day."""
    head = path(100.0, [(100, 150.0), (60, older)])  # A at index 160
    mid = path(head[-1], [(8, 180.0), (7, 205.0)])[1:]  # B 15 sessions after A
    tail = path(mid[-1], [(n_tail, 175.0)])[1:]
    return head + mid + tail


def test_base_start_stays_put_when_the_high_is_inside_the_window() -> None:
    closes = path(300.0, [*RUNUP, (40, 460.0)])
    b = bars_of(closes)
    s, w, ext = base_start(b.high, swing_highs(b.high, 5, 5), CFG)
    assert (b.dates[s], w, ext) == (b.dates[100], 130, 0)


def test_base_start_extends_once_for_an_older_higher_high() -> None:
    b = bars_of(_edge_case(older=210.0))
    s, w, ext = base_start(b.high, swing_highs(b.high, 5, 5), CFG)
    assert b.close[s] == pytest.approx(210.0) and (w, ext) == (160, 1)


def test_an_older_but_lower_high_is_not_taken() -> None:
    b = bars_of(_edge_case(older=200.0))
    s, w, ext = base_start(b.high, swing_highs(b.high, 5, 5), CFG)
    assert b.close[s] == pytest.approx(205.0) and ext == 0


def test_base_start_extends_repeatedly_while_the_high_stays_at_the_left_edge() -> None:
    a1 = path(100.0, [(100, 150.0), (40, 220.0)])  # A1, 185 sessions back
    rest = path(a1[-1], [(20, 190.0), (25, 210.0), (8, 180.0), (7, 205.0), (125, 175.0)])[1:]
    b = bars_of(a1 + rest)
    s, w, ext = base_start(b.high, swing_highs(b.high, 5, 5), CFG)
    assert b.close[s] == pytest.approx(220.0) and (w, ext) == (190, 2)


def test_the_cap_stops_the_extension() -> None:
    cfg = VCP2Config(base_start=BaseStartConfig(max_window_bars=150))
    b = bars_of(_edge_case(older=210.0))
    s, w, ext = base_start(b.high, swing_highs(b.high, 5, 5), cfg)
    assert b.close[s] == pytest.approx(210.0) and (w, ext) == (150, 1)
    cfg = VCP2Config(base_start=BaseStartConfig(max_window_bars=130))  # no room at all
    s, w, ext = base_start(b.high, swing_highs(b.high, 5, 5), cfg)
    assert b.close[s] == pytest.approx(205.0) and (w, ext) == (130, 0)


def test_non_default_window_settings_are_used() -> None:
    cfg = VCP2Config(base_start=BaseStartConfig(window_bars=200, max_window_bars=325))
    b = bars_of(_edge_case(older=210.0))
    s, w, ext = base_start(b.high, swing_highs(b.high, 5, 5), cfg)
    assert b.close[s] == pytest.approx(210.0) and (w, ext) == (200, 0)  # A already inside


def test_a_close_above_the_base_high_means_no_current_base() -> None:
    closes = path(300.0, [*RUNUP, (20, 450.0), (20, 505.0), (3, 506.0)])
    assert detect(bars_of(closes), CFG).reason == BROKEN_OUT


def test_no_run_up_means_no_base() -> None:
    closes = path(480.0, [(100, 470.0), (20, 500.0), (30, 470.0)])
    assert detect(bars_of(closes), CFG).reason == NO_RUNUP


def test_an_unconfirmed_new_high_is_not_a_base_start() -> None:
    # the newest bars make a high only 3 sessions ago: not a swing high yet; its close is above
    # the last confirmed one, so the stock has broken out
    closes = path(300.0, [*RUNUP, (20, 460.0), (17, 510.0), (3, 505.0)])
    assert detect(bars_of(closes), CFG).reason == BROKEN_OUT


# ---- contractions (3.2) ---------------------------------------------------------------------


def test_a_staircase_decline_is_one_contraction() -> None:
    closes = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (12, 450.0), (6, 465.0)])
    c = detect(bars_of(closes), CFG).candidate
    assert c is not None and c.contraction_count == 2
    t1, t2 = c.contractions
    assert t1.peak_price == pytest.approx(500 * (1 + SPREAD))
    assert t1.low_price == pytest.approx(420 * (1 - SPREAD))
    assert t1.depth_pct == pytest.approx(16.34, abs=0.02) and t1.lower_highs == 2
    assert t2.peak_price == pytest.approx(485 * (1 + SPREAD))
    assert t2.low_price == pytest.approx(450 * (1 - SPREAD)) and t2.depth_pct < t1.depth_pct
    assert c.pivot == pytest.approx(485 * (1 + SPREAD)) and c.tightening_share == 1.0


def test_the_leg_ends_only_on_a_close_beyond_the_tolerance() -> None:
    # last lower high 465 x 1.002 = 465.9; with t = 1.5 % a close must exceed ~472.9
    base = [*RUNUP, *staircase()]
    not_yet = detect(bars_of(path(300.0, [*base, (8, 470.0)])), CFG).candidate
    ended = detect(bars_of(path(300.0, [*base, (8, 476.0)])), CFG).candidate
    assert not_yet is not None and not not_yet.final_ended
    assert ended is not None and ended.final_ended


def test_an_equal_low_within_the_tolerance_is_not_an_undercut() -> None:
    closes = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (12, 415.0), (8, 440.0)])
    c = detect(bars_of(closes), CFG).candidate  # 415 is 1.2 % under 420: an equal low
    assert c is not None and c.contraction_count == 2 and c.undercuts == 0


def test_an_undercut_merges_everything_back_into_t1() -> None:
    closes = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (12, 410.0), (8, 440.0)])
    c = detect(bars_of(closes), CFG).candidate  # 410 is 2.4 % under 420: a real undercut
    assert c is not None and c.undercuts == 1 and c.contraction_count == 1
    (t1,) = c.contractions
    assert t1.peak_price == pytest.approx(500 * (1 + SPREAD))
    assert t1.low_price == pytest.approx(410 * (1 - SPREAD)) and t1.lower_highs >= 3
    assert c.deepest_undercut_pct == pytest.approx(2.38, abs=0.05)


def test_a_wider_tolerance_turns_the_same_undercut_into_an_equal_low() -> None:
    cfg = VCP2Config(contraction=ContractionConfig(level_tolerance_pct=3.0))
    closes = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (12, 410.0), (8, 440.0)])
    c = detect(bars_of(closes), cfg).candidate
    assert c is not None and c.undercuts == 0 and c.contraction_count == 2


def test_the_shakeout_setting_accepts_a_quick_recovery_only() -> None:
    quick = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (12, 410.0), (2, 440.0), (8, 445.0)])
    slow = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (12, 410.0), (20, 425.0)])
    on = VCP2Config(contraction=ContractionConfig(shakeout_tolerance_pct=3.0))
    c = detect(bars_of(quick), on).candidate
    assert c is not None and c.shakeouts == 1 and c.undercuts == 0 and c.contraction_count == 2
    c = detect(bars_of(slow), on).candidate  # back above 420 only after more than 5 sessions
    assert c is not None and c.shakeouts == 0 and c.undercuts == 1
    c = detect(bars_of(quick), CFG).candidate  # off by default: the reset applies
    assert c is not None and c.undercuts == 1 and c.shakeouts == 0


def test_a_shallow_wiggle_is_not_a_contraction() -> None:
    closes = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (6, 480.0), (6, 484.0),
                          (12, 450.0), (6, 465.0)])  # fmt: skip
    c = detect(bars_of(closes), CFG).candidate  # the 1 % dip from 485 is noise
    assert c is not None and c.contraction_count == 2


def test_the_candidate_minimum_is_reported_not_enforced() -> None:
    closes = path(300.0, [*RUNUP, (10, 470.0), (8, 490.0)])  # one shallow leg so far
    c = detect(bars_of(closes), CFG).candidate
    assert c is not None and not c.is_candidate and "contractions" in c.unmet


def test_measurements_are_filled() -> None:
    closes = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (12, 450.0), (6, 465.0)])
    c = detect(bars_of(closes), CFG).candidate
    assert c is not None
    assert c.prior_advance_pct == pytest.approx(500 * 1.002 / (300 * 0.998) * 100 - 100, abs=0.5)
    assert c.base_depth_pct == pytest.approx(16.34, abs=0.02) and c.window_extensions == 0
    assert c.right_side_range_pct > 0 and c.days_since_final_low == 6
    assert c.final_volume_ratio == pytest.approx(1.0) and c.last5_volume_ratio == pytest.approx(1.0)
    assert c.distance_to_pivot_pct < 0 and 0 <= (c.close_in_base_pct or 0) <= 100


def test_a_later_bar_never_changes_an_earlier_day() -> None:
    closes = path(300.0, [*RUNUP, *staircase(), (12, 485.0), (12, 450.0), (20, 470.0)])
    full = bars_of(closes)
    for k in range(150, len(closes), 7):
        day = Bars(full.dates[:k], full.high[:k], full.low[:k], full.close[:k], full.volume[:k])
        assert detect(day, CFG) == detect(day, CFG)
        # appending later bars and cutting them off again gives the same answer
        longer = Bars(full.dates[: k + 5], full.high[: k + 5], full.low[: k + 5],
                      full.close[: k + 5], full.volume[: k + 5])  # fmt: skip
        again = Bars(longer.dates[:k], longer.high[:k], longer.low[:k], longer.close[:k],
                     longer.volume[:k])  # fmt: skip
        assert detect(again, CFG) == detect(day, CFG)
