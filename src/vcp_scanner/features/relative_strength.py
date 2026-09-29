import logging
from datetime import date

from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)


class RelativeStrengthEngine:
    """Calculates relative strength across a universe snapshot."""

    def __init__(self, store: DuckDBStore, calculation_version: str = "rs-1.0.0") -> None:
        self.store = store
        self.calculation_version = calculation_version

    def compute_for_date(self, as_of_date: date, universe_snapshot_id: str) -> int:
        """
        Compute RS for all eligible instruments in the universe as of a specific date.

        Formula:
        R_n = adj_close(t) / adj_close(t - n) - 1
        rs_raw = 0.40*R_63 + 0.20*R_126 + 0.20*R_189 + 0.20*R_252
        """

        # We need to get the `close_adj` at `as_of_date` (or the last available before it)
        # And the `close_adj` exactly `N` trading days prior. Since DuckDB window functions can get the Nth preceding row:
        # We can calculate it for all history and pick the `as_of_date`, but it's more efficient to just
        # pull the history for the instruments and assign row numbers descending from `as_of_date`.

        sql = """
            WITH universe_instruments AS (
                SELECT instrument_id
                FROM universe_memberships
                WHERE universe_snapshot_id = ? AND eligible = TRUE
            ),
            daily_series AS (
                SELECT
                    d.instrument_id,
                    d.trade_date,
                    d.close_adj,
                    ROW_NUMBER() OVER (PARTITION BY d.instrument_id ORDER BY d.trade_date DESC) as rn
                FROM daily_prices_adjusted d
                JOIN universe_instruments u ON d.instrument_id = u.instrument_id
                WHERE d.trade_date <= ?
            ),
            -- We need rn=1 (as_of), rn=1+63 (63 days ago), 1+126, 1+189, 1+252
            -- Or if history is missing, it won't join correctly.
            returns AS (
                SELECT
                    t0.instrument_id,
                    t0.close_adj as close_0,
                    t63.close_adj as close_63,
                    t126.close_adj as close_126,
                    t189.close_adj as close_189,
                    t252.close_adj as close_252,
                    (t0.close_adj / t63.close_adj) - 1 AS ret_63,
                    (t0.close_adj / t126.close_adj) - 1 AS ret_126,
                    (t0.close_adj / t189.close_adj) - 1 AS ret_189,
                    (t0.close_adj / t252.close_adj) - 1 AS ret_252
                FROM (SELECT * FROM daily_series WHERE rn = 1) t0
                LEFT JOIN (SELECT * FROM daily_series WHERE rn = 64) t63 ON t0.instrument_id = t63.instrument_id
                LEFT JOIN (SELECT * FROM daily_series WHERE rn = 127) t126 ON t0.instrument_id = t126.instrument_id
                LEFT JOIN (SELECT * FROM daily_series WHERE rn = 190) t189 ON t0.instrument_id = t189.instrument_id
                LEFT JOIN (SELECT * FROM daily_series WHERE rn = 253) t252 ON t0.instrument_id = t252.instrument_id
            ),
            rs_calc AS (
                SELECT
                    instrument_id,
                    ret_63,
                    ret_126,
                    ret_189,
                    ret_252,
                    CASE
                        WHEN ret_252 IS NULL THEN NULL
                        ELSE 0.40 * ret_63 + 0.20 * ret_126 + 0.20 * ret_189 + 0.20 * ret_252
                    END AS rs_raw,
                    CASE WHEN ret_252 IS NULL THEN 'INSUFFICIENT_DATA' ELSE 'PASS' END AS rs_status
                FROM returns
            ),
            population AS (
                SELECT COUNT(*) as pop_size FROM rs_calc WHERE rs_raw IS NOT NULL
            ),
            rankings AS (
                SELECT
                    r.instrument_id,
                    r.ret_63,
                    r.ret_126,
                    r.ret_189,
                    r.ret_252,
                    r.rs_raw,
                    r.rs_status,
                    (SELECT pop_size FROM population) AS population_size,
                    -- pct = (count_below + 0.5*count_equal) / N
                    (
                        (SELECT COUNT(*) FROM rs_calc WHERE rs_raw < r.rs_raw) +
                        0.5 * (SELECT COUNT(*) FROM rs_calc WHERE rs_raw = r.rs_raw)
                    ) / NULLIF((SELECT pop_size FROM population), 0) AS rs_percentile
                FROM rs_calc r
            )
            INSERT INTO relative_strength_snapshots (
                as_of_date, instrument_id, ret_63, ret_126, ret_189, ret_252,
                rs_raw, rs_rank, rs_percentile, population_size, rs_status,
                universe_snapshot_id, calculation_version
            )
            SELECT
                ? AS as_of_date,
                instrument_id,
                ret_63, ret_126, ret_189, ret_252,
                rs_raw,
                CASE WHEN rs_raw IS NOT NULL THEN 1 + CAST(FLOOR(98 * rs_percentile) AS INTEGER) ELSE NULL END AS rs_rank,
                rs_percentile,
                population_size,
                rs_status,
                ? AS universe_snapshot_id,
                ? AS calculation_version
            FROM rankings
            ON CONFLICT (as_of_date, instrument_id, calculation_version) DO UPDATE SET
                ret_63 = EXCLUDED.ret_63,
                ret_126 = EXCLUDED.ret_126,
                ret_189 = EXCLUDED.ret_189,
                ret_252 = EXCLUDED.ret_252,
                rs_raw = EXCLUDED.rs_raw,
                rs_rank = EXCLUDED.rs_rank,
                rs_percentile = EXCLUDED.rs_percentile,
                population_size = EXCLUDED.population_size,
                rs_status = EXCLUDED.rs_status,
                universe_snapshot_id = EXCLUDED.universe_snapshot_id
        """

        cursor = self.store.conn.cursor()
        cursor.execute(
            sql,
            [
                universe_snapshot_id,
                as_of_date,
                as_of_date,
                universe_snapshot_id,
                self.calculation_version,
            ],
        )
        return 1
