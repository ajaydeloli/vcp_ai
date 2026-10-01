"""Pre-run health check and rolling backups for `vcp run daily` (owner decision 2026-10-02)."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from vcp_scanner.backup import (
    backup_database,
    check_database,
    list_backups,
    restore_hint,
)
from vcp_scanner.daily import run_daily

T0 = datetime(2026, 10, 2, 13, 45, tzinfo=UTC)


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "vcp_scanner.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t AS SELECT range AS i FROM range(1000)")
    con.close()
    return path


def test_healthy_database_passes_and_garbage_fails(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert check_database(db) is None
    bad = tmp_path / "bad.duckdb"
    bad.write_bytes(b"not a database" * 100)
    assert check_database(bad) is not None
    assert "not found" in (check_database(tmp_path / "missing.duckdb") or "")


def test_backup_is_a_working_copy_and_only_the_newest_are_kept(tmp_path: Path) -> None:
    db = _db(tmp_path)
    backups = tmp_path / "backups"
    made = [backup_database(db, backups, keep=3, now=T0 + timedelta(hours=h)) for h in range(5)]
    kept = list_backups(backups, "vcp_scanner")
    assert kept == made[:1:-1]  # newest three, newest first
    con = duckdb.connect(str(kept[0]), read_only=True)
    assert con.execute("SELECT count(*) FROM t").fetchone() == (1000,)
    con.close()
    assert not list(backups.glob("*.partial"))


def test_uncheckpointed_writes_are_in_the_backup(tmp_path: Path) -> None:
    """The WAL is folded in before copying, so recent writes are not lost."""
    db = _db(tmp_path)
    con = duckdb.connect(str(db))
    con.execute("INSERT INTO t VALUES (424242)")
    con.close()
    out = backup_database(db, tmp_path / "b", now=T0)
    con = duckdb.connect(str(out), read_only=True)
    assert con.execute("SELECT count(*) FROM t WHERE i = 424242").fetchone() == (1,)
    con.close()


def test_leftover_partial_copy_is_removed_and_never_listed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "vcp_scanner_20261001_000000.duckdb.partial").write_bytes(b"half")
    assert list_backups(backups, "vcp_scanner") == []
    backup_database(db, backups, now=T0)
    assert not list(backups.glob("*.partial"))


def test_damaged_database_is_refused_with_a_restore_hint(tmp_path: Path) -> None:
    db = _db(tmp_path)
    backups = tmp_path / "backups"
    good = backup_database(db, backups, now=T0)
    db.write_bytes(b"\x00" * 4096)
    with pytest.raises(RuntimeError, match="health check"):
        backup_database(db, backups, now=T0 + timedelta(days=1))
    hint = restore_hint(db, backups)
    assert str(good) in hint and "cp " in hint
    assert list_backups(backups, "vcp_scanner") == [good]  # the good copy is not rotated out


def test_daily_run_stops_before_any_step_on_a_damaged_database(tmp_path: Path) -> None:
    db = tmp_path / "vcp_scanner.duckdb"
    db.write_bytes(b"\x00" * 4096)
    calls: list[list[str]] = []

    def fake_main(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    args = argparse.Namespace(
        db=str(db), config_dir="config", env_file=".env", history_start="2021-01-01"
    )
    assert run_daily(args, fake_main) == 1
    assert calls == []
    log = (tmp_path / "logs" / "daily_runs.log").read_text()
    assert "FAILED: database check/backup" in log
