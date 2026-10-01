"""When each instrument was last asked of a secondary corporate-action source.

Upstox allows about 1,000 requests per 30 minutes and is asked once per instrument, so a run
asks only a budget of instruments (DATA_SPECIFICATION 18A): first those with a price-affecting
NSE action in the window, then the least recently checked. This table is that rotation's
memory. It is operational state (last check per instrument), not market data.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from vcp_scanner.data.storage.duckdb_store import DuckDBStore


class DuckDBSecondaryCheckRepository:
    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def last_checked(self, provider: str) -> dict[str, datetime]:
        rows = self._store.conn.execute(
            "SELECT instrument_id, checked_at FROM secondary_ca_checks WHERE provider = ?",
            [provider],
        ).fetchall()
        return {str(r[0]): r[1] for r in rows}

    def record_checked(self, provider: str, instrument_ids: Iterable[str], at: datetime) -> int:
        params = [(provider, iid, at) for iid in sorted(set(instrument_ids))]
        if params:
            self._store.conn.executemany(
                """
                INSERT INTO secondary_ca_checks (provider, instrument_id, checked_at)
                VALUES (?, ?, ?)
                ON CONFLICT (provider, instrument_id) DO UPDATE SET checked_at = EXCLUDED.checked_at
                """,
                params,
            )
        return len(params)
