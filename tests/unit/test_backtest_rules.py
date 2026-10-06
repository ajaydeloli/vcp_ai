"""Entry rules, market regime and trailing exits of the event engine (STRATEGY_SPECIFICATION 20;
Multi-Strategy step 7b; synthetic bars)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from tests.unit.test_backtest_engine import _bars, _set
from vcp_scanner.backtest.engine import EngineConfig, Signal, moving_average, run_signals
from vcp_scanner.backtest.regime import DayBreadth, breadth_regime, ew_regime, regime_by_day
from vcp_scanner.research.outcomes import ENGINE_RULES, TRADE_RULES

RULES = {r.name: r for r in ENGINE_RULES}
HOLD = RULES["hold_s7"]
BASE = EngineConfig(HOLD, cost_bps=0.0)


def _sig(bs: list, pivot: float = 104.0) -> Signal:
    return Signal("A", bs[60].day, pivot, None, None, "X")


def test_defaults_are_unchanged() -> None:
    assert BASE.entry == "breakout" and BASE.regime is None
    assert [r.name for r in ENGINE_RULES[: len(TRADE_RULES)]] == [r.name for r in TRADE_RULES]
    assert all(r.trail is None and r.horizon is None for r in TRADE_RULES)
    with pytest.raises(ValueError, match="unknown entry rule"):
        EngineConfig(HOLD, entry="gap")


def test_cross_needs_the_previous_close_at_or_below_the_pivot() -> None:
    bs = _bars()  # closes 100, pivot 104
    _set(bs, 64, close=106.0)  # above the pivot already, ordinary volume
    _set(bs, 65, close=107.0, volume=2000.0)  # breakout volume, but no cross
    _set(bs, 66, close=103.0)
    _set(bs, 67, close=105.0, volume=2000.0)  # a real cross on volume
    (t,), _ = run_signals([_sig(bs)], {"A": bs}, BASE)
    assert t.entry_day == bs[65].day  # today's rule enters on the first busy close above
    (t,), _ = run_signals([_sig(bs)], {"A": bs}, replace(BASE, entry="cross_5"))
    assert t.entry_day == bs[67].day and t.entry == 105.0


def test_cross_skips_a_close_more_than_5_pct_above_the_pivot() -> None:
    bs = _bars()
    _set(bs, 65, close=110.0, volume=2000.0)  # +5.8 % above 104: too extended
    _set(bs, 66, close=103.0)
    _set(bs, 67, close=109.0, volume=2000.0)  # +4.8 %: inside the buy zone
    (t,), _ = run_signals([_sig(bs)], {"A": bs}, replace(BASE, entry="cross_5"))
    assert t.entry_day == bs[67].day


def test_regime_off_delays_entry_and_never_closes_a_trade() -> None:
    bs = _bars()
    _set(bs, 65, close=105.0, volume=2000.0)
    _set(bs, 66, close=103.0)
    _set(bs, 67, close=105.0, volume=2000.0)
    on = {b.day: True for b in bs}
    off65 = {**on, bs[65].day: False}
    (t,), _ = run_signals([_sig(bs)], {"A": bs}, replace(BASE, regime=off65))
    assert t.entry_day == bs[67].day  # the off day did not end the watch
    off_after = {**on, **{b.day: False for b in bs[66:]}}
    (t,), _ = run_signals([_sig(bs)], {"A": bs}, replace(BASE, regime=off_after))
    assert t.entry_day == bs[65].day and t.exit_kind == "TIME_EXIT"  # held to the horizon
    (none_), _ = run_signals([_sig(bs)], {"A": bs}, replace(BASE, regime={}))
    assert none_ == []  # a day missing from the regime is "off"


def test_moving_averages() -> None:
    bs = _bars(60)
    for i in range(60):
        _set(bs, i, close=float(i + 1))
    sma = moving_average(bs, "sma50")
    assert sma[48] is None and sma[49] == pytest.approx(25.5) and sma[59] == pytest.approx(35.5)
    ema = moving_average(bs, "ema20")
    assert ema[18] is None and ema[19] == pytest.approx(10.5)
    assert ema[20] == pytest.approx(10.5 + 2 / 21 * (21 - 10.5))
    with pytest.raises(ValueError):
        moving_average(bs, "wma9")


def _trend(n: int = 260) -> list:
    """Closes rising 0.5 a day from 100 (lows 1 % below), so trailing lines lag the close."""
    bs = _bars(n)
    for i in range(n):
        c = 100.0 + 0.5 * i
        _set(bs, i, close=c, high=c * 1.005, low=c * 0.99)
    return bs


def test_trailing_exit_on_the_close_below_the_line() -> None:
    bs = _trend()
    sig = Signal("A", bs[60].day, 130.2, None, None, "X")  # close 130.5 on bar 61 crosses it
    _set(bs, 61, close=130.5, high=131.0, low=129.5, volume=2000.0)
    _set(bs, 63, close=124.0, low=123.0)  # below the EMA (stop 121.4), only 2 sessions in
    _set(bs, 90, close=130.0, low=129.5)  # 10 % under the trend: below EMA20 and SMA50
    for name in ("trail_e20", "trail_s50"):
        cfg = replace(BASE, rule=RULES[name])
        (t,), _ = run_signals([sig], {"A": bs}, cfg)
        assert t.entry_day == bs[61].day
        assert t.exit_kind == "TRAIL_EXIT" and t.exit_day == bs[90].day and t.exit == 130.0


def test_initial_stop_comes_first_and_horizon_is_120() -> None:
    bs = _trend()
    _set(bs, 61, close=130.5, volume=2000.0)
    _set(bs, 62, low=120.0)  # -8 %: the -7 % stop on day 1
    sig = Signal("A", bs[60].day, 130.2, None, None, "X")
    (t,), _ = run_signals([sig], {"A": bs}, replace(BASE, rule=RULES["trail_e20"]))
    assert t.exit_kind == "STOP" and t.exit == pytest.approx(130.5 * 0.93)
    clean = _trend()
    _set(clean, 61, close=130.5, volume=2000.0)
    (t,), _ = run_signals([sig], {"A": clean}, replace(BASE, rule=RULES["trail_s50"]))
    assert t.exit_kind == "TIME_EXIT" and t.exit_day == clean[61 + 120].day


def _days(n: int) -> list[date]:
    return [date(2023, 1, 2) + timedelta(days=i) for i in range(n)]


def test_breadth_regime() -> None:
    rows = [DayBreadth(d, 100, a, 0.0) for d, a in zip(_days(3), (39, 40, 80), strict=True)]
    rows.append(DayBreadth(date(2023, 2, 1), 49, 49, 0.0))  # too few members
    assert list(breadth_regime(rows).values()) == [False, True, True, False]


def test_ew_regime_follows_the_index_against_its_average() -> None:
    days = _days(70)
    rows = [DayBreadth(d, 100, 50, 0.01 if i < 60 else -0.03) for i, d in enumerate(days)]
    reg = ew_regime(rows)
    assert not any(reg[d] for d in days[:49])  # fewer than 50 values
    assert reg[days[49]] and reg[days[59]]  # rising index above its average
    assert not reg[days[69]]  # ten 3 % drops: below its average
    assert regime_by_day("none", rows) is None
    assert regime_by_day("ew50", rows) == reg
    with pytest.raises(ValueError):
        regime_by_day("nifty", rows)
