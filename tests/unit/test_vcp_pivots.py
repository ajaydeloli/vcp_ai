"""Pivot candidates and selection (VCP_SPECIFICATION 19-22, 19A)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vcp_scanner.config.models import VCPThresholdsConfig
from vcp_scanner.domain.enums import PivotSource
from vcp_scanner.patterns.vcp.measurements import PriceSeries, measure
from vcp_scanner.patterns.vcp.pivots import _visits, select_pivots
from vcp_scanner.patterns.vcp.segmentation import segment_base

D0 = date(2026, 1, 1)
CLASSIC = [(60, 100), (20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (5, 135), (5, 140)]


def _cfg(max_rs: float = 5.0) -> VCPThresholdsConfig:
    return VCPThresholdsConfig(
        pivot={"max_right_side_range_pct": max_rs},  # type: ignore[arg-type]
        swing={"left_bars": 2, "right_bars": 2, "min_depth_pct": 2.0, "min_duration_days": 3},  # type: ignore[arg-type]
        base={"max_duration_days": 80},  # type: ignore[arg-type]
        prior_advance={"lookback_days": 20, "min_return_pct": 20.0},  # type: ignore[arg-type]
    )


def _series(legs: list[tuple[int, float]], extra: list[float] | None = None) -> PriceSeries:
    closes = [100.0]
    for bars, target in legs:
        a = closes[-1]
        closes += [a + (target - a) * (i + 1) / bars for i in range(bars)]
    closes += extra or []
    high = [c * 1.002 for c in closes]
    low = [c * 0.998 for c in closes]
    d = [D0 + timedelta(days=i) for i in range(len(closes))]
    return PriceSeries(d, high, low, closes, [1000.0] * len(closes), [2.0] * len(closes))


def _select(s: PriceSeries, max_rs: float = 5.0):  # type: ignore[no-untyped-def]
    cfg = _cfg(max_rs)
    res = segment_base(s.dates, s.high, s.low, as_of=s.dates[-1], config=cfg)
    assert res.base is not None
    m = measure(s, res.base, cfg)
    return select_pivots(s, res, m, cfg), res.base, m


def test_classic_base_pivots_on_the_final_contraction_peak() -> None:
    s = _series(CLASSIC)
    sel, base, m = _select(s, max_rs=3.0)  # last 10 bars range 4.4 %: not tight at 3 %
    assert m.tight_pivot_pass is False
    by_source = {c.source: c for c in sel.candidates}
    assert by_source[PivotSource.BASE_HIGH].pivot_date == s.dates[80]
    assert by_source[PivotSource.SWING_HIGH].pivot_date == s.dates[114]
    assert by_source[PivotSource.RIGHT_SIDE_HIGH].pivot_date == s.dates[115]
    p = sel.primary
    assert p is not None and p.source is PivotSource.SWING_HIGH and p.pivot_date == s.dates[114]
    assert p.distance_to_close_pct == pytest.approx((142 * 1.002 - 140) / 140 * 100)
    assert p in sel.candidates
    # At the default 5 % the same right side counts as tight: rule 1 picks its top.
    sel5, _, m5 = _select(s)
    assert m5.tight_pivot_pass is True
    assert sel5.primary is not None and sel5.primary.source is PivotSource.RIGHT_SIDE_HIGH
    assert sel5.primary.pivot_date == s.dates[115]


def test_tight_right_side_pivots_on_the_top_of_the_tight_area() -> None:
    flat = [140.0, 140.2, 139.9, 140.1, 140.0, 139.8, 140.1, 140.0, 139.9, 140.0, 140.05]
    s = _series(CLASSIC, flat)
    sel, base, m = _select(s)
    assert m.tight_pivot_pass is True
    p = sel.primary
    assert p is not None and p.source is PivotSource.RIGHT_SIDE_HIGH
    assert p.pivot_price == pytest.approx(140.2 * 1.002)
    assert p.pivot_price >= s.close[-1]
    assert p.right_side_tightness_pct is not None and p.right_side_tightness_pct < 1.0


def test_repeated_resistance_touches_and_rejections() -> None:
    # The stock fails twice near 150: at the base start and again at 149.
    legs = [(60, 100), (20, 150), (10, 125), (10, 149), (8, 133), (8, 141), (5, 136), (5, 139)]
    s = _series(legs)
    sel, base, _ = _select(s)
    rr = [c for c in sel.candidates if c.source is PivotSource.REPEATED_RESISTANCE]
    assert len(rr) == 1
    assert rr[0].pivot_price == pytest.approx(150 * 1.002)
    assert rr[0].pivot_date == s.dates[100]  # dated at the cluster's latest swing
    bh = next(c for c in sel.candidates if c.source is PivotSource.BASE_HIGH)
    assert bh.touches == 2 and bh.rejection_count == 2


def test_no_contraction_pivots_on_the_base_high() -> None:
    # A new high followed by a 1 % dip: base, but no contraction yet.
    s = _series([(60, 100), (20, 150), (3, 148.6), (2, 149.5)])
    sel, base, _ = _select(s)
    assert base.contractions == ()
    assert sel.primary is not None and sel.primary.source is PivotSource.BASE_HIGH


def test_duplicates_keep_the_structural_label() -> None:
    # One contraction only: the final peak IS the base start.
    s = _series([(60, 100), (20, 150), (10, 128), (4, 133)])
    sel, base, _ = _select(s)
    assert len(base.contractions) == 1
    dates = [c.pivot_date for c in sel.candidates]
    assert len(dates) == len(set((c.pivot_date, c.pivot_price) for c in sel.candidates))
    assert sel.primary is not None and sel.primary.source is PivotSource.BASE_HIGH


def _mini(highs: list[float], closes: list[float]) -> PriceSeries:
    n = len(highs)
    d = [D0 + timedelta(days=i) for i in range(n)]
    return PriceSeries(d, highs, [h * 0.9 for h in highs], closes, [1.0] * n, [1.0] * n)


@pytest.mark.parametrize(
    ("highs", "closes", "expected"),
    [
        # Two finished visits below 100 -> two touches, two rejections.
        ([99.5, 90, 99, 90, 91], [95, 88, 95, 88, 89], (2, 2)),
        # A visit that closes above the level is not a rejection.
        ([99.5, 90, 101, 90, 91], [95, 88, 100.5, 88, 89], (2, 1)),
        # A visit still running at the as-of bar is not a rejection.
        ([99.5, 90, 91, 99.2], [95, 88, 89, 97], (2, 1)),
        # Consecutive bars in the zone are one visit.
        ([99.5, 99.6, 99.1, 90], [95, 96, 95, 88], (1, 1)),
    ],
)
def test_touches_and_rejections(
    highs: list[float], closes: list[float], expected: tuple[int, int]
) -> None:
    s = _mini(highs, closes)
    assert _visits(s, 0, len(highs), 100.0, 0.015) == expected


def test_no_base_and_mismatch() -> None:
    s = _series(CLASSIC)
    cfg = _cfg()
    res = segment_base(s.dates, s.high, s.low, as_of=s.dates[-1], config=cfg)
    m = measure(s, res.base, cfg)  # type: ignore[arg-type]
    shifted = PriceSeries(
        *(col[1:] for col in (s.dates, s.high, s.low, s.close, s.volume, s.atr_pct_14))
    )
    with pytest.raises(ValueError, match="does not match"):
        select_pivots(shifted, res, m, cfg)
    from dataclasses import replace

    empty = replace(res, base=None)
    assert select_pivots(s, empty, m, cfg).primary is None
    assert select_pivots(s, res, m, cfg) == select_pivots(s, res, m, cfg)  # deterministic
