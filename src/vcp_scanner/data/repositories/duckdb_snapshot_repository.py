"""DuckDB persistence for ``data_snapshots`` (audit finding P0-1).

A snapshot row records the system time (``known_at``) a frozen view of the data is taken
at. Creating the same ``known_at`` twice returns the existing snapshot, so a re-run never
forks the lineage.
"""

from __future__ import annotations

from datetime import datetime

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.snapshot import DataSnapshot, snapshot_id_for


class DuckDBSnapshotRepository:
    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def create(
        self,
        known_at: datetime,
        *,
        created_at: datetime,
        description: str | None = None,
    ) -> DataSnapshot:
        """Create the snapshot for ``known_at``, or return the existing one (idempotent).

        ``created_at`` is injected by the caller; this method never reads the clock.
        """
        snapshot_id = snapshot_id_for(known_at)
        self._store.conn.execute(
            """
            INSERT INTO data_snapshots (data_snapshot_id, known_at, created_at, description)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (data_snapshot_id) DO NOTHING
            """,
            [snapshot_id, known_at, created_at, description],
        )
        loaded = self.load(snapshot_id)
        if loaded is None:  # pragma: no cover - the insert above guarantees the row
            raise RuntimeError(f"Snapshot {snapshot_id} was not persisted")
        return loaded

    def load(self, data_snapshot_id: str) -> DataSnapshot | None:
        row = self._store.conn.execute(
            """
            SELECT data_snapshot_id, known_at, created_at, description
            FROM data_snapshots WHERE data_snapshot_id = ?
            """,
            [data_snapshot_id],
        ).fetchone()
        return None if row is None else DataSnapshot(row[0], row[1], row[2], row[3])

    def latest(self) -> DataSnapshot | None:
        """The snapshot with the newest ``known_at``, or None if none exist."""
        row = self._store.conn.execute(
            """
            SELECT data_snapshot_id, known_at, created_at, description
            FROM data_snapshots ORDER BY known_at DESC, data_snapshot_id DESC LIMIT 1
            """
        ).fetchone()
        return None if row is None else DataSnapshot(row[0], row[1], row[2], row[3])
