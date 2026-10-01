"""Kite cross-check: steps in the Kite/ours close ratio and their attribution (audit step 2.5)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vcp_scanner.data.quality.kite_crosscheck import CrosscheckKind, crosscheck
from vcp_scanner.domain.corporate_actions import CorporateActionResolution, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType as T

DAYS = [date(2024, 7, 1) + timedelta(days=i) for i in range(10)]


def _ours(price: float = 100.0) -> list[tuple[date, float]]:
    return [(d, price + i) for i, d in enumerate(DAYS)]


def _kite(factor_before: dict[int, float]) -> list[tuple[date, float]]:
    """Kite = ours, scaled by the product of factors whose ex-day index is after t."""
    out = []
    for i, (d, c) in enumerate(_ours()):
        k = 1.0
        for ex_index, f in factor_before.items():
            if i < ex_index:
                k *= f
        out.append((d, round(c * k, 2)))
    return out


def _action(t: T, ex_index: int) -> CorporateActionResolution:
    return CorporateActionResolution(
        f"r{ex_index}", "I", t, CorporateActionStatus.CONFIRMED, ex_date=DAYS[ex_index]
    )


def test_identical_series_have_no_findings() -> None:
    report = crosscheck(_ours(), _ours(), [])
    assert report.common_days == 10 and report.findings == []


def test_dividend_step_is_info() -> None:
    report = crosscheck(_ours(), _kite({4: 0.978}), [_action(T.DIVIDEND, 4)])
    (f,) = report.findings
    assert (f.kind, f.severity, f.trade_date, f.previous_date) == (
        CrosscheckKind.KITE_DIVIDEND,
        "INFO",
        DAYS[4],
        DAYS[3],
    )
    assert f.step == pytest.approx(0.978, abs=1e-4)
    assert report.warnings == []


def test_demerger_method_difference_is_info() -> None:
    # RELIANCE 2023: Kite about 0.9532 vs our 0.9079 -> Kite/ours step 1.0499 before the ex-date.
    report = crosscheck(_ours(), _kite({6: 0.9532 / 0.9079}), [_action(T.DEMERGER, 6)])
    (f,) = report.findings
    assert f.kind is CrosscheckKind.DEMERGER_METHOD and f.severity == "INFO"


@pytest.mark.parametrize("action_type", [T.SPLIT, T.BONUS, T.RIGHTS])
def test_price_scaling_factor_mismatch_is_a_warning(action_type: T) -> None:
    report = crosscheck(_ours(), _kite({5: 0.9}), [_action(action_type, 5)])
    (f,) = report.warnings
    assert f.kind is CrosscheckKind.FACTOR_MISMATCH and f.actions == (action_type.value,)


def test_step_without_any_action_is_unexplained() -> None:
    report = crosscheck(_ours(), _kite({3: 0.95}), [_action(T.DIVIDEND, 8)])
    (f,) = report.warnings
    assert f.kind is CrosscheckKind.UNEXPLAINED and f.trade_date == DAYS[3]


def test_a_single_bad_bar_is_one_bar_mismatch() -> None:
    kite = _ours()
    kite[5] = (DAYS[5], kite[5][1] * 1.03)
    (f,) = crosscheck(_ours(), kite, []).findings
    assert f.kind is CrosscheckKind.BAR_MISMATCH and f.trade_date == DAYS[5]


def test_latest_level_must_agree() -> None:
    kite = [(d, c * 1.01) for d, c in _ours()]
    (f,) = crosscheck(_ours(), kite, []).findings
    assert f.kind is CrosscheckKind.LEVEL_MISMATCH and f.trade_date == DAYS[-1]


def test_rounding_noise_below_tolerance_is_ignored_and_gaps_are_counted() -> None:
    kite = [(d, c + 0.004) for d, c in _ours()][:-2]  # Kite rounding; misses 2 days
    report = crosscheck(_ours(), kite, [])
    assert report.findings == []
    assert (report.common_days, report.only_ours, report.only_kite) == (8, 2, 0)
