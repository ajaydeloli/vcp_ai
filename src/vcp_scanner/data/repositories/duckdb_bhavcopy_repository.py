"""DuckDB persistence for the NSE bhavcopy manifest ``bhavcopy_files`` (audit step 2)."""

from __future__ import annotations

from datetime import date, datetime

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.bhavcopy import BhavcopyFileRecord, BhavcopyFileStatus, BhavcopyFormat

_COLUMNS = (
    "trade_date, status, url, file_format, sha256, row_count, rejected_count, cache_path, detail"
)


class DuckDBBhavcopyRepository:
    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def record_file(self, record: BhavcopyFileRecord, *, recorded_at: datetime) -> None:
        """Insert or update the entry for (trade_date, sha256).

        Entries without a file (NO_SESSION / PENDING / ERROR) share sha256 '' per date, so a
        PENDING date that later settles is updated in place rather than duplicated.
        """
        self._store.conn.execute(
            """
            INSERT INTO bhavcopy_files (trade_date, sha256, status, url, file_format, row_count,
                                        rejected_count, cache_path, detail, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (trade_date, sha256) DO UPDATE SET
                status = excluded.status, url = excluded.url,
                file_format = excluded.file_format, row_count = excluded.row_count,
                rejected_count = excluded.rejected_count, cache_path = excluded.cache_path,
                detail = excluded.detail, recorded_at = excluded.recorded_at
            """,
            [
                record.trade_date,
                record.sha256 or "",
                str(record.status),
                record.url,
                str(record.file_format),
                record.row_count,
                record.rejected_count,
                record.cache_path,
                record.detail,
                recorded_at,
            ],
        )

    def latest_files(self, start: date, end: date) -> dict[date, BhavcopyFileRecord]:
        """The most recently recorded entry per date in ``[start, end]``."""
        rows = self._store.conn.execute(
            f"""
            SELECT {_COLUMNS} FROM bhavcopy_files
            WHERE trade_date BETWEEN ? AND ?
            QUALIFY row_number() OVER (PARTITION BY trade_date ORDER BY recorded_at DESC) = 1
            ORDER BY trade_date
            """,
            [start, end],
        ).fetchall()
        return {r[0]: _record(r) for r in rows}

    def latest_file(self, trade_date: date) -> BhavcopyFileRecord | None:
        return self.latest_files(trade_date, trade_date).get(trade_date)


def _record(row: tuple[object, ...]) -> BhavcopyFileRecord:
    trade_date, status, url, fmt, sha, row_count, rejected, cache_path, detail = row
    assert isinstance(trade_date, date)
    return BhavcopyFileRecord(
        trade_date=trade_date,
        status=BhavcopyFileStatus(str(status)),
        url=str(url),
        file_format=BhavcopyFormat(str(fmt)),
        sha256=str(sha) or None,
        row_count=int(str(row_count)),
        rejected_count=int(str(rejected)),
        cache_path=None if cache_path is None else str(cache_path),
        detail=None if detail is None else str(detail),
    )
