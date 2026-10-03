"""Look-ahead check comparison (backtest/lookahead.py; Phase 9 step 5)."""

from __future__ import annotations

from datetime import date

import duckdb

from vcp_scanner.backtest.lookahead import compare


def _db(rank: float) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE trend_template_results (scan_id VARCHAR, instrument_id VARCHAR,"
        " status VARCHAR, trend_template_pass BOOLEAN, weekly_stage VARCHAR, rs_rank DOUBLE)"
    )
    c.execute(
        "CREATE TABLE vcp_patterns (scan_id VARCHAR, instrument_id VARCHAR,"
        " classification VARCHAR, status VARCHAR, pivot_price DOUBLE,"
        " contraction_count INTEGER, is_primary BOOLEAN)"
    )
    c.execute(
        "CREATE TABLE setup_scores (scan_id VARCHAR, instrument_id VARCHAR, eligible"
        " BOOLEAN, final_setup_score DOUBLE, ranking_percentile DOUBLE)"
    )
    c.execute("INSERT INTO trend_template_results VALUES ('trend-2024-01-05-h', 'A', 'PASS',"
              " true, 'STAGE_2', ?), ('trend-2024-01-05-h', 'B', 'FAIL', false, 'STAGE_2', 40)",
              [rank])  # fmt: skip
    return c


def test_compare_counts_differences_per_table() -> None:
    same = compare(_db(80.0), _db(80.0), date(2024, 1, 5), "h")
    assert [(d.name, d.compared, d.differing) for d in same] == [
        ("trend_template", 2, 0), ("vcp", 0, 0), ("scores", 0, 0)]  # fmt: skip
    diff = compare(_db(80.0), _db(81.0), date(2024, 1, 5), "h")
    assert diff[0].differing == 1 and diff[0].examples[0].startswith("A: ")
    # Float noise below 6 decimals is not a difference.
    assert compare(_db(80.0), _db(80.0000001), date(2024, 1, 5), "h")[0].differing == 0
