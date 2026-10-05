"""Backtest inputs and storage (DATABASE_SCHEMA 48-49; Phase 9 step 3)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from vcp_scanner.backtest.engine import Bar, Event, Signal
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.domain.strategy import VCP_STRATEGY_ID


class DuckDBBacktestRepository:
    def __init__(self, store: DuckDBStore, data_snapshot_id: str = LIVE_SNAPSHOT_ID) -> None:
        self._store = store
        self._snapshot = data_snapshot_id

    def signals(
        self, start: date, end: date, config_hash: str, classes: Sequence[str],
        min_score: float | None = None, eligible_only: bool = True,
        strategy_id: str = VCP_STRATEGY_ID,
    ) -> list[Signal]:  # fmt: skip
        """Scored setups of one strategy with a pivot, scanned between ``start`` and ``end``:
        eligible ones only, or (``eligible_only=False``, the baseline) every scored row whose
        primary setup has a pivot, whatever its class and status. The stop level is the
        setup's ``stop_reference_price`` (VCP: the last contraction's trough)."""
        rows = self._store.conn.execute(
            """
            SELECT s.instrument_id, s.as_of_date, p.pivot_price, s.final_setup_score,
                   p.stop_reference_price, s.classification
            FROM setup_scores s
            JOIN setups p
              ON p.strategy_id = s.strategy_id AND p.instrument_id = s.instrument_id
             AND p.as_of_date = s.as_of_date AND p.config_hash = s.config_hash
             AND p.data_snapshot_id = s.data_snapshot_id AND p.is_primary
            WHERE s.strategy_id = ? AND s.config_hash = ? AND s.data_snapshot_id = ?
              AND (s.eligible OR NOT ?)
              AND s.as_of_date BETWEEN ? AND ? AND p.pivot_price IS NOT NULL
              AND s.classification IN (SELECT unnest(?))
              AND (? IS NULL OR s.final_setup_score >= ?)
            ORDER BY s.as_of_date, s.instrument_id
            """,
            [
                strategy_id,
                config_hash,
                self._snapshot,
                eligible_only,
                start,
                end,
                list(classes),
                min_score,
                min_score,
            ],
        ).fetchall()
        return [Signal(r[0], r[1], float(r[2]), None if r[3] is None else float(r[3]),
                       None if r[4] is None else float(r[4]), str(r[5])) for r in rows]  # fmt: skip

    def bars(self, instrument_ids: Sequence[str], start: date, end: date) -> dict[str, list[Bar]]:
        """Adjusted bars from 120 calendar days before ``start`` (volume base) to 130 days after
        ``end`` (exits of late signals: 60 sessions)."""
        out: dict[str, list[Bar]] = defaultdict(list)
        if not instrument_ids:
            return out
        for iid, d, h, lo, c, v in self._store.conn.execute(
            "SELECT instrument_id, trade_date, high_adj, low_adj, close_adj, volume_adj"
            " FROM daily_prices_adjusted_current WHERE computed_from_snapshot_id = ?"
            " AND trade_date BETWEEN ? AND ? AND instrument_id IN (SELECT unnest(?))"
            " ORDER BY instrument_id, trade_date",
            [
                self._snapshot,
                start - timedelta(days=120),
                end + timedelta(days=130),
                list(instrument_ids),
            ],
        ).fetchall():
            out[str(iid)].append(Bar(d, float(h), float(lo), float(c),
                                     None if v is None else float(v)))  # fmt: skip
        return out

    def save(self, run: dict[str, Any], events: Sequence[Event]) -> None:
        conn = self._store.conn
        cols = list(run)
        conn.execute("BEGIN TRANSACTION")
        try:
            marks = ", ".join("?" * len(cols))
            conn.execute(f"INSERT INTO backtest_runs ({', '.join(cols)}) VALUES ({marks})",
                         [run[c] for c in cols])  # fmt: skip
            self._store.insert_rows(
                "backtest_events",
                ("backtest_id", "instrument_id", "event_date", "event_type", "price", "quantity",
                 "signal_id", "metadata_json"),
                [(run["backtest_id"], e.instrument_id, e.day, e.kind, e.price, None, None,
                  json.dumps(e.meta, sort_keys=True, default=str) if e.meta else None)
                 for e in events],
            )  # fmt: skip
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
