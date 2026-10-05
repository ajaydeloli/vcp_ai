"""Setups of file-configured strategies (STRATEGY_SPECIFICATION 12B; DATABASE_SCHEMA 35A).

The counterpart of ``duckdb_vcp_repository`` for every strategy other than VCP: inputs (bars,
recorded breakouts, the previous scan's verdicts) and outputs (``strategy_setups``, status
history, breakout events, run results). ``save_scan`` replaces every row of one scan id, so a
rerun of a date overwrites instead of forking; events detected on or after that date are
regenerated, so dates are computed in order (as for VCP).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.domain.strategy import Breakout, StrategyResult
from vcp_scanner.patterns.strategy_base import DailyBars

SETUP_COLUMNS = (
    "setup_id", "strategy_id", "scan_id", "instrument_id", "as_of_date", "is_primary",
    "base_start_date", "base_end_date", "base_high", "base_low", "base_depth_pct",
    "base_duration_days", "prior_advance_pct", "pivot_price", "pivot_date", "pivot_source",
    "pivot_distance_pct", "stop_reference_price", "dryup_volume_ratio", "classification",
    "grade", "status", "confirmation_state", "trend_gate", "weekly_stage2_pass",
    "invalidation_reasons", "unmet_rules", "breakout_event_id", "details_json",
    "algorithm_version", "config_hash", "data_snapshot_id", "created_at",
)  # fmt: skip
#: Verdict columns of a strategy scan (``strategy_scan_run_results`` and the results hash).
RESULT_COLUMNS = ("instrument_id", "classification", "grade", "status", "confirmation_state",
                  "pivot_price", "no_pattern_reason")  # fmt: skip


def setup_id(scan_id: str, instrument_id: str, base_start: date) -> str:
    raw = f"{scan_id}|{instrument_id}|{base_start.isoformat()}"
    return "st-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def event_id(strategy_id: str, instrument_id: str, base_start: date, config_hash: str) -> str:
    raw = f"{strategy_id}|{instrument_id}|{base_start.isoformat()}|{config_hash}"
    return "sb-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def verdict_row(r: StrategyResult) -> tuple[Any, ...]:
    s = r.setup
    if s is None:
        return (r.instrument_id, None, None, r.data_state, None, None, r.no_setup_reason)
    return (r.instrument_id, s.classification, s.grade, s.status, s.confirmation_state,
            s.pivot_price, None)  # fmt: skip


def results_hash(rows: Sequence[Sequence[Any]]) -> str:
    """SHA-256 over verdict rows, independent of order; pivots rounded to 6 decimals."""
    canon = sorted(
        [*(None if v is None else str(v) for v in r[:5]),
         None if r[5] is None else round(float(r[5]), 6),
         None if r[6] is None else str(r[6])]
        for r in rows
    )  # fmt: skip
    return hashlib.sha256(json.dumps(canon, separators=(",", ":")).encode()).hexdigest()


class DuckDBStrategyRepository:
    def __init__(
        self, store: DuckDBStore, strategy_id: str, data_snapshot_id: str = LIVE_SNAPSHOT_ID
    ) -> None:
        self._store = store
        self._strategy = strategy_id
        self._snapshot = data_snapshot_id

    # -- inputs ---------------------------------------------------------------------------

    def load_bars(
        self, instrument_ids: Sequence[str], as_of: date, bars: int
    ) -> dict[str, DailyBars]:
        """The last ``bars`` adjusted bars up to ``as_of`` per instrument (``None`` volume where
        missing)."""
        if not instrument_ids:
            return {}
        rows = self._store.conn.execute(
            """
            SELECT instrument_id, trade_date, high_adj, low_adj, close_adj, volume_adj
            FROM daily_prices_adjusted_current
            WHERE computed_from_snapshot_id = ? AND trade_date <= ?
              AND instrument_id IN (SELECT unnest(?))
            QUALIFY row_number() OVER (PARTITION BY instrument_id ORDER BY trade_date DESC) <= ?
            ORDER BY instrument_id, trade_date
            """,
            [self._snapshot, as_of, list(instrument_ids), bars],
        ).fetchall()
        cols: dict[str, list[list[Any]]] = {}
        for iid, d, h, lo, c, v in rows:
            cs = cols.setdefault(iid, [[], [], [], [], []])
            for k, val in enumerate((d, float(h), float(lo), float(c),
                                     None if v is None else float(v))):  # fmt: skip
                cs[k].append(val)
        return {iid: DailyBars(*cs) for iid, cs in cols.items()}

    def breakouts(
        self, instrument_ids: Sequence[str], as_of: date, config_hash: str
    ) -> dict[str, dict[date, Breakout]]:
        """Breakouts detected *before* ``as_of``, per instrument by base start."""
        out: dict[str, dict[date, Breakout]] = {}
        for iid, start, day, pivot, ratio in self._store.conn.execute(
            """
            SELECT instrument_id, base_start_date, breakout_date, pivot_price, volume_ratio
            FROM strategy_breakout_events
            WHERE strategy_id = ? AND config_hash = ? AND detected_as_of < ?
              AND instrument_id IN (SELECT unnest(?))
            """,
            [self._strategy, config_hash, as_of, list(instrument_ids)],
        ).fetchall():
            out.setdefault(iid, {})[start] = Breakout(day, float(pivot), float(ratio))
        return out

    def previous_scan(
        self, as_of: date, config_hash: str
    ) -> tuple[date | None, dict[str, tuple[str, str]]]:
        """The latest earlier scan date of this strategy and config, and its primary
        (classification, status) per instrument."""
        row = self._store.conn.execute(
            "SELECT max(as_of_date) FROM strategy_setups WHERE strategy_id = ?"
            " AND config_hash = ? AND data_snapshot_id = ? AND as_of_date < ?",
            [self._strategy, config_hash, self._snapshot, as_of],
        ).fetchone()
        prev = row[0] if row else None
        if prev is None:
            return None, {}
        rows = self._store.conn.execute(
            "SELECT instrument_id, classification, status FROM strategy_setups"
            " WHERE is_primary AND strategy_id = ? AND config_hash = ? AND data_snapshot_id = ?"
            " AND as_of_date = ?",
            [self._strategy, config_hash, self._snapshot, prev],
        ).fetchall()
        return prev, {r[0]: (r[1], r[2]) for r in rows}

    def later_scan_dates(self, as_of: date, config_hash: str) -> list[date]:
        return [r[0] for r in self._store.conn.execute(
            "SELECT DISTINCT as_of_date FROM strategy_setups WHERE strategy_id = ?"
            " AND config_hash = ? AND data_snapshot_id = ? AND as_of_date > ? ORDER BY 1",
            [self._strategy, config_hash, self._snapshot, as_of],
        ).fetchall()]  # fmt: skip

    # -- outputs --------------------------------------------------------------------------

    def save_scan(
        self, scan_id: str, as_of: date, config_hash: str, algorithm_version: str,
        results: Sequence[StrategyResult], created_at: datetime,
    ) -> int:  # fmt: skip
        """Replace every row of ``scan_id``, this date's status history, and the events
        detected on or after ``as_of``; returns the number of new breakout events."""
        conn = self._store.conn
        sid = self._strategy
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM strategy_setups WHERE scan_id = ?", [scan_id])
            conn.execute(
                "DELETE FROM strategy_setup_status_history WHERE strategy_id = ?"
                " AND as_of_date = ? AND config_hash = ?", [sid, as_of, config_hash],
            )  # fmt: skip
            conn.execute(
                "DELETE FROM strategy_breakout_events WHERE strategy_id = ?"
                " AND detected_as_of >= ? AND config_hash = ?", [sid, as_of, config_hash],
            )  # fmt: skip
            known = {(iid, start) for iid, by in self.breakouts(
                [r.instrument_id for r in results], as_of, config_hash).items()
                for start in by}  # fmt: skip
            prev_date, prev = self.previous_scan(as_of, config_hash)
            setups, events, history = [], [], []
            for r in results:
                s = r.setup
                if s is not None:
                    eid = None
                    if s.breakout is not None:
                        eid = event_id(sid, s.instrument_id, s.base_start, config_hash)
                        if (s.instrument_id, s.base_start) not in known:
                            b = s.breakout
                            events.append((
                                eid, sid, s.instrument_id, s.base_start, config_hash,
                                b.breakout_date, b.pivot_price, s.pivot_date, s.pivot_source,
                                b.volume_ratio, as_of, "STRUCTURAL", created_at,
                            ))  # fmt: skip
                    details = {**s.details, "measures": s.measures}
                    setups.append((
                        setup_id(scan_id, s.instrument_id, s.base_start), sid, scan_id,
                        s.instrument_id, s.as_of_date, s.is_primary, s.base_start, s.base_end,
                        s.base_high, s.base_low, s.base_depth_pct, s.base_duration_days,
                        s.prior_advance_pct, s.pivot_price, s.pivot_date, s.pivot_source,
                        s.pivot_distance_pct, s.stop_reference_price, s.dryup_volume_ratio,
                        s.classification, s.grade, s.status, s.confirmation_state, s.trend_gate,
                        s.weekly_stage2_pass, ",".join(s.invalidation_reasons) or None,
                        json.dumps(s.unmet_rules, sort_keys=True), eid,
                        json.dumps(details, sort_keys=True, default=str), algorithm_version,
                        config_hash, self._snapshot, created_at,
                    ))  # fmt: skip
                h = self._history_row(r, scan_id, as_of, config_hash, algorithm_version,
                                      prev_date, prev)  # fmt: skip
                if h is not None:
                    history.append(h)
            self._store.insert_rows("strategy_setups", SETUP_COLUMNS, setups)
            self._store.insert_rows(
                "strategy_breakout_events",
                ("breakout_event_id", "strategy_id", "instrument_id", "base_start_date",
                 "config_hash", "breakout_date", "pivot_price", "pivot_date", "pivot_source",
                 "volume_ratio", "detected_as_of", "method", "created_at"),
                events,
            )  # fmt: skip
            self._store.insert_rows(
                "strategy_setup_status_history",
                ("strategy_id", "instrument_id", "as_of_date", "config_hash", "setup_id",
                 "previous_as_of_date", "previous_classification", "new_classification",
                 "previous_status", "new_status", "reason", "algorithm_version"),
                history,
            )  # fmt: skip
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return len(events)

    def _history_row(
        self, r: StrategyResult, scan_id: str, as_of: date, config_hash: str,
        algorithm_version: str, prev_date: date | None, prev: dict[str, tuple[str, str]],
    ) -> tuple[Any, ...] | None:  # fmt: skip
        """A row when the primary class or status changed since the previous scan date."""
        s = r.setup
        new_cls = s.classification if s else "NONE"
        new_status = s.status if s else r.data_state
        old = prev.get(r.instrument_id)
        old_cls, old_status = old if old else ("NONE", None)
        if (old_cls, old_status) == (new_cls, new_status) or (old is None and s is None):
            return None
        return (self._strategy, r.instrument_id, as_of, config_hash,
                setup_id(scan_id, r.instrument_id, s.base_start) if s else None,
                prev_date if old else None, old_cls if old else None, new_cls, old_status,
                new_status, r.no_setup_reason if s is None else None,
                algorithm_version)  # fmt: skip

    def record_run_results(self, scan_run_id: str, rows: Sequence[Sequence[Any]]) -> None:
        if rows:
            self._store.upsert_rows(
                "INSERT INTO strategy_scan_run_results (scan_run_id, instrument_id,"
                " classification, grade, status, confirmation_state, pivot_price,"
                " no_pattern_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(scan_run_id, *r) for r in rows],
            )
