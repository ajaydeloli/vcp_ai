"""Audit P1-8c (D1): scan-run records are immutable and their result hash is canonical."""

from __future__ import annotations

import subprocess
from datetime import UTC, date, datetime

import duckdb
import pytest

from vcp_scanner import versioning
from vcp_scanner.data.repositories.duckdb_scan_run_repository import (
    DuckDBScanRunRepository,
    ScanRun,
    results_hash,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore

AT = datetime(2026, 10, 1, 13, tzinfo=UTC)
ROWS = [
    ("NSE_EQ|B", "FAIL", False, "STAGE_3", 41.0, None),
    ("NSE_EQ|A", "PASS", True, "STAGE_2", 91.00000001, None),
    ("NSE_EQ|C", "DATA_QUALITY_BLOCKED", None, None, None, "TRADING_ABSENCE"),
]


def test_results_hash_ignores_row_order_and_float_noise_but_not_verdicts() -> None:
    h = results_hash(ROWS)
    assert h == results_hash(list(reversed(ROWS)))
    assert h == results_hash([*ROWS[:1], ("NSE_EQ|A", "PASS", True, "STAGE_2", 91.0, None),
                              ROWS[2]])  # fmt: skip
    assert h != results_hash([*ROWS[:1], ("NSE_EQ|A", "FAIL", False, "STAGE_2", 91.0, None),
                              ROWS[2]])  # fmt: skip


def _run(run_id: str = "run-1") -> ScanRun:
    return ScanRun(
        scan_run_id=run_id, scan_type="TREND_TEMPLATE", as_of_date=date(2026, 9, 30),
        scan_id="trend-x", data_snapshot_id="LIVE", data_cutoff=AT,
        universe_snapshot_id="uv_20260930_abc", universe_cutoff=AT, scan_config_hash="h",
        section_hashes={"strategy": "s", "universe": "u", "gate": "g"}, code_commit="c0ffee",
        code_dirty=False, versions={"trend_algorithm_version": "trend-1.0.0"},
        survivorship_status="PARTIAL", survivorship_detail="why", counts={"considered": 3},
        results_hash=results_hash(ROWS), started_at=AT, completed_at=AT,
    )  # fmt: skip


def test_record_round_trip_and_no_overwrite() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBScanRunRepository(store)
    repo.record(_run(), ROWS)
    loaded = repo.load("run-1")
    assert loaded is not None
    assert loaded["section_hashes"] == {"strategy": "s", "universe": "u", "gate": "g"}
    assert loaded["counts"] == {"considered": 3} and loaded["code_dirty"] is False
    assert results_hash(repo.load_results("run-1")) == loaded["results_hash"]
    with pytest.raises(duckdb.ConstraintException):
        repo.record(_run(), ROWS)  # a run id is written once
    assert repo.load("nope") is None


def test_code_state_reports_the_checkout_or_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    commit, dirty = versioning.code_state()
    assert commit == "unknown" or len(commit) == 40

    def fail(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(versioning.subprocess, "run", fail)
    assert versioning.code_state() == ("unknown", None)
