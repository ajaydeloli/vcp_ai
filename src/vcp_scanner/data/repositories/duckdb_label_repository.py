"""Forward labels: observations, bars and storage (PROJECT_DESIGN 39; Phase 9 step 2)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from vcp_scanner.backtest.labels import HORIZONS, LABEL_VERSION, VOLUME_BASE, ForwardLabels
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.domain.strategy import VCP_STRATEGY_ID

_UPSERT = f"""
    INSERT INTO forward_labels (
        strategy_id, instrument_id, as_of_date, config_hash, label_version, data_snapshot_id,
        score_scan_id,
        entry_close, pivot_price, {", ".join(f"ret_{h}" for h in HORIZONS)}, mfe_60, mae_60,
        breakout_within_20, breakout_day, failed_breakout, bars_after, complete, computed_at
    ) VALUES ({", ".join(["?"] * (14 + len(HORIZONS)))})
    ON CONFLICT (strategy_id, instrument_id, as_of_date, config_hash, label_version,
                 data_snapshot_id)
    DO UPDATE SET {", ".join(f"ret_{h} = EXCLUDED.ret_{h}" for h in HORIZONS)},
        mfe_60 = EXCLUDED.mfe_60, mae_60 = EXCLUDED.mae_60,
        breakout_within_20 = EXCLUDED.breakout_within_20, breakout_day = EXCLUDED.breakout_day,
        failed_breakout = EXCLUDED.failed_breakout, bars_after = EXCLUDED.bars_after,
        complete = EXCLUDED.complete, computed_at = EXCLUDED.computed_at,
        pivot_price = EXCLUDED.pivot_price, entry_close = EXCLUDED.entry_close,
        score_scan_id = EXCLUDED.score_scan_id
"""


@dataclass(frozen=True, slots=True)
class Observation:
    instrument_id: str
    as_of: date
    score_scan_id: str
    pivot: float | None


class DuckDBLabelRepository:
    def __init__(self, store: DuckDBStore, data_snapshot_id: str = LIVE_SNAPSHOT_ID) -> None:
        self._store = store
        self._snapshot = data_snapshot_id

    def pending(self, config_hash: str, strategy_id: str = VCP_STRATEGY_ID) -> list[Observation]:
        """Scored observations of this strategy and config without a complete label of this
        version. The pivot is the strategy's primary setup of the same date, config and
        snapshot (``setups`` view, DATABASE_SCHEMA 35A.3)."""
        rows = self._store.conn.execute(
            """
            SELECT s.instrument_id, s.as_of_date, s.scan_id, p.pivot_price
            FROM setup_scores s
            LEFT JOIN setups p
              ON p.strategy_id = s.strategy_id AND p.instrument_id = s.instrument_id
             AND p.as_of_date = s.as_of_date AND p.config_hash = s.config_hash
             AND p.data_snapshot_id = s.data_snapshot_id AND p.is_primary
            LEFT JOIN forward_labels f
              ON f.strategy_id = s.strategy_id AND f.instrument_id = s.instrument_id
             AND f.as_of_date = s.as_of_date AND f.config_hash = s.config_hash
             AND f.label_version = ? AND f.data_snapshot_id = s.data_snapshot_id
            WHERE s.strategy_id = ? AND s.config_hash = ? AND s.data_snapshot_id = ?
              AND coalesce(NOT f.complete, true)
            ORDER BY s.as_of_date, s.instrument_id
            """,
            [LABEL_VERSION, strategy_id, config_hash, self._snapshot],
        ).fetchall()
        return [Observation(r[0], r[1], r[2], None if r[3] is None else float(r[3]))
                for r in rows]  # fmt: skip

    def bars(self, instrument_ids: list[str], start: date) -> dict[str, list[tuple[Any, ...]]]:
        """(date, high, low, close, volume) per instrument from ``start`` on, oldest first."""
        out: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        if not instrument_ids:
            return out
        for r in self._store.conn.execute(
            "SELECT instrument_id, trade_date, high_adj, low_adj, close_adj, volume_adj"
            " FROM daily_prices_adjusted_current WHERE computed_from_snapshot_id = ?"
            " AND trade_date >= ? AND instrument_id IN (SELECT unnest(?))"
            " ORDER BY instrument_id, trade_date",
            [self._snapshot, start, instrument_ids],
        ).fetchall():
            out[str(r[0])].append(r[1:])
        return out

    @staticmethod
    def bars_start(observations: list[Observation]) -> date:
        """Early enough for the 50-bar volume base before the first observation."""
        return min(o.as_of for o in observations) - timedelta(days=VOLUME_BASE * 2 + 30)

    def save(
        self, config_hash: str,
        items: list[tuple[Observation, float, ForwardLabels]], computed_at: datetime,
        strategy_id: str = VCP_STRATEGY_ID,
    ) -> int:  # fmt: skip
        rows = [
            (strategy_id, o.instrument_id, o.as_of, config_hash, LABEL_VERSION, self._snapshot,
             o.score_scan_id, entry, o.pivot, *(lab.ret[h] for h in HORIZONS), lab.mfe_60,
             lab.mae_60, lab.breakout_within_20, lab.breakout_day, lab.failed_breakout,
             lab.bars_after, lab.complete, computed_at)
            for o, entry, lab in items
        ]  # fmt: skip
        return self._store.upsert_rows(_UPSERT, rows)
