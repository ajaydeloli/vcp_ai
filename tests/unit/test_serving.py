"""The serving copy for the dashboard API (FRONTEND_SPECIFICATION 67.2)."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from vcp_scanner import daily
from vcp_scanner.daily import run_daily
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.serving import SERVING_NAME, default_serving_path, refresh_serving_copy

T0 = datetime(2026, 9, 25, 13, tzinfo=UTC)


def _db(tmp_path: Path) -> str:
    db = str(tmp_path / "vcp.duckdb")
    with DuckDBStore(db) as store:
        store.migrate()
        store.conn.execute(
            "INSERT INTO bhavcopy_files (trade_date, sha256, status, url, file_format,"
            " recorded_at) VALUES (?, 'x', 'OK', 'u', 'CM_UDIFF', ?)",
            [date(2026, 9, 25), T0],
        )
    return db


def _files(path: Path) -> int:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return int(con.execute("SELECT count(*) FROM bhavcopy_files").fetchone()[0])  # type: ignore[index]
    finally:
        con.close()


def test_default_path_is_next_to_the_database(tmp_path: Path) -> None:
    assert default_serving_path(tmp_path / "vcp.duckdb") == tmp_path / "serving" / SERVING_NAME


def test_refresh_makes_a_readable_copy_and_no_partial_file(tmp_path: Path) -> None:
    db = _db(tmp_path)
    out = refresh_serving_copy(db)
    assert out == tmp_path / "serving" / SERVING_NAME
    assert _files(out) == 1
    assert list(out.parent.glob("*.partial")) == []


def test_refresh_replaces_the_old_copy_with_the_new_data(tmp_path: Path) -> None:
    db = _db(tmp_path)
    out = refresh_serving_copy(db)
    with DuckDBStore(db) as store:
        store.conn.execute(
            "INSERT INTO bhavcopy_files (trade_date, sha256, status, url, file_format,"
            " recorded_at) VALUES (?, 'y', 'OK', 'u', 'CM_UDIFF', ?)",
            [date(2026, 9, 28), T0],
        )
    assert _files(out) == 1  # the copy is a snapshot until refreshed
    refresh_serving_copy(db)
    assert _files(out) == 2


def test_a_failed_refresh_leaves_the_previous_copy(tmp_path: Path) -> None:
    db = _db(tmp_path)
    out = refresh_serving_copy(db)
    before = out.stat().st_mtime_ns
    with pytest.raises(RuntimeError, match="health check"):
        refresh_serving_copy(tmp_path / "missing.duckdb", out)
    assert out.stat().st_mtime_ns == before and _files(out) == 1


def _args(db: str, **extra: object) -> argparse.Namespace:
    return argparse.Namespace(
        db=db, config_dir="config", env_file=".env", history_start="2021-01-01", **extra
    )


def test_the_daily_run_writes_no_serving_copy_unless_asked(tmp_path: Path) -> None:
    db = _db(tmp_path)
    run_daily(_args(db), lambda argv: 0)
    assert not (tmp_path / "serving").exists()


def test_the_daily_run_refreshes_the_serving_copy_last(tmp_path: Path) -> None:
    db = _db(tmp_path)
    order: list[str] = []

    def fake_main(argv: list[str]) -> int:
        order.append(argv[0])
        return 0

    assert run_daily(_args(db, serving_copy=True), fake_main) == 0
    assert _files(tmp_path / "serving" / SERVING_NAME) == 1
    log = (tmp_path / "logs" / "daily_runs.log").read_text()
    assert "all steps OK" in log


def test_a_failed_serving_copy_fails_the_run_and_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _db(tmp_path)

    def boom(db: object, target: object = None) -> Path:
        raise RuntimeError("disk full")

    monkeypatch.setattr(daily, "refresh_serving_copy", boom)
    assert run_daily(_args(db, serving_copy=True), lambda argv: 0) == 1
    assert "FAILED: serving copy" in (tmp_path / "logs" / "daily_runs.log").read_text()
