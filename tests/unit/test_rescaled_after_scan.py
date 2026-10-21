"""A corporate action entered after the newest scan flags the stock (owner, 2026-10-21)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from vcp_scanner.api.queries import rescaled_after_scan

SCAN = "2026-10-19 14:20:00+00"


def _db(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.execute("SET TimeZone = 'UTC'")
    con.execute("CREATE TABLE instruments (instrument_id VARCHAR, symbol VARCHAR)")
    con.execute("CREATE TABLE scan_runs (as_of_date DATE, started_at TIMESTAMPTZ, status VARCHAR)")
    con.execute(
        "CREATE TABLE corporate_action_adjustments (instrument_id VARCHAR, effective_date DATE,"
        " price_factor DECIMAL(18,8), known_from TIMESTAMPTZ, known_to TIMESTAMPTZ)"
    )
    con.execute("INSERT INTO instruments VALUES ('I|A', 'A'), ('I|B', 'B'), ('I|C', 'C')")
    con.execute(
        f"INSERT INTO scan_runs VALUES ('2026-10-16', '2026-10-16 14:20:00+00', 'COMPLETED'),"
        f" ('2026-10-19', '{SCAN}', 'COMPLETED')"
    )
    rows = [
        # A: rebuilt after the scan with the same factor -> not flagged
        ("I|A", "2024-09-11", 0.95, "2026-10-01 00:00:00+00", "2026-10-19 16:31:00+00"),
        ("I|A", "2024-09-11", 0.95, "2026-10-19 16:31:00+00", None),
        # B: a consolidation entered after the scan -> flagged
        ("I|B", "2024-09-11", 0.5, "2026-10-01 00:00:00+00", "2026-10-19 16:31:00+00"),
        ("I|B", "2024-09-11", 0.5, "2026-10-19 16:31:00+00", None),
        ("I|B", "2026-10-13", 10.0, "2026-10-19 16:31:00+00", None),
        # C: an action effective after the scan day -> not the scan's business
        ("I|C", "2026-10-20", 2.0, "2026-10-19 16:31:00+00", None),
    ]
    con.executemany("INSERT INTO corporate_action_adjustments VALUES (?, ?, ?, ?, ?)", rows)
    return con


def test_only_changed_factors_up_to_the_scan_day_are_flagged(tmp_path: Path) -> None:
    con = _db(tmp_path)
    day, stale = rescaled_after_scan(con.cursor())
    assert day == date(2026, 10, 19)
    assert stale == {"I|B": "B"}


def test_the_next_scan_clears_the_flag(tmp_path: Path) -> None:
    con = _db(tmp_path)
    con.execute(
        "INSERT INTO scan_runs VALUES ('2026-10-21', '2026-10-21 14:20:00+00', 'COMPLETED')"
    )
    assert rescaled_after_scan(con.cursor()) == (date(2026, 10, 21), {})
