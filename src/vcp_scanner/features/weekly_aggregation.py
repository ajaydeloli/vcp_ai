import logging

from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class WeeklyAggregationEngine:
    """Derives weekly prices from daily prices."""

    def __init__(self, store: DuckDBStore, source_daily_version: str = "daily-adj-1.0.0") -> None:
        self.store = store
        self.source_daily_version = source_daily_version

    def compute_for_instrument(self, instrument_id: str) -> int:
        """
        Aggregate weekly OHLCV for an instrument.
        Week is defined by Monday-Sunday (ISO week). The week_end is the maximum trade_date in that week.
        """
        sql = """
            WITH week_group AS (
                SELECT
                    instrument_id,
                    date_trunc('week', trade_date) AS week_start,
                    MIN(trade_date) AS first_trade_date,
                    MAX(trade_date) AS last_trade_date,
                    SUM(volume_adj) AS volume,
                    MAX(high_adj) AS high,
                    MIN(low_adj) AS low
                FROM daily_prices_adjusted
                WHERE instrument_id = ?
                GROUP BY instrument_id, date_trunc('week', trade_date)
            ),
            first_last_prices AS (
                SELECT
                    d.instrument_id,
                    date_trunc('week', d.trade_date) AS week_start,
                    d.trade_date,
                    d.open_adj,
                    d.close_adj
                FROM daily_prices_adjusted d
                WHERE d.instrument_id = ?
            ),
            weekly_ohlcv AS (
                SELECT
                    w.instrument_id,
                    w.last_trade_date AS week_end,
                    f_first.open_adj AS open,
                    w.high,
                    w.low,
                    f_last.close_adj AS close,
                    w.volume
                FROM week_group w
                JOIN first_last_prices f_first ON w.instrument_id = f_first.instrument_id
                    AND w.week_start = f_first.week_start
                    AND w.first_trade_date = f_first.trade_date
                JOIN first_last_prices f_last ON w.instrument_id = f_last.instrument_id
                    AND w.week_start = f_last.week_start
                    AND w.last_trade_date = f_last.trade_date
            )
            INSERT INTO weekly_prices (
                instrument_id, week_end, open, high, low, close, volume, source_daily_version
            )
            SELECT
                instrument_id, week_end, open, high, low, close, volume, ?
            FROM weekly_ohlcv
            ON CONFLICT (instrument_id, week_end, source_daily_version) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume
        """

        cursor = self.store.conn.cursor()
        cursor.execute(sql, [instrument_id, instrument_id, self.source_daily_version])
        return 1
