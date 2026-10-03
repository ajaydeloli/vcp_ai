"""Outcome study (VCP_SPECIFICATION 53, 62, 63; owner decision 2026-10-03, option B)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vcp_scanner.research.outcomes import (
    HORIZON,
    Outcome,
    WindowRow,
    format_stats,
    forward_outcome,
    summarize,
    wilson,
)

D0 = date(2025, 1, 31)


def _flat(n: int = HORIZON, price: float = 100.0) -> tuple[list[float], list[float], list[float]]:
    return [price * 1.01] * n, [price * 0.99] * n, [price] * n


def test_win_loss_none_and_conservative_same_bar() -> None:
    h, lo, c = _flat()
    assert forward_outcome(h, lo, c, 100.0, None).result == "NONE"  # type: ignore[union-attr]
    h[5] = 111.0  # +11 % on bar 5
    assert forward_outcome(h, lo, c, 100.0, None).result == "WIN"  # type: ignore[union-attr]
    lo[3] = 92.0  # -8 % on bar 3, before the win
    assert forward_outcome(h, lo, c, 100.0, None).result == "LOSS"  # type: ignore[union-attr]
    h2, lo2, c2 = _flat()
    h2[4], lo2[4] = 111.0, 92.0  # both on one bar: LOSS (conservative)
    assert forward_outcome(h2, lo2, c2, 100.0, None).result == "LOSS"  # type: ignore[union-attr]


def test_returns_excursions_breakout_and_censoring() -> None:
    h, lo, c = _flat()
    c[19], c[59] = 105.0, 120.0
    h[30], lo[40] = 125.0, 95.0
    o = forward_outcome(h, lo, c, 100.0, 104.0)
    assert o is not None
    assert o.ret_20 == pytest.approx(5.0) and o.ret_60 == pytest.approx(20.0)
    assert o.mfe_60 == pytest.approx(25.0) and o.mae_60 == pytest.approx(-5.0)
    assert o.breakout_20 is True  # close 105 > pivot 104 on bar 19
    assert forward_outcome(h, lo, c, 100.0, 130.0).breakout_20 is False  # type: ignore[union-attr]
    assert forward_outcome(h, lo, c, 100.0, None).breakout_20 is None  # type: ignore[union-attr]
    short = _flat(HORIZON - 1)
    assert forward_outcome(*short, 100.0, None) is None


def test_wilson_interval() -> None:
    lo, hi = wilson(5, 10)
    assert lo < 0.5 < hi and lo == pytest.approx(0.2366, abs=1e-3)
    assert wilson(0, 0) == (0.0, 1.0)


def _row(d: date, group: str, result: str | None, status: str = "FORMING") -> WindowRow:
    o = None if result is None else Outcome(1.0, 2.0, 3.0, -4.0, result, None)
    return WindowRow(d, f"{group}{d}{result}", "S", group, status, o)


def test_summary_excess_is_against_the_same_dates() -> None:
    good, bad = D0, D0 + timedelta(days=30)
    rows = [
        # Good month: everyone wins.
        _row(good, "VCP", "WIN"), _row(good, "NONE", "WIN"), _row(good, "NONE", "WIN"),
        # Bad month: VCP wins, others lose.
        _row(bad, "VCP", "WIN"), _row(bad, "NONE", "LOSS"), _row(bad, "NONE", "LOSS"),
        _row(bad, "NONE", None),  # censored
    ]  # fmt: skip
    stats, censored = summarize(rows)
    by = {s.group: s for s in stats}
    assert censored == 1
    assert by["VCP"].n == 2 and by["VCP"].win_rate == 1.0
    # Same-date baseline: good 3/3, bad 1/3 -> expected for VCP = (1 + 1/3) / 2 = 2/3.
    assert by["VCP"].excess_win == pytest.approx(1 / 3)
    assert by["NONE"].excess_win == pytest.approx(0.5 - (1 + 1 / 3) / 2)
    assert by["ALL"].n == 6
    assert [s.group for s in stats] == ["VCP", "NONE", "ALL"]
    status_stats, _ = summarize(rows, key="status")
    assert {s.group for s in status_stats} == {"FORMING", "ALL"}
    assert "VCP" in format_stats(stats, "t")


def test_breakout_trade_needs_a_close_above_the_pivot_on_volume() -> None:
    from vcp_scanner.research.outcomes import breakout_trade

    n = HORIZON + 30
    h, lo, c = _flat(n)
    vol: list[float | None] = [1000.0] * n
    prior: list[float | None] = [1000.0] * 50
    assert breakout_trade(h, lo, c, vol, prior, 104.0) is None  # never above the pivot
    c[3], h[3] = 105.0, 105.5  # close above the pivot ...
    assert breakout_trade(h, lo, c, vol, prior, 104.0) is None  # ... but on normal volume
    vol[3] = 2000.0
    h[10] = 116.0  # +10.5 % from the 105 entry
    assert breakout_trade(h, lo, c, vol, prior, 104.0) == 10.0
    lo[6] = 97.0  # -7.6 % first: stopped out
    assert breakout_trade(h, lo, c, vol, prior, 104.0) == -7.0
    lo[6], h[10] = 99.0, 101.0  # neither: exit at the close HORIZON bars after entry
    assert breakout_trade(h, lo, c, vol, prior, 104.0) == pytest.approx((100 / 105 - 1) * 100)
    assert breakout_trade(h, lo, c, vol, prior, None) is None
    missing: list[float | None] = [1000.0] * 49 + [None]
    assert breakout_trade(h, lo, c, vol, missing, 104.0) is None  # baseline incomplete


def test_trade_stats_in_summary() -> None:
    o_win = Outcome(1.0, 2.0, 3.0, -4.0, "WIN", True, 10.0)
    o_loss = Outcome(1.0, 2.0, 3.0, -4.0, "LOSS", True, -7.0)
    o_none = Outcome(1.0, 2.0, 3.0, -4.0, "NONE", False, None)
    rows = [WindowRow(D0, f"I{k}", "S", "VCP", "FORMING", o)
            for k, o in enumerate((o_win, o_loss, o_win, o_none))]  # fmt: skip
    stats, _ = summarize(rows)
    vcp = next(s for s in stats if s.group == "VCP")
    assert vcp.trades == 3 and vcp.trade_win_rate == pytest.approx(2 / 3)
    assert vcp.expectancy == pytest.approx((10 - 7 + 10) / 3)
