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
from collections.abc import Sequence
from pathlib import Path

from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID

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

# Persisted data-quality events (DATABASE_SCHEMA section 19; audit P0-2). Every consumer that
# emits signals asks the gate built on this table which instruments are blocked.
#   event_id     deterministic per condition, so re-detection updates instead of duplicating
#   trade_date   first date affected; NULL = all dates. A block applies to as_of >= trade_date
#   blocks_signal only OPEN events with this flag stop signals
#   resolved_by  'SYSTEM' = condition cleared on its own (may reopen); anything else is a
#                human decision that the system never overrides
# Spec columns observed_value / expected_value are carried in ``context`` (JSON text).
_DDL_DATA_QUALITY_EVENTS = """
CREATE TABLE IF NOT EXISTS data_quality_events (
    event_id            VARCHAR     NOT NULL,
    instrument_id       VARCHAR     NOT NULL,
    trade_date          DATE,
    dataset             VARCHAR     NOT NULL,
    severity            VARCHAR     NOT NULL,
    blocks_signal       BOOLEAN     NOT NULL,
    event_type          VARCHAR     NOT NULL,
    description         VARCHAR     NOT NULL,
    detected_at         TIMESTAMPTZ NOT NULL,
    resolved_at         TIMESTAMPTZ,
    status              VARCHAR     NOT NULL,
    resolved_by         VARCHAR,
    resolution_note     VARCHAR,
    context             VARCHAR,
    PRIMARY KEY (event_id)
)
"""

_DDL_DATA_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS data_snapshots (
    data_snapshot_id    VARCHAR     NOT NULL,
    known_at            TIMESTAMPTZ NOT NULL,   -- everything with known_from <= known_at
    created_at          TIMESTAMPTZ NOT NULL,
    description         VARCHAR,
    PRIMARY KEY (data_snapshot_id)
)
"""

# ``computed_from_snapshot_id`` is part of the key: an adjusted series built from snapshot A
# is never overwritten by a rebuild under snapshot B, so earlier results stay reproducible.
# 'LIVE' marks unfrozen working data (see domain.snapshot).
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
    computed_from_snapshot_id   VARCHAR     NOT NULL DEFAULT 'LIVE',
    computed_at                 TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, trade_date, adjustment_version, computed_from_snapshot_id)
)
"""

# Once corporate actions change, an instrument can hold several ``adjustment_version``s
# side by side (old ones stay reproducible). Every reader that aggregates adjusted prices
# (features, weekly bars, RS) must see exactly ONE version per instrument *within one data
# snapshot*, otherwise sums double and joins fan out. This view keeps, for each
# (instrument, computed_from_snapshot_id), only the most recently computed version. The
# version is chosen per (instrument, snapshot, version) by its newest computed_at, so rows
# inside one version are never filtered out.
#
# The view spans every snapshot, so EVERY reader must also filter
# ``computed_from_snapshot_id = ?`` (LIVE for unfrozen work); tests enforce this.
_DDL_DAILY_PRICES_ADJUSTED_CURRENT = """
CREATE OR REPLACE VIEW daily_prices_adjusted_current AS
WITH ranked_versions AS (
    SELECT
        instrument_id,
        computed_from_snapshot_id,
        adjustment_version,
        ROW_NUMBER() OVER (
            PARTITION BY instrument_id, computed_from_snapshot_id
            ORDER BY MAX(computed_at) DESC, adjustment_version DESC
        ) AS rn
    FROM daily_prices_adjusted
    GROUP BY instrument_id, computed_from_snapshot_id, adjustment_version
)
SELECT a.*
FROM daily_prices_adjusted a
JOIN ranked_versions v
  ON a.instrument_id = v.instrument_id
 AND a.computed_from_snapshot_id = v.computed_from_snapshot_id
 AND a.adjustment_version = v.adjustment_version
WHERE v.rn = 1
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
    data_snapshot_id        VARCHAR     NOT NULL DEFAULT 'LIVE',
    PRIMARY KEY (instrument_id, trade_date, calculation_version, data_snapshot_id)
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
    data_snapshot_id        VARCHAR     NOT NULL DEFAULT 'LIVE',
    PRIMARY KEY (instrument_id, week_end, source_daily_version, data_snapshot_id)
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
    data_snapshot_id        VARCHAR     NOT NULL DEFAULT 'LIVE',
    -- Both snapshot ids are in the key: the same date ranked over a different universe, or
    -- computed from different price knowledge, is a different result, never an overwrite.
    PRIMARY KEY (
        as_of_date, instrument_id, calculation_version, data_snapshot_id, universe_snapshot_id
    )
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
    data_snapshot_id        VARCHAR     NOT NULL DEFAULT 'LIVE',
    blocked_by              VARCHAR,    -- comma-separated data-quality flags when blocked
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
    config_hash             VARCHAR     NOT NULL,
    data_snapshot_id        VARCHAR     NOT NULL DEFAULT 'LIVE',
    PRIMARY KEY (
        instrument_id, as_of_date, condition_id, calculation_version, config_hash,
        data_snapshot_id
    )
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
    data_snapshot_id        VARCHAR     NOT NULL DEFAULT 'LIVE',
    PRIMARY KEY (instrument_id, as_of_date, algorithm_version, data_snapshot_id)
)
"""

# Provider identifier -> permanent instrument_id (DATABASE_SCHEMA section 10; audit P1-1).
#   valid_from   first date this mapping was observed (dumps carry no earlier history)
#   valid_to     NULL while the provider still lists the token for this instrument; a token
#                that is re-pointed at another instrument or disappears from the dump is closed
#   Kite tokens can be reused, so (provider, provider_instrument_id) alone is not unique
#   over time; raw_ohlcv keeps the token actually used for every bar as the ground truth.
_DDL_PROVIDER_INSTRUMENTS = """
CREATE TABLE IF NOT EXISTS provider_instruments (
    provider                VARCHAR     NOT NULL,
    provider_instrument_id  VARCHAR     NOT NULL,
    instrument_id           VARCHAR     NOT NULL,
    provider_symbol         VARCHAR     NOT NULL,
    exchange                VARCHAR     NOT NULL,
    valid_from              DATE        NOT NULL,
    valid_to                DATE,
    metadata_json           VARCHAR,
    recorded_at             TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, provider_instrument_id, valid_from)
)
"""


# Manifest of NSE bhavcopy files (audit step 2). One row per (calendar date, file hash):
# a re-published file with different bytes is a new row, so what was ingested stays provable.
# Dates with no file carry sha256 = '' and status NO_SESSION / PENDING / ERROR.
_DDL_BHAVCOPY_FILES = """
CREATE TABLE IF NOT EXISTS bhavcopy_files (
    trade_date      DATE        NOT NULL,
    sha256          VARCHAR     NOT NULL,
    status          VARCHAR     NOT NULL,
    url             VARCHAR     NOT NULL,
    file_format     VARCHAR     NOT NULL,
    row_count       INTEGER     NOT NULL DEFAULT 0,
    rejected_count  INTEGER     NOT NULL DEFAULT 0,
    cache_path      VARCHAR,
    detail          VARCHAR,
    recorded_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trade_date, sha256)
)
"""


# Identifier history (audit step 2.2): the (symbol, ISIN) an instrument traded under, per span
# of sessions. valid_to is exclusive (first session under the next identifiers). Rebuilt from
# bhavcopy files; ISINs change after face-value splits, symbols on renames.
_DDL_INSTRUMENT_IDENTIFIER_HISTORY = """
CREATE TABLE IF NOT EXISTS instrument_identifier_history (
    instrument_id   VARCHAR     NOT NULL,
    symbol          VARCHAR     NOT NULL,
    isin            VARCHAR     NOT NULL,
    valid_from      DATE        NOT NULL,
    valid_to        DATE,
    change_reason   VARCHAR     NOT NULL,
    source          VARCHAR     NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, valid_from)
)
"""

# Per-day mapping of every kept bhavcopy row to its instrument (audit step 2.2): the series a
# stock traded in on each day (EQ / BE / BZ / SM / ST), for point-in-time universe rules.
_DDL_DAILY_SERIES = """
CREATE TABLE IF NOT EXISTS daily_series (
    trade_date      DATE        NOT NULL,
    symbol          VARCHAR     NOT NULL,
    series          VARCHAR     NOT NULL,
    instrument_id   VARCHAR     NOT NULL,
    isin            VARCHAR     NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (trade_date, symbol, series)
)
"""

_ALL_DDL: list[tuple[str, str]] = [
    ("instruments", _DDL_INSTRUMENTS),
    ("provider_instruments", _DDL_PROVIDER_INSTRUMENTS),
    ("ingestion_runs", _DDL_INGESTION_RUNS),
    ("raw_ohlcv", _DDL_RAW_OHLCV),
    ("daily_prices", _DDL_DAILY_PRICES),
    ("data_snapshots", _DDL_DATA_SNAPSHOTS),
    ("data_quality_events", _DDL_DATA_QUALITY_EVENTS),
    ("daily_prices_adjusted", _DDL_DAILY_PRICES_ADJUSTED),
    ("daily_prices_adjusted_current", _DDL_DAILY_PRICES_ADJUSTED_CURRENT),
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
    ("bhavcopy_files", _DDL_BHAVCOPY_FILES),
    ("instrument_identifier_history", _DDL_INSTRUMENT_IDENTIFIER_HISTORY),
    ("daily_series", _DDL_DAILY_SERIES),
]


# Derived tables whose rows must carry the data snapshot they were computed from.
_DERIVED_SNAPSHOT_TABLES: list[tuple[str, str]] = [
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

    def insert_rows(
        self,
        table: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[object]],
        *,
        ignore_conflicts: bool = False,
    ) -> int:
        """Bulk-insert ``rows`` into ``table`` through an Arrow table.

        ``executemany`` inserts row by row (about 24 s for one bhavcopy day of ~3,400 rows);
        a registered Arrow table inserts the same rows in milliseconds. Column types come from
        the target table, so values only need to be castable to them. ``ignore_conflicts``
        skips rows whose key already exists (``INSERT OR IGNORE``).
        """
        if not rows:
            return 0
        import pyarrow as pa  # type: ignore[import-untyped]  # noqa: PLC0415

        data = {c: [r[i] for r in rows] for i, c in enumerate(columns)}
        view = f"_bulk_{table}"
        self.conn.register(view, pa.table(data))
        try:
            cols = ", ".join(columns)
            verb = "INSERT OR IGNORE" if ignore_conflicts else "INSERT"
            self.conn.execute(f"{verb} INTO {table} ({cols}) SELECT {cols} FROM {view}")
        finally:
            self.conn.unregister(view)
        return len(rows)

    def migrate(self) -> None:
        """Create all Phase 1 tables if they do not exist (idempotent).

        Safe to call on every application start.  Later phases append more
        DDL here; the ``CREATE … IF NOT EXISTS`` pattern guarantees no data
        loss on repeated calls.
        """
        self._migrate_trend_conditions_config_hash()
        self._migrate_adjusted_snapshot_key()
        self._migrate_derived_snapshot_lineage()
        self._add_column_if_missing("trend_template_results", "blocked_by", "VARCHAR")
        for table_name, ddl in _ALL_DDL:
            self.conn.execute(ddl)
            logger.debug("Ensured table: %s", table_name)
        logger.info("DuckDBStore migration complete (%d tables)", len(_ALL_DDL))

    def _migrate_trend_conditions_config_hash(self) -> None:
        """Rebuild a pre-existing ``trend_template_conditions`` that lacks ``config_hash``.

        The key gained ``config_hash`` so scans with different thresholds no longer overwrite
        each other. DuckDB cannot alter a primary key, so the table is rebuilt; legacy rows
        keep their data under the placeholder hash ``LEGACY``.
        """
        cols = {
            row[0]
            for row in self.conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'trend_template_conditions'"
            ).fetchall()
        }
        if not cols or "config_hash" in cols:
            return
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                "ALTER TABLE trend_template_conditions RENAME TO trend_template_conditions_old"
            )
            self.conn.execute(_DDL_TREND_TEMPLATE_CONDITIONS)
            self.conn.execute(
                """
                INSERT INTO trend_template_conditions (
                    instrument_id, as_of_date, condition_id, condition_name, measurement,
                    threshold, passed, calculation_version, config_hash
                )
                SELECT instrument_id, as_of_date, condition_id, condition_name, measurement,
                       threshold, passed, calculation_version, 'LEGACY'
                FROM trend_template_conditions_old
                """
            )
            self.conn.execute("DROP TABLE trend_template_conditions_old")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")
        logger.info("Rebuilt trend_template_conditions with config_hash in its key")

    def _column_nullability(self, table: str) -> dict[str, bool]:
        """Map column name -> is_nullable for ``table`` (empty if it does not exist)."""
        rows = self.conn.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_schema = 'main' AND table_name = ?",
            [table],
        ).fetchall()
        return {str(name): str(nullable).upper() == "YES" for name, nullable in rows}

    def _rebuild_table(
        self,
        table: str,
        ddl: str,
        *,
        fill: dict[str, str] | None = None,
        drop_views: tuple[str, ...] = (),
    ) -> None:
        """Rebuild ``table`` from ``ddl`` (DuckDB cannot alter a primary key), keeping rows.

        Columns present in both the old and new table are copied. ``fill`` maps a column to
        the SQL expression used to populate it (e.g. ``COALESCE(col, 'LIVE')``); columns
        that exist only in the new table take their DDL default. Runs in one transaction,
        so a failure leaves the original table untouched. ``drop_views`` are views that
        depend on the table; the caller's later DDL pass recreates them.
        """
        fill = fill or {}
        old_cols = list(self._column_nullability(table))
        legacy = f"{table}__legacy"
        self.conn.execute("BEGIN TRANSACTION")
        try:
            for view in drop_views:
                self.conn.execute(f"DROP VIEW IF EXISTS {view}")
            self.conn.execute(f"ALTER TABLE {table} RENAME TO {legacy}")
            self.conn.execute(ddl)
            new_cols = set(self._column_nullability(table))
            shared = [c for c in old_cols if c in new_cols]
            select = ", ".join(fill.get(c, c) for c in shared)
            self.conn.execute(
                f"INSERT INTO {table} ({', '.join(shared)}) SELECT {select} FROM {legacy}"  # noqa: S608
            )
            self.conn.execute(f"DROP TABLE {legacy}")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _add_column_if_missing(self, table: str, column: str, declaration: str) -> None:
        """Add a nullable column to an existing table (no-op if the table or column exists)."""
        cols = self._column_nullability(table)
        if cols and column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            logger.info("Added %s.%s", table, column)

    def _migrate_adjusted_snapshot_key(self) -> None:
        """Give a pre-snapshot ``daily_prices_adjusted`` the snapshot-aware key.

        Legacy rows had a nullable ``computed_from_snapshot_id`` outside the key. They are
        kept under the explicit unfrozen marker ``LIVE``.
        """
        cols = self._column_nullability("daily_prices_adjusted")
        if not cols or not cols.get("computed_from_snapshot_id", False):
            return  # missing (fresh database) or already migrated (column is NOT NULL)
        self._rebuild_table(
            "daily_prices_adjusted",
            _DDL_DAILY_PRICES_ADJUSTED,
            fill={
                "computed_from_snapshot_id": (
                    f"COALESCE(computed_from_snapshot_id, '{LIVE_SNAPSHOT_ID}')"
                )
            },
            drop_views=("daily_prices_adjusted_current",),
        )
        logger.info("Rebuilt daily_prices_adjusted with computed_from_snapshot_id in its key")

    def _migrate_derived_snapshot_lineage(self) -> None:
        """Add ``data_snapshot_id`` (and the key change it implies) to derived tables.

        Existing rows are kept under the explicit unfrozen marker ``LIVE`` (the column's
        DDL default). Tables that already carry the column, or do not exist yet, are left
        alone, so this is safe to run on every start.
        """
        for table, ddl in _DERIVED_SNAPSHOT_TABLES:
            cols = self._column_nullability(table)
            if cols and "data_snapshot_id" not in cols:
                self._rebuild_table(table, ddl)
                logger.info("Rebuilt %s with data_snapshot_id lineage", table)

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
