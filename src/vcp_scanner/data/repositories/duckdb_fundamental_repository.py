"""Manifest of fundamental filings (``fundamental_filings``; FUNDAMENTALS_SPECIFICATION §4).

One row per filing seen. Rows are never deleted. A re-listed filing updates its own row (status,
hash, cache path); the revision number of a period and basis is recomputed from the broadcast
times of all its filings, so a restatement becomes revision 1 without touching revision 0.
"""

from __future__ import annotations

from datetime import date, datetime

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.fundamentals.base import STATUS_PARSE_ERROR, FilingRef, FilingRow, StoredFiling
from vcp_scanner.fundamentals.snapshots import PROVIDER, Fact, SnapshotRow


class DuckDBFundamentalRepository:
    def __init__(self, store: DuckDBStore) -> None:
        self._conn = store.conn

    def get(self, filing_id: str) -> StoredFiling | None:
        row = self._conn.execute(
            "SELECT filing_id, status, sha256, cache_path FROM fundamental_filings"
            " WHERE filing_id = ?",
            [filing_id],
        ).fetchone()
        return StoredFiling(*row) if row else None

    def record(
        self,
        ref: FilingRef,
        *,
        instrument_id: str,
        status: str,
        sha256: str | None,
        cache_path: str | None,
        fetched_at: datetime,
    ) -> None:
        conn = self._conn
        exists = conn.execute(
            "SELECT 1 FROM fundamental_filings WHERE filing_id = ?", [ref.filing_id]
        ).fetchone()
        if exists:
            conn.execute(
                "UPDATE fundamental_filings SET status = ?, sha256 = ?, cache_path = ?,"
                " fetched_at = ? WHERE filing_id = ?",
                [status, sha256, cache_path, fetched_at, ref.filing_id],
            )
        else:
            conn.execute(
                "INSERT INTO fundamental_filings (filing_id, instrument_id, period_end,"
                " period_type, statement_basis, revision_number, broadcast_at, source_feed,"
                " source_record_id, url, audited, revision_flags, sha256, cache_path,"
                " fetched_at, status) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [ref.filing_id, instrument_id, ref.period_end, ref.period_type,
                 ref.statement_basis, ref.broadcast_at, ref.source_feed,
                 ref.source_record_id, ref.url, ref.audited, ref.revision_flags, sha256,
                 cache_path, fetched_at, status],
            )  # fmt: skip
            self._renumber(instrument_id, ref.period_end, ref.statement_basis, ref.period_type)

    def _renumber(
        self, instrument_id: str, period_end: date, basis: str, period_type: str | None
    ) -> None:
        """Revision n = the n-th filing of the period, type and basis by broadcast time.

        A quarterly and an annual filing share the March period end, so the type is part of
        the key. The integrated feed does not say which it is (period_type NULL): its
        filings are renumbered once the parser (F2) has set the type.
        """
        ids = [
            r[0]
            for r in self._conn.execute(
                "SELECT filing_id FROM fundamental_filings WHERE instrument_id = ?"
                " AND period_end = ? AND statement_basis = ?"
                " AND period_type IS NOT DISTINCT FROM ?"
                " ORDER BY broadcast_at, filing_id",
                [instrument_id, period_end, basis, period_type],
            ).fetchall()
        ]
        for number, filing_id in enumerate(ids):
            self._conn.execute(
                "UPDATE fundamental_filings SET revision_number = ? WHERE filing_id = ?"
                " AND revision_number <> ?",
                [number, filing_id, number],
            )

    # --- SnapshotStore (F2) ---------------------------------------------------------------

    _FILING_COLS = (
        "filing_id, instrument_id, period_end, period_type, statement_basis, revision_number,"
        " broadcast_at, cache_path, sha256"
    )

    def unparsed_filings(self) -> list[FilingRow]:
        rows = self._conn.execute(
            f"SELECT {self._FILING_COLS} FROM fundamental_filings f WHERE status = 'OK'"
            " AND NOT EXISTS (SELECT 1 FROM fundamental_snapshots s"
            " WHERE s.source_record_id = f.filing_id) ORDER BY period_end, broadcast_at"
        ).fetchall()
        return [FilingRow(*r) for r in rows]

    def filing(self, filing_id: str) -> FilingRow | None:
        row = self._conn.execute(
            f"SELECT {self._FILING_COLS} FROM fundamental_filings WHERE filing_id = ?",
            [filing_id],
        ).fetchone()
        return FilingRow(*row) if row else None

    def mark_parse_error(self, filing_id: str) -> None:
        self._conn.execute(
            "UPDATE fundamental_filings SET status = ? WHERE filing_id = ?",
            [STATUS_PARSE_ERROR, filing_id],
        )

    def set_parsed_attributes(self, filing_id: str, period_type: str, basis: str) -> None:
        row = self.filing(filing_id)
        if row is None:
            return
        self._conn.execute(
            "UPDATE fundamental_filings SET period_type = ?, statement_basis = ?"
            " WHERE filing_id = ?",
            [period_type, basis, filing_id],
        )
        self._renumber(row.instrument_id, row.period_end, basis, period_type)

    def prior_ytd(
        self, instrument_id: str, basis: str, ytd_start: date, before: date, available_at: datetime
    ) -> tuple[date, dict[str, float]] | None:
        row = self._conn.execute(
            "SELECT s.fundamental_snapshot_id, s.period_end FROM fundamental_snapshots s"
            " JOIN fundamental_facts f ON f.fundamental_snapshot_id = s.fundamental_snapshot_id"
            " WHERE s.instrument_id = ? AND s.statement_basis = ? AND s.period_type = 'QUARTER'"
            " AND s.period_end < ? AND s.available_at <= ? AND s.data_status <> 'INVALID'"
            " AND f.scope = 'YTD' AND f.period_start = ?"
            " ORDER BY s.period_end DESC, s.revision_number DESC LIMIT 1",
            [instrument_id, basis, before, available_at, ytd_start],
        ).fetchone()
        if row is None:
            return None
        items = self._conn.execute(
            "SELECT item, value FROM fundamental_facts WHERE fundamental_snapshot_id = ?"
            " AND scope = 'YTD'",
            [row[0]],
        ).fetchall()
        return row[1], {k: v for k, v in items}

    def add_snapshot(self, row: SnapshotRow, facts: list[Fact]) -> None:
        conn = self._conn
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO fundamental_snapshots (fundamental_snapshot_id, instrument_id,"
                " period_end, period_type, statement_basis, revision_number,"
                " superseded_by_snapshot_id, filing_date, available_at, provider, currency,"
                " data_status, source_record_id, ingestion_run_id)"
                " VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL)",
                [row.snapshot_id, row.instrument_id, row.period_end, row.period_type,
                 row.statement_basis, row.revision_number, row.filing_date, row.available_at,
                 PROVIDER, row.currency, row.data_status, row.source_record_id],
            )  # fmt: skip
            for f in facts:
                conn.execute(
                    "INSERT INTO fundamental_facts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [row.snapshot_id, f.scope, f.item, f.value, f.derived, f.period_start,
                     f.period_end],
                )  # fmt: skip
            conn.execute(
                "UPDATE fundamental_snapshots SET superseded_by_snapshot_id = ?"
                " WHERE instrument_id = ? AND period_end = ? AND period_type = ?"
                " AND statement_basis = ? AND revision_number = ?",
                [row.snapshot_id, row.instrument_id, row.period_end, row.period_type,
                 row.statement_basis, row.revision_number - 1],
            )  # fmt: skip
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
