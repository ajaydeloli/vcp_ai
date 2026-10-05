"""Storage and scoring of file-configured strategies (STRATEGY_SPECIFICATION 9.2, 12B)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from vcp_scanner.data.repositories.duckdb_strategy_repository import (
    DuckDBStrategyRepository,
    event_id,
    results_hash,
    setup_id,
    verdict_row,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.strategy import Breakout, Setup, StrategyResult
from vcp_scanner.patterns.registry import load_runtime
from vcp_scanner.scoring.engine import GenericPatternInputs, SetupInputs, score_setup

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
D1, D2 = date(2026, 9, 30), date(2026, 10, 1)
H = "c4918be30855" + "0" * 52


def _setup(iid: str, as_of: date, status: str = "FORMING", breakout: Breakout | None = None,
           grade: int = 2) -> Setup:  # fmt: skip
    return Setup(
        instrument_id=iid, as_of_date=as_of, base_start=date(2026, 8, 3), base_end=None,
        base_high=121.0, base_low=110.0, base_depth_pct=9.09, base_duration_days=40,
        prior_advance_pct=45.0, pivot_price=121.121, pivot_date=date(2026, 8, 3),
        pivot_source="BASE_HIGH", pivot_distance_pct=2.0, stop_reference_price=114.0,
        dryup_volume_ratio=0.7, classification="FLAT_BASE" if grade == 2 else "FLAT_BASE_LIKE",
        grade=grade, status=status, confirmation_state="CONFIRMED", trend_gate="PASS",
        weekly_stage2_pass=True, breakout=breakout, unmet_rules={"TIGHT_FLAT_BASE": ["x"]},
        details={"weeks": 8}, measures={"depth": 9.09, "weekly_tightness": 4.0},
    )  # fmt: skip


def test_save_scan_writes_setups_events_history_and_replaces_on_rerun() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBStrategyRepository(store, "flat_base")
    r1 = [StrategyResult("A", D1, _setup("A", D1)),
          StrategyResult("B", D1, None, "TOO_DEEP")]  # fmt: skip
    assert repo.save_scan("setup-flat_base-2026-09-30-x", D1, H, "flat_base-1.0.0", r1, NOW) == 0
    bo = Breakout(D2, 121.121, 2.4)
    r2 = [StrategyResult("A", D2, _setup("A", D2, "BREAKOUT", bo))]
    for _ in range(2):  # a rerun replaces the scan's rows and its new events
        assert repo.save_scan("setup-flat_base-2026-10-01-x", D2, H, "flat_base-1.0.0", r2,
                              NOW) == 1  # fmt: skip
    q = store.conn.execute
    assert q("SELECT count(*) FROM strategy_setups").fetchone() == (2,)
    row = q("SELECT setup_id, breakout_event_id, details_json, grade FROM strategy_setups"
            " WHERE as_of_date = ?", [D2]).fetchone()  # fmt: skip
    assert row is not None
    assert row[0] == setup_id("setup-flat_base-2026-10-01-x", "A", date(2026, 8, 3))
    assert row[1] == event_id("flat_base", "A", date(2026, 8, 3), H) and row[3] == 2
    assert json.loads(row[2])["measures"]["depth"] == 9.09
    assert q("SELECT count(*), max(volume_ratio) FROM strategy_breakout_events").fetchone() == (
        1, 2.4)  # fmt: skip
    hist = q("SELECT as_of_date, previous_status, new_status FROM"
             " strategy_setup_status_history ORDER BY 1").fetchall()  # fmt: skip
    assert hist == [(D1, None, "FORMING"), (D2, "FORMING", "BREAKOUT")]
    # The view shows the setups next to VCP's.
    assert q("SELECT count(*) FROM setups WHERE strategy_id = 'flat_base'").fetchone() == (2,)
    # A later date reads the breakout back as history.
    known = repo.breakouts(["A"], date(2026, 10, 5), H)
    assert known == {"A": {date(2026, 8, 3): bo}}


def test_verdicts_and_hash_are_order_independent() -> None:
    rows = [verdict_row(StrategyResult("A", D1, _setup("A", D1))),
            verdict_row(StrategyResult("B", D1, None, "TOO_DEEP"))]  # fmt: skip
    assert rows[1] == ("B", None, None, None, None, None, "TOO_DEEP")
    assert results_hash(rows) == results_hash(rows[::-1])


def test_file_pattern_scoring_uses_the_strategy_parts() -> None:
    rt = load_runtime(ROOT / "config", "flat_base")
    from vcp_scanner.config import load_scanner_config

    scoring = load_scanner_config(ROOT / "config").strategy.scoring
    p = GenericPatternInputs("FLAT_BASE", "PIVOT_READY", "CONFIRMED", 2,
                             {"depth": 10.0, "weekly_tightness": 6.5, "right_side": 5.0,
                              "length": 7.5, "prior_advance": 40.0}, 0.4)  # fmt: skip
    x = SetupInputs("A", D1, 90.0, 100.0, 105.0, 95.0, 90.0, 88.0, [], [], p)
    s = score_setup(x, scoring, 70, rt.scoring)
    comps = {c.component: c for c in s.components}
    assert set(comps) == {"TREND", "PATTERN", "VOLUME", "RS"} and s.eligible
    assert comps["PATTERN"].score == pytest.approx(50.0)  # each sub-component at its midpoint
    dry = next(x for x in comps["VOLUME"].subs if x.name == "dryup_quality")
    assert dry.raw == 0.4  # the setup's own dry-up measure
    assert s.final.effective_weights["PATTERN"] > 0
    grade1 = GenericPatternInputs("FLAT_BASE_LIKE", "FORMING", "CONFIRMED", 1, {}, None)
    assert not rt.scoring.eligible(grade1)  # decision G1: grade 2+ only
