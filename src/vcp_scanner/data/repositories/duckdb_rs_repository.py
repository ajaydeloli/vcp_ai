"""DuckDB implementation of ``RelativeStrengthRepository`` (audit Fix 6).

Only data access lives here: fetching the newest adjusted close and its lagged closes, and
upserting ``relative_strength_snapshots``. The ranking formula is in
``features.relative_strength``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from vcp_scanner.data.repositories.session_calendar import load_session_calendar
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID, validate_snapshot_id
from vcp_scanner.domain.trend import RSPriceInput, RSRow

_UPSERT = """
    INSERT INTO relative_strength_snapshots (
        as_of_date, instrument_id, ret_63, ret_126, ret_189, ret_252,
        rs_raw, rs_rank, rs_percentile, population_size, rs_status,
        universe_snapshot_id, calculation_version, data_snapshot_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (
        as_of_date, instrument_id, calculation_version, data_snapshot_id, universe_snapshot_id
    ) DO UPDATE SET
        ret_63 = EXCLUDED.ret_63,
        ret_126 = EXCLUDED.ret_126,
        ret_189 = EXCLUDED.ret_189,
        ret_252 = EXCLUDED.ret_252,
        rs_raw = EXCLUDED.rs_raw,
        rs_rank = EXCLUDED.rs_rank,
        rs_percentile = EXCLUDED.rs_percentile,
        population_size = EXCLUDED.population_size,
        rs_status = EXCLUDED.rs_status
"""


class DuckDBRelativeStrengthRepository:
    """RS inputs/outputs scoped to one data snapshot (``LIVE`` = unfrozen working data)."""

    def __init__(self, store: DuckDBStore, data_snapshot_id: str = LIVE_SNAPSHOT_ID) -> None:
        self.store = store
        self.data_snapshot_id = validate_snapshot_id(data_snapshot_id)

    def eligible_members(self, universe_snapshot_id: str) -> list[str]:
        rows = self.store.conn.execute(
            "SELECT instrument_id FROM universe_memberships"
            " WHERE universe_snapshot_id = ? AND eligible = TRUE ORDER BY instrument_id",
            [universe_snapshot_id],
        ).fetchall()
        return [str(r[0]) for r in rows]

    def load_sessions(self, as_of_date: date, universe_snapshot_id: str) -> list[date]:
        """NSE sessions up to ``as_of_date`` as known when the universe snapshot was built
        (audit P2-2), so a rebuilt run counts staleness against the same calendar."""
        row = self.store.conn.execute(
            "SELECT created_at FROM universe_snapshots WHERE universe_snapshot_id = ?",
            [universe_snapshot_id],
        ).fetchone()
        return load_session_calendar(self.store.conn, as_of_date, row[0] if row else None)

    def load_rs_inputs(
        self, instrument_ids: Sequence[str], as_of_date: date, lags: Sequence[int]
    ) -> list[RSPriceInput]:
        if not instrument_ids:
            return []
        wanted = [1, *(int(n) + 1 for n in lags)]
        rows = self.store.conn.execute(
            """
            WITH series AS (
                SELECT
                    instrument_id,
                    trade_date,
                    close_adj,
                    ROW_NUMBER() OVER (
                        PARTITION BY instrument_id ORDER BY trade_date DESC
                    ) AS rn
                FROM daily_prices_adjusted_current
                WHERE list_contains(CAST(? AS VARCHAR[]), instrument_id)
                  AND trade_date <= ? AND computed_from_snapshot_id = ?
            )
            SELECT instrument_id, rn, trade_date, close_adj
            FROM series
            WHERE list_contains(CAST(? AS INTEGER[]), CAST(rn AS INTEGER))
            ORDER BY instrument_id, rn
            """,
            [list(instrument_ids), as_of_date, self.data_snapshot_id, wanted],
        ).fetchall()
        by_id: dict[str, dict[int, tuple[date, float]]] = {}
        for iid, rn, trade_date, close in rows:
            by_id.setdefault(str(iid), {})[int(rn)] = (trade_date, float(close))
        inputs: list[RSPriceInput] = []
        for iid, points in by_id.items():
            last_date, last_close = points[1]
            lagged = tuple(points[int(n) + 1][1] if int(n) + 1 in points else None for n in lags)
            inputs.append(RSPriceInput(iid, last_date, last_close, lagged))
        return inputs

    def save_relative_strength(
        self,
        as_of_date: date,
        universe_snapshot_id: str,
        calculation_version: str,
        rows: Sequence[RSRow],
    ) -> int:
        if not rows:
            return 0
        params = [
            (
                as_of_date,
                r.instrument_id,
                *r.returns,
                r.rs_raw,
                r.rs_rank,
                r.rs_percentile,
                r.population_size,
                r.rs_status,
                universe_snapshot_id,
                calculation_version,
                self.data_snapshot_id,
            )
            for r in rows
        ]
        conn = self.store.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            self.store.upsert_rows(_UPSERT, params)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return len(rows)
