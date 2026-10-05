"""Event engine (backtest/engine.py, metrics.py; Phase 9 step 3)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vcp_scanner.backtest.engine import (
    Bar,
    EngineConfig,
    Signal,
    run_portfolio,
    run_signals,
)
from vcp_scanner.backtest.metrics import equity_stats, trade_stats
from vcp_scanner.research.outcomes import TradeRule

D0 = date(2024, 1, 1)
RULE = TradeRule("t10_s7", 10.0, 7.0)
CFG = EngineConfig(RULE, watch_days=20, horizon=60, cost_bps=0.0, max_positions=2)


def _bars(n: int = 150, price: float = 100.0) -> list[Bar]:
    return [Bar(D0 + timedelta(days=i), price * 1.01, price * 0.99, price, 1000.0)
            for i in range(n)]  # fmt: skip


def _set(bs: list[Bar], i: int, **kw: float) -> None:
    b = bs[i]
    bs[i] = Bar(b.day, kw.get("high", b.high), kw.get("low", b.low), kw.get("close", b.close),
                kw.get("volume", b.volume))  # fmt: skip


def test_breakout_entry_target_and_events() -> None:
    bs = _bars()
    sig = Signal("A", bs[60].day, 104.0, 70.0, None, "VCP")
    _set(bs, 60, close=105.0, volume=5000.0)  # the scan date's own bar is not an entry
    _set(bs, 63, close=103.0, volume=5000.0)  # below the pivot
    _set(bs, 65, close=105.0, high=105.5, volume=2000.0)  # breakout on 2x volume
    _set(bs, 70, high=116.0)  # +10.5 % from 105: target
    trades, events = run_signals([sig], {"A": bs}, CFG)
    assert [e.kind for e in events] == ["ENTRY_SIGNAL", "BREAKOUT", "EXIT_SIGNAL"]
    (t,) = trades
    assert t.entry_day == bs[65].day and t.entry == 105.0
    assert t.exit_kind == "EXIT_SIGNAL" and t.ret_pct == pytest.approx(10.0)


def test_stop_first_costs_and_no_breakout() -> None:
    bs = _bars()
    _set(bs, 65, close=105.0, volume=2000.0)
    _set(bs, 66, high=120.0, low=97.0)  # both on one bar: the stop wins
    cfg = EngineConfig(RULE, cost_bps=15.0)
    (t,), _ = run_signals([Signal("A", bs[60].day, 104.0, None, None, "VCP")], {"A": bs}, cfg)
    assert t.exit_kind == "STOP"
    assert t.ret_pct == pytest.approx(((105 * 0.93 * (1 - 0.0015)) / (105 * 1.0015) - 1) * 100)
    quiet = _bars()
    trades, events = run_signals([Signal("A", quiet[60].day, 104.0, None, None, "VCP")],
                                 {"A": quiet}, CFG)  # fmt: skip
    assert trades == [] and events[-1].kind == "INVALIDATION"
    assert events[-1].day == quiet[80].day and events[-1].meta["reason"] == "NO_BREAKOUT"


def test_time_exit_newer_scan_and_one_position_per_stock() -> None:
    bs = _bars()
    s1 = Signal("A", bs[55].day, 104.0, None, None, "VCP")
    s2 = Signal("A", bs[60].day, 102.0, None, None, "VCP")  # replaces the watch
    _set(bs, 62, close=103.0, volume=2000.0)  # above s2's pivot, not s1's
    s3 = Signal("A", bs[70].day, 101.0, None, None, "VCP")  # in a position: skipped
    rule = TradeRule("hold", None, 7.0)
    trades, events = run_signals([s1, s2, s3], {"A": bs}, EngineConfig(rule, cost_bps=0))
    kinds = [e.kind for e in events]
    assert kinds[:4] == ["ENTRY_SIGNAL", "INVALIDATION", "ENTRY_SIGNAL", "BREAKOUT"]
    assert events[1].meta["reason"] == "REPLACED_BY_NEWER_SCAN"
    assert any(e.meta.get("skipped") == "IN_POSITION" for e in events)
    (t,) = trades
    assert t.exit_kind == "TIME_EXIT" and t.exit_day == bs[122].day
    assert t.ret_pct == pytest.approx((100 / 103 - 1) * 100)


def test_open_at_end_is_not_a_trade() -> None:
    bs = _bars(90)
    _set(bs, 65, close=105.0, volume=2000.0)
    trades, events = run_signals([Signal("A", bs[60].day, 104.0, None, None, "VCP")],
                                 {"A": bs}, CFG)  # fmt: skip
    assert trades == [] and events[-1].kind == "OPEN_AT_END"


def test_portfolio_slots_score_priority_and_equity() -> None:
    bars = {}
    sigs = []
    for k, (iid, score) in enumerate((("A", 50.0), ("B", 90.0), ("C", 70.0))):
        bs = _bars()
        _set(bs, 65, close=105.0, volume=2000.0)
        _set(bs, 70, high=116.0)
        bars[iid] = bs
        sigs.append(Signal(iid, bs[60].day, 104.0, score, None, "VCP"))
        _ = k
    trades, _ = run_signals(sigs, bars, CFG)
    assert len(trades) == 3
    p = run_portfolio(trades, bars, CFG)
    assert {t.instrument_id for t in p.taken} == {"B", "C"} and p.skipped == 1
    assert p.equity[-1][1] == pytest.approx(1.0 + 0.5 * 0.10 + 0.5 * 0.10)
    st = equity_stats(p.equity)
    assert st["total_ret"] == pytest.approx(10.0) and st["max_drawdown"] <= 0  # type: ignore[operator]
    ts = trade_stats(trades)
    assert ts["trades"] == 3 and ts["win_rate"] == 1.0 and ts["profit_factor"] is None


@pytest.mark.parametrize(
    ("final_low", "low", "stopped"),
    [(103.0, 102.0, True),    # setup low 103 x 0.995 = 102.5 (2.4 %) is tighter than 8 %
     (90.0, 97.0, False),     # setup low far away: the 8 % cap (96.6) applies, 97 holds
     (90.0, 96.0, True)],     # ... and 96 breaks it
)  # fmt: skip
def test_hold_low8_uses_the_tighter_of_setup_low_and_8_pct(
    final_low: float, low: float, stopped: bool
) -> None:
    from vcp_scanner.research.outcomes import TRADE_RULES

    rule = next(r for r in TRADE_RULES if r.name == "hold_low8")
    cfg = EngineConfig(rule, watch_days=20, horizon=60, cost_bps=0.0, max_positions=2)
    bs = _bars()
    sig = Signal("A", bs[60].day, 104.0, 70.0, final_low, "FLAT_BASE")
    _set(bs, 65, close=105.0, high=105.5, volume=2000.0)
    _set(bs, 70, low=low)
    (t,) = run_signals([sig], {"A": bs}, cfg)[0]
    assert (t.exit_kind == "STOP") == stopped
    if stopped:
        expected = max(-8.0, (final_low * 0.995 / 105.0 - 1) * 100)
        assert t.ret_pct == pytest.approx(expected)
