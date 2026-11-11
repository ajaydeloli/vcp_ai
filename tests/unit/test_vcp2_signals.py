"""Daily VCP v2 candidates as backtest signals (research/vcp2_signals.py)."""

from __future__ import annotations

from datetime import date, timedelta

from vcp_scanner.backtest.engine import Bar, EngineConfig, run_signals
from vcp_scanner.research.outcomes import ENGINE_RULES
from vcp_scanner.research.vcp2_signals import CandidateDay, daily_signals

D0 = date(2020, 1, 1)
DAYS = [D0 + timedelta(days=k) for k in range(200)]


def cand(k: int, pivot: float = 100.0, iid: str = "A") -> CandidateDay:
    return CandidateDay(iid, DAYS[k], pivot, 90.0, None)


def test_a_run_of_candidate_days_with_one_pivot_is_one_signal() -> None:
    sigs = daily_signals([cand(k) for k in range(10, 20)], DAYS)
    assert [s.scan_date for s in sigs] == [DAYS[10]]


def test_a_new_pivot_is_a_new_signal() -> None:
    rows = [cand(k) for k in range(10, 15)] + [cand(k, 104.0) for k in range(15, 20)]
    assert [s.scan_date for s in daily_signals(rows, DAYS)] == [DAYS[10], DAYS[15]]


def test_a_gap_starts_again() -> None:
    rows = [cand(k) for k in (10, 11, 12, 14, 15)]  # not a candidate on day 13
    assert [s.scan_date for s in daily_signals(rows, DAYS)] == [DAYS[10], DAYS[14]]


def test_a_long_base_is_rearmed() -> None:
    sigs = daily_signals([cand(k) for k in range(10, 55)], DAYS, rearm_sessions=20)
    assert [s.scan_date for s in sigs] == [DAYS[10], DAYS[30], DAYS[50]]


def test_stocks_are_independent() -> None:
    rows = [cand(k) for k in range(10, 13)] + [cand(k, iid="B") for k in range(11, 13)]
    sigs = daily_signals(rows, DAYS)
    assert [(s.instrument_id, s.scan_date) for s in sigs] == [("A", DAYS[10]), ("B", DAYS[11])]


def test_the_engine_enters_on_a_day_the_stock_is_still_a_candidate() -> None:
    # candidate days 60..70 with pivot 100; the close crosses 100 on day 65 while v2 still
    # calls it a candidate (the pivot is under the base high). One signal (day 60), so the
    # engine watches from day 61 and enters on day 65.
    bars = [Bar(d, 99.0, 97.0, 98.0, 1000.0) for d in DAYS]
    bars[65] = Bar(DAYS[65], 102.0, 99.0, 101.0, 1000.0)
    sigs = daily_signals([cand(k) for k in range(60, 71)], DAYS)
    rule = next(r for r in ENGINE_RULES if r.name == "hold_low8")
    trades, _ = run_signals(sigs, {"A": bars}, EngineConfig(rule, min_volume_ratio=0.0))
    assert [(t.entry_day, t.entry, t.exit_kind) for t in trades] == [(DAYS[65], 101.0,
                                                                      "TIME_EXIT")]  # fmt: skip
    # a signal on every candidate day (no de-duplication) would never be entered
    every = [s for k in range(60, 71) for s in daily_signals([cand(k)], DAYS)]
    assert run_signals(every, {"A": bars}, EngineConfig(rule, min_volume_ratio=0.0))[0] == []
