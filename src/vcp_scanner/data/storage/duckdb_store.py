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
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID

logger = logging.getLogger(__name__)


def _mark_pandas_absent() -> None:
    """DuckDB's Python client tries ``import pandas`` for every bound parameter it converts.
    pandas is not a dependency, so each try was a failed import searching the whole path:
    327,690 failed imports and about 30 s in one Trend Template scan (found 2026-10-03, Phase 9
    step 1). Recording it as absent in ``sys.modules`` makes each try fail at once. Nothing in
    the project imports pandas; if it is installed, this does nothing."""
    import importlib.util
    import sys

    if "pandas" not in sys.modules and importlib.util.find_spec("pandas") is None:
        sys.modules["pandas"] = None  # type: ignore[assignment]


_mark_pandas_absent()

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
#                until the instrument has data.quality.block_lifetime_bars bars from it (P1-2)
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

# Every state an event has been in, as system-time intervals (audit P1-2a, C5). The event row
# above holds only the latest state; reopening it overwrote resolved_at, so the gate could not
# tell that a stock was unblocked in between. The point-in-time gate reads this table.
_DDL_DATA_QUALITY_EVENT_HISTORY = """
CREATE TABLE IF NOT EXISTS data_quality_event_history (
    event_id        VARCHAR     NOT NULL,
    status          VARCHAR     NOT NULL,   -- OPEN | RESOLVED
    blocks_signal   BOOLEAN     NOT NULL,
    valid_from      TIMESTAMPTZ NOT NULL,   -- system time this state became known
    valid_to        TIMESTAMPTZ,            -- NULL while current
    resolved_by     VARCHAR,
    PRIMARY KEY (event_id, valid_from)
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
    survivorship_status   VARCHAR NOT NULL,
    survivorship_detail   VARCHAR
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

# One row per day a surveillance list (ASM, GSM, T2T) was collected in full (audit P0-4 /
# daily run). NSE publishes only the current lists, so a day without a row is a day whose
# flags can never be known exactly; universe snapshots for it cannot be POINT_IN_TIME_COMPLETE.
_DDL_SURVEILLANCE_COLLECTIONS = """
CREATE TABLE IF NOT EXISTS surveillance_collections (
    collected_on    DATE        NOT NULL,
    flag_type       VARCHAR     NOT NULL,
    record_count    INTEGER     NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (collected_on, flag_type)
)
"""

# Scan-run lineage (audit P1-8; DATABASE_SCHEMA section 40). Immutable: one row per scan, never
# updated. JSON columns are text: section_hashes (strategy/universe/gate), versions (algorithm
# tags), counts (by status). scan_run_results copies each run's verdicts so a later rerun under
# the same scan id cannot erase what this run reported.
_DDL_SCAN_RUNS = """
CREATE TABLE IF NOT EXISTS scan_runs (
    scan_run_id          VARCHAR     PRIMARY KEY,
    scan_type            VARCHAR     NOT NULL,
    as_of_date           DATE        NOT NULL,
    scan_id              VARCHAR     NOT NULL,
    data_snapshot_id     VARCHAR     NOT NULL,
    data_cutoff          TIMESTAMPTZ NOT NULL,   -- knowledge cutoff the scan saw
    universe_snapshot_id VARCHAR     NOT NULL,
    universe_cutoff      TIMESTAMPTZ,            -- that universe snapshot's created_at
    scan_config_hash     VARCHAR     NOT NULL,
    section_hashes       VARCHAR     NOT NULL,
    code_commit          VARCHAR     NOT NULL,   -- git commit, or 'unknown'
    code_dirty           BOOLEAN,                -- uncommitted changes to tracked files
    versions             VARCHAR     NOT NULL,
    survivorship_status  VARCHAR,
    survivorship_detail  VARCHAR,
    counts               VARCHAR     NOT NULL,
    results_hash         VARCHAR     NOT NULL,
    started_at           TIMESTAMPTZ NOT NULL,
    completed_at         TIMESTAMPTZ NOT NULL,
    status               VARCHAR     NOT NULL,
    strategy_id          VARCHAR                 -- VCP / SCORE / SETUP runs; NULL for trend
)
"""

# When each instrument was last asked of a secondary corporate-action source (rotation under a
# per-run request budget; DATA_SPECIFICATION 18A). Operational state, overwritten in place.
_DDL_SECONDARY_CA_CHECKS = """
CREATE TABLE IF NOT EXISTS secondary_ca_checks (
    provider        VARCHAR     NOT NULL,
    instrument_id   VARCHAR     NOT NULL,
    checked_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (provider, instrument_id)
)
"""

_DDL_SCAN_RUN_RESULTS = """
CREATE TABLE IF NOT EXISTS scan_run_results (
    scan_run_id          VARCHAR     NOT NULL,
    instrument_id        VARCHAR     NOT NULL,
    status               VARCHAR     NOT NULL,
    trend_template_pass  BOOLEAN,
    weekly_stage         VARCHAR,
    rs_rank              DOUBLE,
    blocked_by           VARCHAR,
    PRIMARY KEY (scan_run_id, instrument_id)
)
"""


# VCP results (DATABASE_SCHEMA 31-34, VCP_SPECIFICATION 42-48, 61B; Phase 6 step 7). One row per
# detected pattern with every measurement; a rerun of the same scan id replaces its rows.
_DDL_VCP_PATTERNS = """
CREATE TABLE IF NOT EXISTS vcp_patterns (
    vcp_pattern_id              VARCHAR     PRIMARY KEY,
    scan_id                     VARCHAR     NOT NULL,
    instrument_id               VARCHAR     NOT NULL,
    as_of_date                  DATE        NOT NULL,
    is_primary                  BOOLEAN     NOT NULL,
    base_start_date             DATE        NOT NULL,
    base_end_date               DATE,
    base_high                   DOUBLE      NOT NULL,
    base_low                    DOUBLE      NOT NULL,
    base_depth_pct              DOUBLE      NOT NULL,
    base_duration_days          INTEGER     NOT NULL,
    prior_advance_return_pct    DOUBLE,
    contraction_count           INTEGER     NOT NULL,
    first_contraction_pct       DOUBLE,
    final_contraction_pct       DOUBLE,
    max_tightening_ratio        DOUBLE,
    progressive_tightening      BOOLEAN,
    final_volume_ratio          DOUBLE,
    volume_dryup_pass           BOOLEAN,
    atr_contraction_ratio       DOUBLE,
    tr_contraction_ratio        DOUBLE,
    volatility_contraction_pass BOOLEAN,
    right_side_range_pct        DOUBLE,
    tight_pivot_pass            BOOLEAN,
    tightening_quality          DOUBLE,
    volatility_quality          DOUBLE,
    volume_quality              DOUBLE,
    pivot_quality               DOUBLE,
    base_quality                DOUBLE,
    pivot_price                 DOUBLE,
    pivot_date                  DATE,
    pivot_source                VARCHAR,
    pivot_distance_pct          DOUBLE,
    classification              VARCHAR     NOT NULL,
    status                      VARCHAR     NOT NULL,
    confirmation_state          VARCHAR     NOT NULL,
    invalidation_reasons        VARCHAR,     -- comma-separated, NULL when none
    unmet_rules                 VARCHAR,     -- JSON {tier: [rule, ...]} (explainability)
    breakout_event_id           VARCHAR,
    trend_template_pass         BOOLEAN     NOT NULL,
    weekly_stage2_pass          BOOLEAN,
    algorithm_version           VARCHAR     NOT NULL,
    config_hash                 VARCHAR     NOT NULL,
    data_snapshot_id            VARCHAR     NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL
)
"""

_DDL_VCP_CONTRACTIONS = """
CREATE TABLE IF NOT EXISTS vcp_contractions (
    vcp_pattern_id            VARCHAR NOT NULL,
    sequence_number           INTEGER NOT NULL,
    peak_date                 DATE    NOT NULL,
    peak_price                DOUBLE  NOT NULL,
    trough_date               DATE    NOT NULL,
    trough_price              DOUBLE  NOT NULL,
    depth_pct                 DOUBLE  NOT NULL,
    duration_days             INTEGER NOT NULL,
    atr_pct                   DOUBLE,
    tr_pct                    DOUBLE,
    range_pct                 DOUBLE,
    volume_ratio              DOUBLE,
    tightening_ratio_to_prior DOUBLE,
    confirmation_date         DATE,
    is_confirmed              BOOLEAN NOT NULL,
    PRIMARY KEY (vcp_pattern_id, sequence_number)
)
"""

_DDL_VCP_PIVOTS = """
CREATE TABLE IF NOT EXISTS vcp_pivots (
    vcp_pattern_id           VARCHAR NOT NULL,
    pivot_id                 INTEGER NOT NULL,
    pivot_price              DOUBLE  NOT NULL,
    pivot_date               DATE    NOT NULL,
    source                   VARCHAR NOT NULL,
    touches                  INTEGER NOT NULL,
    rejection_count          INTEGER NOT NULL,
    right_side_tightness_pct DOUBLE,
    distance_to_close_pct    DOUBLE  NOT NULL,
    is_primary               BOOLEAN NOT NULL,
    is_structural            BOOLEAN NOT NULL,
    PRIMARY KEY (vcp_pattern_id, pivot_id)
)
"""

# Changes of an instrument's primary classification/status between consecutive scan dates
# (DATABASE_SCHEMA 34). Append-only; a rerun of the same date and config replaces its rows.
_DDL_VCP_STATUS_HISTORY = """
CREATE TABLE IF NOT EXISTS vcp_status_history (
    instrument_id           VARCHAR     NOT NULL,
    as_of_date              DATE        NOT NULL,
    config_hash             VARCHAR     NOT NULL,
    vcp_pattern_id          VARCHAR,
    previous_as_of_date     DATE,
    previous_classification VARCHAR,
    new_classification      VARCHAR     NOT NULL,
    previous_status         VARCHAR,
    new_status              VARCHAR,
    reason                  VARCHAR,
    algorithm_version       VARCHAR     NOT NULL,
    PRIMARY KEY (instrument_id, as_of_date, config_hash)
)
"""

# Breakouts are separate, immutable events (VCP_SPECIFICATION 47, 61B): the first breakout of a
# base is never rewritten, and later days of that base are judged against its pivot.
_DDL_VCP_BREAKOUT_EVENTS = """
CREATE TABLE IF NOT EXISTS vcp_breakout_events (
    breakout_event_id VARCHAR     PRIMARY KEY,
    instrument_id     VARCHAR     NOT NULL,
    base_start_date   DATE        NOT NULL,
    config_hash       VARCHAR     NOT NULL,
    breakout_date     DATE        NOT NULL,
    pivot_price       DOUBLE      NOT NULL,
    pivot_date        DATE        NOT NULL,
    pivot_source      VARCHAR     NOT NULL,
    volume_ratio      DOUBLE      NOT NULL,
    detected_as_of    DATE        NOT NULL,
    method            VARCHAR     NOT NULL,  -- STRUCTURAL | PRIOR_DAY_PIVOT
    created_at        TIMESTAMPTZ NOT NULL,
    UNIQUE (instrument_id, base_start_date, config_hash)
)
"""

# Frozen copy of each VCP scan run's per-instrument verdicts (like scan_run_results).
_DDL_VCP_SCAN_RUN_RESULTS = """
CREATE TABLE IF NOT EXISTS vcp_scan_run_results (
    scan_run_id        VARCHAR NOT NULL,
    instrument_id      VARCHAR NOT NULL,
    classification     VARCHAR,
    status             VARCHAR,
    confirmation_state VARCHAR,
    pivot_price        DOUBLE,
    no_pattern_reason  VARCHAR,
    PRIMARY KEY (scan_run_id, instrument_id)
)
"""


# Setup scores (SCORING_SPECIFICATION; DATABASE_SCHEMA 35). Every Trend Template passer of a
# scan is stored; only ``eligible`` rows are ranked (owner decision 2026-10-03).
_DDL_SETUP_SCORES = """
CREATE TABLE IF NOT EXISTS setup_scores (
    scan_id               VARCHAR NOT NULL,
    strategy_id           VARCHAR NOT NULL,
    instrument_id         VARCHAR NOT NULL,
    as_of_date            DATE NOT NULL,
    classification        VARCHAR,
    vcp_status            VARCHAR,
    eligible              BOOLEAN NOT NULL,
    trend_score           DOUBLE,
    vcp_score             DOUBLE,
    volume_score          DOUBLE,
    rs_score              DOUBLE,
    fundamental_score     DOUBLE,
    final_setup_score     DOUBLE,
    ranking_percentile    DOUBLE,
    confirmation_state    VARCHAR,
    fundamental_available BOOLEAN NOT NULL,
    weights_renormalized  BOOLEAN NOT NULL,
    flags                 VARCHAR,
    trend_weight          DOUBLE NOT NULL,
    vcp_weight            DOUBLE NOT NULL,
    volume_weight         DOUBLE NOT NULL,
    rs_weight             DOUBLE NOT NULL,
    fundamental_weight    DOUBLE NOT NULL,
    scoring_version       VARCHAR NOT NULL,
    config_hash           VARCHAR NOT NULL,
    data_snapshot_id      VARCHAR NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (scan_id, instrument_id)
)
"""

_DDL_SCORE_COMPONENTS = """
CREATE TABLE IF NOT EXISTS score_components (
    scan_id                 VARCHAR NOT NULL,
    strategy_id             VARCHAR NOT NULL,
    instrument_id           VARCHAR NOT NULL,
    component               VARCHAR NOT NULL,
    sub_component           VARCHAR NOT NULL,
    raw_measurement         DOUBLE,
    normalized_0_100        DOUBLE,
    weight_within_component DOUBLE NOT NULL,
    points                  DOUBLE NOT NULL,
    max_points              DOUBLE NOT NULL,
    scoring_version         VARCHAR NOT NULL,
    PRIMARY KEY (scan_id, instrument_id, component, sub_component)
)
"""

# Forward labels (PROJECT_DESIGN 39; Phase 9 step 2): what happened after each scored
# observation. Filled in as future bars arrive (``complete`` once final).
_DDL_FORWARD_LABELS = """
CREATE TABLE IF NOT EXISTS forward_labels (
    strategy_id        VARCHAR NOT NULL,
    instrument_id      VARCHAR NOT NULL,
    as_of_date         DATE NOT NULL,
    config_hash        VARCHAR NOT NULL,
    label_version      VARCHAR NOT NULL,
    data_snapshot_id   VARCHAR NOT NULL,
    score_scan_id      VARCHAR NOT NULL,
    entry_close        DOUBLE NOT NULL,
    pivot_price        DOUBLE,
    ret_5              DOUBLE,
    ret_10             DOUBLE,
    ret_20             DOUBLE,
    ret_40             DOUBLE,
    ret_60             DOUBLE,
    mfe_60             DOUBLE,
    mae_60             DOUBLE,
    breakout_within_20 BOOLEAN,
    breakout_day       INTEGER,
    failed_breakout    BOOLEAN,
    bars_after         INTEGER NOT NULL,
    complete           BOOLEAN NOT NULL,
    computed_at        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (strategy_id, instrument_id, as_of_date, config_hash, label_version,
                 data_snapshot_id)
)
"""

# Backtests (DATABASE_SCHEMA 48-49; Phase 9 step 3).
_DDL_BACKTEST_RUNS = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    backtest_id         VARCHAR PRIMARY KEY,
    strategy_id         VARCHAR NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    completed_at        TIMESTAMPTZ,
    start_date          DATE NOT NULL,
    end_date            DATE NOT NULL,
    universe_definition VARCHAR NOT NULL,
    strategy_version    VARCHAR NOT NULL,
    algorithm_version   VARCHAR,                 -- the strategy's detector version
    config_hash         VARCHAR NOT NULL,
    data_snapshot_id    VARCHAR NOT NULL,
    execution_model     VARCHAR NOT NULL,
    research_mode       BOOLEAN NOT NULL,
    survivorship_status VARCHAR,
    status              VARCHAR NOT NULL,
    period_name         VARCHAR,
    settings_json       VARCHAR NOT NULL,
    metrics_json        VARCHAR,
    code_commit         VARCHAR
)
"""

_DDL_BACKTEST_EVENTS = """
CREATE TABLE IF NOT EXISTS backtest_events (
    backtest_id   VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    event_date    DATE NOT NULL,
    event_type    VARCHAR NOT NULL,
    price         DOUBLE,
    quantity      DOUBLE,
    signal_id     VARCHAR,
    metadata_json VARCHAR
)
"""

# Paper ledger (STRATEGY_SPECIFICATION 21.3; DATABASE_SCHEMA 49B; monitoring phase M3).
# Append-only: rows are inserted, never updated or deleted (a later data fix is recorded as a
# DIVERGENCE row, not an edit).
_DDL_PAPER_EVENTS = """
CREATE TABLE IF NOT EXISTS paper_events (
    event_id      VARCHAR     PRIMARY KEY,
    rule_set      VARCHAR     NOT NULL,
    strategy_id   VARCHAR     NOT NULL,
    config_hash   VARCHAR     NOT NULL,
    instrument_id VARCHAR,
    event_date    DATE        NOT NULL,
    event_type    VARCHAR     NOT NULL,
    price         DOUBLE,
    scan_date     DATE,
    metadata_json VARCHAR,
    code_commit   VARCHAR,
    recorded_at   TIMESTAMPTZ NOT NULL
)
"""

# Strategy dimension (STRATEGY_SPECIFICATION; DATABASE_SCHEMA 35A; Multi-Strategy step 2).
# Setups of strategies other than VCP (VCP keeps vcp_patterns; the ``setups`` view unions both).
_DDL_STRATEGY_SETUPS = """
CREATE TABLE IF NOT EXISTS strategy_setups (
    setup_id             VARCHAR     PRIMARY KEY,
    strategy_id          VARCHAR     NOT NULL,
    scan_id              VARCHAR     NOT NULL,
    instrument_id        VARCHAR     NOT NULL,
    as_of_date           DATE        NOT NULL,
    is_primary           BOOLEAN     NOT NULL,
    base_start_date      DATE        NOT NULL,
    base_end_date        DATE,
    base_high            DOUBLE      NOT NULL,
    base_low             DOUBLE      NOT NULL,
    base_depth_pct       DOUBLE      NOT NULL,
    base_duration_days   INTEGER     NOT NULL,
    prior_advance_pct    DOUBLE,
    pivot_price          DOUBLE,
    pivot_date           DATE,
    pivot_source         VARCHAR,
    pivot_distance_pct   DOUBLE,
    stop_reference_price DOUBLE,
    dryup_volume_ratio   DOUBLE,
    classification       VARCHAR     NOT NULL,
    grade                INTEGER     NOT NULL,
    status               VARCHAR     NOT NULL,
    confirmation_state   VARCHAR     NOT NULL,
    trend_gate           VARCHAR     NOT NULL,   -- PASS | RELAXED
    weekly_stage2_pass   BOOLEAN,
    invalidation_reasons VARCHAR,
    unmet_rules          VARCHAR,                -- JSON
    breakout_event_id    VARCHAR,
    details_json         VARCHAR     NOT NULL,   -- JSON, '{}' when none
    algorithm_version    VARCHAR     NOT NULL,
    config_hash          VARCHAR     NOT NULL,   -- strategy config hash
    data_snapshot_id     VARCHAR     NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL
)
"""
_DDL_STRATEGY_SETUP_STATUS_HISTORY = """
CREATE TABLE IF NOT EXISTS strategy_setup_status_history (
    strategy_id             VARCHAR NOT NULL,
    instrument_id           VARCHAR NOT NULL,
    as_of_date              DATE    NOT NULL,
    config_hash             VARCHAR NOT NULL,
    setup_id                VARCHAR,
    previous_as_of_date     DATE,
    previous_classification VARCHAR,
    new_classification      VARCHAR NOT NULL,
    previous_status         VARCHAR,
    new_status              VARCHAR,
    reason                  VARCHAR,
    algorithm_version       VARCHAR NOT NULL,
    PRIMARY KEY (strategy_id, instrument_id, as_of_date, config_hash)
)
"""
_DDL_STRATEGY_BREAKOUT_EVENTS = """
CREATE TABLE IF NOT EXISTS strategy_breakout_events (
    breakout_event_id VARCHAR     PRIMARY KEY,
    strategy_id       VARCHAR     NOT NULL,
    instrument_id     VARCHAR     NOT NULL,
    base_start_date   DATE        NOT NULL,
    config_hash       VARCHAR     NOT NULL,
    breakout_date     DATE        NOT NULL,
    pivot_price       DOUBLE      NOT NULL,
    pivot_date        DATE        NOT NULL,
    pivot_source      VARCHAR     NOT NULL,
    volume_ratio      DOUBLE      NOT NULL,
    detected_as_of    DATE        NOT NULL,
    method            VARCHAR     NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    UNIQUE (strategy_id, instrument_id, base_start_date, config_hash)
)
"""
_DDL_STRATEGY_SCAN_RUN_RESULTS = """
CREATE TABLE IF NOT EXISTS strategy_scan_run_results (
    scan_run_id        VARCHAR NOT NULL,
    instrument_id      VARCHAR NOT NULL,
    classification     VARCHAR,
    grade              INTEGER,
    status             VARCHAR,
    confirmation_state VARCHAR,
    pivot_price        DOUBLE,
    no_pattern_reason  VARCHAR,
    PRIMARY KEY (scan_run_id, instrument_id)
)
"""
# One reading surface for every strategy (DATABASE_SCHEMA 35A.3). VCP rows are read in place
# from vcp_patterns (decision O2); the stop level is the last contraction's trough.
_VIEW_SETUPS = """
CREATE OR REPLACE VIEW setups AS
SELECT p.vcp_pattern_id AS setup_id, 'vcp' AS strategy_id, p.scan_id, p.instrument_id,
       p.as_of_date, p.is_primary, p.base_start_date, p.base_end_date, p.base_high, p.base_low,
       p.base_depth_pct, p.base_duration_days, p.prior_advance_return_pct AS prior_advance_pct,
       p.pivot_price, p.pivot_date, p.pivot_source, p.pivot_distance_pct,
       c.stop_reference_price, p.final_volume_ratio AS dryup_volume_ratio, p.classification,
       CASE p.classification WHEN 'A_PLUS_VCP' THEN 3 WHEN 'VCP' THEN 2
                             WHEN 'VCP_LIKE' THEN 1 ELSE 0 END AS grade,
       p.status, p.confirmation_state,
       CASE WHEN p.trend_template_pass THEN 'PASS' ELSE 'FAIL' END AS trend_gate,
       p.weekly_stage2_pass, p.invalidation_reasons, p.unmet_rules, p.breakout_event_id,
       '{}' AS details_json, p.algorithm_version, p.config_hash, p.data_snapshot_id, p.created_at
FROM vcp_patterns p
LEFT JOIN (
    SELECT vcp_pattern_id, arg_max(trough_price, sequence_number) AS stop_reference_price
    FROM vcp_contractions GROUP BY vcp_pattern_id
) c ON c.vcp_pattern_id = p.vcp_pattern_id
UNION ALL BY NAME
SELECT * FROM strategy_setups
"""
_VIEW_BREAKOUT_EVENTS = """
CREATE OR REPLACE VIEW breakout_events AS
SELECT 'vcp' AS strategy_id, * FROM vcp_breakout_events
UNION ALL BY NAME
SELECT * FROM strategy_breakout_events
"""
_VIEW_SETUP_SCORES_V = """
CREATE OR REPLACE VIEW setup_scores_v AS
SELECT *, vcp_score AS pattern_score, vcp_weight AS pattern_weight FROM setup_scores
"""
# Fundamentals (FUNDAMENTALS_SPECIFICATION §4, DATABASE_SCHEMA §36-39). Separate from every
# scan/score/paper table: nothing in the scan reads them (freeze rule, spec §2).
_DDL_FUNDAMENTAL_FILINGS = """
CREATE TABLE IF NOT EXISTS fundamental_filings (
    filing_id         VARCHAR PRIMARY KEY,       -- '<source_feed>:<source record id>'
    instrument_id     VARCHAR NOT NULL,
    period_end        DATE NOT NULL,
    period_type       VARCHAR,                   -- QUARTER | ANNUAL | NULL until parsed
    statement_basis   VARCHAR NOT NULL,          -- CONSOLIDATED | STANDALONE | UNKNOWN
    revision_number   INTEGER NOT NULL,          -- 0 = first filing of the period and basis
    broadcast_at      TIMESTAMPTZ NOT NULL,      -- when the market could first see it
    source_feed       VARCHAR NOT NULL,
    source_record_id  VARCHAR NOT NULL,
    url               VARCHAR NOT NULL,
    audited           BOOLEAN,
    revision_flags    VARCHAR,
    sha256            VARCHAR,
    cache_path        VARCHAR,
    fetched_at        TIMESTAMPTZ,
    status            VARCHAR NOT NULL           -- OK | FETCH_ERROR | PARSE_ERROR | NOT_APPLICABLE
)
"""
_DDL_FUNDAMENTAL_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS fundamental_snapshots (
    fundamental_snapshot_id    VARCHAR PRIMARY KEY,
    instrument_id              VARCHAR NOT NULL,
    period_end                 DATE NOT NULL,
    period_type                VARCHAR NOT NULL,   -- QUARTER | ANNUAL
    statement_basis            VARCHAR NOT NULL,   -- CONSOLIDATED | STANDALONE
    revision_number            INTEGER NOT NULL,
    superseded_by_snapshot_id  VARCHAR,
    filing_date                DATE,
    available_at               TIMESTAMPTZ NOT NULL,
    provider                   VARCHAR NOT NULL,
    currency                   VARCHAR,
    data_status                VARCHAR NOT NULL,
    source_record_id           VARCHAR,            -- fundamental_filings.filing_id
    ingestion_run_id           VARCHAR,
    UNIQUE (instrument_id, period_end, period_type, statement_basis, revision_number)
)
"""
_DDL_FUNDAMENTAL_FACTS = """
CREATE TABLE IF NOT EXISTS fundamental_facts (
    fundamental_snapshot_id VARCHAR NOT NULL,
    scope        VARCHAR NOT NULL,        -- QUARTER | YTD | ANNUAL | INSTANT
    item         VARCHAR NOT NULL,        -- revenue, eps, net_profit, equity, ...
    value        DOUBLE NOT NULL,
    derived      BOOLEAN NOT NULL,        -- true: computed from year-to-date values
    period_start DATE,
    period_end   DATE,
    PRIMARY KEY (fundamental_snapshot_id, scope, item)
)
"""
# One row per stock and date on which its fundamental view changed (a usable filing appeared):
# the view as of the close of that date (FUNDAMENTALS_SPECIFICATION §6-8, F4). The view of any
# later date D is the row with the greatest as_of_date <= D. Point in time by construction.
_DDL_FUNDAMENTAL_METRICS = """
CREATE TABLE IF NOT EXISTS fundamental_metrics (
    instrument_id           VARCHAR NOT NULL,
    as_of_date              DATE NOT NULL,
    fundamental_snapshot_id VARCHAR NOT NULL,   -- the latest usable quarter
    period_end              DATE NOT NULL,
    statement_basis         VARCHAR NOT NULL,
    revenue DOUBLE, revenue_yoy DOUBLE, revenue_qoq DOUBLE,
    eps DOUBLE, eps_yoy DOUBLE, eps_qoq DOUBLE, eps_acceleration DOUBLE, ttm_eps DOUBLE,
    gross_margin DOUBLE, operating_margin DOUBLE, net_margin DOUBLE, margin_expansion DOUBLE,
    roe DOUBLE, debt DOUBLE, debt_to_equity DOUBLE,
    statuses_json           VARCHAR NOT NULL,   -- metric -> AVAILABLE | MISSING | ...
    computed_at             TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (instrument_id, as_of_date)
)
"""
_DDL_FUNDAMENTAL_DATA_QUALITY = """
CREATE TABLE IF NOT EXISTS fundamental_data_quality (
    instrument_id                VARCHAR NOT NULL,
    as_of_date                   DATE NOT NULL,
    eps_available                BOOLEAN NOT NULL,
    sales_available              BOOLEAN NOT NULL,
    margin_available             BOOLEAN NOT NULL,
    roe_available                BOOLEAN NOT NULL,
    debt_available               BOOLEAN NOT NULL,
    periods_available            INTEGER NOT NULL,
    availability_score           DOUBLE NOT NULL,
    stale_after                  DATE NOT NULL,     -- STALE on later dates (§8)
    estimated                    BOOLEAN NOT NULL,
    restated                     BOOLEAN NOT NULL,
    fundamental_hard_gate_pass   BOOLEAN NOT NULL,  -- shown only, never applied (spec §2)
    fundamental_gate_reason      VARCHAR,
    PRIMARY KEY (instrument_id, as_of_date)
)
"""
_DDL_FUNDAMENTAL_RESEARCH_SCORES = """
CREATE TABLE IF NOT EXISTS fundamental_research_scores (
    instrument_id   VARCHAR NOT NULL,
    as_of_date      DATE NOT NULL,
    score_version   VARCHAR NOT NULL,
    score           DOUBLE,
    components_json VARCHAR,
    PRIMARY KEY (instrument_id, as_of_date, score_version)
)
"""


_ALL_VIEWS: list[tuple[str, str]] = [
    ("setups", _VIEW_SETUPS),
    ("breakout_events", _VIEW_BREAKOUT_EVENTS),
    ("setup_scores_v", _VIEW_SETUP_SCORES_V),
]

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
    ("surveillance_collections", _DDL_SURVEILLANCE_COLLECTIONS),
    ("scan_runs", _DDL_SCAN_RUNS),
    ("scan_run_results", _DDL_SCAN_RUN_RESULTS),
    ("secondary_ca_checks", _DDL_SECONDARY_CA_CHECKS),
    ("data_quality_event_history", _DDL_DATA_QUALITY_EVENT_HISTORY),
    ("vcp_patterns", _DDL_VCP_PATTERNS),
    ("vcp_contractions", _DDL_VCP_CONTRACTIONS),
    ("vcp_pivots", _DDL_VCP_PIVOTS),
    ("vcp_status_history", _DDL_VCP_STATUS_HISTORY),
    ("vcp_breakout_events", _DDL_VCP_BREAKOUT_EVENTS),
    ("vcp_scan_run_results", _DDL_VCP_SCAN_RUN_RESULTS),
    ("setup_scores", _DDL_SETUP_SCORES),
    ("score_components", _DDL_SCORE_COMPONENTS),
    ("forward_labels", _DDL_FORWARD_LABELS),
    ("backtest_runs", _DDL_BACKTEST_RUNS),
    ("backtest_events", _DDL_BACKTEST_EVENTS),
    ("strategy_setups", _DDL_STRATEGY_SETUPS),
    ("strategy_setup_status_history", _DDL_STRATEGY_SETUP_STATUS_HISTORY),
    ("strategy_breakout_events", _DDL_STRATEGY_BREAKOUT_EVENTS),
    ("strategy_scan_run_results", _DDL_STRATEGY_SCAN_RUN_RESULTS),
    ("paper_events", _DDL_PAPER_EVENTS),
    ("fundamental_filings", _DDL_FUNDAMENTAL_FILINGS),
    ("fundamental_snapshots", _DDL_FUNDAMENTAL_SNAPSHOTS),
    ("fundamental_facts", _DDL_FUNDAMENTAL_FACTS),
    ("fundamental_metrics", _DDL_FUNDAMENTAL_METRICS),
    ("fundamental_data_quality", _DDL_FUNDAMENTAL_DATA_QUALITY),
    ("fundamental_research_scores", _DDL_FUNDAMENTAL_RESEARCH_SCORES),
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

    @contextmanager
    def registered(
        self, name: str, columns: Sequence[str], rows: Sequence[Sequence[object]]
    ) -> Iterator[str]:
        """Expose ``rows`` to SQL as the view ``name`` for the duration of the block.

        Backed by an Arrow table, so set-based SQL over thousands of rows runs in
        milliseconds; ``executemany`` goes row by row (about 24 s for one bhavcopy day).
        """
        import pyarrow as pa  # type: ignore[import-untyped]  # noqa: PLC0415

        data = {c: [r[i] for r in rows] for i, c in enumerate(columns)}
        self.conn.register(name, pa.table(data))
        try:
            yield name
        finally:
            self.conn.unregister(name)

    def insert_rows(
        self,
        table: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[object]],
        *,
        ignore_conflicts: bool = False,
    ) -> int:
        """Bulk-insert ``rows`` into ``table`` (through :meth:`registered`).

        Column types come from the target table, so values only need to be castable to them.
        ``ignore_conflicts`` skips rows whose key already exists (``INSERT OR IGNORE``).
        """
        if not rows:
            return 0
        with self.registered(f"_bulk_{table}", columns, rows) as view:
            cols = ", ".join(columns)
            verb = "INSERT OR IGNORE" if ignore_conflicts else "INSERT"
            self.conn.execute(f"{verb} INTO {table} ({cols}) SELECT {cols} FROM {view}")
        return len(rows)

    def upsert_rows(
        self,
        insert_sql: str,
        rows: Sequence[Sequence[object]],
    ) -> int:
        """Run an ``INSERT INTO t (cols) VALUES (?, ...) ON CONFLICT ...`` statement for all
        ``rows`` at once: the ``VALUES`` list is replaced by a select from an Arrow view, so the
        conflict handling is unchanged but there is one statement instead of one per row
        (``executemany`` took about 40 s for one Trend Template scan; Phase 9 step 1)."""
        if not rows:
            return 0
        import re

        m = re.search(r"INSERT INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*\([?,\s]*\)", insert_sql)
        if m is None:
            raise ValueError("upsert_rows needs INSERT INTO t (cols) VALUES (?, ...) ...")
        table = m.group(1)
        columns = [c.strip() for c in m.group(2).split(",")]
        if insert_sql[m.end() :].count("?"):
            raise ValueError("upsert_rows: no parameters allowed after VALUES")
        with self.registered(f"_upsert_{table}", columns, rows) as view:
            cols = ", ".join(columns)
            self.conn.execute(
                f"INSERT INTO {table} ({cols}) SELECT {cols} FROM {view}{insert_sql[m.end() :]}"
            )
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
        self._add_column_if_missing("universe_snapshots", "survivorship_detail", "VARCHAR")
        self._migrate_fundamental_views()
        new_collections = not self._column_nullability("surveillance_collections")
        for table_name, ddl in _ALL_DDL:
            self.conn.execute(ddl)
            logger.debug("Ensured table: %s", table_name)
        if new_collections:
            # Databases from before per-day tracking: every flag row's valid_from is a day the
            # lists were collected, so seed the collection days from them (once).
            self.conn.execute(
                """
                INSERT OR IGNORE INTO surveillance_collections
                SELECT valid_from, flag_type, count(*), min(known_from)
                FROM surveillance_flags_history GROUP BY 1, 2
                """
            )
        # After the DDL pass: the backtest backfill reads vcp_patterns.
        self._migrate_strategy_dimension()
        for view_name, view_ddl in _ALL_VIEWS:
            self.conn.execute(view_ddl)
            logger.debug("Ensured view: %s", view_name)
        self._seed_event_history()
        logger.info("DuckDBStore migration complete (%d tables)", len(_ALL_DDL))

    def _migrate_fundamental_views(self) -> None:
        """F4 keyed fundamental_metrics/_data_quality by (stock, as-of date) instead of by
        snapshot. The old tables were created empty in F1 and never written, so an empty old
        table is dropped and recreated; a non-empty one is left alone and reported."""
        for table in ("fundamental_metrics", "fundamental_data_quality"):
            cols = {r[0] for r in self.conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                [table],
            ).fetchall()}  # fmt: skip
            if not cols or "as_of_date" in cols:
                continue
            count = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            if count is not None and count[0] == 0:
                self.conn.execute(f"DROP TABLE {table}")
            else:  # pragma: no cover - no such database exists
                raise RuntimeError(f"{table} has rows in the pre-F4 layout; migrate by hand")

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

    def _seed_event_history(self) -> None:
        """Give every event without history the intervals its row can still prove (C5).

        OPEN: one OPEN interval from ``detected_at``. RESOLVED: OPEN from ``detected_at`` to
        ``resolved_at``, then RESOLVED. Earlier reopen cycles were overwritten before the
        history existed and cannot be recovered.
        """
        self.conn.execute(
            """
            INSERT INTO data_quality_event_history
                (event_id, status, blocks_signal, valid_from, valid_to, resolved_by)
            SELECT e.event_id, 'OPEN', e.blocks_signal, e.detected_at,
                   CASE WHEN e.status = 'RESOLVED' THEN e.resolved_at END, NULL
            FROM data_quality_events e
            WHERE NOT EXISTS (SELECT 1 FROM data_quality_event_history h
                              WHERE h.event_id = e.event_id)
              AND (e.status = 'OPEN' OR e.resolved_at > e.detected_at)
            """
        )
        self.conn.execute(
            """
            INSERT INTO data_quality_event_history
                (event_id, status, blocks_signal, valid_from, valid_to, resolved_by)
            SELECT e.event_id, 'RESOLVED', e.blocks_signal, e.resolved_at, NULL, e.resolved_by
            FROM data_quality_events e
            WHERE e.status = 'RESOLVED' AND e.resolved_at IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM data_quality_event_history h
                              WHERE h.event_id = e.event_id AND h.valid_to IS NULL)
            """
        )

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
        the SQL expression used to populate it (e.g. ``COALESCE(col, 'LIVE')``), also for a
        column that exists only in the new table; other new columns take their DDL default.
        Runs in one transaction, so a failure leaves the original table untouched.
        ``drop_views`` are views that depend on the table; the caller's later DDL pass
        recreates them.
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
            new_cols = list(self._column_nullability(table))
            shared = [c for c in old_cols if c in new_cols]
            shared += [c for c in new_cols if c not in old_cols and c in fill]
            select = ", ".join(fill.get(c, c) for c in shared)
            self.conn.execute(
                f"INSERT INTO {table} ({', '.join(shared)}) SELECT {select} FROM {legacy}"  # noqa: S608
            )
            self.conn.execute(f"DROP TABLE {legacy}")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _migrate_strategy_dimension(self) -> None:
        """Add ``strategy_id`` to score, label and backtest tables (DATABASE_SCHEMA 35A.4).

        Every existing row is VCP's (the only strategy before the Multi-Strategy phase), so it
        gets ``'vcp'``; ``forward_labels`` gains it in its key. Backtest runs get the VCP
        algorithm version of their config. ``scan_runs`` rows of type VCP and SCORE get
        ``'vcp'``, Trend Template rows stay NULL. Tables that already have the column, or do
        not exist yet, are left alone, so this is safe to run on every start.
        """
        vcp = "'vcp'"
        for table, ddl, fill, views in (
            ("setup_scores", _DDL_SETUP_SCORES, {"strategy_id": vcp}, ("setup_scores_v",)),
            ("score_components", _DDL_SCORE_COMPONENTS, {"strategy_id": vcp}, ()),
            ("forward_labels", _DDL_FORWARD_LABELS, {"strategy_id": vcp}, ()),
            ("backtest_runs", _DDL_BACKTEST_RUNS, {
                "strategy_id": vcp,
                "algorithm_version": "(SELECT max(v.algorithm_version) FROM vcp_patterns v"
                                     " WHERE v.config_hash = backtest_runs__legacy.config_hash)",
            }, ()),
        ):  # fmt: skip
            cols = self._column_nullability(table)
            if cols and "strategy_id" not in cols:
                before = self._count(table)
                self._rebuild_table(table, ddl, fill=fill, drop_views=views)
                after = self._count(table)
                if after != before:  # pragma: no cover - the rebuild is one transaction
                    raise RuntimeError(f"{table}: {before} rows before the rebuild, {after} after")
                logger.info("Rebuilt %s with strategy_id ('vcp' for %d rows)", table, after)
        cols = self._column_nullability("scan_runs")
        if cols and "strategy_id" not in cols:
            self.conn.execute("ALTER TABLE scan_runs ADD COLUMN strategy_id VARCHAR")
            self.conn.execute(
                "UPDATE scan_runs SET strategy_id = 'vcp' WHERE scan_type IN ('VCP', 'SCORE')"
            )
            logger.info("Added scan_runs.strategy_id ('vcp' for VCP and SCORE runs)")

    def _count(self, table: str) -> int:
        row = self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
        return int(row[0]) if row else 0

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
