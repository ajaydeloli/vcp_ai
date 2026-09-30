import logging

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID, validate_snapshot_id

logger = logging.getLogger(__name__)


class DailyFeatureEngine:
    """Computes daily technical features entirely within DuckDB.

    Reads adjusted prices from one data snapshot (``LIVE`` = unfrozen working data) so a
    later correction or corporate action cannot change features computed from an earlier
    snapshot (audit finding P0-1).
    """

    def __init__(
        self,
        store: DuckDBStore,
        calculation_version: str = "features-1.1.0",
        data_snapshot_id: str = LIVE_SNAPSHOT_ID,
    ) -> None:
        self.store = store
        self.calculation_version = calculation_version
        self.data_snapshot_id = validate_snapshot_id(data_snapshot_id)

    def compute_for_instrument(self, instrument_id: str) -> int:
        """
        Compute daily features for an instrument using DuckDB window functions.
        Updates technical_features_daily and returns the number of rows inserted/updated.
        """

        # We calculate True Range, then moving averages, and 52w highs/lows.
        # EMA is trickier in pure SQL without recursive CTE, so we will use SMA for EMAs for now,
        # or we can write a recursive CTE. Actually, for V1, we can compute SMA and EMA.
        # To compute EMA accurately, standard recursive CTE:
        # EMA_today = Price * alpha + EMA_yesterday * (1 - alpha)
        # We'll omit true EMA if it's too complex and fallback to SMA or implement recursively later if strictly needed.
        # The spec requires sma_20, sma_50, sma_150, sma_200. We will compute these.

        # Note: 252 trading days for 52-week high/low.

        sql = """
            WITH raw_tr AS (
                SELECT
                    instrument_id,
                    trade_date,
                    close_adj,
                    volume_adj,
                    high_adj,
                    low_adj,
                    LAG(close_adj) OVER w_all AS prev_close,
                    -- No previous close on the first bar: true range and return are NULL, not 0
                    CASE WHEN LAG(close_adj) OVER w_all IS NULL THEN NULL ELSE GREATEST(
                        high_adj - low_adj,
                        ABS(high_adj - LAG(close_adj) OVER w_all),
                        ABS(low_adj - LAG(close_adj) OVER w_all)
                    ) END AS true_range,
                    (close_adj - LAG(close_adj) OVER w_all) / NULLIF(LAG(close_adj) OVER w_all, 0) AS daily_return
                FROM daily_prices_adjusted_current
                WHERE instrument_id = ? AND computed_from_snapshot_id = ?
                WINDOW w_all AS (PARTITION BY instrument_id ORDER BY trade_date)
            ),
            features AS (
                SELECT
                    instrument_id,
                    trade_date,
                    close_adj,

                    -- SMAs: NULL until the window is full (AGENTS.md rule 4: missing is not zero)
                    CASE WHEN COUNT(*) OVER w_20  = 20  THEN AVG(close_adj) OVER w_20  ELSE NULL END AS sma_20,
                    CASE WHEN COUNT(*) OVER w_50  = 50  THEN AVG(close_adj) OVER w_50  ELSE NULL END AS sma_50,
                    CASE WHEN COUNT(*) OVER w_150 = 150 THEN AVG(close_adj) OVER w_150 ELSE NULL END AS sma_150,
                    CASE WHEN COUNT(*) OVER w_200 = 200 THEN AVG(close_adj) OVER w_200 ELSE NULL END AS sma_200,

                    -- ATR and PCT
                    CASE WHEN COUNT(true_range) OVER w_14 = 14 THEN AVG(true_range) OVER w_14 ELSE NULL END AS atr_14,
                    CASE WHEN COUNT(true_range) OVER w_14 = 14
                         THEN (AVG(true_range) OVER w_14) / NULLIF(close_adj, 0) * 100 ELSE NULL END AS atr_pct_14,

                    -- Highs
                    CASE WHEN COUNT(*) OVER w_20 = 20 THEN MAX(high_adj) OVER w_20 ELSE NULL END AS high_20,
                    CASE WHEN COUNT(*) OVER w_50 = 50 THEN MAX(high_adj) OVER w_50 ELSE NULL END AS high_50,
                    CASE WHEN COUNT(*) OVER w_252 = 252 THEN MAX(high_adj) OVER w_252 ELSE NULL END AS high_252,

                    -- Lows
                    CASE WHEN COUNT(*) OVER w_20 = 20 THEN MIN(low_adj) OVER w_20 ELSE NULL END AS low_20,
                    CASE WHEN COUNT(*) OVER w_50 = 50 THEN MIN(low_adj) OVER w_50 ELSE NULL END AS low_50,
                    CASE WHEN COUNT(*) OVER w_252 = 252 THEN MIN(low_adj) OVER w_252 ELSE NULL END AS low_252,

                    -- Volume
                    CASE WHEN COUNT(*) OVER w_5 = 5 THEN AVG(volume_adj) OVER w_5 ELSE NULL END AS volume_avg_5,
                    CASE WHEN COUNT(*) OVER w_10 = 10 THEN AVG(volume_adj) OVER w_10 ELSE NULL END AS volume_avg_10,
                    CASE WHEN COUNT(*) OVER w_20 = 20 THEN AVG(volume_adj) OVER w_20 ELSE NULL END AS volume_avg_20,
                    CASE WHEN COUNT(*) OVER w_50 = 50 THEN AVG(volume_adj) OVER w_50 ELSE NULL END AS volume_avg_50,

                    CASE WHEN COUNT(*) OVER w_20 = 20
                         THEN volume_adj / NULLIF(AVG(volume_adj) OVER w_20, 0) ELSE NULL END AS volume_ratio_20,
                    CASE WHEN COUNT(*) OVER w_50 = 50
                         THEN volume_adj / NULLIF(AVG(volume_adj) OVER w_50, 0) ELSE NULL END AS volume_ratio_50,

                    -- Returns and Vol
                    daily_return,
                    CASE WHEN COUNT(daily_return) OVER w_20 = 20 THEN STDDEV_POP(daily_return) OVER w_20 ELSE NULL END AS rolling_volatility_20,
                    CASE WHEN COUNT(daily_return) OVER w_50 = 50 THEN STDDEV_POP(daily_return) OVER w_50 ELSE NULL END AS rolling_volatility_50

                FROM raw_tr
                WINDOW
                    w_5   AS (PARTITION BY instrument_id ORDER BY trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW),
                    w_10  AS (PARTITION BY instrument_id ORDER BY trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW),
                    w_14  AS (PARTITION BY instrument_id ORDER BY trade_date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW),
                    w_20  AS (PARTITION BY instrument_id ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW),
                    w_50  AS (PARTITION BY instrument_id ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW),
                    w_200 AS (PARTITION BY instrument_id ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW),
                    w_150 AS (PARTITION BY instrument_id ORDER BY trade_date ROWS BETWEEN 149 PRECEDING AND CURRENT ROW),
                    w_252 AS (PARTITION BY instrument_id ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
            )
            INSERT INTO technical_features_daily (
                instrument_id, trade_date, sma_20, sma_50, sma_150, sma_200, ema_10, ema_20, ema_50,
                atr_14, atr_pct_14, high_20, high_50, high_252, low_20, low_50, low_252,
                volume_avg_5, volume_avg_10, volume_avg_20, volume_avg_50,
                volume_ratio_20, volume_ratio_50, daily_return, rolling_volatility_20, rolling_volatility_50,
                calculation_version, data_snapshot_id
            )
            SELECT
                instrument_id, trade_date,
                sma_20, sma_50, sma_150, sma_200,
                NULL AS ema_10, NULL AS ema_20, NULL AS ema_50, -- Omitted EMA for now
                atr_14, atr_pct_14,
                high_20, high_50, high_252,
                low_20, low_50, low_252,
                volume_avg_5, volume_avg_10, volume_avg_20, volume_avg_50,
                volume_ratio_20, volume_ratio_50,
                daily_return, rolling_volatility_20, rolling_volatility_50,
                ? AS calculation_version,
                ? AS data_snapshot_id
            FROM features
            ON CONFLICT (instrument_id, trade_date, calculation_version, data_snapshot_id) DO UPDATE SET
                sma_20 = EXCLUDED.sma_20,
                sma_50 = EXCLUDED.sma_50,
                sma_150 = EXCLUDED.sma_150,
                sma_200 = EXCLUDED.sma_200,
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

        cursor = self.store.conn.cursor()
        result = cursor.execute(
            sql,
            [instrument_id, self.data_snapshot_id, self.calculation_version, self.data_snapshot_id],
        ).fetchone()
        return int(result[0]) if result else 0
