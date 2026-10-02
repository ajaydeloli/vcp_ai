"""VCP results storage (DATABASE_SCHEMA 31-34, VCP_SPECIFICATION 47; Phase 6 step 7).

``save_scan`` replaces every row of one VCP scan id (patterns, contractions, pivots, the status
history of that date and config, and breakout events detected on that date), so rerunning a date
with the same config and data snapshot overwrites instead of forking, as Trend Template scans do.
The immutable record of each run is its ``scan_runs`` row plus ``vcp_scan_run_results``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.domain.vcp import VCPPattern
from vcp_scanner.patterns.vcp.detector import VCPDetection
from vcp_scanner.patterns.vcp.measurements import PriceSeries
from vcp_scanner.patterns.vcp.monitor import BreakoutEvent, PriorPattern

#: Verdict columns of a VCP scan, in order (``vcp_scan_run_results`` and the results hash).
VCP_RESULT_COLUMNS = ("instrument_id", "classification", "status", "confirmation_state",
                      "pivot_price", "no_pattern_reason")  # fmt: skip


def pattern_id(scan_id: str, instrument_id: str, base_start: date) -> str:
    raw = f"{scan_id}|{instrument_id}|{base_start.isoformat()}"
    return "vp-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def vcp_verdict_row(d: VCPDetection) -> tuple[Any, ...]:
    p = d.pattern
    if p is None:
        return (d.instrument_id, None, d.status.value if d.status else None, None, None,
                d.no_pattern_reason)  # fmt: skip
    return (
        d.instrument_id,
        p.classification.value,
        p.status.value,
        p.confirmation_state.value,
        p.pivot.pivot_price if p.pivot else None,
        None,
    )


def vcp_results_hash(rows: Sequence[Sequence[Any]]) -> str:
    """SHA-256 over verdict rows, independent of order; pivot prices rounded to 6 decimals."""
    canon = sorted(
        [*(None if v is None else str(v) for v in r[:4]),
         None if r[4] is None else round(float(r[4]), 6),
         None if r[5] is None else str(r[5])]
        for r in rows
    )  # fmt: skip
    return hashlib.sha256(json.dumps(canon, separators=(",", ":")).encode()).hexdigest()


class DuckDBVCPRepository:
    def __init__(self, store: DuckDBStore, data_snapshot_id: str = LIVE_SNAPSHOT_ID) -> None:
        self._store = store
        self._snapshot = data_snapshot_id

    # -- inputs ---------------------------------------------------------------------------

    def load_series(
        self, instrument_ids: Sequence[str], as_of: date, bars: int
    ) -> dict[str, PriceSeries]:
        """The last ``bars`` adjusted bars up to ``as_of`` per instrument, with the feature
        engine's ``atr_pct_14`` (current features version) and ``None`` for missing volume."""
        if not instrument_ids:
            return {}
        rows = self._store.conn.execute(
            """
            WITH p AS (
                SELECT instrument_id, trade_date, high_adj, low_adj, close_adj, volume_adj,
                       row_number() OVER (PARTITION BY instrument_id ORDER BY trade_date DESC) AS rn
                FROM daily_prices_adjusted_current
                WHERE computed_from_snapshot_id = ? AND trade_date <= ?
                  AND instrument_id IN (SELECT unnest(?))
            )
            SELECT p.instrument_id, p.trade_date, p.high_adj, p.low_adj, p.close_adj,
                   p.volume_adj, f.atr_pct_14
            FROM p LEFT JOIN technical_features_daily f
              ON f.instrument_id = p.instrument_id AND f.trade_date = p.trade_date
             AND f.calculation_version = ? AND f.data_snapshot_id = ?
            WHERE p.rn <= ?
            ORDER BY p.instrument_id, p.trade_date
            """,
            [self._snapshot, as_of, list(instrument_ids), FEATURES_CALCULATION_VERSION,
             self._snapshot, bars],
        ).fetchall()  # fmt: skip
        cols: dict[str, list[list[Any]]] = {}
        for iid, d, h, lo, c, v, atr in rows:
            cs = cols.setdefault(iid, [[], [], [], [], [], []])
            for k, val in enumerate((d, h, lo, c, None if v is None else float(v), atr)):
                cs[k].append(val)
        return {iid: PriceSeries(*cs) for iid, cs in cols.items()}

    def prior_patterns(
        self, instrument_ids: Sequence[str], as_of: date, config_hash: str
    ) -> dict[str, PriorPattern]:
        """Each instrument's primary pattern on its latest scan date before ``as_of``."""
        rows = self._store.conn.execute(
            """
            SELECT instrument_id, as_of_date, base_start_date, pivot_price, pivot_date,
                   pivot_source, classification, status
            FROM vcp_patterns
            WHERE is_primary AND config_hash = ? AND data_snapshot_id = ? AND as_of_date < ?
              AND instrument_id IN (SELECT unnest(?))
            QUALIFY row_number() OVER (PARTITION BY instrument_id ORDER BY as_of_date DESC) = 1
            """,
            [config_hash, self._snapshot, as_of, list(instrument_ids)],
        ).fetchall()
        return {r[0]: PriorPattern(*r[1:]) for r in rows}

    def previous_scan_classes(
        self, as_of: date, config_hash: str
    ) -> tuple[date | None, dict[str, tuple[str, str]]]:
        """The latest earlier scan date with this config and its primary (class, status) per
        instrument (status-history baseline)."""
        row = self._store.conn.execute(
            "SELECT max(as_of_date) FROM vcp_patterns WHERE config_hash = ?"
            " AND data_snapshot_id = ? AND as_of_date < ?",
            [config_hash, self._snapshot, as_of],
        ).fetchone()
        prev = row[0] if row else None
        if prev is None:
            return None, {}
        rows = self._store.conn.execute(
            "SELECT instrument_id, classification, status FROM vcp_patterns"
            " WHERE is_primary AND config_hash = ? AND data_snapshot_id = ? AND as_of_date = ?",
            [config_hash, self._snapshot, prev],
        ).fetchall()
        return prev, {r[0]: (r[1], r[2]) for r in rows}

    def events(
        self, instrument_ids: Sequence[str], as_of: date, config_hash: str
    ) -> dict[tuple[str, date], BreakoutEvent]:
        """Breakout events detected *before* ``as_of``, by (instrument, base start).

        Events detected on ``as_of`` itself are produced by the scan of that date (and replaced
        when it is rerun), so they are not read back as history.
        """
        rows = self._store.conn.execute(
            """
            SELECT breakout_event_id, instrument_id, base_start_date, breakout_date, pivot_price,
                   pivot_date, pivot_source, volume_ratio, detected_as_of, method
            FROM vcp_breakout_events
            WHERE config_hash = ? AND detected_as_of < ? AND instrument_id IN (SELECT unnest(?))
            """,
            [config_hash, as_of, list(instrument_ids)],
        ).fetchall()
        return {(r[1], r[2]): BreakoutEvent(*r) for r in rows}

    # -- outputs --------------------------------------------------------------------------

    def save_scan(
        self,
        scan_id: str,
        as_of: date,
        config_hash: str,
        algorithm_version: str,
        detections: Sequence[VCPDetection],
        new_events: Sequence[BreakoutEvent],
        created_at: datetime,
    ) -> None:
        """Replace every row of ``scan_id`` (and this date's history and new events)."""
        conn = self._store.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            old = "SELECT vcp_pattern_id FROM vcp_patterns WHERE scan_id = ?"
            conn.execute(f"DELETE FROM vcp_contractions WHERE vcp_pattern_id IN ({old})", [scan_id])  # noqa: S608
            conn.execute(f"DELETE FROM vcp_pivots WHERE vcp_pattern_id IN ({old})", [scan_id])  # noqa: S608
            conn.execute("DELETE FROM vcp_patterns WHERE scan_id = ?", [scan_id])
            conn.execute(
                "DELETE FROM vcp_status_history WHERE as_of_date = ? AND config_hash = ?",
                [as_of, config_hash],
            )
            # Events detected on this date are regenerated below; events detected later were
            # built on history this rerun changes, so they go too (rerun later dates in order).
            conn.execute(
                "DELETE FROM vcp_breakout_events WHERE detected_as_of >= ? AND config_hash = ?",
                [as_of, config_hash],
            )
            prev_date, prev = self.previous_scan_classes(as_of, config_hash)
            for e in new_events:
                conn.execute(
                    "INSERT INTO vcp_breakout_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [e.breakout_event_id, e.instrument_id, e.base_start, config_hash,
                     e.breakout_date, e.pivot_price, e.pivot_date, e.pivot_source,
                     e.volume_ratio, e.detected_as_of, e.method, created_at],
                )  # fmt: skip
            event_by_base = {(e.instrument_id, e.base_start): e.breakout_event_id
                             for e in new_events}  # fmt: skip
            for d in detections:
                p = d.pattern
                if p is not None:
                    self._insert_pattern(scan_id, d, p, created_at, event_by_base)
                self._history(d, as_of, config_hash, algorithm_version, prev_date, prev, scan_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _history(
        self,
        d: VCPDetection,
        as_of: date,
        config_hash: str,
        algorithm_version: str,
        prev_date: date | None,
        prev: dict[str, tuple[str, str]],
        scan_id: str,
    ) -> None:
        """One row when the primary class or status changed since the previous scan date."""
        p = d.pattern
        new_cls = p.classification.value if p else "NONE"
        new_status = p.status.value if p else (d.status.value if d.status else None)
        old = prev.get(d.instrument_id)
        old_cls, old_status = old if old else ("NONE", None)
        if (old_cls, old_status) == (new_cls, new_status):
            return
        if old is None and p is None:
            return  # nothing before, nothing now
        reason = (
            d.no_pattern_reason
            if p is None
            else (",".join(r.value for r in p.invalidation_reasons) or None)
        )
        self._store.conn.execute(
            "INSERT INTO vcp_status_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [d.instrument_id, as_of, config_hash,
             pattern_id(scan_id, d.instrument_id, p.base_start) if p else None,
             prev_date if old else None, old_cls if old else None, new_cls,
             old_status, new_status, reason, algorithm_version],
        )  # fmt: skip

    def _insert_pattern(
        self,
        scan_id: str,
        d: VCPDetection,
        p: VCPPattern,
        created_at: datetime,
        event_by_base: dict[tuple[str, date], str],
    ) -> None:
        conn = self._store.conn
        pid = pattern_id(scan_id, p.instrument_id, p.base_start)
        m = d.measurements
        unmet = (
            {k.value: list(v) for k, v in d.classification.unmet.items()}
            if d.classification
            else {}
        )
        event_id = event_by_base.get((p.instrument_id, p.base_start))
        if event_id is None and p.base_end is not None:
            row = conn.execute(
                "SELECT breakout_event_id FROM vcp_breakout_events WHERE instrument_id = ?"
                " AND base_start_date = ? AND config_hash = ?",
                [p.instrument_id, p.base_start, p.config_hash],
            ).fetchone()
            event_id = row[0] if row else None
        conn.execute(
            f"INSERT INTO vcp_patterns VALUES ({', '.join(['?'] * 45)})",
            [
                pid, scan_id, p.instrument_id, p.as_of_date, p.is_primary,
                p.base_start, p.base_end, p.base_high, p.base_low, p.base_depth_pct,
                p.base_duration_days, p.prior_advance_return_pct, p.contraction_count,
                p.first_contraction_pct, p.final_contraction_pct, p.max_tightening_ratio,
                p.progressive_tightening, p.final_volume_ratio, p.volume_dryup_pass,
                p.atr_contraction_ratio, m.tr_contraction_ratio if m else None,
                p.volatility_contraction_pass, p.right_side_range_pct, p.tight_pivot_pass,
                p.tightening_quality, p.volatility_quality, p.volume_quality, p.pivot_quality,
                p.base_quality,
                p.pivot.pivot_price if p.pivot else None,
                p.pivot.pivot_date if p.pivot else None,
                p.pivot.source.value if p.pivot else None,
                p.pivot_distance_pct,
                p.classification.value, p.status.value, p.confirmation_state.value,
                ",".join(r.value for r in p.invalidation_reasons) or None,
                json.dumps(unmet, sort_keys=True), event_id,
                p.trend_template_pass, p.weekly_stage2_pass, p.algorithm_version,
                p.config_hash, self._snapshot, created_at,
            ],
        )  # fmt: skip
        ratios = (None, *p.tightening_ratios)
        tr = [c.tr_pct for c in m.contractions] if m else [None] * len(p.contractions)
        if p.contractions:
            conn.executemany(
                "INSERT INTO vcp_contractions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    [pid, c.sequence_number, c.peak_date, c.peak_price, c.trough_date,
                     c.trough_price, c.depth_pct, c.duration_days, c.atr_pct, tr[k], c.range_pct,
                     c.volume_ratio, ratios[k], c.confirmation_date, c.is_confirmed]
                    for k, c in enumerate(p.contractions)
                ],
            )  # fmt: skip
        structural = d.pivots.structural if d.pivots else None
        if p.pivot_candidates:
            conn.executemany(
                "INSERT INTO vcp_pivots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    [pid, k + 1, c.pivot_price, c.pivot_date, c.source.value, c.touches,
                     c.rejection_count, c.right_side_tightness_pct, c.distance_to_close_pct,
                     c == p.pivot, c == structural]
                    for k, c in enumerate(p.pivot_candidates)
                ],
            )  # fmt: skip

    def later_scan_dates(self, as_of: date, config_hash: str) -> list[date]:
        """VCP scan dates after ``as_of`` with the same config (they need a rerun in order when
        ``as_of`` is recomputed: their breakout events were removed)."""
        return [
            r[0]
            for r in self._store.conn.execute(
                "SELECT DISTINCT as_of_date FROM vcp_patterns WHERE config_hash = ?"
                " AND data_snapshot_id = ? AND as_of_date > ? ORDER BY 1",
                [config_hash, self._snapshot, as_of],
            ).fetchall()
        ]

    def load_results(self, scan_id: str) -> list[tuple[Any, ...]]:
        return self._store.conn.execute(
            "SELECT instrument_id, classification, status, confirmation_state, pivot_price"
            " FROM vcp_patterns WHERE scan_id = ? AND is_primary ORDER BY instrument_id",
            [scan_id],
        ).fetchall()

    def record_run_results(self, scan_run_id: str, rows: Sequence[Sequence[Any]]) -> None:
        if rows:
            self._store.conn.executemany(
                "INSERT INTO vcp_scan_run_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(scan_run_id, *r) for r in rows],
            )
