"""Paper ledger (STRATEGY_SPECIFICATION 21.3; monitoring M3; synthetic bars)."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tests.unit.test_backtest_engine import _bars, _set
from vcp_scanner.backtest.engine import EngineConfig, Signal, run_portfolio, run_signals
from vcp_scanner.data.repositories import duckdb_paper_repository
from vcp_scanner.data.repositories.duckdb_paper_repository import DuckDBPaperRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.paper.ledger import RULE_SET, PaperEvent, derive_events, plan_update
from vcp_scanner.paper.status import summarize
from vcp_scanner.research.outcomes import ENGINE_RULES

LOW8 = next(r for r in ENGINE_RULES if r.name == "hold_low8")
CFG = EngineConfig(LOW8, cost_bps=0.0, entry="cross_5")


def _setup(n: int = 150) -> tuple[list, list[Signal]]:
    bs = _bars(n)  # closes 100, volume 1000
    _set(bs, 65, close=105.0, volume=2000.0)  # a cross of 104 on 2x volume: entry
    sigs = [
        Signal("A", bs[60].day, 104.0, 80.0, None, "FLAT_BASE"),
        Signal("B", bs[61].day, 120.0, 70.0, None, "FLAT_BASE"),
    ]  # never breaks out
    return bs, sigs


def _derive(bs: list, sigs: list[Signal], through_i: int, cfg: EngineConfig = CFG) -> list:
    cut = bs[: through_i + 1]
    bars = {"A": cut, "B": cut}
    days = [b.day for b in cut]
    return derive_events("flat_base", sigs, bars, cfg, bs[50].day, cut[-1].day, days)


def test_entry_open_then_stop_exit() -> None:
    bs, sigs = _setup()
    ev = _derive(bs, sigs, 70)
    kinds = [(e.event_type, e.instrument_id) for e in ev if e.event_type != "DAY_CLOSED"]
    assert ("WATCH", "A") in kinds and ("ENTRY", "A") in kinds and ("WATCH", "B") in kinds
    entry = next(e for e in ev if e.event_type == "ENTRY")
    assert entry.price == 105.0 and entry.meta["stop"] == pytest.approx(105 * 0.92)
    assert not any(e.event_type == "EXIT" for e in ev)  # still open at bar 70
    assert sum(e.event_type == "DAY_CLOSED" for e in ev) == 21  # bars 50 .. 70
    _set(bs, 75, low=90.0)  # below the -8 % stop
    ev = _derive(bs, sigs, 85)  # B watched from bar 61: expires after 20 sessions
    ex = next(e for e in ev if e.event_type == "EXIT")
    assert ex.event_date == bs[75].day and ex.meta["kind"] == "STOP"
    assert ex.meta["entry_day"] == bs[65].day.isoformat()
    assert any(e.event_type == "WATCH_EXPIRED" and e.instrument_id == "B" for e in ev)


def test_open_trades_keep_their_portfolio_slot() -> None:
    bs, _ = _setup()
    _set(bs, 66, close=103.0)
    _set(bs, 67, close=105.0, volume=2000.0)
    sigs = [
        Signal("A", bs[60].day, 104.0, 80.0, None, "X"),
        Signal("C", bs[60].day, 104.0, 70.0, None, "X"),
    ]
    cfg = replace(CFG, max_positions=1)
    trades, _ = run_signals(sigs, {"A": bs[:68], "C": bs[:68]}, cfg, include_open=True)
    assert {t.exit_kind for t in trades} == {"OPEN"}
    port = run_portfolio(trades, {"A": bs[:68], "C": bs[:68]}, cfg)
    assert len(port.taken) == 1 and port.skipped == 1  # the open one is not freed
    ev = derive_events("x", sigs, {"A": bs[:68], "C": bs[:68]}, cfg, bs[50].day, bs[67].day, [])
    assert sorted(e.event_type for e in ev) == ["ENTRY", "SKIPPED_NO_SLOT", "WATCH", "WATCH"]


def test_plan_update_appends_only_after_the_last_closed_day() -> None:
    bs, sigs = _setup()
    first = _derive(bs, sigs, 66)
    p1 = plan_update("flat_base", first, [], bs[66].day)
    assert p1.new == first and p1.divergence is None and p1.last_closed is None
    second = _derive(bs, sigs, 70)
    p2 = plan_update("flat_base", second, first, bs[70].day)
    assert p2.last_closed == bs[66].day and p2.divergence is None
    assert all(e.event_date > bs[66].day for e in p2.new)
    assert sum(e.event_type == "DAY_CLOSED" for e in p2.new) == 4


def test_a_data_fix_is_a_divergence_recorded_once() -> None:
    bs, sigs = _setup()
    stored = _derive(bs, sigs, 70)
    _set(bs, 65, close=104.5, volume=2000.0)  # a later price fix of the entry day
    fixed = _derive(bs, sigs, 72)
    p = plan_update("flat_base", fixed, stored, bs[72].day)
    assert p.divergence is not None and p.divergence.event_type == "DIVERGENCE"
    assert any("ENTRY A" in x and "105.0" in x for x in p.divergence.meta["stored_not_recomputed"])
    again = plan_update("flat_base", fixed, [*stored, p.divergence], bs[72].day)
    assert again.divergence is None  # the same difference is not recorded twice


def test_repository_appends_and_never_changes(tmp_path) -> None:
    bs, sigs = _setup()
    ev = _derive(bs, sigs, 70)
    with DuckDBStore(str(tmp_path / "p.duckdb")) as store:
        store.migrate()
        repo = DuckDBPaperRepository(store)
        assert repo.append(RULE_SET, "h", ev, "abc", datetime(2026, 10, 6, tzinfo=UTC)) == len(ev)
        assert repo.append(RULE_SET, "h", ev, "abc") == 0  # already stored: ignored
        back = repo.events(RULE_SET, "flat_base", "h")
        assert [e.key() for e in back] == [e.key() for e in ev]
        assert repo.events(RULE_SET, "flat_base", "other") == []
    src = inspect.getsource(duckdb_paper_repository).upper()
    assert "UPDATE PAPER_EVENTS" not in src and "DELETE FROM PAPER_EVENTS" not in src


def test_summary_counts_closed_open_and_skipped() -> None:
    d = _bars(3)
    ev = [PaperEvent("x", "A", d[0].day, "ENTRY", 100.0, None, {"stop": 92.0}),
          PaperEvent("x", "A", d[1].day, "EXIT", 110.0, None,
                     {"ret_pct": 10.0, "entry_day": d[0].day.isoformat()}),
          PaperEvent("x", "B", d[1].day, "ENTRY", 50.0, None, {"stop": 46.0}),
          PaperEvent("x", "C", d[1].day, "ENTRY", 20.0, None, {"stop": 18.4}),
          PaperEvent("x", "C", d[2].day, "EXIT", 18.4, None,
                     {"ret_pct": -8.0, "entry_day": d[1].day.isoformat()}),
          PaperEvent("x", "D", d[2].day, "SKIPPED_NO_SLOT", 10.0),
          PaperEvent("x", None, d[2].day, "DAY_CLOSED")]  # fmt: skip
    s = summarize(ev, {"B": 55.0})
    assert s.closed == 2 and s.win_rate == 50.0 and s.avg_ret == 1.0
    assert s.profit_factor == pytest.approx(10 / 8) and s.skipped == 1 and s.through == d[2].day
    (o,) = s.open
    assert o.instrument_id == "B" and o.open_pct == pytest.approx(10.0) and o.stop == 46.0
