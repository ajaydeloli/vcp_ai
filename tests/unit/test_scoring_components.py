"""Component scores (SCORING_SPECIFICATION 2-6, 9-10; Phase 7 step 1)."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from vcp_scanner.config.models import ScoringConfig, TrendScoreComponents
from vcp_scanner.scoring.components import (
    linear,
    measure_distribution,
    measure_up_down_volume,
    rs_score,
    trend_score,
    vcp_score,
    volume_score,
)

CFG = ScoringConfig().components


def test_linear_maps_clips_and_works_both_ways() -> None:
    assert linear(5, 0, 10) == 50
    assert linear(-3, 0, 10) == 0 and linear(30, 0, 10) == 100
    assert linear(20, 40, 15) == pytest.approx(80)  # smaller is better
    assert linear(50, 40, 15) == 0 and linear(10, 40, 15) == 100


def test_trend_score_known_answer_and_null_sub_component() -> None:
    t = CFG.trend
    s = trend_score(90.0, 100.0, 110.0, 100.0, 96.1538461538, t.weights, t.bounds)
    by = {x.name: x for x in s.subs}
    assert by["high_proximity"].raw == pytest.approx(10.0)
    assert by["high_proximity"].normalized == pytest.approx(60.0)
    assert by["sma200_slope"].raw == pytest.approx(4.0)
    assert by["ma_stack_margin"].normalized == pytest.approx(50.0)
    assert by["high_proximity"].points == pytest.approx(24.0)  # 60 x 40 / 100
    assert by["high_proximity"].max_points == 40.0
    assert s.score == pytest.approx(54.0)  # (24 + 15 + 15) / 100
    # A missing input is NULL: dropped and the rest renormalized, never scored as 0.
    m = trend_score(90.0, 100.0, 110.0, 100.0, None, t.weights, t.bounds)
    slope = next(x for x in m.subs if x.name == "sma200_slope")
    assert slope.normalized is None and slope.points == 0.0
    assert m.score == pytest.approx((24 + 15) / 70 * 100)
    assert trend_score(None, None, None, None, None, t.weights, t.bounds).score is None
    assert trend_score(float("nan"), 100.0, None, None, None, t.weights, t.bounds).score is None


def test_vcp_score_known_answer_and_no_volume_input() -> None:
    v = CFG.vcp
    s = vcp_score(3, 0.85, 9.0, 0.75, 5.0, 27.5, v.weights, v.bounds)
    assert all(x.normalized == pytest.approx(50.0) for x in s.subs)
    assert s.score == pytest.approx(50.0)
    best = vcp_score(6, 0.4, 1.0, 0.2, 1.0, 5.0, v.weights, v.bounds)
    assert best.score == pytest.approx(100.0)  # clipped at the best end
    # Volume is scored once, in the Volume Score (section 4): no volume input exists here.
    assert not any("volume" in p for p in inspect.signature(vcp_score).parameters)


def test_volume_and_rs_scores() -> None:
    vol = CFG.volume
    s = volume_score(0.70, 1.15, 2.0, vol.weights, vol.bounds)
    assert s.score == pytest.approx(50.0)
    assert volume_score(None, None, None, vol.weights, vol.bounds).score is None
    assert rs_score(70, 70).score == 0.0
    assert rs_score(99, 70).score == 100.0
    assert rs_score(84.5, 70).score == pytest.approx(50.0)
    assert rs_score(None, 70).score is None
    assert rs_score(99, 99).score == 100.0  # min_rs_rank 99 is valid config


def test_up_down_volume_and_distribution_from_bars() -> None:
    # 51 bars: alternate up (volume 300) and down (volume 100) days.
    close = [100.0 + (i % 2) for i in range(51)]
    volume: list[float | None] = [300.0 if i % 2 else 100.0 for i in range(51)]
    assert measure_up_down_volume(close, volume) == pytest.approx(3.0)
    assert measure_up_down_volume(close[:50], volume[:50]) is None  # too few bars
    volume[40] = 0.0
    assert measure_up_down_volume(close, volume) is None  # zero volume is suspect
    rising = [float(i) for i in range(1, 52)]
    assert measure_up_down_volume(rising, [100.0] * 51) == 10.0  # no down volume: capped

    n = 80
    close2 = [100.0] * n
    vol2: list[float | None] = [1000.0] * n
    for i in (60, 70):  # two down days on 2x volume
        close2[i] = 99.0
        vol2[i] = 2000.0
    close2[65] = 98.0  # a down day on normal volume does not count
    assert measure_distribution(close2, vol2, 1.5) == 2.0
    vol2[i] = 1400.0  # 1.4x: below the 1.5x multiple
    assert measure_distribution(close2, vol2, 1.5) == 1.0
    assert measure_distribution(close2[:70], vol2[:70], 1.5) is None  # needs 25 + 50 bars
    vol2[10] = None
    assert measure_distribution(close2, vol2, 1.5) is None


def test_sub_component_names_are_validated() -> None:
    with pytest.raises(ValidationError, match="must be exactly"):
        TrendScoreComponents(
            weights={"high_proximty": 40.0, "sma200_slope": 30.0, "ma_stack_margin": 30.0},
            bounds={k: {"worst": 0.0, "best": 1.0}  # type: ignore[misc]
                    for k in ("high_proximty", "sma200_slope", "ma_stack_margin")},
        )  # fmt: skip


def test_scores_are_deterministic() -> None:
    v = CFG.vcp
    a = vcp_score(3, 0.8, 7.0, 0.6, 4.0, 22.0, v.weights, v.bounds)
    b = vcp_score(3, 0.8, 7.0, 0.6, 4.0, 22.0, v.weights, v.bounds)
    assert a == b
