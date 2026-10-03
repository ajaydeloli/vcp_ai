"""Forward labels (backtest/labels.py; PROJECT_DESIGN 39; Phase 9 step 2)."""

from __future__ import annotations

import pytest

from vcp_scanner.backtest.labels import forward_labels

PRIOR: list[float | None] = [1000.0] * 50


def _flat(n: int, price: float = 100.0) -> tuple[list[float], list[float], list[float]]:
    return [price * 1.01] * n, [price * 0.99] * n, [price] * n


def test_returns_excursions_and_completion() -> None:
    h, lo, c = _flat(70)
    c[4], c[59] = 105.0, 90.0
    h[30], lo[40] = 120.0, 80.0
    lab = forward_labels(100.0, h, lo, c, [1000.0] * 70, PRIOR, None, 1.5)
    assert lab.ret[5] == pytest.approx(5.0) and lab.ret[60] == pytest.approx(-10.0)
    assert lab.ret[10] == pytest.approx(0.0)
    assert lab.mfe_60 == pytest.approx(20.0) and lab.mae_60 == pytest.approx(-20.0)
    assert lab.breakout_within_20 is None and lab.failed_breakout is None  # no pivot
    assert lab.complete and lab.bars_after == 70


def test_partial_labels_fill_in_as_bars_arrive() -> None:
    h, lo, c = _flat(12)
    lab = forward_labels(100.0, h, lo, c, [1000.0] * 12, PRIOR, 104.0, 1.5)
    assert lab.ret[5] is not None and lab.ret[10] is not None and lab.ret[20] is None
    assert lab.mfe_60 is None and lab.breakout_within_20 is None  # 20-session window open
    assert not lab.complete


def test_breakout_on_volume_and_failure() -> None:
    h, lo, c = _flat(70)
    vol: list[float | None] = [1000.0] * 70
    c[2], vol[2] = 105.0, 1400.0  # above the pivot but only 1.4x volume: not a breakout
    c[6], vol[6] = 106.0, 2000.0  # breakout on day 7
    lab = forward_labels(100.0, h, lo, c, vol, PRIOR, 104.0, 1.5)
    assert lab.breakout_within_20 is True and lab.breakout_day == 7
    assert lab.failed_breakout is True  # day 8 closes at 100, back below 104
    for i in range(7, 70):
        c[i] = 107.0
    held = forward_labels(100.0, h, lo, c, vol, PRIOR, 104.0, 1.5)
    assert held.failed_breakout is False and held.complete
    early = forward_labels(100.0, h[:12], lo[:12], c[:12], vol[:12], PRIOR, 104.0, 1.5)
    assert early.breakout_within_20 is True and early.failed_breakout is None  # window open


def test_no_breakout_after_twenty_sessions_and_missing_volume() -> None:
    h, lo, c = _flat(25)
    assert (
        forward_labels(100.0, h, lo, c, [1000.0] * 25, PRIOR, 104.0, 1.5).breakout_within_20
        is False
    )
    c[3] = 110.0
    vol: list[float | None] = [1000.0] * 25
    vol[3] = None  # missing volume: cannot confirm a breakout on that day
    assert forward_labels(100.0, h, lo, c, vol, PRIOR, 104.0, 1.5).breakout_within_20 is False
