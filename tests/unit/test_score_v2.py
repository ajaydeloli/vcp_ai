"""VCP setup score v2 (paper/score_v2.py; owner approval 2026-11-08; synthetic data)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from tests.api.fixture import put
from tests.unit.test_backtest_engine import _bars, _set
from tests.unit.test_paper_ledger import CFG, _derive
from vcp_scanner.backtest.engine import Signal
from vcp_scanner.data.repositories.duckdb_backtest_repository import DuckDBBacktestRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.paper.research import research_ledgers
from vcp_scanner.paper.score_v2 import PARTS, above_sma50_pct, part_score, rescore, score_v2

APPROVED = {  # the owner's approved table: a changed number is a new version, not an edit
    "right_side_range_pct": (7.0, 16.0),
    "final_contraction_pct": (6.0, 13.0),
    "base_depth_pct": (11.0, 34.0),
    "above_sma50_pct": (1.0, 14.0),
    "stack_margin_pct": (9.0, 39.0),
    "prior_advance_pct": (50.0, 170.0),
}


def test_parts_and_bounds_are_the_approved_ones() -> None:
    assert {p.key: (p.best, p.worst) for p in PARTS} == APPROVED


def test_each_part_is_linear_and_clipped() -> None:
    p = next(x for x in PARTS if x.key == "right_side_range_pct")  # best 7, worst 16
    assert part_score(7.0, p) == 100.0 and part_score(16.0, p) == 0.0
    assert part_score(11.5, p) == pytest.approx(50.0)
    assert part_score(2.0, p) == 100.0 and part_score(40.0, p) == 0.0  # clipped


def test_the_score_is_the_mean_of_the_parts_with_a_value() -> None:
    best = {k: b for k, (b, _) in APPROVED.items()}
    assert score_v2(best) == 100.0
    worst = {k: w for k, (_, w) in APPROVED.items()}
    assert score_v2(worst) == 0.0
    # a worked example (PAGEIND, 2022-03-17): parts 72.4, 50.2, 84.4, 93.1, 80.1, 98.1
    ex = {
        "right_side_range_pct": 9.48, "final_contraction_pct": 9.49, "base_depth_pct": 14.59,
        "above_sma50_pct": 1.9, "stack_margin_pct": 14.96, "prior_advance_pct": 52.25,
    }  # fmt: skip
    assert score_v2(ex) == pytest.approx(79.7, abs=0.05)  # inputs rounded
    # a missing part is left out of the mean; none at all: no score
    assert score_v2({"right_side_range_pct": 7.0, "base_depth_pct": None}) == 100.0
    assert score_v2({}) is None and score_v2(dict.fromkeys(APPROVED)) is None


def test_distance_above_the_50_day_average() -> None:
    bs = _bars(60)  # closes 100
    _set(bs, 59, close=150.0)
    assert above_sma50_pct(bs, bs[59].day) == pytest.approx((150 / 101 - 1) * 100)
    assert above_sma50_pct(bs, bs[40].day) is None  # fewer than 50 bars
    assert above_sma50_pct(bs, date(1990, 1, 1)) is None  # no such bar


def test_rescore_replaces_the_frozen_score_with_v2() -> None:
    bs = _bars(60)
    sig = Signal("A", bs[55].day, 104.0, 90.0, None, "VCP")
    stored = {("A", bs[55].day): {"right_side_range_pct": 16.0, "final_contraction_pct": 13.0}}
    [s] = rescore([sig], stored, {"A": bs})
    # two worst parts (0) and the 50-day distance at its best (0 % above, 100): mean 33.3
    assert s.score == pytest.approx(100 / 3) and (s.pivot, s.classification) == (104.0, "VCP")
    [none_] = rescore([replace(sig, scan_date=bs[10].day)], {}, {"A": bs})
    assert none_.score is None  # no parts at all: no score, ranks last


def test_with_one_free_slot_v2_picks_the_other_breakout() -> None:
    bs = _bars(150)
    _set(bs, 65, close=105.0, volume=2000.0)  # both stocks cross 104 on the same day
    sigs = [Signal("A", bs[60].day, 104.0, 90.0, None, "VCP"),  # frozen score prefers A
            Signal("B", bs[60].day, 104.0, 40.0, None, "VCP")]  # fmt: skip
    one = replace(CFG, max_positions=1)
    frozen = [e.instrument_id for e in _derive(bs, sigs, 100, one) if e.event_type == "ENTRY"]
    assert frozen == ["A"]
    tight = {"right_side_range_pct": 7.0, "final_contraction_pct": 6.0}
    loose = {"right_side_range_pct": 16.0, "final_contraction_pct": 13.0}
    v2 = rescore(sigs, {("A", bs[60].day): loose, ("B", bs[60].day): tight}, {"A": bs, "B": bs})
    picked = [e.instrument_id for e in _derive(bs, v2, 100, one) if e.event_type == "ENTRY"]
    assert picked == ["B"]


def test_the_score2_ledger_is_the_sma50_ledger_with_v2_ranking() -> None:
    sma50, score2 = research_ledgers("vcp")
    assert (score2.rule_set, score2.decided) == ("research-score2-v1", date(2026, 11, 8))
    assert score2.rank_by_score_v2 and not sma50.rank_by_score_v2
    assert score2.engine(CFG) == sma50.engine(CFG) and score2.start == sma50.start


def test_the_repository_reads_the_parts_of_the_right_scans(tmp_path) -> None:
    d = date(2026, 11, 6)
    with DuckDBStore(str(tmp_path / "s.duckdb")) as store:
        store.migrate()
        c = store.conn
        put(c, "setup_scores", scan_id="score-1", strategy_id="vcp", instrument_id="A",
            as_of_date=d, config_hash="h", data_snapshot_id="LIVE")  # fmt: skip
        for comp, sub, v in (("VCP", "pivot", 8.0), ("VCP", "final_contraction", 7.0),
                             ("VCP", "base_structure", 20.0), ("TREND", "ma_stack_margin", 15.0),
                             ("TREND", "sma200_slope", 99.0)):  # fmt: skip
            put(c, "score_components", scan_id="score-1", strategy_id="vcp", instrument_id="A",
                component=comp, sub_component=sub, raw_measurement=v)  # fmt: skip
        # the VCP scan of the same day and config, and one of another config (ignored)
        for sid, h in (("vcp-1", "h"), ("vcp-old", "other")):
            put(c, "scan_runs", scan_run_id=sid, scan_type="VCP", as_of_date=d, scan_id=sid,
                data_snapshot_id="LIVE", scan_config_hash=h, status="COMPLETED")  # fmt: skip
        for sid, adv in (("vcp-1", 80.0), ("vcp-old", 999.0)):
            put(c, "vcp_patterns", vcp_pattern_id=f"p-{sid}", scan_id=sid, instrument_id="A",
                as_of_date=d, is_primary=True, prior_advance_return_pct=adv)  # fmt: skip
        parts = DuckDBBacktestRepository(store, "LIVE").score_v2_parts(d, d, "h")
    assert parts == {("A", d): {"right_side_range_pct": 8.0, "final_contraction_pct": 7.0,
                                "base_depth_pct": 20.0, "stack_margin_pct": 15.0,
                                "prior_advance_pct": 80.0}}  # fmt: skip
