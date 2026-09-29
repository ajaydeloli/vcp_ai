"""DuckDB-backed Universe Repository."""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

from vcp_scanner.domain.universe import UniverseMembership, UniverseSnapshot

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class DuckDBUniverseRepository:
    """UniverseRepository backed by DuckDB."""

    def __init__(self, store: DuckDBStore) -> None:
        self._store = store

    def save_snapshot(
        self,
        snapshot: UniverseSnapshot,
        memberships: list[UniverseMembership],
    ) -> None:
        """Persist a universe snapshot and its memberships."""
        # Insert snapshot
        self._store.conn.execute(
            """
            INSERT INTO universe_snapshots (
                universe_snapshot_id, universe_name, as_of_date, created_at,
                config_hash, method_version, survivorship_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                snapshot.universe_snapshot_id,
                snapshot.universe_name,
                snapshot.as_of_date,
                snapshot.created_at,
                snapshot.config_hash,
                snapshot.method_version,
                snapshot.survivorship_status.value,
            ],
        )

        # Insert memberships
        if memberships:
            # We use executemany for bulk insert
            rows = [
                (
                    m.universe_snapshot_id,
                    m.instrument_id,
                    m.eligible,
                    m.exclusion_reason,
                    m.avg_traded_value,
                    m.price,
                    m.instrument_type,
                    m.series,
                    m.asm_flag,
                    m.gsm_flag,
                    m.t2t_flag,
                )
                for m in memberships
            ]
            self._store.conn.executemany(
                """
                INSERT INTO universe_memberships (
                    universe_snapshot_id, instrument_id, eligible, exclusion_reason,
                    avg_traded_value, price, instrument_type, series,
                    asm_flag, gsm_flag, t2t_flag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def load_snapshot(self, as_of_date: date) -> list[str]:
        """Load eligible instrument IDs for a given date's universe snapshot.

        If multiple snapshots exist for the same date, loads the most recently created one.
        """
        # Find the latest snapshot ID for the date
        row = self._store.conn.execute(
            """
            SELECT universe_snapshot_id
            FROM universe_snapshots
            WHERE as_of_date = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            [as_of_date],
        ).fetchone()

        if not row:
            return []

        snapshot_id = row[0]

        rows = self._store.conn.execute(
            """
            SELECT instrument_id
            FROM universe_memberships
            WHERE universe_snapshot_id = ?
              AND eligible = TRUE
            """,
            [snapshot_id],
        ).fetchall()

        return [r[0] for r in rows]
