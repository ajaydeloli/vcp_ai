"""Manifest of fundamental filings (``fundamental_filings``; FUNDAMENTALS_SPECIFICATION §4).

One row per filing seen. Rows are never deleted. A re-listed filing updates its own row (status,
hash, cache path); the revision number of a period and basis is recomputed from the broadcast
times of all its filings, so a restatement becomes revision 1 without touching revision 0.
"""

from __future__ import annotations

from datetime import datetime

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.fundamentals.base import FilingRef, StoredFiling


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
            self._renumber(instrument_id, ref)

    def _renumber(self, instrument_id: str, ref: FilingRef) -> None:
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
                [instrument_id, ref.period_end, ref.statement_basis, ref.period_type],
            ).fetchall()
        ]
        for number, filing_id in enumerate(ids):
            self._conn.execute(
                "UPDATE fundamental_filings SET revision_number = ? WHERE filing_id = ?"
                " AND revision_number <> ?",
                [number, filing_id, number],
            )
