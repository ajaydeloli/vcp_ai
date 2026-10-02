"""Swing detector (VCP_SPECIFICATION 9, 9A, 26): rule, confirmation dates, as-of safety."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from vcp_scanner.config.models import VCPSwingConfig
from vcp_scanner.domain.enums import SwingKind
from vcp_scanner.domain.errors import DataValidationError
from vcp_scanner.patterns.vcp.swings import SwingDetector, detect_swings

D0 = date(2026, 1, 1)


def _dates(n: int) -> list[date]:
    return [D0 + timedelta(days=i) for i in range(n)]


def _bars(highs: list[float]) -> tuple[list[date], list[float], list[float]]:
    """Bars whose low is high - 1, so lows mirror highs."""
    return _dates(len(highs)), highs, [h - 1 for h in highs]


def _swings(highs: list[float], as_of: date | None = None, lr: int = 2):  # type: ignore[no-untyped-def]
    d, h, lo = _bars(highs)
    return detect_swings(d, h, lo, as_of=as_of or d[-1], left_bars=lr, right_bars=lr)


def test_single_peak_and_trough_with_confirmation_dates() -> None:
    #        0   1   2   3   4   5   6   7   8
    highs = [10, 11, 12, 15, 12, 11, 10, 9, 10, 11, 12]
    s = _swings(highs)
    hi = s.highs()
    assert [(x.swing_date, x.price) for x in hi] == [(D0 + timedelta(3), 15)]
    assert hi[0].confirmation_date == D0 + timedelta(5)  # two bars to the right
    lows = s.lows()
    assert [(x.swing_date, x.price) for x in lows] == [(D0 + timedelta(7), 8)]
    assert lows[0].confirmation_date == D0 + timedelta(9)


def test_needs_left_bars_before_a_swing() -> None:
    # The first bar is the lowest, but has no left side: not a swing low.
    s = _swings([5, 9, 10, 11, 12, 13])
    assert s.lows() == ()


def test_confirmation_waits_for_right_bars_and_pending_is_reported() -> None:
    highs = [10, 11, 12, 15, 12]
    s = _swings(highs)  # as of bar 4: only one bar right of the peak
    assert s.highs() == ()
    assert [(p.kind, p.swing_date, p.confirmation_date) for p in s.pending] == [
        (SwingKind.HIGH, D0 + timedelta(3), None),
        (SwingKind.LOW, D0 + timedelta(4), None),  # the as-of bar's low so far
    ]
    s2 = _swings([*highs, 11])
    assert [x.swing_date for x in s2.highs()] == [D0 + timedelta(3)]
    assert s2.pending == () or all(p.swing_date > D0 + timedelta(3) for p in s2.pending)


def test_pending_swing_can_be_cancelled() -> None:
    s = _swings([10, 11, 12, 15, 12])
    assert s.pending
    s2 = _swings([10, 11, 12, 15, 12, 16, 12, 11])  # 16 > 15 within the right window
    assert D0 + timedelta(3) not in [x.swing_date for x in s2.highs()]
    assert [x.swing_date for x in s2.highs()] == [D0 + timedelta(5)]


def test_equal_highs_both_qualify_literal_rule() -> None:
    s = _swings([10, 11, 15, 15, 11, 10, 9])
    assert [x.swing_date for x in s.highs()] == [D0 + timedelta(2), D0 + timedelta(3)]


def test_outside_bar_is_high_and_low_high_first() -> None:
    d = _dates(5)
    highs = [10.0, 10.0, 20.0, 10.0, 10.0]
    lows = [9.0, 9.0, 1.0, 9.0, 9.0]
    s = detect_swings(d, highs, lows, as_of=d[-1], left_bars=2, right_bars=2)
    assert [(x.kind, x.swing_date) for x in s.confirmed] == [
        (SwingKind.HIGH, d[2]),
        (SwingKind.LOW, d[2]),
    ]


def test_bars_after_as_of_are_ignored() -> None:
    highs = [10, 11, 12, 15, 12, 11, 10]
    d, h, lo = _bars(highs)
    as_of = d[4]
    future = detect_swings(d, h, lo, as_of=as_of, left_bars=2, right_bars=2)
    trimmed = detect_swings(d[:5], h[:5], lo[:5], as_of=as_of, left_bars=2, right_bars=2)
    assert future == trimmed
    # Even a malformed bar after as_of is never inspected.
    bad = [*h[:5], float("nan"), 1.0]
    assert detect_swings(d, bad, lo, as_of=as_of, left_bars=2, right_bars=2) == trimmed


@pytest.mark.parametrize("seed", range(20))
@pytest.mark.parametrize(("left", "right"), [(5, 5), (3, 5), (5, 3), (1, 1)])
def test_as_of_runs_equal_full_history_filtered_by_confirmation(
    seed: int, left: int, right: int
) -> None:
    """Section 26: as of every date, the confirmed swings are exactly the full-history swings
    confirmed by then, so no later run changes an earlier answer."""
    rng = random.Random(seed)
    n = 160
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(round(closes[-1] * (1 + rng.gauss(0, 0.02)), 1))  # ties occur
    highs = [round(c * (1 + abs(rng.gauss(0, 0.01))), 1) for c in closes]
    lows = [round(c * (1 - abs(rng.gauss(0, 0.01))), 1) for c in closes]
    d = _dates(n)
    full = detect_swings(d, highs, lows, as_of=d[-1], left_bars=left, right_bars=right)
    assert full.confirmed  # the series is long enough to have swings
    for k in range(n):
        as_of = d[k]
        got = detect_swings(d, highs, lows, as_of=as_of, left_bars=left, right_bars=right)
        expected = tuple(
            s for s in full.confirmed if s.confirmation_date and s.confirmation_date <= as_of
        )
        assert got.confirmed == expected
        assert all(s.swing_date > d[max(k - right, 0)] - timedelta(1) for s in got.pending)
        assert all(s.confirmation_date is None for s in got.pending)


def test_detector_uses_config() -> None:
    d, h, lo = _bars([10, 11, 12, 15, 12, 11, 10, 9, 10, 11, 12])
    det = SwingDetector(VCPSwingConfig(left_bars=2, right_bars=2))
    assert det.detect(d, h, lo, as_of=d[-1]) == detect_swings(
        d, h, lo, as_of=d[-1], left_bars=2, right_bars=2
    )


def test_input_validation() -> None:
    d = _dates(3)
    with pytest.raises(DataValidationError, match="differ in length"):
        detect_swings(d, [1.0, 2.0], [1.0, 1.0, 1.0], as_of=d[-1], left_bars=1, right_bars=1)
    with pytest.raises(DataValidationError, match="strictly increasing"):
        detect_swings(
            [d[0], d[0], d[1]], [2.0] * 3, [1.0] * 3, as_of=d[-1], left_bars=1, right_bars=1
        )
    with pytest.raises(DataValidationError, match="bad bar"):
        detect_swings(d, [2.0, 1.0, 2.0], [1.0, 1.5, 1.0], as_of=d[-1], left_bars=1, right_bars=1)
    with pytest.raises(ValueError, match=">= 1"):
        detect_swings(d, [2.0] * 3, [1.0] * 3, as_of=d[-1], left_bars=0, right_bars=1)


def test_empty_and_short_series() -> None:
    assert detect_swings([], [], [], as_of=D0, left_bars=5, right_bars=5).confirmed == ()
    s = _swings([10, 11, 12], lr=5)
    assert s.confirmed == () and s.pending == ()
