"""Setup scores: inputs and storage (SCORING_SPECIFICATION; DATABASE_SCHEMA 35; Phase 7 step 3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from vcp_scanner.data.repositories.duckdb_vcp_repository import DuckDBVCPRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.scoring.components import SMA200_SLOPE_LAG
from vcp_scanner.scoring.engine import BARS_NEEDED, PatternInputs, ScoredSetup, SetupInputs

SCORE_COLUMNS = (
    "scan_id", "instrument_id", "as_of_date", "classification", "vcp_status", "eligible",
    "trend_score", "vcp_score", "volume_score", "rs_score", "fundamental_score",
    "final_setup_score", "ranking_percentile", "confirmation_state", "fundamental_available",
    "weights_renormalized", "flags", "trend_weight", "vcp_weight", "volume_weight", "rs_weight",
    "fundamental_weight", "scoring_version", "config_hash", "data_snapshot_id", "created_at",
)  # fmt: skip
COMPONENT_COLUMNS = (
    "scan_id", "instrument_id", "component", "sub_component", "raw_measurement",
    "normalized_0_100", "weight_within_component", "points", "max_points", "scoring_version",
)  # fmt: skip


def score_results_hash(rows: Sequence[ScoredSetup]) -> str:
    """SHA-256 over (instrument, eligible, final, percentile), order-independent, rounded."""

    def r6(x: float | None) -> float | None:
        return None if x is None else round(x, 6)

    canon = sorted([s.instrument_id, s.eligible, r6(s.final.final), r6(s.ranking_percentile)]
                   for s in rows)  # fmt: skip
    return hashlib.sha256(json.dumps(canon, separators=(",", ":")).encode()).hexdigest()


class DuckDBScoreRepository:
    def __init__(self, store: DuckDBStore, data_snapshot_id: str = LIVE_SNAPSHOT_ID) -> None:
        self._store = store
        self._snapshot = data_snapshot_id

    def load_inputs(
        self, as_of: date, trend_scan: str, vcp_scan: str, volatility_measure: str
    ) -> list[SetupInputs]:
        """Inputs for every PASS row of ``trend_scan`` (module docstring of scoring.engine)."""
        q = self._store.conn.execute
        passers = q(
            "SELECT instrument_id, rs_rank FROM trend_template_results"
            " WHERE scan_id = ? AND status = 'PASS' ORDER BY instrument_id",
            [trend_scan],
        ).fetchall()
        ids = [r[0] for r in passers]
        if not ids:
            return []
        vol_col = "atr_contraction_ratio" if volatility_measure == "atr" else "tr_contraction_ratio"
        patterns = {
            r[0]: PatternInputs(*r[1:])
            for r in q(
                f"""
                SELECT instrument_id, classification, status, confirmation_state,
                       contraction_count, max_tightening_ratio, final_contraction_pct,
                       {vol_col}, right_side_range_pct, base_depth_pct, final_volume_ratio
                FROM vcp_patterns WHERE scan_id = ? AND is_primary
                """,
                [vcp_scan],
            ).fetchall()
        }
        feats: dict[str, dict[int, tuple[Any, ...]]] = {}
        for iid, rn, close, high_252, sma50, sma200 in q(
            """
            WITH f AS (
                SELECT f.instrument_id, f.trade_date, f.high_252, f.sma_50, f.sma_200,
                       row_number() OVER (PARTITION BY f.instrument_id
                                          ORDER BY f.trade_date DESC) AS rn
                FROM technical_features_daily f
                WHERE f.calculation_version = ? AND f.data_snapshot_id = ?
                  AND f.trade_date <= ? AND f.instrument_id IN (SELECT unnest(?))
            )
            SELECT f.instrument_id, f.rn, p.close_adj, f.high_252, f.sma_50, f.sma_200
            FROM f LEFT JOIN daily_prices_adjusted_current p
              ON p.instrument_id = f.instrument_id AND p.trade_date = f.trade_date
             AND p.computed_from_snapshot_id = ?
            WHERE f.rn IN (1, ?)
            """,
            [FEATURES_CALCULATION_VERSION, self._snapshot, as_of, ids, self._snapshot,
             SMA200_SLOPE_LAG + 1],
        ).fetchall():  # fmt: skip
            feats.setdefault(iid, {})[rn] = (close, high_252, sma50, sma200)
        series = DuckDBVCPRepository(self._store, self._snapshot).load_series(
            ids, as_of, BARS_NEEDED
        )
        out = []
        for iid, rs_rank in passers:
            s = series.get(iid)
            f = feats.get(iid, {})
            now, lag = f.get(1), f.get(SMA200_SLOPE_LAG + 1)
            on_date = s is not None and bool(s.dates) and s.dates[-1] == as_of
            out.append(SetupInputs(
                instrument_id=iid, as_of=as_of,
                rs_rank=None if rs_rank is None else float(rs_rank),
                close=now[0] if now and on_date else None,
                high_252=now[1] if now and on_date else None,
                sma50=now[2] if now and on_date else None,
                sma200=now[3] if now and on_date else None,
                sma200_lagged=lag[3] if lag and now and on_date else None,
                closes=list(s.close) if s and on_date else [],
                volumes=list(s.volume) if s and on_date else [],
                pattern=patterns.get(iid),
            ))  # fmt: skip
        return out

    def save_scan(
        self, scan_id: str, rows: Sequence[ScoredSetup], scoring_version: str,
        config_hash: str, created_at: datetime,
    ) -> None:  # fmt: skip
        """Replace the scan's rows (a rerun of a date overwrites it)."""
        conn = self._store.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("DELETE FROM setup_scores WHERE scan_id = ?", [scan_id])
            conn.execute("DELETE FROM score_components WHERE scan_id = ?", [scan_id])
            score_rows = []
            comp_rows = []
            for s in rows:
                by = {c.component: c.score for c in s.components}
                w = s.final.effective_weights
                score_rows.append((
                    scan_id, s.instrument_id, s.as_of, s.classification, s.status, s.eligible,
                    by.get("TREND"), by.get("VCP"), by.get("VOLUME"), by.get("RS"), None,
                    s.final.final, s.ranking_percentile, s.confirmation_state,
                    s.final.fundamental_available, s.final.weights_renormalized,
                    ",".join(s.final.flags) or None,
                    w["TREND"], w["VCP"], w["VOLUME"], w["RS"], w["FUNDAMENTAL"],
                    scoring_version, config_hash, self._snapshot, created_at,
                ))  # fmt: skip
                for c in s.components:
                    for sub in c.subs:
                        comp_rows.append((
                            scan_id, s.instrument_id, c.component, sub.name, sub.raw,
                            sub.normalized, sub.weight, sub.points, sub.max_points,
                            scoring_version,
                        ))  # fmt: skip
            self._store.insert_rows("setup_scores", SCORE_COLUMNS, score_rows)
            self._store.insert_rows("score_components", COMPONENT_COLUMNS, comp_rows)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
