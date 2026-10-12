"""Market Health Score 2: ladders, pillars, labels, caps, the missing-data rule, settings and the
per-day readings (MARKET_HEALTH_SPECIFICATION)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner.market_health.features import (
    NIFTY50,
    NIFTY500,
    SMALLCAP,
    Breakout,
    Series,
    assemble,
)
from vcp_scanner.market_health.scoring import (
    DayFeatures,
    accumulation,
    breadth,
    caps,
    index_trend,
    label_of,
    ladder,
    score_day,
)
from vcp_scanner.market_health.settings import HealthSettings, load_settings

CONFIG = Path(__file__).resolve().parents[2] / "config" / "market_health.yaml"


@pytest.fixture(scope="module")
def s() -> HealthSettings:
    return load_settings(CONFIG)


def healthy(**kw: Any) -> DayFeatures:
    """A day on which every pillar reads well."""
    base = DayFeatures(
        n500_close=110, n500_sma50=105, n500_sma150=100, n500_sma200=95, n500_sma200_prior=94,
        n500_high_252=111, nifty50_above_50=True, smallcap_above_50=True,
        universe=1200, pct_above_50=72, pct_above_150=75, pct_above_200=78, ad_ratio=0.2,
        highs_lows=0.9, above_50_change=20, vqi_gap=5, ad_line_gap=0.0,
        tt_share=22, tt_count=260, tt_count_prior=250, leaders=200, leader_change=45,
        highs_20=13, highs_52=6, sectors=85, leaders_vs_vqi=6,
        dist_nifty=1, dist_vqi=2, acc_nifty=7, up_down_value=1.6, dist_cluster=0,
        up_on_volume=0.8, judged=40, success=55, held=75, failure=15, median_return=5,
        near_pivot=7,
    )  # fmt: skip
    return replace(base, **kw)


def test_ladder_interpolates_and_holds_flat() -> None:
    pts = [(30.0, 0.0), (70.0, 100.0)]
    assert ladder(10, pts) == 0
    assert ladder(50, pts) == 50
    assert ladder(90, pts) == 100
    falling = [(2.0, 100.0), (4.0, 60.0), (5.0, 30.0), (6.0, 0.0)]
    assert ladder(3, falling) == 80
    assert ladder(4.5, falling) == 45


def test_settings_version_ignores_layout_but_not_meaning(s: HealthSettings) -> None:
    again = HealthSettings.model_validate(s.model_dump())
    assert again.settings_version == s.settings_version
    assert s.settings_version.startswith("mhs-2.0.0+")
    other = HealthSettings.model_validate({**s.model_dump(), "min_universe": 301})
    assert other.settings_version != s.settings_version


def test_weights_add_up(s: HealthSettings) -> None:
    assert sum(s.pillars.values()) == 100
    for group in (s.breadth, s.leadership, s.accumulation, s.breakouts):
        rungs = [v for v in group.model_dump().values() if isinstance(v, dict) and "weight" in v]
        assert sum(r["weight"] for r in rungs) == 100, type(group).__name__
    it = s.index_trend
    assert (
        it.above_50
        + it.above_150
        + it.above_200
        + it.stacked
        + it.rising_200
        + it.confirm_nifty50
        + it.confirm_smallcap
        == 100
    )


def test_a_healthy_day_is_strong_and_full(s: HealthSettings) -> None:
    sc = score_day(healthy(), s)
    assert sc.complete and not sc.caps and not sc.missing
    assert sc.raw is not None and sc.raw >= 95
    assert sc.label == sc.raw_label == "Strong"
    assert sc.exposure == "full"


def test_index_trend_points(s: HealthSettings) -> None:
    p = index_trend(healthy(), s)
    assert p.score == 100
    # below the 50-day only, averages stacked one way of two, NIFTY 50 unconfirmed
    p = index_trend(healthy(n500_close=100, n500_sma50=101, n500_sma150=96, n500_sma200=98,
                            nifty50_above_50=False), s)  # fmt: skip
    assert p.score == pytest.approx(0 + 20 + 20 + 10 + 10 + 0 + 5)
    # a missing confirmation is left out and the rest rescaled
    p = index_trend(healthy(smallcap_above_50=None), s)
    assert p.score == 100
    assert index_trend(healthy(n500_sma200=None), s).score is None


def test_breadth_ladders(s: HealthSettings) -> None:
    p = breadth(healthy(pct_above_50=50, pct_above_150=50, pct_above_200=50, ad_ratio=0.0,
                        highs_lows=0.5, above_50_change=0, vqi_gap=0), s)  # fmt: skip
    assert p.score == pytest.approx(50)
    # no new highs and no new lows: neutral
    p = breadth(healthy(highs_lows=None), s)
    assert next(x for x in p.parts if x.id == "highs_lows").points == 50
    assert breadth(healthy(universe=100), s).score is None


def test_distribution_blends_nifty_and_vqi(s: HealthSettings) -> None:
    p = accumulation(healthy(dist_nifty=6, dist_vqi=2), s)
    part = next(x for x in p.parts if x.id == "distribution")
    assert part.reading == pytest.approx(0.6 * 6 + 0.4 * 2)
    assert part.points == pytest.approx(ladder(4.4, s.accumulation.distribution.ladder))


def test_labels(s: HealthSettings) -> None:
    assert [label_of(x, s) for x in (80, 79.9, 65, 50, 49.9, 35, 34.9, 0)] == [
        "Strong", "Constructive", "Constructive", "Mixed", "Weak", "Weak", "Adverse", "Adverse",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("kw", "cap", "label"),
    [
        ({"n500_close": 90, "n500_sma200_prior": 96, "pct_above_50": 25}, "downtrend", "Weak"),
        ({"dist_cluster": 5}, "distribution", "Mixed"),
        ({"held": 35, "judged": 12}, "failures", "Weak"),
        ({"tt_count": 160, "tt_count_prior": 250}, "tt_collapse", "Mixed"),
        ({"ad_line_gap": 0.4}, "divergence", "Constructive"),
    ],
)
def test_each_cap_limits_the_label_not_the_raw_score(
    s: HealthSettings, kw: dict[str, Any], cap: str, label: str
) -> None:
    sc = score_day(healthy(**kw), s)
    assert cap in [h.id for h in sc.caps]
    assert sc.label == label
    assert sc.raw_label in ("Strong", "Constructive")  # the raw score is shown separately


def test_caps_need_their_conditions(s: HealthSettings) -> None:
    # below a rising 200-day average is not a downtrend cap; few judged breakouts are not read
    f = healthy(n500_close=90, pct_above_50=25, held=10, judged=9, dist_cluster=4)
    assert [h.id for h in caps(f, s, complete=True)] == []
    # far from its high, a weak A/D line is no divergence
    assert caps(healthy(n500_close=100, ad_line_gap=0.9), s, complete=True) == []


def test_worst_cap_wins(s: HealthSettings) -> None:
    sc = score_day(healthy(dist_cluster=6, held=20, judged=30), s)
    assert sc.label == "Weak"
    assert sc.exposure == "none"


def test_missing_pillar_rescales_and_caps_at_mixed(s: HealthSettings) -> None:
    sc = score_day(healthy(judged=5), s)
    assert sc.missing == ["breakouts"]
    assert not sc.complete
    assert sc.raw is not None and sc.raw >= 95  # the other four, reweighted
    assert sc.raw_label == "Strong" and sc.label == "Mixed" and sc.exposure == "half"
    empty = score_day(DayFeatures(), s)
    assert empty.raw is None and empty.label is None


def _rows(count: int, **kw: Any) -> list[dict[str, Any]]:
    start = date(2024, 1, 1)
    base = {"n": 1000, "n50": 1000, "a50": 600, "n150": 1000, "a150": 600, "n200": 1000,
            "a200": 600, "adv": 600, "dec": 400, "hi52": 20, "lo52": 5, "hi20": 80,
            "tt_n": 900, "tt_pass": 150, "leaders": 100, "up_tv": 3e9, "down_tv": 2e9,
            "mean_ret": 0.001, "vol": 1e8, "near": 30, "lead_ret": 0.05, "lead_n": 100}  # fmt: skip
    return [{"d": start + timedelta(days=i), **base, **kw} for i in range(count)]


def _index(days: list[date], step: float, vol_every: int = 0) -> Series:
    rows = []
    for i, d in enumerate(days):
        down = vol_every and i % vol_every == 0
        close = 100.0 * (1 + step) ** i * (0.99 if down else 1.0)
        rows.append((d, close, 2e8 if down else 1e8))
    return Series(rows)


def test_assemble_reads_universe_indices_and_breakouts(s: HealthSettings) -> None:
    rows = _rows(300)
    days = [r["d"] for r in rows]
    idx = {NIFTY500: _index(days, 0.001), NIFTY50: _index(days, 0.001),
           SMALLCAP: _index(days, -0.001)}  # fmt: skip
    outs = [Breakout(days[250 + k], held=k % 2 == 0, success=k % 4 == 0, failed=k % 4 == 1,
                     ret5=1.0, retj=2.0) for k in range(25)]  # fmt: skip
    fds = assemble(rows, outs, idx, s)
    f = fds[-1].features
    assert f.universe == 1000 and f.pct_above_50 == 60
    assert f.ad_ratio == pytest.approx(0.2)
    assert f.highs_lows == pytest.approx(0.8)
    assert f.above_50_change == 0
    assert f.tt_share == 15 and f.tt_count == 150 and f.tt_count_prior == 150
    assert f.leader_change == 0
    assert f.highs_20 == 8 and f.highs_52 == 2
    assert f.up_down_value == pytest.approx(1.5)
    assert f.near_pivot == 3
    assert f.nifty50_above_50 is True and f.smallcap_above_50 is False
    assert f.n500_sma200 is not None and f.n500_sma200_prior is not None
    assert f.dist_nifty == 0 and f.acc_nifty == 0 and f.up_on_volume is None
    # breakouts count from the day their result is known, judge_sessions later
    known = [o for k, o in enumerate(outs) if 250 + k + s.breakouts.judge_sessions <= 299
             and 250 + k + s.breakouts.judge_sessions > 299 - s.breakouts.window]  # fmt: skip
    assert f.judged == len(known)
    assert f.median_return == pytest.approx(1.5)
    assert fds[0].features.n500_sma50 is None
    # shown on the cards, not scored
    assert (f.advancers, f.decliners, f.new_highs, f.new_lows) == (600, 400, 20, 5)
    assert f.ad_line == 200 * 300 and f.thrust_ratio == pytest.approx(60)
    assert f.n500_kind is None


def test_nifty_distribution_days_need_higher_volume(s: HealthSettings) -> None:
    rows = _rows(60)
    days = [r["d"] for r in rows]
    fds = assemble(rows, [], {NIFTY500: _index(days, 0.0, vol_every=5)}, s)
    f = fds[-1].features
    # one 1 % down day on doubled volume every 5 sessions; the next day recovers on lower volume
    assert f.dist_nifty == 5 and f.dist_cluster == 2 and f.acc_nifty == 0
    assert f.up_on_volume == 0


def test_small_universe_has_no_breadth(s: HealthSettings) -> None:
    fds = assemble(_rows(30, n=100), [], {}, s)
    f = fds[-1].features
    assert f.pct_above_50 is None and f.tt_share is None
    assert fds[-1].vqi is None
