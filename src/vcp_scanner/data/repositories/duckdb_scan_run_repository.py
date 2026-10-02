"""Immutable scan-run records (audit P1-8, DATABASE_SCHEMA section 40).

One ``scan_runs`` row per Trend Template scan, written once and never updated: the as-of date,
the data cutoff, the exact universe snapshot (and the RS ranking over it), the code commit,
per-section config hashes, the survivorship label, counts and a content hash of the verdicts.
``scan_run_results`` keeps a copy of each run's verdicts, so a later rerun with the same scan
id (which overwrites ``trend_template_results``) cannot erase what an earlier run reported.

``vcp verify scan`` rebuilds a run at its recorded cutoff and compares ``results_hash``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.trend import TrendTemplateResult

#: Verdict columns that define a scan's result and its hash, in order.
RESULT_COLUMNS = ("instrument_id", "status", "trend_template_pass", "weekly_stage", "rs_rank",
                  "blocked_by")  # fmt: skip


def verdict_row(r: TrendTemplateResult) -> tuple[Any, ...]:
    """The stored verdict of one result, in ``RESULT_COLUMNS`` order (as in
    ``trend_template_results``)."""
    w = r.weekly_context
    return (
        r.instrument_id,
        r.status.value,
        r.trend_template_pass,
        w.weekly_stage.value if w is not None else None,
        r.rs_rank,
        ",".join(r.blocked_by) or None,
    )


def results_hash(rows: Sequence[Sequence[Any]]) -> str:
    """SHA-256 over verdict rows (``RESULT_COLUMNS`` order), independent of row order.

    RS ranks are rounded to 6 decimals so float formatting cannot change the hash.
    """
    canon = sorted(
        [
            str(r[0]),
            None if r[1] is None else str(r[1]),
            None if r[2] is None else bool(r[2]),
            None if r[3] is None else str(r[3]),
            None if r[4] is None else round(float(r[4]), 6),
            None if r[5] is None else str(r[5]),
        ]
        for r in rows
    )
    return hashlib.sha256(json.dumps(canon, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ScanRun:
    scan_run_id: str
    scan_type: str
    as_of_date: date
    scan_id: str
    data_snapshot_id: str
    data_cutoff: datetime
    universe_snapshot_id: str
    universe_cutoff: datetime | None
    scan_config_hash: str
    section_hashes: dict[str, str]
    code_commit: str
    code_dirty: bool | None
    versions: dict[str, str | int]
    survivorship_status: str | None
    survivorship_detail: str | None
    counts: dict[str, int]
    results_hash: str
    started_at: datetime
    completed_at: datetime
    status: str = "COMPLETED"


class DuckDBScanRunRepository:
    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def record(self, run: ScanRun, results: Sequence[Sequence[Any]]) -> None:
        """Insert one run and its verdicts. Rows are never updated (a duplicate id fails)."""
        conn = self._store.conn
        conn.execute(
            """
            INSERT INTO scan_runs (
                scan_run_id, scan_type, as_of_date, scan_id, data_snapshot_id, data_cutoff,
                universe_snapshot_id, universe_cutoff, scan_config_hash, section_hashes,
                code_commit, code_dirty, versions, survivorship_status, survivorship_detail,
                counts, results_hash, started_at, completed_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run.scan_run_id, run.scan_type, run.as_of_date, run.scan_id,
                run.data_snapshot_id, run.data_cutoff, run.universe_snapshot_id,
                run.universe_cutoff, run.scan_config_hash,
                json.dumps(run.section_hashes, sort_keys=True), run.code_commit, run.code_dirty,
                json.dumps(run.versions, sort_keys=True), run.survivorship_status,
                run.survivorship_detail, json.dumps(run.counts, sort_keys=True),
                run.results_hash, run.started_at, run.completed_at, run.status,
            ],
        )  # fmt: skip
        if results:
            conn.executemany(
                "INSERT INTO scan_run_results (scan_run_id, instrument_id, status,"
                " trend_template_pass, weekly_stage, rs_rank, blocked_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(run.scan_run_id, *r) for r in results],
            )

    def load(self, scan_run_id: str) -> dict[str, Any] | None:
        cur = self._store.conn.execute(
            "SELECT * FROM scan_runs WHERE scan_run_id = ?", [scan_run_id]
        )
        row = cur.fetchone()
        if row is None:
            return None
        out = dict(zip([d[0] for d in cur.description], row, strict=True))
        for key in ("section_hashes", "versions", "counts"):
            out[key] = json.loads(out[key]) if out[key] else {}
        return out

    def load_results(self, scan_run_id: str) -> list[tuple[Any, ...]]:
        return self._store.conn.execute(
            "SELECT instrument_id, status, trend_template_pass, weekly_stage, rs_rank, blocked_by"
            " FROM scan_run_results WHERE scan_run_id = ? ORDER BY instrument_id",
            [scan_run_id],
        ).fetchall()

    def list_runs(
        self, as_of_date: date | None = None, limit: int = 20, scan_type: str | None = None
    ) -> list[tuple[Any, ...]]:
        clauses: list[str] = []
        params: list[Any] = []
        if as_of_date:
            clauses.append("as_of_date = ?")
            params.append(as_of_date)
        if scan_type:
            clauses.append("scan_type = ?")
            params.append(scan_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self._store.conn.execute(
            "SELECT scan_run_id, as_of_date, data_snapshot_id, universe_snapshot_id, code_commit,"
            f" code_dirty, results_hash, started_at FROM scan_runs {where}"  # noqa: S608
            " ORDER BY started_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
