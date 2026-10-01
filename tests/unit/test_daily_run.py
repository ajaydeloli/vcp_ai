"""`vcp run daily`: catch-up after skipped evenings (daily run)."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

from vcp_scanner.daily import run_daily, sessions_to_scan
from vcp_scanner.data.storage.duckdb_store import DuckDBStore

T0 = datetime(2026, 9, 25, 13, tzinfo=UTC)


def _db(tmp_path: Path, ok_days: list[date], scanned: date | None) -> str:
    db = str(tmp_path / "vcp.duckdb")
    with DuckDBStore(db) as store:
        store.migrate()
        for d in ok_days:
            store.conn.execute(
                "INSERT INTO bhavcopy_files (trade_date, sha256, status, url, file_format,"
                " recorded_at) VALUES (?, ?, 'OK', 'u', 'CM_UDIFF', ?)",
                [d, d.isoformat(), T0],
            )
        if scanned is not None:
            store.conn.execute(
                "INSERT INTO trend_template_results (scan_id, instrument_id, as_of_date, status,"
                " trend_template_pass, calculation_version, config_hash, data_snapshot_id)"
                " VALUES ('s', 'X', ?, 'FAIL', FALSE, '1', 'h', 'LIVE')",
                [scanned],
            )
    return db


def test_first_run_scans_only_the_latest_session(tmp_path: Path) -> None:
    db = _db(tmp_path, [date(2026, 9, 28), date(2026, 9, 29)], None)
    assert sessions_to_scan(db) == [date(2026, 9, 29)]


def test_skipped_evenings_are_all_scanned(tmp_path: Path) -> None:
    days = [date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28), date(2026, 9, 29)]
    db = _db(tmp_path, days, scanned=date(2026, 9, 24))
    assert sessions_to_scan(db) == days[1:]


def test_run_daily_chains_the_steps_from_the_last_settled_day(tmp_path: Path) -> None:
    db = _db(tmp_path, [date(2026, 9, 24), date(2026, 9, 25)], scanned=date(2026, 9, 24))
    calls: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    args = argparse.Namespace(
        db=db, config_dir="config", env_file=".env", history_start="2021-01-01"
    )
    code = run_daily(args, fake_main)
    names = [" ".join(a[:2]) for a in calls]
    assert names[:5] == [
        "ingest security-master",
        "ingest bhavcopy",
        "ingest corporate-actions",
        "ingest adjusted-prices",
        "compute features",
    ]
    assert calls[1][calls[1].index("--start") + 1] == "2026-09-26"  # day after last settled
    # 2026-09-25 had prices but no scan: universe, RS and Trend Template for it.
    assert names[5:] == ["ingest universe", "compute rs", "compute trend-template"]
    assert all("2026-09-25" in a for a in calls[5:])
    # The fake security-master step collected nothing, so the summary says so; steps passed.
    assert code == 0
    log = (tmp_path / "logs" / "daily_runs.log").read_text()
    assert "surveillance lists collected: NONE" in log and "all steps OK" in log


def test_a_failed_step_fails_the_run_but_later_steps_still_run(tmp_path: Path) -> None:
    db = _db(tmp_path, [date(2026, 9, 25)], scanned=date(2026, 9, 25))
    calls: list[str] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv[1])
        return 1 if argv[1] == "security-master" else 0

    args = argparse.Namespace(
        db=db, config_dir="config", env_file=".env", history_start="2021-01-01"
    )
    assert run_daily(args, fake_main) == 1
    assert "features" in calls
