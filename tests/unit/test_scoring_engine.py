"""Scoring a scan: eligibility, ranking, storage (SCORING_SPECIFICATION 1, 11; Phase 7 step 3)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from vcp_scanner.config.models import ScoringConfig
from vcp_scanner.data.repositories.duckdb_score_repository import (
    DuckDBScoreRepository,
    score_results_hash,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.scoring.engine import PatternInputs, SetupInputs, score_scan, score_setup

D = date(2026, 9, 30)
CFG = ScoringConfig()
PAT = PatternInputs("VCP", "FORMING", "CONFIRMED", 3, 0.85, 9.0, 0.75, 5.0, 27.5, 0.70)


def _inputs(iid: str = "A", pattern: PatternInputs | None = PAT, rs: float = 84.5) -> SetupInputs:
    closes = [100.0 + (i % 2) for i in range(76)]
    volumes: list[float | None] = [1000.0] * 76
    return SetupInputs(iid, D, rs, 90.0, 100.0, 110.0, 100.0, 96.1538461538, closes, volumes,
                       pattern)  # fmt: skip


def test_score_setup_combines_all_components() -> None:
    s = score_setup(_inputs(), CFG, 70)
    by = {c.component: c.score for c in s.components}
    assert by["TREND"] == pytest.approx(54.0)
    assert by["VCP"] == pytest.approx(50.0)
    assert by["RS"] == pytest.approx(50.0)
    # Volume: dry-up 0.70 -> 50; up/down 1.0 (equal volumes) -> 28.6; no distribution -> 100.
    assert by["VOLUME"] == pytest.approx((50 * 50 + 25 * (0.2 / 0.7 * 100) + 25 * 100) / 100)
    assert s.final.final == pytest.approx(
        (25 * by["TREND"] + 35 * 50 + 15 * by["VOLUME"] + 15 * 50) / 90  # type: ignore[operator]
    )
    assert s.final.flags == ("FUNDAMENTALS_UNAVAILABLE",) and s.eligible


def test_passer_without_pattern_is_scored_but_not_ranked() -> None:
    s = score_setup(_inputs(pattern=None), CFG, 70)
    by = {c.component: c.score for c in s.components}
    assert by["VCP"] is None and s.final.final is not None and not s.eligible
    assert "VCP_UNAVAILABLE" in s.final.flags


def test_only_eligible_setups_are_ranked_per_state() -> None:
    inputs = [
        _inputs("A", rs=99), _inputs("B", rs=80),
        _inputs("C", replace(PAT, classification="VCP_LIKE"), rs=90),
        _inputs("D", replace(PAT, status="INVALIDATED"), rs=99),  # broken: not ranked
        _inputs("E", replace(PAT, classification="NONE"), rs=99),  # no VCP class
        _inputs("F", replace(PAT, confirmation_state="PROVISIONAL"), rs=75),
        _inputs("G", None, rs=99),
    ]  # fmt: skip
    out = {s.instrument_id: s for s in score_scan(inputs, CFG, 70)}
    assert [k for k, s in out.items() if s.eligible] == ["A", "B", "C", "F"]
    assert out["A"].ranking_percentile == 100.0 and out["B"].ranking_percentile == 0.0
    assert out["C"].ranking_percentile == 50.0
    assert out["F"].ranking_percentile == 100.0  # alone among provisional patterns
    assert all(out[k].ranking_percentile is None for k in "DEG")
    assert out["D"].final.final is not None  # still scored, for research


def test_save_scan_round_trip_and_rerun_replaces() -> None:
    scored = score_scan([_inputs("A", rs=99), _inputs("G", None)], CFG, 70)
    now = datetime(2026, 10, 3, tzinfo=UTC)
    with DuckDBStore(":memory:") as store:
        store.migrate()
        repo = DuckDBScoreRepository(store)
        repo.save_scan("score-x", scored, "scoring-1.0.0", "h" * 64, now)
        repo.save_scan("score-x", scored, "scoring-1.0.0", "h" * 64, now)
        q = store.conn.execute
        rows = q("SELECT instrument_id, eligible, final_setup_score, ranking_percentile, flags,"
                 " vcp_weight, fundamental_weight, fundamental_score FROM setup_scores"
                 " ORDER BY 1").fetchall()  # fmt: skip
        assert len(rows) == 2
        a, g = rows
        assert a[1] is True and a[3] == 100.0 and a[4] == "FUNDAMENTALS_UNAVAILABLE"
        assert a[5] == pytest.approx(35 / 90 * 100) and a[6] == 0.0 and a[7] is None
        assert g[1] is False and g[3] is None and "VCP_UNAVAILABLE" in g[4]
        n = q("SELECT count(*) FROM score_components WHERE scan_id = 'score-x'").fetchone()
        assert n == (2 * (3 + 6 + 3 + 1),)
        nulls = q("SELECT count(*) FROM score_components WHERE instrument_id = 'G'"
                  " AND normalized_0_100 IS NULL").fetchone()  # fmt: skip
        assert nulls == (7,)  # 6 VCP sub-components + dry-up
    assert score_results_hash(scored) == score_results_hash(list(reversed(scored)))
