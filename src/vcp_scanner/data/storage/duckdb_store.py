"""DuckDB catalogue — schema creation and migration (DATABASE_SCHEMA §3, §8, §11–14).

Rules obeyed (AGENTS.md):
- Strategy / pattern code must never import this module directly (rule 3).
  Use repository interfaces instead.
- Raw data tables are append-only; no UPDATE or DELETE helpers are exposed
  on raw_ohlcv (rule 2).
- daily_prices uses bitemporal versioning: corrections append a new row and
  close the previous one; no in-place mutation (rule 2).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL statements — Phase 1 Phase tables only.
# Additional tables (corporate_actions, universe_snapshots, …) are created
# in later phases following the same idempotent migration pattern.
# ---------------------------------------------------------------------------

_DDL_INSTRUMENTS = """
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id   VARCHAR     NOT NULL,
    isin            VARCHAR,
    exchange        VARCHAR     NOT NULL DEFAULT 'NSE',
    segment         VARCHAR,
    symbol          VARCHAR     NOT NULL,
    tradingsymbol   VARCHAR,
    company_name    VARCHAR,
    instrument_type VARCHAR,
    currency        VARCHAR     DEFAULT 'INR',
    tick_size       DOUBLE,
    lot_size        INTEGER,
    valid_from      DATE,
    valid_to        DATE,
    is_active       BOOLEAN     DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id)
)
"""

_DDL_INGESTION_RUNS = """
CREATE TABLE IF NOT EXISTS ingestion_runs (
    ingestion_run_id    VARCHAR     NOT NULL,
    provider            VARCHAR     NOT NULL,
    dataset             VARCHAR     NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    completed_at        TIMESTAMPTZ,
    status              VARCHAR     NOT NULL,   -- RUNNING|SUCCESS|PARTIAL|FAILED
    requested_start     DATE,
    requested_end       DATE,
    records_received    BIGINT      DEFAULT 0,
    records_written     BIGINT      DEFAULT 0,
    records_rejected    BIGINT      DEFAULT 0,
    error_count         BIGINT      DEFAULT 0,
    config_hash         VARCHAR,
    code_version        VARCHAR,
    source_metadata_json VARCHAR,
    PRIMARY KEY (ingestion_run_id)
)
"""

_DDL_RAW_OHLCV = """
CREATE TABLE IF NOT EXISTS raw_ohlcv (
    provider                VARCHAR     NOT NULL,
    provider_instrument_id  VARCHAR     NOT NULL,
    instrument_id           VARCHAR     NOT NULL,
    timestamp               TIMESTAMPTZ NOT NULL,
    interval                VARCHAR     NOT NULL,   -- "1d", "5m", …
    open                    DOUBLE      NOT NULL,
    high                    DOUBLE      NOT NULL,
    low                     DOUBLE      NOT NULL,
    close                   DOUBLE      NOT NULL,
    volume                  BIGINT,                 -- NULL = not supplied
    oi                      DOUBLE,
    received_at             TIMESTAMPTZ NOT NULL,
    ingestion_run_id        VARCHAR     NOT NULL,
    source_hash             VARCHAR     NOT NULL
    -- No PRIMARY KEY: raw storage preserves provenance including re-ingestions.
    -- Logical uniqueness is (provider, provider_instrument_id, timestamp, interval).
    -- The canonical layer deduplicates.
)
"""

_DDL_DAILY_PRICES = """
CREATE TABLE IF NOT EXISTS daily_prices (
    instrument_id       VARCHAR     NOT NULL,
    trade_date          DATE        NOT NULL,
    open_raw            DOUBLE      NOT NULL,
    high_raw            DOUBLE      NOT NULL,
    low_raw             DOUBLE      NOT NULL,
    close_raw           DOUBLE      NOT NULL,
    volume_raw          BIGINT,                     -- NULL = missing, never 0-filled
    primary_provider    VARCHAR     NOT NULL,
    selection_reason    VARCHAR,
    data_status         VARCHAR     NOT NULL DEFAULT 'OK',
    source_run_id       VARCHAR     NOT NULL,
    source_hash         VARCHAR     NOT NULL,
    known_from          TIMESTAMPTZ NOT NULL,       -- system time row became current
    known_to            TIMESTAMPTZ                 -- NULL while current
    -- No PK because bitemporal rows mean multiple rows share (instrument_id, trade_date).
    -- Current row: WHERE known_to IS NULL
    -- Point-in-time read: WHERE known_from <= :known_at
    --   AND (known_to IS NULL OR known_to > :known_at)
)
"""

_DDL_DAILY_PRICES_ADJUSTED = """
CREATE TABLE IF NOT EXISTS daily_prices_adjusted (
    instrument_id               VARCHAR     NOT NULL,
    trade_date                  DATE        NOT NULL,
    open_adj                    DOUBLE      NOT NULL,
    high_adj                    DOUBLE      NOT NULL,
    low_adj                     DOUBLE      NOT NULL,
    close_adj                   DOUBLE      NOT NULL,
    volume_adj                  DOUBLE,
    adjustment_version          VARCHAR     NOT NULL,
    price_factor_applied        DECIMAL(18,8) NOT NULL,
    volume_factor_applied       DECIMAL(18,8) NOT NULL,
    computed_from_snapshot_id   VARCHAR,
    computed_at                 TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, trade_date, adjustment_version)
)
"""

_DDL_CORPORATE_ACTIONS = """
CREATE TABLE IF NOT EXISTS corporate_actions (
    corporate_action_id VARCHAR NOT NULL,
    instrument_id       VARCHAR NOT NULL,
    isin                VARCHAR,
    action_date         DATE,
    announcement_date   DATE,
    ex_date             DATE,
    record_date         DATE,
    action_type         VARCHAR NOT NULL,
    ratio_numerator     DOUBLE,
    ratio_denominator   DOUBLE,
    cash_amount         DOUBLE,
    old_symbol          VARCHAR,
    new_symbol          VARCHAR,
    source              VARCHAR NOT NULL,
    source_record_id    VARCHAR,
    created_at          TIMESTAMPTZ NOT NULL,
    known_from          TIMESTAMPTZ NOT NULL,
    known_to            TIMESTAMPTZ,
    UNIQUE (corporate_action_id, source)
)
"""

_DDL_CORPORATE_ACTION_RESOLUTION = """
CREATE TABLE IF NOT EXISTS corporate_action_resolution (
    resolution_id       VARCHAR NOT NULL,
    instrument_id       VARCHAR NOT NULL,
    isin                VARCHAR,
    action_type         VARCHAR NOT NULL,
    ex_date             DATE,
    ratio_numerator     DOUBLE,
    ratio_denominator   DOUBLE,
    cash_amount         DOUBLE,
    nse_action_id       VARCHAR,
    upstox_action_id    VARCHAR,
    status              VARCHAR NOT NULL,
    conflict_fields     VARCHAR,
    resolved_by         VARCHAR,
    resolved_at         TIMESTAMPTZ,
    known_from          TIMESTAMPTZ NOT NULL,
    known_to            TIMESTAMPTZ
)
"""

_DDL_CORPORATE_ACTION_ADJUSTMENTS = """
CREATE TABLE IF NOT EXISTS corporate_action_adjustments (
    resolution_id            VARCHAR NOT NULL,
    instrument_id            VARCHAR NOT NULL,
    effective_date           DATE NOT NULL,
    price_factor             DECIMAL(18,8) NOT NULL,
    volume_factor            DECIMAL(18,8) NOT NULL,
    cumulative_price_factor  DECIMAL(18,8) NOT NULL,
    cumulative_volume_factor DECIMAL(18,8) NOT NULL,
    source                   VARCHAR NOT NULL,
    calculation_version      VARCHAR NOT NULL,
    known_from               TIMESTAMPTZ NOT NULL,
    known_to                 TIMESTAMPTZ
)
"""

_DDL_SECURITY_MASTER_HISTORY = """
CREATE TABLE IF NOT EXISTS security_master_history (
    instrument_id     VARCHAR NOT NULL,
    isin              VARCHAR,
    symbol            VARCHAR,
    exchange          VARCHAR,
    listing_date      DATE,
    delisting_date    DATE,
    delisting_reason  VARCHAR,
    series            VARCHAR,
    valid_from        DATE NOT NULL,
    valid_to          DATE,
    source            VARCHAR,
    known_from        TIMESTAMPTZ NOT NULL,
    known_to          TIMESTAMPTZ
)
"""

_DDL_SURVEILLANCE_FLAGS_HISTORY = """
CREATE TABLE IF NOT EXISTS surveillance_flags_history (
    instrument_id     VARCHAR NOT NULL,
    flag_type         VARCHAR NOT NULL,
    stage             VARCHAR,
    valid_from        DATE NOT NULL,
    valid_to          DATE,
    source            VARCHAR,
    known_from        TIMESTAMPTZ NOT NULL,
    known_to          TIMESTAMPTZ
)
"""

_DDL_UNIVERSE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS universe_snapshots (
    universe_snapshot_id  VARCHAR PRIMARY KEY,
    universe_name         VARCHAR NOT NULL,
    as_of_date            DATE NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL,
    config_hash           VARCHAR NOT NULL,
    method_version        VARCHAR NOT NULL,
    survivorship_status   VARCHAR NOT NULL
)
"""

_DDL_UNIVERSE_MEMBERSHIPS = """
CREATE TABLE IF NOT EXISTS universe_memberships (
    universe_snapshot_id  VARCHAR NOT NULL,
    instrument_id         VARCHAR NOT NULL,
    eligible              BOOLEAN NOT NULL,
    exclusion_reason      VARCHAR,
    avg_traded_value      DOUBLE,
    price                 DOUBLE,
    instrument_type       VARCHAR,
    series                VARCHAR,
    asm_flag              VARCHAR,
    gsm_flag              VARCHAR,
    t2t_flag              VARCHAR,
    PRIMARY KEY (universe_snapshot_id, instrument_id)
)
"""

_DDL_TECHNICAL_FEATURES_DAILY = """
CREATE TABLE IF NOT EXISTS technical_features_daily (
    instrument_id           VARCHAR     NOT NULL,
    trade_date              DATE        NOT NULL,
    sma_20                  DOUBLE,
    sma_50                  DOUBLE,
    sma_150                 DOUBLE,
    sma_200                 DOUBLE,
    ema_10                  DOUBLE,
    ema_20                  DOUBLE,
    ema_50                  DOUBLE,
    atr_14                  DOUBLE,
    atr_pct_14              DOUBLE,
    high_20                 DOUBLE,
    high_50                 DOUBLE,
    high_252                DOUBLE,
    low_20                  DOUBLE,
    low_50                  DOUBLE,
    low_252                 DOUBLE,
    volume_avg_5            DOUBLE,
    volume_avg_10           DOUBLE,
    volume_avg_20           DOUBLE,
    volume_avg_50           DOUBLE,
    volume_ratio_20         DOUBLE,
    volume_ratio_50         DOUBLE,
    daily_return            DOUBLE,
    rolling_volatility_20   DOUBLE,
    rolling_volatility_50   DOUBLE,
    calculation_version     VARCHAR     NOT NULL,
    PRIMARY KEY (instrument_id, trade_date, calculation_version)
)
"""

_DDL_WEEKLY_PRICES = """
CREATE TABLE IF NOT EXISTS weekly_prices (
    instrument_id           VARCHAR     NOT NULL,
    week_end                DATE        NOT NULL,
    open                    DOUBLE      NOT NULL,
    high                    DOUBLE      NOT NULL,
    low                     DOUBLE      NOT NULL,
    close                   DOUBLE      NOT NULL,
    volume                  DOUBLE,
    source_daily_version    VARCHAR     NOT NULL,
    PRIMARY KEY (instrument_id, week_end, source_daily_version)
)
"""

_DDL_RELATIVE_STRENGTH_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS relative_strength_snapshots (
    as_of_date              DATE        NOT NULL,
    instrument_id           VARCHAR     NOT NULL,
    ret_63                  DOUBLE,
    ret_126                 DOUBLE,
    ret_189                 DOUBLE,
    ret_252                 DOUBLE,
    rs_raw                  DOUBLE,
    rs_rank                 INTEGER,
    rs_percentile           DOUBLE,
    population_size         INTEGER,
    rs_status               VARCHAR,
    universe_snapshot_id    VARCHAR     NOT NULL,
    calculation_version     VARCHAR     NOT NULL,
    PRIMARY KEY (as_of_date, instrument_id, calculation_version)
)
"""

_DDL_TREND_TEMPLATE_RESULTS = """
CREATE TABLE IF NOT EXISTS trend_template_results (
    scan_id                 VARCHAR     NOT NULL,
    instrument_id           VARCHAR     NOT NULL,
    as_of_date              DATE        NOT NULL,
    status                  VARCHAR     NOT NULL,
    trend_template_pass     BOOLEAN,
    weekly_stage            VARCHAR,
    weekly_stage2_pass      BOOLEAN,
    sma_w                   DOUBLE,
    slope_pct               DOUBLE,
    is_partial_week         BOOLEAN,
    rs_rank                 INTEGER,
    trend_score             DOUBLE,
    calculation_version     VARCHAR     NOT NULL,
    config_hash             VARCHAR     NOT NULL,
    PRIMARY KEY (scan_id, instrument_id)
)
"""

_DDL_TREND_TEMPLATE_CONDITIONS = """
CREATE TABLE IF NOT EXISTS trend_template_conditions (
    instrument_id           VARCHAR     NOT NULL,
    as_of_date              DATE        NOT NULL,
    condition_id            INTEGER     NOT NULL,
    condition_name          VARCHAR     NOT NULL,
    measurement             DOUBLE,
    threshold               DOUBLE,
    passed                  BOOLEAN,
    calculation_version     VARCHAR     NOT NULL,
    PRIMARY KEY (instrument_id, as_of_date, condition_id, calculation_version)
)
"""

_DDL_WEEKLY_CONTEXT = """
CREATE TABLE IF NOT EXISTS weekly_context (
    instrument_id           VARCHAR     NOT NULL,
    as_of_date              DATE        NOT NULL,
    weekly_stage            VARCHAR     NOT NULL,
    sma_w                   DOUBLE,
    slope_pct               DOUBLE,
    prior_pct               DOUBLE,
    is_partial_week         BOOLEAN     NOT NULL,
    algorithm_version       VARCHAR     NOT NULL,
    PRIMARY KEY (instrument_id, as_of_date, algorithm_version)
)
"""

_ALL_DDL: list[tuple[str, str]] = [
    ("instruments", _DDL_INSTRUMENTS),
    ("ingestion_runs", _DDL_INGESTION_RUNS),
    ("raw_ohlcv", _DDL_RAW_OHLCV),
    ("daily_prices", _DDL_DAILY_PRICES),
    ("daily_prices_adjusted", _DDL_DAILY_PRICES_ADJUSTED),
    ("corporate_actions", _DDL_CORPORATE_ACTIONS),
    ("corporate_action_resolution", _DDL_CORPORATE_ACTION_RESOLUTION),
    ("corporate_action_adjustments", _DDL_CORPORATE_ACTION_ADJUSTMENTS),
    ("security_master_history", _DDL_SECURITY_MASTER_HISTORY),
    ("surveillance_flags_history", _DDL_SURVEILLANCE_FLAGS_HISTORY),
    ("universe_snapshots", _DDL_UNIVERSE_SNAPSHOTS),
    ("universe_memberships", _DDL_UNIVERSE_MEMBERSHIPS),
    ("technical_features_daily", _DDL_TECHNICAL_FEATURES_DAILY),
    ("weekly_prices", _DDL_WEEKLY_PRICES),
    ("relative_strength_snapshots", _DDL_RELATIVE_STRENGTH_SNAPSHOTS),
    ("trend_template_results", _DDL_TREND_TEMPLATE_RESULTS),
    ("trend_template_conditions", _DDL_TREND_TEMPLATE_CONDITIONS),
    ("weekly_context", _DDL_WEEKLY_CONTEXT),
]


class DuckDBStore:
    """Thin catalogue wrapper around a DuckDB connection.

    Responsibilities:
    - Open the database file (or ``':memory:'`` for tests).
    - Run idempotent ``CREATE TABLE IF NOT EXISTS`` migrations.
    - Expose the raw ``conn`` for repositories that need to run queries.

    Strategy / pattern code must never receive a ``DuckDBStore`` directly.
    They operate through repository *interfaces* (MarketDataRepository, etc.)
    which happen to be backed by this store internally.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        """Open or create the DuckDB database.

        Args:
            db_path: Filesystem path to the ``.duckdb`` file, or ``':memory:'``
                     for an in-memory database (used by tests).
        """
        try:
            import duckdb  # noqa: PLC0415 — intentional lazy import; duckdb is Phase 1+
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "duckdb is required for the storage layer. "
                "Install it with: pip install 'duckdb>=1.1,<2'"
            ) from exc

        self._path = str(db_path)
        self.conn = duckdb.connect(self._path)
        logger.debug("DuckDBStore opened: %s", self._path)

    # ------------------------------------------------------------------
    # Schema migration
    # ------------------------------------------------------------------

    def migrate(self) -> None:
        """Create all Phase 1 tables if they do not exist (idempotent).

        Safe to call on every application start.  Later phases append more
        DDL here; the ``CREATE … IF NOT EXISTS`` pattern guarantees no data
        loss on repeated calls.
        """
        for table_name, ddl in _ALL_DDL:
            self.conn.execute(ddl)
            logger.debug("Ensured table: %s", table_name)
        logger.info("DuckDBStore migration complete (%d tables)", len(_ALL_DDL))

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self.conn.close()
        logger.debug("DuckDBStore closed: %s", self._path)

    def __enter__(self) -> DuckDBStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
