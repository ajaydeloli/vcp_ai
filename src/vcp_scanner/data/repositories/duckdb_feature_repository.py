from datetime import date
from typing import Any

from vcp_scanner.data.repositories.base import FeatureRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.features import AdjustedClose, DailyFeatures, WeeklyPrice


class DuckDBFeatureRepository(FeatureRepository):
    def __init__(self, store: DuckDBStore) -> None:
        self.store = store

    def save_daily_features(self, features: list[DailyFeatures]) -> None:
        if not features:
            return

        # We assume UPSERT by replacing rows on conflict for idempotent updates
        sql = """
            INSERT INTO technical_features_daily (
                instrument_id, trade_date, sma_20, sma_50, sma_150, sma_200, ema_10, ema_20, ema_50,
                atr_14, atr_pct_14, high_20, high_50, high_252, low_20, low_50, low_252,
                volume_avg_5, volume_avg_10, volume_avg_20, volume_avg_50,
                volume_ratio_20, volume_ratio_50, daily_return,
                rolling_volatility_20, rolling_volatility_50,
                calculation_version
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (instrument_id, trade_date, calculation_version) DO UPDATE SET
                sma_20 = EXCLUDED.sma_20,
                sma_50 = EXCLUDED.sma_50,
                sma_150 = EXCLUDED.sma_150,
                sma_200 = EXCLUDED.sma_200,
                ema_10 = EXCLUDED.ema_10,
                ema_20 = EXCLUDED.ema_20,
                ema_50 = EXCLUDED.ema_50,
                atr_14 = EXCLUDED.atr_14,
                atr_pct_14 = EXCLUDED.atr_pct_14,
                high_20 = EXCLUDED.high_20,
                high_50 = EXCLUDED.high_50,
                high_252 = EXCLUDED.high_252,
                low_20 = EXCLUDED.low_20,
                low_50 = EXCLUDED.low_50,
                low_252 = EXCLUDED.low_252,
                volume_avg_5 = EXCLUDED.volume_avg_5,
                volume_avg_10 = EXCLUDED.volume_avg_10,
                volume_avg_20 = EXCLUDED.volume_avg_20,
                volume_avg_50 = EXCLUDED.volume_avg_50,
                volume_ratio_20 = EXCLUDED.volume_ratio_20,
                volume_ratio_50 = EXCLUDED.volume_ratio_50,
                daily_return = EXCLUDED.daily_return,
                rolling_volatility_20 = EXCLUDED.rolling_volatility_20,
                rolling_volatility_50 = EXCLUDED.rolling_volatility_50
        """

        records = [
            (
                f.instrument_id,
                f.trade_date,
                f.sma_20,
                f.sma_50,
                f.sma_150,
                f.sma_200,
                f.ema_10,
                f.ema_20,
                f.ema_50,
                f.atr_14,
                f.atr_pct_14,
                f.high_20,
                f.high_50,
                f.high_252,
                f.low_20,
                f.low_50,
                f.low_252,
                f.volume_avg_5,
                f.volume_avg_10,
                f.volume_avg_20,
                f.volume_avg_50,
                f.volume_ratio_20,
                f.volume_ratio_50,
                f.daily_return,
                f.rolling_volatility_20,
                f.rolling_volatility_50,
                f.calculation_version,
            )
            for f in features
        ]

        self.store.conn.executemany(sql, records)

    def save_weekly_prices(self, prices: list[WeeklyPrice]) -> None:
        if not prices:
            return

        sql = """
            INSERT INTO weekly_prices (
                instrument_id, week_end, open, high, low, close, volume, source_daily_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (instrument_id, week_end, source_daily_version) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume
        """

        records = [
            (
                p.instrument_id,
                p.week_end,
                p.open,
                p.high,
                p.low,
                p.close,
                p.volume,
                p.source_daily_version,
            )
            for p in prices
        ]

        self.store.conn.executemany(sql, records)

    def load_daily_features(self, instrument_id: str, as_of: date) -> DailyFeatures | None:
        sql = """
            SELECT
                instrument_id, trade_date, sma_20, sma_50, sma_150, sma_200, ema_10, ema_20, ema_50,
                atr_14, atr_pct_14, high_20, high_50, high_252, low_20, low_50, low_252,
                volume_avg_5, volume_avg_10, volume_avg_20, volume_avg_50,
                volume_ratio_20, volume_ratio_50, daily_return,
                rolling_volatility_20, rolling_volatility_50,
                calculation_version
            FROM technical_features_daily
            WHERE instrument_id = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 1
        """
        result = self.store.conn.execute(sql, [instrument_id, as_of]).fetchone()
        if not result:
            return None

        return DailyFeatures(
            instrument_id=result[0],
            trade_date=result[1],
            sma_20=result[2],
            sma_50=result[3],
            sma_150=result[4],
            sma_200=result[5],
            ema_10=result[6],
            ema_20=result[7],
            ema_50=result[8],
            atr_14=result[9],
            atr_pct_14=result[10],
            high_20=result[11],
            high_50=result[12],
            high_252=result[13],
            low_20=result[14],
            low_50=result[15],
            low_252=result[16],
            volume_avg_5=result[17],
            volume_avg_10=result[18],
            volume_avg_20=result[19],
            volume_avg_50=result[20],
            volume_ratio_20=result[21],
            volume_ratio_50=result[22],
            daily_return=result[23],
            rolling_volatility_20=result[24],
            rolling_volatility_50=result[25],
            calculation_version=result[26],
        )

    def load_weekly_prices(self, instrument_id: str, start: date, end: date) -> list[WeeklyPrice]:
        sql = """
            SELECT
                instrument_id, week_end, open, high, low, close, volume, source_daily_version
            FROM weekly_prices
            WHERE instrument_id = ? AND week_end >= ? AND week_end <= ?
            ORDER BY week_end ASC, source_daily_version ASC
        """
        results = self.store.conn.execute(sql, [instrument_id, start, end]).fetchall()
        return [
            WeeklyPrice(
                instrument_id=row[0],
                week_end=row[1],
                open=row[2],
                high=row[3],
                low=row[4],
                close=row[5],
                volume=row[6],
                source_daily_version=row[7],
            )
            for row in results
        ]

    def load_daily_feature_history(
        self,
        instrument_id: str,
        as_of: date,
        limit: int,
        calculation_version: str | None = None,
    ) -> list[DailyFeatures]:
        """Up to ``limit`` feature rows with trade_date <= as_of, newest first."""
        version_clause = "AND calculation_version = ?" if calculation_version is not None else ""
        params: list[Any] = [instrument_id, as_of]
        if calculation_version is not None:
            params.append(calculation_version)
        params.append(limit)
        sql = f"""
            SELECT
                instrument_id, trade_date, sma_20, sma_50, sma_150, sma_200, ema_10, ema_20, ema_50,
                atr_14, atr_pct_14, high_20, high_50, high_252, low_20, low_50, low_252,
                volume_avg_5, volume_avg_10, volume_avg_20, volume_avg_50,
                volume_ratio_20, volume_ratio_50, daily_return, rolling_volatility_20,
                rolling_volatility_50, calculation_version
            FROM technical_features_daily
            WHERE instrument_id = ? AND trade_date <= ? {version_clause}
            ORDER BY trade_date DESC, calculation_version DESC
            LIMIT ?
        """
        rows = self.store.conn.execute(sql, params).fetchall()
        return [DailyFeatures(*row) for row in rows]

    def load_adjusted_closes(
        self,
        instrument_id: str,
        as_of: date,
        limit: int,
    ) -> list[AdjustedClose]:
        """Up to ``limit`` adjusted closes with trade_date <= as_of, newest first.

        If several adjustment versions exist for a date, the most recently computed
        one wins (deterministic tie-break on version name).
        """
        sql = """
            SELECT trade_date, close_adj FROM (
                SELECT
                    trade_date,
                    close_adj,
                    ROW_NUMBER() OVER (
                        PARTITION BY trade_date
                        ORDER BY computed_at DESC, adjustment_version DESC
                    ) AS rn
                FROM daily_prices_adjusted
                WHERE instrument_id = ? AND trade_date <= ?
            )
            WHERE rn = 1
            ORDER BY trade_date DESC
            LIMIT ?
        """
        rows = self.store.conn.execute(sql, [instrument_id, as_of, limit]).fetchall()
        return [AdjustedClose(trade_date=row[0], close=row[1]) for row in rows]
