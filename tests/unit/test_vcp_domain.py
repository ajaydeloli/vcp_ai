"""VCP domain model invariants (VCP_SPECIFICATION 9, 9A, 10, 20, 42; DATABASE_SCHEMA 31-33)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from vcp_scanner.domain.enums import (
    ConfirmationState,
    InvalidationReason,
    PivotSource,
    SwingKind,
    VCPClassification,
    VCPStatus,
)
from vcp_scanner.domain.vcp import Contraction, PivotCandidate, Swing, VCPPattern, depth_pct


def _c(
    n: int, peak: date, trough: date, hi: float, lo: float, confirmed: date | None
) -> Contraction:
    return Contraction(
        sequence_number=n,
        peak_date=peak,
        peak_price=hi,
        trough_date=trough,
        trough_price=lo,
        depth_pct=depth_pct(hi, lo),
        duration_days=5,
        atr_pct=2.0,
        range_pct=depth_pct(hi, lo),
        volume_ratio=0.9,
        confirmation_date=confirmed,
    )


T1 = _c(1, date(2026, 6, 1), date(2026, 6, 10), 100.0, 80.0, date(2026, 6, 17))  # 20 %
T2 = _c(2, date(2026, 7, 1), date(2026, 7, 8), 98.0, 88.2, date(2026, 7, 15))  # 10 %
T3 = _c(3, date(2026, 8, 3), date(2026, 8, 7), 97.0, 92.15, None)  # 5 %, provisional
PIVOT = PivotCandidate(
    pivot_price=97.0,
    pivot_date=date(2026, 8, 3),
    source=PivotSource.SWING_HIGH,
    distance_to_close_pct=2.0,
    touches=2,
    rejection_count=1,
    right_side_tightness_pct=4.5,
)


def _pattern(**overrides: Any) -> VCPPattern:
    fields: dict[str, Any] = dict(
        instrument_id="INE000A01010",
        as_of_date=date(2026, 8, 12),
        base_start=date(2026, 6, 1),
        base_end=None,
        base_high=100.0,
        base_low=80.0,
        base_duration_days=50,
        prior_advance_return_pct=42.0,
        contractions=(T1, T2, T3),
        progressive_tightening=True,
        final_volume_ratio=0.6,
        volume_dryup_pass=True,
        atr_contraction_ratio=0.7,
        volatility_contraction_pass=True,
        right_side_range_pct=4.5,
        tight_pivot_pass=True,
        tightening_quality=None,
        volatility_quality=None,
        volume_quality=None,
        pivot_quality=None,
        base_quality=None,
        pivot=PIVOT,
        classification=VCPClassification.A_PLUS_VCP,
        status=VCPStatus.PIVOT_READY,
        confirmation_state=ConfirmationState.PROVISIONAL,
        trend_template_pass=True,
        weekly_stage2_pass=True,
        algorithm_version="vcp-1.0.0",
        config_hash="h",
        pivot_candidates=(PIVOT,),
    )
    fields.update(overrides)
    return VCPPattern(**fields)


def test_derived_measurements() -> None:
    p = _pattern()
    assert p.contraction_count == 3
    assert p.first_contraction_pct == pytest.approx(20.0)
    assert p.final_contraction_pct == pytest.approx(5.0)
    assert p.final_contraction is T3
    assert p.tightening_ratios == pytest.approx((0.5, 0.5))
    assert p.max_tightening_ratio == pytest.approx(0.5)
    assert p.base_depth_pct == pytest.approx(20.0)
    assert p.pivot_distance_pct == 2.0


def test_empty_pattern_has_no_derived_values() -> None:
    p = _pattern(
        contractions=(),
        classification=VCPClassification.NONE,
        status=VCPStatus.FORMING,
        confirmation_state=ConfirmationState.CONFIRMED,
        pivot=None,
        pivot_candidates=(),
    )
    assert p.first_contraction_pct is None and p.final_contraction_pct is None
    assert p.tightening_ratios == () and p.max_tightening_ratio is None
    assert p.pivot_distance_pct is None


def test_confirmation_state_must_match_contractions() -> None:
    with pytest.raises(ValueError, match="confirmation_state"):
        _pattern(confirmation_state=ConfirmationState.CONFIRMED)
    confirmed_t3 = replace(T3, confirmation_date=date(2026, 8, 12))
    assert _pattern(
        contractions=(T1, T2, confirmed_t3), confirmation_state=ConfirmationState.CONFIRMED
    )
    with pytest.raises(ValueError, match="confirmation_state"):
        _pattern(contractions=(T1, T2, confirmed_t3))


def test_only_the_final_contraction_may_be_provisional() -> None:
    t2_open = replace(T2, confirmation_date=None)
    with pytest.raises(ValueError, match="only the final contraction"):
        _pattern(contractions=(T1, t2_open, T3))


def test_sequence_numbers_and_order() -> None:
    with pytest.raises(ValueError, match="1..n"):
        _pattern(contractions=(T1, T3))
    early = _c(2, date(2026, 6, 5), date(2026, 6, 20), 99.0, 90.0, date(2026, 6, 27))
    with pytest.raises(ValueError, match="before"):
        _pattern(contractions=(T1, early), confirmation_state=ConfirmationState.CONFIRMED)


def test_contractions_lie_inside_the_base() -> None:
    with pytest.raises(ValueError, match="outside the base"):
        _pattern(base_start=date(2026, 6, 2))
    with pytest.raises(ValueError, match="outside the base"):
        _pattern(as_of_date=date(2026, 8, 6))


@pytest.mark.parametrize(
    ("tt", "stage2"), [(False, True), (True, False), (True, None)]
)  # fmt: skip
def test_production_classes_need_both_gates(tt: bool, stage2: bool | None) -> None:
    for cls in (VCPClassification.VCP, VCPClassification.A_PLUS_VCP):
        with pytest.raises(ValueError, match="requires Trend Template"):
            _pattern(classification=cls, trend_template_pass=tt, weekly_stage2_pass=stage2)
    # VCP_LIKE is research/watchlist only and may exist outside the gates (section 3).
    assert _pattern(
        classification=VCPClassification.VCP_LIKE, trend_template_pass=tt, weekly_stage2_pass=stage2
    )


def test_data_states_carry_no_classification() -> None:
    with pytest.raises(ValueError, match="classification NONE"):
        _pattern(status=VCPStatus.INSUFFICIENT_DATA, classification=VCPClassification.VCP_LIKE)
    assert _pattern(status=VCPStatus.INSUFFICIENT_DATA, classification=VCPClassification.NONE)


def test_invalidation_needs_reasons() -> None:
    with pytest.raises(ValueError, match="needs at least one"):
        _pattern(status=VCPStatus.INVALIDATED)
    with pytest.raises(ValueError, match="reasons given"):
        _pattern(invalidation_reasons=(InvalidationReason.EXCESS_VOLATILITY,))
    p = _pattern(
        status=VCPStatus.INVALIDATED,
        invalidation_reasons=(InvalidationReason.BASE_STRUCTURE_FAIL,),
    )
    assert p.invalidation_reasons == (InvalidationReason.BASE_STRUCTURE_FAIL,)


def test_pivot_must_be_a_candidate() -> None:
    with pytest.raises(ValueError, match="pivot_candidates"):
        _pattern(pivot_candidates=())
    assert _pattern(pivot=None).pivot_distance_pct is None


def test_base_checks() -> None:
    with pytest.raises(ValueError, match="base_low"):
        _pattern(base_low=101.0)
    with pytest.raises(ValueError, match="base_end"):
        _pattern(base_end=date(2026, 8, 13))
    with pytest.raises(ValueError, match="base_duration_days"):
        _pattern(base_duration_days=0)


def test_contraction_checks() -> None:
    with pytest.raises(ValueError, match="does not match"):
        replace(T1, depth_pct=19.0)
    with pytest.raises(ValueError, match="trough_price"):
        replace(T1, trough_price=100.0, depth_pct=0.0)
    with pytest.raises(ValueError, match="must follow"):
        replace(T1, trough_date=T1.peak_date)
    with pytest.raises(ValueError, match="before trough_date"):
        replace(T1, confirmation_date=date(2026, 6, 9))
    with pytest.raises(ValueError, match="sequence_number"):
        replace(T1, sequence_number=0)
    assert T1.is_confirmed and not T3.is_confirmed


def test_pivot_candidate_checks() -> None:
    with pytest.raises(ValueError, match="rejection_count"):
        replace(PIVOT, rejection_count=3)
    with pytest.raises(ValueError, match="touches"):
        replace(PIVOT, touches=0, rejection_count=0)
    with pytest.raises(ValueError, match="pivot_price"):
        replace(PIVOT, pivot_price=0.0)


def test_swing_is_known_only_from_its_confirmation_date() -> None:
    s = Swing(SwingKind.HIGH, date(2026, 6, 1), 100.0, date(2026, 6, 8))
    assert s.is_confirmed
    assert not s.known_on(date(2026, 6, 5))  # swing date passed, not yet confirmed
    assert s.known_on(date(2026, 6, 8))
    open_swing = Swing(SwingKind.LOW, date(2026, 6, 10), 90.0, None)
    assert not open_swing.is_confirmed and not open_swing.known_on(date(2027, 1, 1))
    with pytest.raises(ValueError, match="before swing_date"):
        Swing(SwingKind.HIGH, date(2026, 6, 1), 100.0, date(2026, 5, 29))
    with pytest.raises(ValueError, match="positive"):
        Swing(SwingKind.LOW, date(2026, 6, 1), float("nan"), None)
