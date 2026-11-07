"""Research paper ledgers (paper/research.py; owner, 2026-11-07; synthetic bars)."""

from __future__ import annotations

from datetime import date

from tests.unit.test_backtest_engine import _set
from tests.unit.test_paper_ledger import CFG, _derive, _setup
from vcp_scanner.paper.ledger import RULE_SET
from vcp_scanner.paper.research import RESEARCH_LEDGERS, research_ledgers


def test_the_sma50_ledger_is_fixed_and_apart_from_the_frozen_rules() -> None:
    r = research_ledgers("vcp")[0]
    assert r.rule_set == "research-sma50-v1" and r.rule_set != RULE_SET
    assert (r.start, r.decided) == (date(2026, 10, 1), date(2026, 11, 7))
    assert r.max_above_sma50_pct == 10.0 and research_ledgers("flat_base") == []
    assert len({x.rule_set for x in RESEARCH_LEDGERS}) == len(RESEARCH_LEDGERS)
    eng = r.engine(CFG)
    assert eng.max_above_sma50_pct == 10.0 and CFG.max_above_sma50_pct is None  # frozen untouched
    assert (eng.rule, eng.entry, eng.regime, eng.max_positions, eng.cost_bps) == (
        CFG.rule, CFG.entry, CFG.regime, CFG.max_positions, CFG.cost_bps
    )


def test_the_research_ledger_skips_an_extended_entry_the_frozen_one_takes() -> None:
    bs, sigs = _setup()
    for i in range(15, 65):
        _set(bs, i, close=95.0)  # a lower base, so the cross sits far above the 50-day average
    _set(bs, 65, close=108.0, volume=2000.0)  # a cross of 104 (+3.8 %), ~13.4 % above the average
    frozen = [e for e in _derive(bs, sigs, 100) if e.event_type == "ENTRY"]
    assert [e.instrument_id for e in frozen] == ["A"]
    research = _derive(bs, sigs, 100, research_ledgers("vcp")[0].engine(CFG))
    assert [e for e in research if e.event_type == "ENTRY"] == []
    assert sum(e.event_type == "DAY_CLOSED" for e in research) == 51  # days still closed
