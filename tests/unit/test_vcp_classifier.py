"""Classification, invalidation, breakout, status and primary selection
(VCP_SPECIFICATION 5, 24-31, 45, 46, 61A, 61B)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest

from vcp_scanner.config.models import ClassificationConfig, VCPThresholdsConfig
from vcp_scanner.domain.enums import (
    ConfirmationState,
    InvalidationReason,
    PivotSource,
    VCPClassification,
    VCPStatus,
)
from vcp_scanner.patterns.vcp.detector import VCPDetector, select_primary
from vcp_scanner.patterns.vcp.measurements import PriceSeries

D0 = date(2026, 1, 1)
# 60 flat bars, advance 100 -> 150, T1 20 %, T2 10 %, T3 5 %, recovery to 140.
# Bars: T1 peak 80 (decline 81-90), T2 peak 100 (101-106), T3 peak 114 (115-119), 120-124 up.
CLASSIC = [(60, 100), (20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (5, 135), (5, 140)]


def _vcp(**over: Any) -> VCPThresholdsConfig:
    base: dict[str, Any] = {
        "swing": {"left_bars": 2, "right_bars": 2},
        "base": {"max_duration_days": 80},
        "prior_advance": {"lookback_days": 20},
    }
    for k, v in over.items():
        base[k] = {**base.get(k, {}), **v}
    return VCPThresholdsConfig(**base)


def _series(
    legs: list[tuple[int, float]] = CLASSIC,
    extra: list[tuple[float, float]] | None = None,
    vol: dict[int, float] | None = None,
) -> PriceSeries:
    """Closes along ``legs`` then ``extra`` (close, volume) bars; default volume 1000, with
    T1's decline on 1500 and T3's on 400 (dry-up)."""
    closes = [100.0]
    for bars, target in legs:
        a = closes[-1]
        closes += [a + (target - a) * (i + 1) / bars for i in range(bars)]
    volume = [1000.0] * len(closes)
    for i in range(81, min(91, len(volume))):
        volume[i] = 1500.0
    for i in range(115, min(120, len(volume))):
        volume[i] = 400.0
    for c, v in extra or []:
        closes.append(c)
        volume.append(v)
    for i, v in (vol or {}).items():
        volume[i] = v
    high = [c * 1.002 for c in closes]
    low = [c * 0.998 for c in closes]
    d = [D0 + timedelta(days=i) for i in range(len(closes))]
    return PriceSeries(d, high, low, closes, volume, [2.0] * len(closes))


def _detect(s: PriceSeries, tt: bool = True, stage2: bool | None = True, **cfg: Any):  # type: ignore[no-untyped-def]
    vcp = _vcp(**cfg)
    det = VCPDetector(vcp, ClassificationConfig(), config_hash="h")
    return det.detect("INE1", s, s.dates[-1], trend_template_pass=tt, weekly_stage2_pass=stage2)


def test_classic_tight_base_is_a_plus_and_pivot_ready() -> None:
    r = _detect(_series())
    p = r.pattern
    assert p is not None
    assert p.classification is VCPClassification.A_PLUS_VCP, r.classification.unmet  # type: ignore[union-attr]
    assert p.status is VCPStatus.PIVOT_READY
    assert p.confirmation_state is ConfirmationState.CONFIRMED
    assert p.contraction_count == 3 and p.is_primary
    assert p.pivot is not None and p.pivot.source is PivotSource.RIGHT_SIDE_HIGH
    assert 0 <= p.pivot_distance_pct <= 3  # type: ignore[operator]
    assert p.base_end is None and p.invalidation_reasons == ()
    assert r.classification.unmet[VCPClassification.A_PLUS_VCP] == ()  # type: ignore[union-attr]


def test_trend_template_failure_invalidates_and_caps_at_vcp_like() -> None:
    r = _detect(_series(), tt=False)
    p = r.pattern
    assert p is not None
    assert p.classification is VCPClassification.VCP_LIKE
    assert p.status is VCPStatus.INVALIDATED
    assert p.invalidation_reasons == (InvalidationReason.TREND_TEMPLATE_FAIL,)
    assert "trend_template" in r.classification.unmet[VCPClassification.A_PLUS_VCP]  # type: ignore[union-attr]
    r2 = _detect(_series(), tt=False, invalidation={"trend_template_failure": False})
    assert r2.pattern.status is VCPStatus.FORMING  # type: ignore[union-attr]
    assert r2.pattern.classification is VCPClassification.VCP_LIKE  # type: ignore[union-attr]


def test_missing_weekly_stage2_is_not_production() -> None:
    r = _detect(_series(), stage2=None)
    assert r.pattern.classification is VCPClassification.VCP_LIKE  # type: ignore[union-attr]
    assert r.pattern.status is VCPStatus.FORMING  # type: ignore[union-attr]  # not PIVOT_READY


def test_no_dryup_drops_to_vcp() -> None:
    vol = {i: 1300.0 for i in range(115, 120)}  # T3 on more volume than the baseline
    r = _detect(_series(vol=vol))
    assert r.pattern.classification is VCPClassification.VCP  # type: ignore[union-attr]
    assert r.classification.unmet[VCPClassification.A_PLUS_VCP] == ("require_volume_dryup",)  # type: ignore[union-attr]


def test_final_depth_tiers() -> None:
    legs = [(60, 100), (20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (5, 124), (5, 130)]
    r = _detect(_series(legs))  # final ~13 %: too deep for VCP (12), fine for VCP_LIKE (15)
    p = r.pattern
    assert p is not None and 12 < p.final_contraction_pct < 15  # type: ignore[operator]
    assert p.classification is VCPClassification.VCP_LIKE
    assert "max_final_contraction_pct" in r.classification.unmet[VCPClassification.VCP]  # type: ignore[union-attr]


def test_breakout_on_volume_then_failure() -> None:
    pivot = 142 * 1.002  # T3's peak (bar 114): the structural pivot
    r = _detect(_series(extra=[(143.0, 2000.0)]))
    p = r.pattern
    assert p is not None and r.breakout is not None
    assert r.pivots.structural.pivot_price == pytest.approx(pivot)  # type: ignore[union-attr]
    assert p.status is VCPStatus.BREAKOUT
    assert p.base_end == r.breakout.breakout_date == r.as_of
    assert r.breakout.volume_ratio >= 1.5
    failed = _detect(_series(extra=[(143.0, 2000.0), (139.0, 1000.0)]))
    assert failed.pattern.status is VCPStatus.FAILED  # type: ignore[union-attr]
    assert failed.pattern.base_end == r.as_of  # type: ignore[union-attr]


def test_close_above_pivot_without_volume_is_not_a_breakout() -> None:
    r = _detect(_series(extra=[(143.0, 1000.0)]))
    assert r.breakout is None
    assert r.pattern.status is VCPStatus.FORMING  # type: ignore[union-attr]  # above pivot: not ready
    assert r.pattern.pivot_distance_pct < 0  # type: ignore[operator]


def test_base_structure_failure() -> None:
    extra = [(130.0, 1000.0), (122.0, 1000.0), (116.0, 1000.0)]
    r = _detect(_series(extra=extra))
    p = r.pattern
    assert p is not None
    assert InvalidationReason.BASE_STRUCTURE_FAIL in p.invalidation_reasons
    assert p.status is VCPStatus.INVALIDATED
    # Within the 2 % allowance below the prior base low (119.76): not broken.
    ok = _detect(_series(extra=[(130.0, 1000.0), (124.0, 1000.0), (118.5, 1000.0)]))
    assert InvalidationReason.BASE_STRUCTURE_FAIL not in ok.pattern.invalidation_reasons  # type: ignore[union-attr]


def test_excess_volatility() -> None:
    extra = [(131.0 if i % 2 else 140.0, 1000.0) for i in range(10)]
    r = _detect(_series(extra=extra))
    p = r.pattern
    assert p is not None
    assert InvalidationReason.EXCESS_VOLATILITY in p.invalidation_reasons
    assert p.status is VCPStatus.INVALIDATED
    calm = _detect(_series(extra=extra), invalidation={"volatility_expansion_multiple": 10.0})
    assert InvalidationReason.EXCESS_VOLATILITY not in calm.pattern.invalidation_reasons  # type: ignore[union-attr]


def test_data_states_and_no_base() -> None:
    s = _series()
    det = VCPDetector(_vcp(), ClassificationConfig(), config_hash="h")
    r = det.detect("I", s, s.dates[-1], trend_template_pass=True, weekly_stage2_pass=True,
                   data_state=VCPStatus.DATA_NOT_READY)  # fmt: skip
    assert r.pattern is None and r.status is VCPStatus.DATA_NOT_READY
    with pytest.raises(ValueError, match="data status"):
        det.detect("I", s, s.dates[-1], trend_template_pass=True, weekly_stage2_pass=True,
                   data_state=VCPStatus.FORMING)  # fmt: skip
    flat = _series([(60, 100), (20, 110), (10, 100), (10, 108)])  # +10 % only
    r2 = _detect(flat)
    assert r2.pattern is None and r2.status is None and r2.no_pattern_reason == "NO_PRIOR_ADVANCE"
    short = _series([(5, 108), (10, 95), (10, 104)])  # short history, weak advance
    r3 = _detect(short)
    assert r3.status is VCPStatus.INSUFFICIENT_DATA


def test_too_many_contractions_is_unmet() -> None:
    r = _detect(_series(), contractions={"max": 6})
    assert r.classification is not None
    from vcp_scanner.patterns.vcp.classifier import _tier_unmet

    unmet = _tier_unmet(
        ClassificationConfig().vcp_like, False, 7, 5.0, r.measurements,  # type: ignore[arg-type]
        _vcp(), True, True,
    )  # fmt: skip
    assert unmet == ("max_contractions",)


def test_primary_selection_order() -> None:
    p = _detect(_series()).pattern
    assert p is not None
    vcp_ = replace(p, classification=VCPClassification.VCP, status=VCPStatus.FORMING)
    like = replace(p, classification=VCPClassification.VCP_LIKE, status=VCPStatus.FORMING)
    assert [x.is_primary for x in select_primary([like, vcp_])] == [False, True]  # class first
    longer = replace(vcp_, base_duration_days=vcp_.base_duration_days + 10)
    assert [x.is_primary for x in select_primary([vcp_, longer])] == [False, True]
    earlier = replace(vcp_, base_start=vcp_.base_start - timedelta(days=1))
    assert [x.is_primary for x in select_primary([vcp_, earlier])] == [False, True]
    assert select_primary([]) == []


def test_detection_is_deterministic_and_as_of_safe() -> None:
    s = _series(extra=[(143.0, 2000.0), (139.0, 1000.0)])
    det = VCPDetector(_vcp(), ClassificationConfig(), config_hash="h")
    as_of = s.dates[124]
    full = det.detect("I", s, as_of, trend_template_pass=True, weekly_stage2_pass=True)
    cut = PriceSeries(*(c[:125] for c in (s.dates, s.high, s.low, s.close, s.volume, s.atr_pct_14)))
    again = det.detect("I", cut, as_of, trend_template_pass=True, weekly_stage2_pass=True)
    assert full == again
    assert full.pattern.status is VCPStatus.PIVOT_READY  # type: ignore[union-attr]
