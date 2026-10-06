"""Backtest inputs and storage (DATABASE_SCHEMA 48-49; Phase 9 step 3)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from vcp_scanner.backtest.engine import Bar, Event, Signal
from vcp_scanner.backtest.regime import DayBreadth
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

    def bars(
        self, instrument_ids: Sequence[str], start: date, end: date, after_days: int = 130
    ) -> dict[str, list[Bar]]:
        """Adjusted bars from 120 calendar days before ``start`` (volume base) to ``after_days``
        after ``end`` (exits of late signals: 130 days for 60 sessions)."""
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
                end + timedelta(days=after_days),
                list(instrument_ids),
            ],
        ).fetchall():
            out[str(iid)].append(Bar(d, float(h), float(lo), float(c),
                                     None if v is None else float(v)))  # fmt: skip
        return out

    def breadth(
        self, start: date, end: date, scan_config_hash: str, features_version: str
    ) -> list[DayBreadth]:
        """Per trading day from 120 calendar days before ``start`` to ``end``: the universe
        members (eligible members of the latest completed Trend Template scan's universe on or
        before the day, for this scan config; before the first scan, the first scan's) with
        that day's adjusted close and features: how many have a 50-day average, how many close
        above it, and their mean daily return (STRATEGY_SPECIFICATION 20.3)."""
        rows = self._store.conn.execute(
            """
            WITH runs AS (
                SELECT as_of_date, universe_snapshot_id,
                       row_number() OVER (PARTITION BY as_of_date ORDER BY completed_at DESC) rn
                FROM scan_runs
                WHERE scan_type = 'TREND_TEMPLATE' AND status = 'COMPLETED'
                  AND scan_config_hash = ? AND data_snapshot_id = ?
            ), scans AS (SELECT as_of_date, universe_snapshot_id FROM runs WHERE rn = 1),
            days AS (
                SELECT DISTINCT trade_date FROM technical_features_daily
                WHERE trade_date BETWEEN ? AND ? AND calculation_version = ?
                  AND data_snapshot_id = ?
            ), day_scan AS (
                SELECT d.trade_date,
                       coalesce(max(s.as_of_date), (SELECT min(as_of_date) FROM scans)) AS scan_date
                FROM days d LEFT JOIN scans s ON s.as_of_date <= d.trade_date
                GROUP BY d.trade_date
            )
            SELECT ds.trade_date, count(f.sma_50),
                   count(*) FILTER (WHERE f.sma_50 IS NOT NULL AND p.close_adj > f.sma_50),
                   avg(f.daily_return)
            FROM day_scan ds
            JOIN scans s ON s.as_of_date = ds.scan_date
            JOIN universe_memberships m
              ON m.universe_snapshot_id = s.universe_snapshot_id AND m.eligible
            JOIN technical_features_daily f
              ON f.instrument_id = m.instrument_id AND f.trade_date = ds.trade_date
             AND f.calculation_version = ? AND f.data_snapshot_id = ?
            JOIN daily_prices_adjusted_current p
              ON p.instrument_id = f.instrument_id AND p.trade_date = f.trade_date
             AND p.computed_from_snapshot_id = ?
            GROUP BY ds.trade_date ORDER BY ds.trade_date
            """,
            [scan_config_hash, self._snapshot, start - timedelta(days=120), end,
             features_version, self._snapshot, features_version, self._snapshot,
             self._snapshot],
        ).fetchall()  # fmt: skip
        return [DayBreadth(r[0], int(r[1]), int(r[2]), None if r[3] is None else float(r[3]))
                for r in rows]  # fmt: skip

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
