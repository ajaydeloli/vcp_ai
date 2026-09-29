import logging
from datetime import date

from vcp_scanner.config.models import RSConfig
from vcp_scanner.data.storage.duckdb_store import DuckDBStore

logger = logging.getLogger(__name__)

# relative_strength_snapshots stores exactly four return columns (ret_63..ret_252).
_RETURN_COLUMNS = 4


class RelativeStrengthEngine:
    """Calculates relative strength across a universe snapshot."""

    def __init__(
        self,
        store: DuckDBStore,
        calculation_version: str | None = None,
        config: RSConfig | None = None,
    ) -> None:
        self.store = store
        self.config = config or RSConfig()
        self.calculation_version = calculation_version or self.config.version
        if len(self.config.windows_days) != _RETURN_COLUMNS:
            raise ValueError(
                f"relative_strength_snapshots stores {_RETURN_COLUMNS} return columns; "
                f"RSConfig.windows_days has {len(self.config.windows_days)}"
            )

    def compute_for_date(self, as_of_date: date, universe_snapshot_id: str) -> int:
        """
        Compute RS for all eligible instruments in the universe as of a specific date.

        Formula (windows and weights come from RSConfig, TREND_TEMPLATE_SPECIFICATION 3):
        R_n = adj_close(t) / adj_close(t - n) - 1
        rs_raw = sum(weight_i * R_window_i)   (default 0.40/0.20/0.20/0.20 over 63/126/189/252)

        Statuses (AGENTS.md rule 4, missing is not zero):
        - PASS               ranked; rs_raw / rs_rank / rs_percentile are set
        - INSUFFICIENT_DATA  not enough history for the longest window; rs_raw, rs_rank and
                             rs_percentile are NULL, and the instrument is not in the
                             ranking population
        - STALE_DATA         latest bar older than max_staleness_days before as_of_date;
                             treated like INSUFFICIENT_DATA for ranking
        """
        windows = self.config.windows_days
        weights = self.config.weights
        max_stale = int(self.config.max_staleness_days)

        # Only ints/floats from validated config are interpolated; values from data are bound.
        join_sql = "\n".join(
            f"LEFT JOIN (SELECT * FROM daily_series WHERE rn = {int(n) + 1}) w{k} "
            f"ON t0.instrument_id = w{k}.instrument_id"
            for k, n in enumerate(windows)
        )
        ret_sql = ",\n".join(
            f"(t0.close_adj / NULLIF(w{k}.close_adj, 0)) - 1 AS ret_{k}"
            for k in range(_RETURN_COLUMNS)
        )
        # NULL propagates: one missing return makes rs_raw NULL.
        raw_sql = " + ".join(f"{float(w)!r} * ret_{k}" for k, w in enumerate(weights))

        sql = f"""
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
                    ROW_NUMBER() OVER (
                        PARTITION BY d.instrument_id ORDER BY d.trade_date DESC
                    ) AS rn
                FROM daily_prices_adjusted_current d
                JOIN universe_instruments u ON d.instrument_id = u.instrument_id
                WHERE d.trade_date <= ?
            ),
            returns AS (
                SELECT
                    t0.instrument_id,
                    t0.trade_date AS last_trade_date,
                    {ret_sql}
                FROM (SELECT * FROM daily_series WHERE rn = 1) t0
                {join_sql}
            ),
            rs_calc AS (
                SELECT
                    instrument_id,
                    ret_0, ret_1, ret_2, ret_3,
                    (CAST(? AS DATE) - last_trade_date) > {max_stale} AS is_stale,
                    {raw_sql} AS rs_raw_candidate
                FROM returns
            ),
            rs_final AS (
                SELECT
                    instrument_id,
                    ret_0, ret_1, ret_2, ret_3,
                    CASE WHEN is_stale THEN NULL ELSE rs_raw_candidate END AS rs_raw,
                    CASE
                        WHEN is_stale THEN 'STALE_DATA'
                        WHEN rs_raw_candidate IS NULL THEN 'INSUFFICIENT_DATA'
                        ELSE 'PASS'
                    END AS rs_status
                FROM rs_calc
            ),
            population AS (
                SELECT COUNT(*) as pop_size FROM rs_final WHERE rs_raw IS NOT NULL
            ),
            rankings AS (
                SELECT
                    r.*,
                    (SELECT pop_size FROM population) AS population_size,
                    -- pct = (count_below + 0.5*count_equal) / N; NULL when not ranked.
                    -- COUNT(*) over a NULL comparison is 0, so guard explicitly: an
                    -- unranked instrument must get NULL, never 0.0.
                    CASE WHEN r.rs_raw IS NULL THEN NULL ELSE (
                        (SELECT COUNT(*) FROM rs_final WHERE rs_raw < r.rs_raw) +
                        0.5 * (SELECT COUNT(*) FROM rs_final WHERE rs_raw = r.rs_raw)
                    ) / NULLIF((SELECT pop_size FROM population), 0) END AS rs_percentile
                FROM rs_final r
            )
            INSERT INTO relative_strength_snapshots (
                as_of_date, instrument_id, ret_63, ret_126, ret_189, ret_252,
                rs_raw, rs_rank, rs_percentile, population_size, rs_status,
                universe_snapshot_id, calculation_version
            )
            SELECT
                ? AS as_of_date,
                instrument_id,
                ret_0, ret_1, ret_2, ret_3,
                rs_raw,
                CASE WHEN rs_percentile IS NOT NULL
                     THEN 1 + CAST(FLOOR(98 * rs_percentile) AS INTEGER) ELSE NULL END AS rs_rank,
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
        result = cursor.execute(
            sql,
            [
                universe_snapshot_id,
                as_of_date,
                as_of_date,  # staleness reference
                as_of_date,
                universe_snapshot_id,
                self.calculation_version,
            ],
        ).fetchone()
        return int(result[0]) if result else 0
