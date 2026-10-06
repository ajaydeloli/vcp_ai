"""Paper ledger storage (DATABASE_SCHEMA 49B; STRATEGY_SPECIFICATION 21.3; monitoring M3).

Append-only: this repository only ever inserts into ``paper_events``; there is no update or
delete (a test pins it).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.paper.ledger import PaperEvent

COLUMNS = ("event_id", "rule_set", "strategy_id", "config_hash", "instrument_id", "event_date",
           "event_type", "price", "scan_date", "metadata_json", "code_commit",
           "recorded_at")  # fmt: skip


class DuckDBPaperRepository:
    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def events(self, rule_set: str, strategy_id: str, config_hash: str) -> list[PaperEvent]:
        rows = self._store.conn.execute(
            "SELECT strategy_id, instrument_id, event_date, event_type, price, scan_date,"
            " metadata_json FROM paper_events WHERE rule_set = ? AND strategy_id = ?"
            " AND config_hash = ? ORDER BY event_date, recorded_at, event_id",
            [rule_set, strategy_id, config_hash],
        ).fetchall()
        return [PaperEvent(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6] or "{}"))
                for r in rows]  # fmt: skip

    def append(
        self, rule_set: str, config_hash: str, events: Sequence[PaperEvent],
        code_commit: str | None, now: datetime | None = None,
    ) -> int:  # fmt: skip
        """Insert ``events``; an event already stored (same id) is left as it is. Returns the
        number of rows inserted."""
        if not events:
            return 0
        at = now or datetime.now(UTC)
        rows = [(e.event_id(rule_set, config_hash), rule_set, e.strategy_id, config_hash,
                 e.instrument_id, e.event_date, e.event_type, e.price, e.scan_date,
                 json.dumps(e.meta, sort_keys=True, default=str) if e.meta else None,
                 code_commit, at) for e in events]  # fmt: skip
        conn = self._store.conn
        before = conn.execute("SELECT count(*) FROM paper_events").fetchone()[0]  # type: ignore[index]
        marks = ", ".join("?" * len(COLUMNS))
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.executemany(
                f"INSERT OR IGNORE INTO paper_events ({', '.join(COLUMNS)}) VALUES ({marks})", rows
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        after = conn.execute("SELECT count(*) FROM paper_events").fetchone()[0]  # type: ignore[index]
        return int(after - before)
