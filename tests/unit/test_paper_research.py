"""Research paper ledgers (paper/research.py; owner, 2026-11-07; synthetic bars)."""

from __future__ import annotations

from datetime import date

from tests.unit.test_backtest_engine import _set
from tests.unit.test_paper_ledger import CFG, _derive, _setup
from vcp_scanner.paper.ledger import RULE_SET
from vcp_scanner.paper.research import (
    RECLAIM_INDEX,
    RECLAIM_RUN,
    RECLAIM_SMA,
    RESEARCH_LEDGERS,
    long_uptrend_days,
    research_ledgers,
)


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


# ---- research-reclaim-v1 (owner, 2026-11-09) ----------------------------------------------


def _index(n: int, dips: tuple[int, ...] = ()) -> list[tuple[date, float]]:
    """``n`` index sessions on consecutive days, rising 1 a day; a dip far below the average
    on each session in ``dips``."""
    from datetime import timedelta

    d0 = date(2026, 1, 1)
    return [(d0 + timedelta(days=k), 50.0 if k in dips else 100.0 + k) for k in range(n)]


def test_the_reclaim_ledger_is_fixed_on_the_frozen_rules() -> None:
    r = next(x for x in RESEARCH_LEDGERS if x.rule_set == "research-reclaim-v1")
    assert (r.strategy_id, r.start, r.decided) == ("vcp", date(2026, 10, 1), date(2026, 11, 9))
    assert r.fresh_reclaim_only and r.max_above_sma50_pct is None and not r.rank_by_score_v2
    assert (RECLAIM_INDEX, RECLAIM_SMA, RECLAIM_RUN) == ("Nifty 500", 50, 21)


def test_long_uptrend_needs_21_sessions_above_the_50_day_before_the_day() -> None:
    closes = _index(120)
    days = [d for d, _ in closes]
    up = long_uptrend_days(closes, days)
    # the average exists from session 49; 21 sessions above it are complete at session 69,
    # so session 70 is the first day judged a long uptrend (the day itself is not read)
    assert [up[d] for d in days[68:72]] == [False, False, True, True]
    # a dip below the average restarts the count: blocked again only 21 sessions later
    closes = _index(160, dips=(100,))
    up = long_uptrend_days(closes, [d for d, _ in closes])
    k = [d for d, _ in closes]
    assert up[k[100]] and not up[k[101]] and not up[k[121]] and up[k[122]]


def test_long_uptrend_lets_the_entry_through_without_enough_history() -> None:
    closes = _index(60)
    assert not any(long_uptrend_days(closes, [d for d, _ in closes]).values())


def test_the_reclaim_engine_closes_regime_days_in_a_long_uptrend_only() -> None:
    from dataclasses import replace

    closes = _index(120)
    days = [d for d, _ in closes]
    frozen = replace(CFG, regime={d: k % 2 == 0 for k, d in enumerate(days)})
    r = next(x for x in RESEARCH_LEDGERS if x.rule_set == "research-reclaim-v1")
    eng = r.engine(frozen, closes)
    assert all(eng.regime[d] == (k % 2 == 0 and k < 70) for k, d in enumerate(days))
    assert frozen.regime[days[80]] is True  # the frozen config is untouched
    assert (eng.rule, eng.entry, eng.max_positions, eng.max_above_sma50_pct) == (
        CFG.rule, CFG.entry, CFG.max_positions, None
    )


def test_the_reclaim_engine_refuses_without_its_inputs() -> None:
    import pytest

    r = next(x for x in RESEARCH_LEDGERS if x.rule_set == "research-reclaim-v1")
    with pytest.raises(ValueError):
        r.engine(CFG, _index(120))  # no frozen regime
    with pytest.raises(ValueError):
        r.engine(CFG, None)


def test_the_reclaim_ledger_skips_an_entry_in_a_long_uptrend_and_takes_it_after_a_dip() -> None:
    from dataclasses import replace
    from datetime import timedelta

    bs, sigs = _setup()
    frozen = replace(CFG, regime={b.day: True for b in bs})
    r = next(x for x in RESEARCH_LEDGERS if x.rule_set == "research-reclaim-v1")

    def index(dip_at: int | None) -> list[tuple[date, float]]:
        early = [(bs[0].day - timedelta(days=j), 90.0 - 0.01 * j) for j in range(100, 0, -1)]
        return early + [(b.day, 50.0 if k == dip_at else 100.0 + k) for k, b in enumerate(bs)]

    entries = [e.instrument_id for e in _derive(bs, sigs, 100, frozen) if e.event_type == "ENTRY"]
    assert entries == ["A"]  # the frozen rules enter on bar 65
    stale = _derive(bs, sigs, 100, r.engine(frozen, index(None)))
    assert [e for e in stale if e.event_type == "ENTRY"] == []
    fresh = _derive(bs, sigs, 100, r.engine(frozen, index(60)))  # below its average 5 days ago
    assert [e.instrument_id for e in fresh if e.event_type == "ENTRY"] == ["A"]
