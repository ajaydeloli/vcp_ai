"""DuckDB persistence for Trend Template, weekly-context and RS reads.

Tables: ``trend_template_results``, ``trend_template_conditions``, ``weekly_context``
(DATABASE_SCHEMA section 30) and ``relative_strength_snapshots`` (section 26, read only).
All writes are idempotent upserts so a re-run with the same inputs yields the same rows.

The repository is bound to one data snapshot (audit finding P0-1): every row it writes is
tagged with ``data_snapshot_id`` and every read is scoped to it, so results computed from
different price knowledge never overwrite or leak into each other. ``LIVE`` is unfrozen
working data.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from vcp_scanner.data.repositories.base import TrendRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import WeeklyStage
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID, validate_snapshot_id
from vcp_scanner.domain.trend import (
    RelativeStrengthResult,
    TrendConditionResult,
    TrendTemplateResult,
    WeeklyContext,
)

_UPSERT_RESULT = """
    INSERT INTO trend_template_results (
        scan_id, instrument_id, as_of_date, status, trend_template_pass,
        weekly_stage, weekly_stage2_pass, sma_w, slope_pct, is_partial_week,
        rs_rank, trend_score, calculation_version, config_hash, data_snapshot_id, blocked_by
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (scan_id, instrument_id) DO UPDATE SET
        as_of_date = EXCLUDED.as_of_date,
        status = EXCLUDED.status,
        trend_template_pass = EXCLUDED.trend_template_pass,
        weekly_stage = EXCLUDED.weekly_stage,
        weekly_stage2_pass = EXCLUDED.weekly_stage2_pass,
        sma_w = EXCLUDED.sma_w,
        slope_pct = EXCLUDED.slope_pct,
        is_partial_week = EXCLUDED.is_partial_week,
        rs_rank = EXCLUDED.rs_rank,
        trend_score = EXCLUDED.trend_score,
        calculation_version = EXCLUDED.calculation_version,
        config_hash = EXCLUDED.config_hash,
        data_snapshot_id = EXCLUDED.data_snapshot_id,
        blocked_by = EXCLUDED.blocked_by
"""

_UPSERT_CONDITION = """
    INSERT INTO trend_template_conditions (
        instrument_id, as_of_date, condition_id, condition_name,
        measurement, threshold, passed, calculation_version, config_hash, data_snapshot_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (
        instrument_id, as_of_date, condition_id, calculation_version, config_hash,
        data_snapshot_id
    ) DO UPDATE SET
        condition_name = EXCLUDED.condition_name,
        measurement = EXCLUDED.measurement,
        threshold = EXCLUDED.threshold,
        passed = EXCLUDED.passed
"""

_UPSERT_WEEKLY = """
    INSERT INTO weekly_context (
        instrument_id, as_of_date, weekly_stage, sma_w, slope_pct, prior_pct,
        is_partial_week, algorithm_version, data_snapshot_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (instrument_id, as_of_date, algorithm_version, data_snapshot_id) DO UPDATE SET
        weekly_stage = EXCLUDED.weekly_stage,
        sma_w = EXCLUDED.sma_w,
        slope_pct = EXCLUDED.slope_pct,
        prior_pct = EXCLUDED.prior_pct,
        is_partial_week = EXCLUDED.is_partial_week
"""


class DuckDBTrendRepository(TrendRepository):
    def __init__(
        self,
        store: DuckDBStore,
        data_snapshot_id: str = LIVE_SNAPSHOT_ID,
        *,
        rs_universe_snapshot_id: str | None = None,
    ) -> None:
        self.store = store
        self.data_snapshot_id = validate_snapshot_id(data_snapshot_id)
        # Audit P1-8 (D2): read RS ranked over exactly this universe snapshot (the one the
        # scan evaluates) instead of whichever universe was created last.
        self.rs_universe_snapshot_id = rs_universe_snapshot_id

    def save_trend_template_results(
        self,
        scan_id: str,
        config_hash: str,
        results: list[TrendTemplateResult],
    ) -> None:
        if not results:
            return

        summary_rows: list[tuple[Any, ...]] = []
        condition_rows: list[tuple[Any, ...]] = []
        for r in results:
            w = r.weekly_context
            summary_rows.append(
                (
                    scan_id,
                    r.instrument_id,
                    r.as_of_date,
                    r.status.value,
                    r.trend_template_pass,
                    w.weekly_stage.value if w is not None else None,
                    w.weekly_stage2_pass if w is not None else None,
                    w.sma_w if w is not None else None,
                    w.slope_pct if w is not None else None,
                    w.is_partial_week if w is not None else None,
                    r.rs_rank,
                    None,  # trend_score: SCORING_SPECIFICATION section 3, Phase 7
                    r.algorithm_version,
                    config_hash,
                    self.data_snapshot_id,
                    ",".join(r.blocked_by) or None,
                )
            )
            condition_rows.extend(
                (
                    r.instrument_id,
                    r.as_of_date,
                    c.condition_id,
                    c.name,
                    c.measurement,
                    c.threshold,
                    c.passed,
                    r.algorithm_version,
                    config_hash,
                    self.data_snapshot_id,
                )
                for c in r.conditions
            )

        conn = self.store.conn
        conn.execute("BEGIN TRANSACTION")
        try:
            self.store.upsert_rows(_UPSERT_RESULT, summary_rows)
            self.store.upsert_rows(_UPSERT_CONDITION, condition_rows)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def load_trend_conditions(
        self,
        instrument_id: str,
        as_of_date: date,
        calculation_version: str,
        config_hash: str,
    ) -> list[TrendConditionResult]:
        sql = """
            SELECT condition_id, condition_name, measurement, threshold, passed
            FROM trend_template_conditions
            WHERE instrument_id = ? AND as_of_date = ? AND calculation_version = ?
              AND config_hash = ? AND data_snapshot_id = ?
            ORDER BY condition_id
        """
        rows = self.store.conn.execute(
            sql,
            [instrument_id, as_of_date, calculation_version, config_hash, self.data_snapshot_id],
        ).fetchall()
        return [
            TrendConditionResult(
                condition_id=row[0],
                name=row[1],
                measurement=row[2],
                threshold=row[3],
                passed=row[4],
            )
            for row in rows
        ]

    def save_weekly_context(self, contexts: list[WeeklyContext]) -> None:
        if not contexts:
            return
        rows = [
            (
                c.instrument_id,
                c.as_of_date,
                c.weekly_stage.value,
                c.sma_w,
                c.slope_pct,
                c.prior_pct,
                c.is_partial_week,
                c.algorithm_version,
                self.data_snapshot_id,
            )
            for c in contexts
        ]
        self.store.upsert_rows(_UPSERT_WEEKLY, rows)

    def load_weekly_context(
        self,
        instrument_id: str,
        as_of_date: date,
        algorithm_version: str,
    ) -> WeeklyContext | None:
        sql = """
            SELECT instrument_id, as_of_date, weekly_stage, sma_w, slope_pct, prior_pct,
                   is_partial_week, algorithm_version
            FROM weekly_context
            WHERE instrument_id = ? AND as_of_date = ? AND algorithm_version = ?
              AND data_snapshot_id = ?
        """
        row = self.store.conn.execute(
            sql, [instrument_id, as_of_date, algorithm_version, self.data_snapshot_id]
        ).fetchone()
        if row is None:
            return None
        return WeeklyContext(
            instrument_id=row[0],
            as_of_date=row[1],
            weekly_stage=WeeklyStage(row[2]),
            sma_w=row[3],
            slope_pct=row[4],
            prior_pct=row[5],
            is_partial_week=row[6],
            algorithm_version=row[7],
        )

    def load_relative_strength(
        self,
        instrument_id: str,
        as_of_date: date,
        calculation_version: str,
        universe_snapshot_id: str | None = None,
    ) -> RelativeStrengthResult | None:
        """RS row for this data snapshot; ``universe_snapshot_id`` picks the ranking universe.

        Without it, the row ranked over the most recently created universe snapshot wins
        (deterministic tie-break on the universe id), so a re-run over a newer universe
        supersedes older ones without deleting them.
        """
        universe_snapshot_id = universe_snapshot_id or self.rs_universe_snapshot_id
        universe_clause = "AND r.universe_snapshot_id = ?" if universe_snapshot_id else ""
        params: list[object] = [instrument_id, as_of_date, calculation_version]
        params.append(self.data_snapshot_id)
        if universe_snapshot_id:
            params.append(universe_snapshot_id)
        sql = f"""
            SELECT r.instrument_id, r.as_of_date, r.ret_63, r.ret_126, r.ret_189, r.ret_252,
                   r.rs_raw, r.rs_rank, r.population_size, r.calculation_version
            FROM relative_strength_snapshots r
            LEFT JOIN universe_snapshots u ON u.universe_snapshot_id = r.universe_snapshot_id
            WHERE r.instrument_id = ? AND r.as_of_date = ? AND r.calculation_version = ?
              AND r.data_snapshot_id = ? {universe_clause}
            ORDER BY u.created_at DESC NULLS LAST, r.universe_snapshot_id DESC
            LIMIT 1
        """  # noqa: S608 - the only interpolated text is a fixed literal; values are bound
        row = self.store.conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return RelativeStrengthResult(
            instrument_id=row[0],
            as_of_date=row[1],
            return_63d=row[2],
            return_126d=row[3],
            return_189d=row[4],
            return_252d=row[5],
            rs_raw=row[6],
            rs_rank=row[7],
            population_size=int(row[8]) if row[8] is not None else 0,
            calculation_version=row[9],
        )
