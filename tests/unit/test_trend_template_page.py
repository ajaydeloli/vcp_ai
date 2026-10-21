"""The Trend Template page's numbers (owner, 2026-10-21), on a small two-day database."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb

from vcp_scanner.api.trend_template import trend_template

D1, D2 = date(2026, 10, 19), date(2026, 10, 21)
NAMES = ["close_above_sma50", "near_52w_high", "rs_rank_min"]
NOW = datetime(2026, 10, 21, 17, tzinfo=UTC)


def _db(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.execute(
        "CREATE TABLE trend_template_results (instrument_id VARCHAR, as_of_date DATE,"
        " status VARCHAR, trend_template_pass BOOLEAN, weekly_stage VARCHAR, rs_rank INTEGER,"
        " config_hash VARCHAR, data_snapshot_id VARCHAR)"
    )
    con.execute(
        "CREATE TABLE trend_template_conditions (instrument_id VARCHAR, as_of_date DATE,"
        " condition_id INTEGER, condition_name VARCHAR, measurement DOUBLE, threshold DOUBLE,"
        " passed BOOLEAN, config_hash VARCHAR, data_snapshot_id VARCHAR)"
    )
    con.execute(
        "CREATE TABLE instruments (instrument_id VARCHAR, symbol VARCHAR, company_name VARCHAR,"
        " valid_from DATE)"
    )
    con.execute(
        "CREATE TABLE daily_prices_adjusted_current (instrument_id VARCHAR, trade_date DATE,"
        " close_adj DOUBLE, computed_from_snapshot_id VARCHAR)"
    )
    return con


def _stock(con: Any, sym: str, day: date, stage: str, rs: int, fails: set[str]) -> None:
    iid = f"NSE_EQ|{sym}"
    con.execute(
        "INSERT INTO trend_template_results VALUES (?, ?, ?, ?, ?, ?, 'h', 'LIVE')",
        [iid, day, "FAIL" if fails else "PASS", not fails, stage, rs],
    )
    for k, name in enumerate(NAMES, 1):
        con.execute(
            "INSERT INTO trend_template_conditions VALUES (?, ?, ?, ?, 1.0, 2.0, ?, 'h', 'LIVE')",
            [iid, day, k, name, name not in fails],
        )
    con.execute(
        "INSERT INTO daily_prices_adjusted_current VALUES (?, ?, 100.0, 'LIVE')", [iid, day]
    )


def test_counts_new_dropped_nearly_and_stage_moves(tmp_path: Path) -> None:
    con = _db(tmp_path)
    for sym in ("NEW", "KEEP", "DROP", "NEAR", "FAR"):
        con.execute(
            "INSERT INTO instruments VALUES (?, ?, ?, NULL)", [f"NSE_EQ|{sym}", sym, sym.title()]
        )
    _stock(con, "NEW", D1, "STAGE_1", 80, {"near_52w_high"})
    _stock(con, "KEEP", D1, "STAGE_2", 90, set())
    _stock(con, "DROP", D1, "STAGE_2", 70, set())
    _stock(con, "NEAR", D1, "STAGE_2", 95, {"close_above_sma50"})
    _stock(con, "FAR", D1, "STAGE_4", 10, set(NAMES))
    _stock(con, "NEW", D2, "STAGE_2", 85, set())
    _stock(con, "KEEP", D2, "STAGE_2", 90, set())
    _stock(con, "DROP", D2, "STAGE_2", 60, {"close_above_sma50"})
    _stock(con, "NEAR", D2, "STAGE_2", 96, {"close_above_sma50"})
    _stock(con, "FAR", D2, "STAGE_4", 10, set(NAMES))

    got = trend_template(con.cursor(), SimpleNamespace(scan_hash="h"), D2, NOW)  # type: ignore[arg-type]

    assert (got.previous, got.week_ago) == (D1, D1)
    assert (got.population, got.passing, got.passing_previous) == (5, 2, 2)
    sma50 = next(c for c in got.conditions if c.name == "close_above_sma50")
    assert (sma50.passed, sma50.only_fail) == (2, 2)  # DROP and NEAR fail only this one
    assert [s.symbol for s in got.new_passers] == ["NEW"]
    assert [(s.symbol, [f.name for f in s.failed]) for s in got.dropped] == [
        ("DROP", ["close_above_sma50"])
    ]
    assert [s.symbol for s in got.nearly] == ["NEAR", "DROP"] and got.nearly_total == 2  # RS order
    assert [(s.symbol, s.previous_stage) for s in got.into_stage2] == [("NEW", "STAGE_1")]
    assert got.new_passers[0].company == "New" and got.new_passers[0].close == 100.0
    everyone = got.stages[0]
    assert everyone.sector == "All" and everyone.counts == {"STAGE_2": 4, "STAGE_4": 1}
