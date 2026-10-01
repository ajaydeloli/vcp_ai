# DATABASE_SCHEMA.md

**Project:** Institutional-Grade NSE VCP Scanner  
**Storage:** DuckDB + Parquet  
**Version:** 1.1  
**Status:** Architecture baseline (revised after design review)  
**Purpose:** Define the persistent data model, lineage, partitioning, point-in-time rules, and repository boundaries for the Minervini VCP system.

---

# 1. Purpose

This document defines the database/storage architecture for the project.

The database must support:

- 10+ years of historical NSE equity data;
- raw and adjusted OHLCV;
- multiple market-data providers;
- corporate actions;
- point-in-time universe construction;
- Trend Template calculations;
- VCP detection;
- technical feature storage;
- fundamental snapshots;
- scoring;
- scan runs;
- historical pattern observations;
- breakout events;
- data-quality monitoring;
- backtesting without survivorship or look-ahead bias;
- complete data lineage and reproducibility.

The storage layer must be independent of the broker/data-provider implementation.

---

# 2. Core Storage Principle

The local data lake is the project's source of truth.

```text
Kite
   \
    → Ingestion → Raw Storage → Normalization → Curated Storage
   /
Dhan
```

The scanner must not depend on a live broker API during normal analysis.

Normal operation:

```text
Local DuckDB + Parquet
        ↓
Universe
        ↓
Features
        ↓
VCP
        ↓
Scoring
        ↓
Reports / Alerts
```

Provider APIs are used for ingestion and repair/backfill.

---

# 3. Storage Architecture

Use a hybrid:

```text
                    ┌─────────────────────┐
                    │       DuckDB        │
                    │ metadata + queries  │
                    └──────────┬──────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
        Curated Parquet                 Raw Parquet
        analytical data                 immutable data
```

DuckDB provides:

- analytical SQL;
- joins;
- scans;
- feature queries;
- backtesting;
- local development;
- reproducible research.

Parquet provides:

- durable columnar storage;
- efficient historical datasets;
- partitioning;
- provider/source separation;
- inexpensive retention.

---

# 4. PostgreSQL Boundary

PostgreSQL is NOT required for V1 analytical storage.

It may be introduced later for multi-writer operational state such as:

- users;
- notification subscriptions;
- alert acknowledgements;
- trade journal;
- portfolio state;
- broker execution state.

Do not introduce PostgreSQL merely because the project is called "production-grade."

For a single-user analytical system, DuckDB + Parquet is the primary architecture.

---

# 5. Database Layers

The system has five logical layers:

```text
RAW
 ↓
NORMALIZED
 ↓
CURATED
 ↓
DERIVED
 ↓
RESEARCH
```

## RAW

Provider data exactly as received.

## NORMALIZED

Standardized instrument and corporate-action representation.

## CURATED

Canonical OHLCV and universe datasets.

## DERIVED

Indicators, features, Trend Template, VCP, scoring.

## RESEARCH

Backtest observations, labels, experiments, and validation results.

---

# 6. Raw Data Must Be Preserved

Never overwrite raw provider data.

If Kite reports:

```text
close = 100
```

and later a correction produces:

```text
close = 101
```

both ingestion observations may need to remain available.

Recommended metadata:

```text
provider
provider_record_id
retrieved_at
ingestion_run_id
source_hash
```

The canonical dataset can select the latest trusted record.

---

# 7. Instrument Identity

Do not use the ticker symbol as the permanent primary key.

Use:

```text
instrument_id
```

Recommended logical model:

```text
instrument_id
exchange
segment
symbol
tradingsymbol
isin
instrument_type
company_name
currency
lot_size
tick_size
valid_from
valid_to
```

The same company/security can have symbol changes.

Historical data must remain connected to the correct security identity.

---

# 8. `instruments`

Purpose:

Canonical security master.

Suggested columns:

| Column | Type | Description |
|---|---|---|
| instrument_id | VARCHAR | Internal immutable identifier |
| isin | VARCHAR | ISIN where available |
| exchange | VARCHAR | NSE |
| segment | VARCHAR | Equity segment |
| symbol | VARCHAR | Canonical symbol |
| tradingsymbol | VARCHAR | Provider trading symbol |
| company_name | VARCHAR | Issuer/security name |
| instrument_type | VARCHAR | EQ/ETF/etc. |
| currency | VARCHAR | INR |
| tick_size | DECIMAL | Minimum price increment |
| lot_size | INTEGER | Trading lot |
| valid_from | DATE | Identity validity start |
| valid_to | DATE | Identity validity end |
| is_active | BOOLEAN | Current status |
| created_at | TIMESTAMP | Record creation |
| updated_at | TIMESTAMP | Last update |

Primary key:

```text
instrument_id
```

---

# 9. `instrument_symbol_history`

Purpose:

Track symbol/name changes.

Columns:

```text
instrument_id
old_symbol
new_symbol
effective_date
change_type
source
created_at
```

Examples:

```text
SYMBOL_CHANGE
NAME_CHANGE
MERGER
DEMERGER
CORPORATE_RESTRUCTURE
```

Historical scans must resolve the symbol valid on the observation date.

---

# 10. Provider Instrument Mapping

Create a provider-specific mapping table:

```text
provider_instruments
```

Columns:

```text
provider
provider_instrument_id
instrument_id
provider_symbol
exchange
valid_from
valid_to
metadata_json
```

This allows:

```text
Kite instrument token
        ↓
instrument_id
        ↓
Dhan security ID
```

The strategy layer never imports broker-specific IDs.

---

# 11. `ingestion_runs`

Every ingestion operation gets a unique run.

Columns:

```text
ingestion_run_id
provider
dataset
started_at
completed_at
status
requested_start
requested_end
records_received
records_written
records_rejected
error_count
config_hash
code_version
source_metadata_json
```

Statuses:

```text
RUNNING
SUCCESS
PARTIAL
FAILED
```

This provides data lineage.

---

# 12. `raw_ohlcv`

Purpose:

Immutable provider-level market data.

Suggested columns:

```text
provider
provider_instrument_id
instrument_id
timestamp
interval
open
high
low
close
volume
oi
received_at
ingestion_run_id
source_hash
```

Do not mix adjusted prices into this table.

Raw means raw provider observations.

---

# 13. Raw Data Uniqueness

Logical uniqueness:

```text
provider
provider_instrument_id
timestamp
interval
```

But raw storage should still preserve ingestion provenance.

If duplicate records arrive:

```text
same provider candle
different ingestion run
```

the canonical layer decides which record is trusted.

---

# 14. Daily Canonical OHLCV (raw, bitemporal, append-only)

`daily_prices` holds one trusted **raw** record per instrument/trade date per system-time version. "Raw" means *as received*: for a provider-adjusted source (Kite) the values are adjusted for actions with ex-date on or before `known_from` (the fetch time); see DATA_SPECIFICATION §21.1. Rows are never updated in place. A correction inserts a new row and closes the previous one by setting `known_to`.

```text
instrument_id
trade_date
open_raw
high_raw
low_raw
close_raw
volume_raw
primary_provider
selection_reason
data_status
source_run_id
source_hash
known_from        -- system time the row became current
known_to          -- NULL while current
```

`last_verified_at` was removed because updating it would mutate history. Verification is recorded in `ingestion_runs` and `data_quality_events`.

`daily_prices_adjusted` is **derived and rebuildable**:

```text
instrument_id
trade_date
open_adj
high_adj
low_adj
close_adj
volume_adj
adjustment_version
price_factor_applied
volume_factor_applied
computed_from_snapshot_id
computed_at
```

Key: `(instrument_id, trade_date, adjustment_version, computed_from_snapshot_id)`. Adjusted values live in their own table because every new corporate action rewrites all earlier adjusted prices, and that must not silently change previously reported results.

`computed_from_snapshot_id` is `NOT NULL`. `LIVE` is the explicit marker for unfrozen working data; any other value is a `data_snapshots.data_snapshot_id` and means the row was built from raw bars and adjustment factors *as known at that snapshot's `known_at`*. A build under one snapshot never overwrites another's rows. The view `daily_prices_adjusted_current` keeps one (the most recently computed) `adjustment_version` per `(instrument_id, computed_from_snapshot_id)`; it spans snapshots, so **every reader must also filter `computed_from_snapshot_id = ?`** (guarded by `test_every_read_of_adjusted_view_filters_by_snapshot`).

As-known-at reads: `get_daily(instrument_id, start, end, as_of_date, known_at=None)`. `known_at` selects rows with `known_from <= known_at < known_to`. Historical reproducibility resolves a `data_snapshot_id` to `known_at` cutoffs (§41). Adjustment uses only corporate actions known at that time.

Liquidity and minimum-price tests use the **raw** columns. VCP/Trend calculations use adjusted columns; raw remains available for audit.

---

# 15. Adjusted vs Raw Prices

Never replace raw prices with adjusted prices.

Maintain both.

```text
RAW
 ↓
Corporate Actions
 ↓
ADJUSTED
```

The adjustment engine must be deterministic.

The project should be able to reconstruct adjusted prices from:

```text
raw_prices
+
corporate_actions
```

---

# 16. Corporate Actions

Create:

```text
corporate_actions
```

Columns:

```text
corporate_action_id
instrument_id
isin
action_date
announcement_date
ex_date
record_date
action_type
ratio_numerator
ratio_denominator
cash_amount
old_symbol
new_symbol
source
source_record_id
created_at
known_from
known_to
```

Corporate actions are bitemporal: a late-discovered action inserts a row with a later `known_from`, so earlier scans reproduce exactly as they were run.

`corporate_actions` holds **one row per source observation** (`source` = `NSE` | `UPSTOX` | ...). Cross-source agreement is recorded in `corporate_action_resolution` (§17A). Adjustments are computed only from resolved actions.

Action types:

```text
SPLIT
BONUS
DIVIDEND
RIGHTS
MERGER
DEMERGER
SYMBOL_CHANGE
NAME_CHANGE
```

Not every action necessarily affects price adjustment in the same way.

---

# 17. Corporate Action Adjustment Events

For reproducibility, store the derived adjustment factor:

```text
corporate_action_adjustments
```

Columns:

```text
resolution_id
instrument_id
effective_date
price_factor
volume_factor
cumulative_price_factor
cumulative_volume_factor
source
calculation_version
known_from
known_to
```

This makes adjustment calculations auditable.

---

# 17A. Corporate Action Resolution

Cross-source reconciliation result (DATA_SPECIFICATION §18A). Bitemporal, append-only.

`corporate_action_resolution`:

```text
resolution_id
instrument_id
isin
action_type
ex_date
ratio_numerator
ratio_denominator
cash_amount
nse_action_id
upstox_action_id
status               -- CONFIRMED | SINGLE_SOURCE | PROVIDER_CONFLICT | MANUAL_OVERRIDE
conflict_fields      -- e.g. ex_date, ratio
resolved_by
resolved_at
known_from
known_to
```

Only `CONFIRMED`, `SINGLE_SOURCE` and `MANUAL_OVERRIDE` rows feed `corporate_action_adjustments`. A `PROVIDER_CONFLICT` row for a SPLIT or BONUS blocks signals for the instrument from its ex-date until superseded by a new row; a DIVIDEND or RIGHTS conflict is recorded as a non-blocking warning (audit P0-2 policy, 2026-09-30). `cash_amount` differences count only when both sources report an amount.

`isin_history` (`instrument_id`, `isin`, `valid_from`, `valid_to`, `source`, `known_from`) maps ISINs to the permanent `instrument_id`.

---

# 18. Corporate Action Reconciliation

The system should compare:

```text
provider_adjusted_data
vs
internally_adjusted_data
```

and flag material differences.

Table:

```text
corporate_action_reconciliation
```

Fields:

```text
instrument_id
trade_date
provider
internal_close
provider_adjusted_close
difference_pct
status
checked_at
```

Statuses:

```text
MATCH
MINOR_DIFFERENCE
MATERIAL_DIFFERENCE
UNRESOLVED
```

---

# 19. Data Quality

Create:

```text
data_quality_events
```

Columns:

```text
event_id
instrument_id
trade_date
dataset
severity
blocks_signal
event_type
observed_value
expected_value
description
detected_at
resolved_at
status
```

Example event types:

```text
MISSING_CANDLE
DUPLICATE_CANDLE
BAD_OHLC
ZERO_VOLUME
UNEXPLAINED_GAP
CORPORATE_ACTION_MISMATCH
STALE_DATA
SYMBOL_MISMATCH
PROVIDER_CONFLICT
```

## 19.1 Implementation (audit P0-2)

`data_quality_events` is implemented and is the single source the signal gate reads. Columns are the ones above plus `resolved_by`, `resolution_note` and `context` (JSON text; carries what the spec calls `observed_value` / `expected_value`, e.g. the gap percentage). `event_id` is deterministic per condition (`dq-` + hash of event type, instrument and a key such as the gap date), so re-detection updates a row instead of duplicating it. `status` is `OPEN` or `RESOLVED`.

| Event type | Raised by | Blocks signals? | Starts blocking at | Clears when |
|---|---|---|---|---|
| `MISSING_CANDLES` | completeness check during `vcp ingest market` | yes | first missing session | the hole is filled and the check re-runs |
| `CORPORATE_ACTION_UNRESOLVED` | reconciliation (`ingest corporate-actions`, `quality scan`) for each current `PROVIDER_CONFLICT` | yes, unless `corporate_actions.conflict_blocks_signals: false` | the action's ex-date | the resolution is superseded (`CONFIRMED` / `MANUAL_OVERRIDE`); **never by hand** |
| `UNEXPLAINED_GAP` | gap detector (`ingest corporate-actions`, `quality scan`) | only when the gap is split-like (§18A) and not across a trading absence; otherwise a warning | the gap date | an action explains it, or a human resolves it as genuine |
| `TRADING_ABSENCE` | absence check (`ingest corporate-actions`, `quality scan`; audit P1-2c) | yes | the return bar after 20+ missed NSE sessions | ends on its own after the block lifetime (253 bars after the return) |

Every dated block also ends once the instrument has `data.quality.block_lifetime_bars` (253) of its own bars from the event date (audit P1-2b), whatever its status.

Lifecycle: detectors call `sync_events(instrument, event type, current events)`. New conditions open, present ones refresh, cleared ones close with `resolved_by = 'SYSTEM'` and reopen if they return. A human resolution (`vcp quality resolve EVENT_ID --by NAME --note TEXT`; both required) is final and never overridden. Corporate-action conflicts refuse human closure because the adjustment stays withheld while the resolution is `PROVIDER_CONFLICT`.

Gate: an event applies to `as_of_date >= trade_date` (NULL applies to every date) and, since audit P1-2, only while the instrument has fewer than `data.quality.block_lifetime_bars` (253) of its own bars in `[trade_date, as_of_date]`; after that every lookback window ending at the as-of date starts on or after the event bar. The bars are read as known at `known_at` when one is given. An undated event never expires. Without a `known_at` only `OPEN` events count. With one (a frozen data snapshot, or a universe snapshot's `created_at`) an event counts only if it was detected by then and not yet resolved, so a later detection never changes an earlier, reproducible run. Consumers: universe builder (ineligible, reason `Data quality blocked: ...`), RS (blocked instruments are left out of the ranking population), Trend Template (status `DATA_QUALITY_BLOCKED`, flags stored in `trend_template_results.blocked_by`).

Not implemented: `DUPLICATE_CANDLE`, `BAD_OHLC`, `ZERO_VOLUME`, `STALE_DATA`, `SYMBOL_MISMATCH` events (bad bars are still rejected at ingestion, not recorded here), and a `MANUAL_OVERRIDE` command to resolve a conflict.

---

# 20. Extreme Gap Detection

Flag overnight price changes around:

```text
±30%
```

when no matching corporate action exists.

This is a diagnostic flag, not an automatic rejection.

Example:

```text
UNEXPLAINED_GAP
severity = HIGH
```

The detector must not silently normalize suspicious data.

---

# 21. Trading Calendar

Create:

```text
trading_calendar
```

Columns:

```text
trade_date
exchange
is_trading_day
session_open
session_close
holiday_name
```

This is important for:

- missing-candle detection;
- rolling windows;
- duration calculations;
- backtesting;
- provider completeness checks.

Do not use calendar days when the strategy means trading days.

---

# 22. Universe Architecture

Universe selection must be point-in-time.

Create:

```text
universe_snapshots
```

and:

```text
universe_memberships
```

This prevents survivorship bias.

---

# 23. `universe_snapshots`

Columns:

```text
universe_snapshot_id
universe_name
as_of_date
created_at
config_hash
method_version
survivorship_status   -- POINT_IN_TIME_COMPLETE | PARTIAL | BIASED
```

Examples:

```text
NSE_EQUITY
NSE_LIQUID_EQUITY
MINERVINI_SCAN_UNIVERSE
```

---

# 24. `universe_memberships`

Columns:

```text
universe_snapshot_id
instrument_id
eligible
exclusion_reason
avg_traded_value
price
instrument_type
series
asm_flag
gsm_flag
t2t_flag
sme_flag
etf_flag
computed_at
```

This table captures what the scanner knew at that point in time.

---

# 24A. Security Master and Surveillance History

Required to rebuild historical universes (PROJECT_DESIGN §14A). Sources are provider-neutral.

`security_master_history`:

```text
instrument_id
isin
listing_date
delisting_date
delisting_reason
series
valid_from
valid_to
source
known_from
```

`surveillance_flags_history`:

```text
instrument_id
flag_type          -- ASM | GSM | ESM | T2T | BE
stage
valid_from
valid_to
source
known_from
```

`universe_memberships` flags are populated from these tables as of the snapshot date, never from today's status.

---

# 25. Liquidity

Store liquidity measurements rather than only a pass/fail result.

Examples:

```text
avg_traded_value_20d
avg_traded_value_50d
median_traded_value_20d
avg_volume_20d
avg_volume_50d
```

Then:

```text
liquidity_pass
```

can be derived from configuration.

---

# 26. RS Data

Relative Strength must be computed across the full eligible universe.

Store:

```text
relative_strength_snapshots
```

Columns:

```text
as_of_date
instrument_id
ret_63
ret_126
ret_189
ret_252
rs_raw
rs_rank
rs_percentile
population_size
rs_status
universe_snapshot_id
calculation_version
```

The RS calculation must not be restricted to stocks that already passed Trend Template.

---

# 27. Weekly Prices

Weekly data may be materialized for performance.

Table:

```text
weekly_prices
```

Columns:

```text
instrument_id
week_end
open
high
low
close
volume
source_daily_version
```

Weekly candles should be reproducibly derived from canonical daily data.

---

# 28. Indicator Storage

Avoid creating one enormous table containing every possible indicator.

Use a normalized feature layer.

Recommended:

```text
technical_features_daily
technical_features_weekly
```

---

# 29. `technical_features_daily`

Suggested fields:

```text
instrument_id
trade_date

sma_20
sma_50
sma_150
sma_200

ema_10
ema_20
ema_50

atr_14
atr_pct_14

high_20
high_50
high_252

low_20
low_50
low_252

volume_avg_5
volume_avg_10
volume_avg_20
volume_avg_50

volume_ratio_20
volume_ratio_50

daily_return
rolling_volatility_20
rolling_volatility_50

calculation_version
```

Windowed features are NULL until their window is full (AGENTS.md rule 4). `atr_14` needs 14 true ranges and `rolling_volatility_N` needs N daily returns; the first bar has no previous close, so its `daily_return` and true range are NULL. Ratios are NULL when their average is NULL. Introduced in `features-1.1.0`.

Additional features can be added without changing the raw market data.

---

# 30. Trend Template Results

Summary row, `trend_template_results`:

```text
scan_id
instrument_id
as_of_date
status                  -- PASS | FAIL | INSUFFICIENT_DATA
trend_template_pass
weekly_stage            -- 1 | 2 | 3 | 4 | TRANSITION
weekly_stage2_pass
sma_w
slope_pct
is_partial_week
rs_rank
trend_score
calculation_version
config_hash
```

One row per condition, `trend_template_conditions` (10 conditions per TREND_TEMPLATE_SPECIFICATION §2):

```text
instrument_id
as_of_date
condition_id            -- 1..10
condition_name
measurement
threshold
passed
calculation_version
config_hash             -- part of the key: scans with different thresholds coexist
```

Primary key: `(instrument_id, as_of_date, condition_id, calculation_version, config_hash)`.

The old fixed `condition_1..8` columns are removed: they did not fit ten conditions and stored no measurements.

Weekly stage context, `weekly_context`:

```text
instrument_id
as_of_date
weekly_stage            -- 1 | 2 | 3 | 4 | TRANSITION
sma_w
slope_pct
prior_pct
is_partial_week
algorithm_version       -- stage algorithm version, e.g. stage-1.0.0
```

The `trend_template_results` summary row deliberately omits `prior_pct` and the stage algorithm version; both live only in `weekly_context`.

---

# 31. VCP Results

`vcp_patterns` stores the current/historical pattern **with raw measurements**, so classification can be re-derived without re-detecting.

```text
vcp_pattern_id
instrument_id
as_of_date
scan_id
is_primary

base_start_date
base_end_date
base_high
base_low
base_depth_pct
base_duration_days
prior_advance_return_pct

contraction_count
first_contraction_pct
final_contraction_pct
max_tightening_ratio
progressive_tightening

final_volume_ratio
volume_dryup_pass
atr_contraction_ratio
volatility_contraction_pass
right_side_range_pct
tight_pivot_pass

tightening_quality
volatility_quality
volume_quality
pivot_quality
base_quality

pivot_price
pivot_date
pivot_distance_pct

classification          -- NONE | VCP_LIKE | VCP | A_PLUS_VCP
status                  -- FORMING | PIVOT_READY | BREAKOUT | FAILED | INVALIDATED | INSUFFICIENT_DATA | DATA_NOT_READY | STALE_DATA
confirmation_state      -- CONFIRMED | PROVISIONAL

trend_template_pass
weekly_stage2_pass

algorithm_version
config_hash
data_snapshot_id
created_at
```

---

# 32. VCP Contractions

Do not store T1/T2/T3 only as JSON.

Create:

```text
vcp_contractions
```

Columns:

```text
vcp_pattern_id
sequence_number
peak_date
peak_price
trough_date
trough_price
depth_pct
duration_days
atr_pct
range_pct
volume_ratio
tightening_ratio_to_prior
confirmation_date
is_confirmed
```

This allows SQL research such as:

```sql
SELECT AVG(depth_pct)
FROM vcp_contractions
WHERE sequence_number = 3;
```

---

# 33. VCP Pivots

Create:

```text
vcp_pivots
```

Columns:

```text
vcp_pattern_id
pivot_id
pivot_price
pivot_date
source
touches
rejection_count
right_side_tightness_pct
distance_to_close_pct
is_primary
```

This preserves alternative candidate pivots.

---

# 34. VCP Status History

Patterns evolve over time.

Create:

```text
vcp_status_history
```

Columns:

```text
instrument_id
as_of_date
vcp_pattern_id
previous_classification
new_classification
previous_status
new_status
reason
algorithm_version
config_hash
```

Example:

```text
VCP_LIKE
    ↓
VCP
    ↓
A_PLUS_VCP
    ↓
PIVOT_READY
    ↓
BREAKOUT
```

---

# 35. Setup Scores

`setup_scores`:

```text
scan_id
instrument_id
as_of_date

trend_score
vcp_score
volume_score
rs_score
fundamental_score          -- NULL when unavailable, never 0

final_setup_score
ranking_percentile
confirmation_state
fundamental_available
weights_renormalized

trend_weight
vcp_weight
volume_weight
rs_weight
fundamental_weight

scoring_version
config_hash
```

`score_components` keeps every underlying measurement (SCORING_SPECIFICATION §2):

```text
scan_id
instrument_id
component              -- TREND | VCP | VOLUME | RS | FUNDAMENTAL
sub_component
raw_measurement
normalized_0_100
weight_within_component
points
max_points
scoring_version
```

Scores are rankings, not probabilities.

---

# 36. Fundamental Snapshots

Fundamentals must be point-in-time, basis-aware and revision-aware.

`fundamental_snapshots`:

```text
fundamental_snapshot_id
instrument_id
period_end
period_type                 -- QUARTER | ANNUAL
statement_basis             -- CONSOLIDATED | STANDALONE
revision_number
superseded_by_snapshot_id
filing_date
available_at
provider
currency
data_status
source_record_id
ingestion_run_id
```

Unique key: `(instrument_id, period_end, period_type, statement_basis, revision_number)`. This prevents the consolidated/standalone collision seen in real NSE XBRL data.

Rules:

- **As-of selection:** for each period, take the highest `revision_number` with `available_at <= as_of_date`. A restatement never alters what earlier scans saw.
- **Basis policy** (`fundamentals.statement_basis_policy`, default `prefer_consolidated`): use CONSOLIDATED when it exists for the period, else STANDALONE. YoY/QoQ growth is computed only between periods of the **same** basis. A basis switch yields NULL growth with `data_status = BASIS_CHANGE`.

The critical date is `available_at`. Backtests must not use a fundamental value before it became publicly available.

---

# 37. Fundamental Metrics

Create:

```text
fundamental_metrics
```

Columns:

```text
fundamental_snapshot_id

revenue
revenue_yoy
revenue_qoq

eps
eps_yoy
eps_qoq
eps_acceleration

gross_margin
operating_margin
net_margin
margin_expansion

roe
debt
debt_to_equity
```

Additional metrics can be added later.

---

# 38. Fundamental Data Availability

Store a data completeness score:

```text
fundamental_data_quality
```

Potential fields:

```text
eps_available
sales_available
margin_available
roe_available
debt_available
periods_available
availability_score
```

A stock with insufficient fundamental history should not silently receive zeros.

---

# 39. Fundamental Hard Gates

The strategy defines limited hard filters.

Examples:

```text
negative trailing EPS
insufficient fundamental data
```

These should be stored as explicit flags:

```text
fundamental_hard_gate_pass
fundamental_gate_reason
```

Do not mix them with technical gates.

---

# 40. Scan Runs

Create:

```text
scan_runs
```

Columns:

```text
scan_id
started_at
completed_at
as_of_date
scan_type
universe_snapshot_id

data_snapshot_id
config_hash
algorithm_version
research_mode        -- point_in_time | revised_history
survivorship_status

symbols_considered
symbols_trend_pass
symbols_vcp
symbols_a_plus
symbols_pivot_ready

status
error_count
```

## 40.1 Implemented (audit P1-8)

`vcp compute trend-template` writes one `scan_runs` row per run, inserted once and never updated (a rerun is a new row). Columns as implemented: `scan_run_id` (`run-<as-of YYYYMMDD>-<UTC start, microseconds>Z`), `scan_type` (`TREND_TEMPLATE`), `as_of_date`, `scan_id` (the `trend_template_results` scan), `data_snapshot_id`, `data_cutoff` (LIVE: the scan's start time; frozen: the snapshot's `known_at`), `universe_snapshot_id` and `universe_cutoff` (its `created_at`), `scan_config_hash` plus `section_hashes` (JSON: strategy, universe, gate), `code_commit` and `code_dirty` (git; `unknown`/NULL outside a checkout), `versions` (JSON algorithm tags), `survivorship_status` and `survivorship_detail` (from the universe snapshot), `counts` (JSON: considered and per status), `results_hash`, `started_at`, `completed_at`, `status`.

`scan_run_results (scan_run_id, instrument_id, status, trend_template_pass, weekly_stage, rs_rank, blocked_by)` keeps each run's verdicts, so a rerun under the same `scan_id` (which overwrites `trend_template_results`) cannot erase what an earlier run reported. `results_hash` is a SHA-256 over those verdict rows, sorted, with RS ranks rounded to 6 decimals.

Verification (D4): no frozen copy is kept per scan (one costs about 0.9 GB). `vcp verify scan RUN_ID` copies the database, freezes the data at the run's `data_cutoff` on the copy, rebuilds the universe as known at `universe_cutoff` (same deterministic id), RS and the Trend Template over exactly that universe and snapshot, and compares `results_hash`; `--keep` keeps the rebuilt copy as a frozen scan. A different code commit is reported, since the code is part of what made the result.

Not implemented from the list above: `research_mode` (every run is point-in-time by construction) and the VCP counts (`symbols_vcp`, `symbols_a_plus`, `symbols_pivot_ready`), which arrive with the Phase 6 detector.

---

# 41. Data Snapshot

A scan must reference the exact data environment used.

`data_snapshots`:

```text
data_snapshot_id
created_at
market_data_known_at
fundamental_data_known_at
corporate_action_known_at
universe_cutoff
provider_versions
dataset_versions
description
```

`snapshot_manifest`:

```text
data_snapshot_id
dataset
dataset_version
known_at_cutoff
row_count
content_hash
```

A snapshot is a `known_at` cutoff per dataset plus a content-hash manifest. Because canonical tables are append-only/bitemporal (§14), a snapshot can be re-materialised, and a verification job recomputes the hashes to prove nothing changed. Cutoff dates alone are not sufficient.

## 41.1 Implemented subset (audit P0-1)

The code implements the price-derivation core of this section; the rest is deliberately deferred (audit P1-5). Implemented `data_snapshots` columns: `data_snapshot_id` (deterministic `snap-YYYYMMDDTHHMMSSZ` from `known_at`), `known_at` (one cutoff applied to raw bars **and** corporate-action adjustments), `created_at`, `description`. **Not implemented:** per-dataset cutoffs (`market_data_known_at`, `fundamental_data_known_at`, `corporate_action_known_at`, `universe_cutoff`), `provider_versions`/`dataset_versions`, and the `snapshot_manifest` with content hashes, so a snapshot's reproducibility is by construction (bitemporal inputs), not yet proven by hash verification.

`LIVE` is a reserved id for unfrozen data and is not a `data_snapshots` row. Results under `LIVE` must not be used to validate thresholds or backtests.

Snapshot lineage on derived tables (`data_snapshot_id VARCHAR NOT NULL DEFAULT 'LIVE'`):

| Table | Key |
|---|---|
| `technical_features_daily` | `(instrument_id, trade_date, calculation_version, data_snapshot_id)` |
| `weekly_prices` | `(instrument_id, week_end, source_daily_version, data_snapshot_id)` |
| `relative_strength_snapshots` | `(as_of_date, instrument_id, calculation_version, data_snapshot_id, universe_snapshot_id)` |
| `weekly_context` | `(instrument_id, as_of_date, algorithm_version, data_snapshot_id)` |
| `trend_template_conditions` | `(instrument_id, as_of_date, condition_id, calculation_version, config_hash, data_snapshot_id)` |
| `trend_template_results` | `(scan_id, instrument_id)`; `data_snapshot_id` recorded as a column, and CLI scan ids embed a non-LIVE snapshot |

Repositories and engines are bound to one data snapshot at construction and read and write only that snapshot. The RS read defaults to the row ranked over the most recently created universe snapshot when no `universe_snapshot_id` is given. `store.migrate()` rebuilds pre-lineage tables and keeps existing rows under `LIVE`.

---

# 42. Configuration Hash

Every analytical result must be reproducible from:

```text
data_snapshot_id
config_hash
algorithm_version
```

The resolved configuration should be serialized canonically before hashing.

Do not hash only the YAML file path.

Hash the resolved values.

---

# 43. Breakout Events

Create:

```text
breakout_events
```

Columns:

```text
breakout_event_id
instrument_id
vcp_pattern_id
pivot_price
breakout_date
breakout_price
breakout_volume
volume_ratio
confirmation_status
algorithm_version
```

The breakout event is separate from the VCP pattern.

---

# 44. Monitoring Observations

Create:

```text
monitoring_observations
```

Purpose:

Track stocks after they enter:

```text
VCP
A_PLUS_VCP
PIVOT_READY
```

Columns:

```text
instrument_id
observation_date
vcp_pattern_id
close
pivot_price
distance_to_pivot_pct
volume_ratio
classification
status
```

This supports daily monitoring without mutating historical VCP results.

---

# 45. Research Observations

Create:

```text
vcp_observations
```

Purpose:

One point-in-time observation suitable for research/ML.

Columns:

```text
observation_id
instrument_id
observation_date
vcp_pattern_id
classification
feature_snapshot_id
market_regime
config_hash
algorithm_version
```

---

# 46. Forward Outcome Labels

Create:

```text
vcp_outcome_labels
```

These are generated separately from the detector.

Fields:

```text
observation_id
horizon_days

forward_return
forward_max_return
forward_min_return

breakout_occurred
breakout_days
max_drawdown_before_target
target_reached

label_version
```

Important:

Outcome labels may use future data.

Features may not.

This separation prevents accidental label leakage.

---

# 47. Feature Snapshots

Create:

```text
feature_snapshots
```

Purpose:

Freeze the exact feature vector used for an observation.

Columns:

```text
feature_snapshot_id
instrument_id
as_of_date
feature_schema_version
feature_json
created_at
```

For highly queried fields, also materialize relational columns.

JSON should not become the primary analytical schema.

---

# 48. Backtest Runs

Create:

```text
backtest_runs
```

Columns:

```text
backtest_id
started_at
completed_at
start_date
end_date
universe_definition
strategy_version
config_hash
data_snapshot_id
execution_model
research_mode
survivorship_status
status
```

---

# 49. Backtest Trades / Simulated Events

Even before real execution exists, create:

```text
backtest_events
```

Columns:

```text
backtest_id
instrument_id
event_date
event_type
price
quantity
signal_id
metadata_json
```

Event types:

```text
ENTRY_SIGNAL
BREAKOUT
EXIT_SIGNAL
STOP
TIME_EXIT
INVALIDATION
```

Position sizing can remain outside the VCP engine.

---

# 50. Alerts

Operational alerts can initially remain in DuckDB.

Create:

```text
alerts
```

Columns:

```text
alert_id
instrument_id
vcp_pattern_id
alert_type
triggered_at
severity
message
status
acknowledged_at
metadata_json
```

Examples:

```text
NEW_A_PLUS_VCP
PIVOT_READY
BREAKOUT
VCP_INVALIDATED
DATA_QUALITY
```

---

# 51. Repository Boundary

Application modules must not directly execute arbitrary SQL against all tables.

Use repository interfaces.

Examples:

```python
class PriceRepository:
    def get_daily_prices(...): ...

class UniverseRepository:
    def get_universe(...): ...

class FeatureRepository:
    def get_features(...): ...

class VCPRepository:
    def save_pattern(...): ...
    def get_patterns(...): ...

class FundamentalRepository:
    def get_snapshot(...): ...
```

This keeps the domain layer independent from DuckDB.

---

# 52. Provider Boundary

The database layer must not import:

```text
kiteconnect
dhan
```

The provider layer writes normalized records through ingestion/repository interfaces.

Architecture:

```text
KiteProvider
DhanProvider
       ↓
Ingestion Service
       ↓
Repository
       ↓
DuckDB / Parquet
```

---

# 53. Partitioning Strategy

Parquet should generally be partitioned by:

```text
dataset
year
```

For very large datasets:

```text
dataset/year/month
```

Example:

```text
data/
├── raw/
│   └── ohlcv/
│       ├── year=2018/
│       ├── year=2019/
│       └── ...
│
├── curated/
│   └── daily_prices/
│       ├── year=2018/
│       ├── year=2019/
│       └── ...
│
└── features/
    └── daily/
        ├── year=2018/
        └── ...
```

Do not partition by `instrument_id` by default because thousands of tiny files can degrade performance.

---

# 54. Parquet File Size

Avoid one file per stock per day.

Target reasonably sized Parquet files, approximately:

```text
100 MB – 1 GB
```

depending on workload.

Optimize for analytical scans rather than individual-row updates.

---

# 55. DuckDB Tables vs Parquet

Use DuckDB tables for:

```text
metadata
instrument master
universe snapshots
scan runs
configuration lineage
alerts
research metadata
```

Use Parquet for:

```text
large OHLCV history
large feature datasets
large research datasets
```

DuckDB can query Parquet directly.

---

# 56. Derived Data Rebuildability

Derived tables must be rebuildable.

Examples:

```text
technical_features
trend_template_results
vcp_patterns
setup_scores
```

should never be treated as irreplaceable source data.

If the calculation engine changes:

```text
raw data remains
        ↓
rebuild derived datasets
```

---

# 57. Immutable vs Mutable Data

Immutable:

```text
raw_ohlcv
scan_runs
vcp_patterns
vcp_contractions
vcp_outcome_labels
```

Append-only / bitemporal (never updated in place):

```text
daily_prices
corporate_actions
corporate_action_adjustments
security_master_history
surveillance_flags_history
universe_memberships
fundamental_snapshots
monitoring_observations
alerts
```

Mutable operational state:

```text
alert acknowledgement
trade journal
portfolio state
```

should eventually move to PostgreSQL if multi-writer requirements appear.

---

# 58. Time Semantics

Every date field must have a defined meaning.

Distinguish:

```text
event_date
trade_date
period_end
filing_date
available_at
retrieved_at
created_at
```

Never use one generic `date` field for fundamentally different meanings.

---

# 59. Point-in-Time Rules

For a scan on:

```text
2026-09-28
```

the scanner may use:

```text
price data <= 2026-09-28
fundamentals available_at <= 2026-09-28
universe membership known as of <= 2026-09-28
```

It must not use:

```text
fundamental filing published later
future corporate-action information
future universe membership
future symbol status
future price
```

---

# 60. Survivorship Bias Protection

Historical universe membership must be persisted.

Do not backtest:

```text
current NSE stocks
```

against historical data.

Instead:

```text
Universe at date T
        ↓
Eligible instruments at T
        ↓
Historical scan at T
```

Dead/delisted securities should remain in historical datasets where data is available.

---

# 61. Look-Ahead Protection

All derived records must carry:

```text
as_of_date
```

Every feature must be computable using only data available by that date.

The repository APIs should support:

```python
get_features(
    instrument_id,
    as_of_date
)
```

rather than only:

```python
get_latest_features()
```

---

# 62. Fundamental Look-Ahead Protection

This is especially important.

A quarter may have:

```text
period_end = 2026-06-30
```

but become available:

```text
2026-07-30
```

A backtest on:

```text
2026-07-15
```

must not use that quarter.

Use:

```text
available_at
```

as the gating timestamp.

---

# 63. Data Provider Fallback

Configured fallback:

```text
Local DuckDB/Parquet
        ↓
Kite
        ↓
Dhan
```

The local dataset is queried first.

If data is missing:

```text
request provider
      ↓
validate
      ↓
store locally
      ↓
retry analysis
```

The system should not repeatedly download the same historical candles.

---

# 64. Staleness

Every dataset should expose freshness.

Example:

```text
last_available_trade_date
last_verified_at
```

The scanner refuses to emit production signals when:

```text
today - last_valid_market_data > configured_staleness_limit
```

The exact trading-day calculation must use the exchange calendar.

---

# 65. Provider Conflicts

If Kite and Dhan disagree:

```text
Provider A
Provider B
     ↓
Reconciliation
```

Do not silently select one.

Store:

```text
provider_conflict
```

with:

```text
difference_pct
selected_source
selection_reason
```

---

# 66. Data Quality Status

Each canonical price record can have:

```text
VALID
SUSPECT
INVALID
MISSING
REPAIRED
```

The VCP engine should refuse or downgrade data containing critical invalid records.

---

# 67. Versioning

Every derived dataset should expose:

```text
schema_version
calculation_version
algorithm_version
```

Example:

```text
schema_version = 1
calculation_version = 1.2.0
algorithm_version = vcp-1.0.0
```

---

# 68. Recommended Directory Layout

```text
project/
├── data/
│   ├── raw/
│   │   ├── ohlcv/
│   │   ├── instruments/
│   │   └── fundamentals/
│   │
│   ├── normalized/
│   │   ├── corporate_actions/
│   │   └── instruments/
│   │
│   ├── curated/
│   │   ├── daily_prices/
│   │   ├── weekly_prices/
│   │   └── universe/
│   │
│   ├── features/
│   │   ├── daily/
│   │   ├── weekly/
│   │   └── vcp/
│   │
│   └── research/
│       ├── observations/
│       └── outcomes/
│
├── db/
│   └── vcp_scanner.duckdb
│
└── migrations/
```

---

# 69. Data Retention

Retention must be configurable.

Recommended policy:

```text
Raw market data:
10+ years

Canonical OHLCV:
10+ years

Features:
rebuildable; retain according to research needs

VCP observations:
retain indefinitely

Scan runs:
retain indefinitely

Outcome labels:
retain indefinitely
```

Do not delete raw historical data merely to reduce storage until a formal retention policy exists.

---

# 70. Database Integrity Checks

Scheduled integrity checks should verify:

### Price integrity

```text
low <= open <= high
low <= close <= high
volume >= 0
```

### Temporal integrity

```text
no duplicate instrument/date
```

### Corporate actions

```text
adjustment factors coherent
```

### Universe

```text
point-in-time membership available
```

### Features

```text
feature date <= source data cutoff
```

### VCP

```text
contraction confirmation <= as_of_date
pivot information <= as_of_date
```

---

# 71. Performance Requirements

Initial targets:

```text
500–2,000 liquid NSE instruments
10+ years daily data
```

Expected capabilities:

```text
full universe scan: practical on a local workstation/server
daily incremental update: seconds to minutes
single-stock VCP analysis: sub-second after data is local
historical research query: seconds where practical
```

Exact performance targets should be benchmarked after schema implementation rather than assumed.

---

# 72. Indexing

DuckDB is columnar, so conventional OLTP indexing should not dominate the design.

Optimize through:

- partition pruning;
- column projection;
- Parquet statistics;
- clustering/sorting;
- materialized derived datasets;
- appropriate DuckDB table layouts.

Common access pattern:

```text
instrument_id + date range
```

Data should be physically organized to support this efficiently.

---

# 73. Canonical Query Contract

A typical VCP request should conceptually be:

```python
prices = price_repository.get_daily(
    instrument_id=instrument_id,
    start_date=...,
    end_date=as_of_date,
)

weekly = feature_repository.get_weekly_context(
    instrument_id=instrument_id,
    as_of_date=as_of_date,
)
```

The VCP engine should never query raw Parquet files directly.

---

# 74. Database Does Not Own Strategy Logic

The database stores:

```text
facts
measurements
snapshots
results
lineage
```

The strategy engine owns:

```text
rules
thresholds
classification
scoring
```

Avoid embedding strategy rules inside SQL.

Bad:

```sql
CASE WHEN contraction_pct < 8 THEN 'A_PLUS'
```

Prefer:

```text
Database → measurements
Python domain logic → classification
Database → classification result
```

This makes strategy experimentation safer.

---

# 75. Recommended Entity Relationship

```text
                         ┌──────────────────┐
                         │    instruments   │
                         └────────┬─────────┘
                                  │
              ┌───────────────────┼──────────────────┐
              │                   │                  │
              ▼                   ▼                  ▼
       daily_prices        corporate_actions    fundamentals
              │
              ▼
      technical_features
              │
       ┌──────┴─────────┐
       ▼                ▼
Trend Template       VCP Pattern
                         │
                 ┌───────┼────────┐
                 ▼       ▼        ▼
           contractions pivots  status_history
                         │
                         ▼
                    setup_scores
                         │
                         ▼
                   scan_runs
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
        monitoring               research
                                     │
                                     ▼
                                outcomes
```

---

# 76. Minimum V1 Tables

Do not attempt to implement every table on day one.

V1 minimum:

```text
instruments
provider_instruments
ingestion_runs
raw_ohlcv
daily_prices
corporate_actions
data_quality_events
trading_calendar
universe_snapshots
universe_memberships
weekly_prices
technical_features_daily
technical_features_weekly
trend_template_results
vcp_patterns
vcp_contractions
vcp_pivots
scan_runs
setup_scores
score_components
daily_prices_adjusted
corporate_action_adjustments
trend_template_conditions
relative_strength_snapshots
security_master_history
surveillance_flags_history
data_snapshots
snapshot_manifest
```

Then add:

```text
fundamentals
research
backtesting
alerts
```

as their respective modules mature.

---

# 77. Migration Strategy

Schema changes must be version controlled.

Use:

```text
migrations/
001_initial_schema.sql
002_corporate_actions.sql
003_vcp_schema.sql
...
```

Never manually modify production tables without recording the migration.

---

# 78. Testing Strategy

Database tests must include:

### Unit tests

- schema creation;
- insert/read;
- type validation;
- repository behavior.

### Data tests

- duplicate candles;
- invalid OHLC;
- missing dates;
- corporate actions;
- provider conflicts.

### Point-in-time tests

Verify that:

```text
future records
```

cannot appear in an as-of query.

### Regression tests

Run known VCP fixtures against frozen datasets.

---

# 79. Disaster Recovery

Because the project is personal but production-grade:

Minimum backup strategy:

```text
DuckDB metadata backup
+
Parquet backup
+
configuration repository
+
database migration files
```

The Git repository must not be the only copy of historical market data.

Recommended:

```text
local primary
+
separate backup disk/cloud storage
```

---

# 80. Security

Secrets must never be stored in the database.

Never store:

```text
Kite API secret
Dhan secret
access tokens
refresh tokens
```

in DuckDB/Parquet.

Use:

```text
.env
secret manager
environment variables
```

The database may store:

```text
provider name
credential reference
```

but not credentials themselves.

---

# 81. Auditability Requirement

For any displayed setup, the system must be able to trace:

```text
Dashboard result
      ↓
setup_score
      ↓
vcp_pattern
      ↓
vcp_contractions
      ↓
technical_features
      ↓
daily_prices
      ↓
provider/raw data
      ↓
ingestion_run
```

This is a core institutional-grade property.

---

# 82. Final Architecture

The database architecture is:

```text
              KITE
                │
              DHAN
                │
                ▼
         ┌──────────────┐
         │   INGESTION  │
         └──────┬───────┘
                ▼
         ┌──────────────┐
         │ RAW PARQUET  │
         └──────┬───────┘
                ▼
      ┌─────────────────────┐
      │ NORMALIZATION       │
      │ Corporate Actions   │
      │ Instrument Identity │
      └──────────┬──────────┘
                 ▼
      ┌─────────────────────┐
      │ CANONICAL DATA      │
      │ Daily / Weekly      │
      │ Universe            │
      └──────────┬──────────┘
                 ▼
      ┌─────────────────────┐
      │ DERIVED FEATURES    │
      │ Indicators          │
      │ Trend Template      │
      │ VCP                 │
      │ Fundamentals        │
      └──────────┬──────────┘
                 ▼
      ┌─────────────────────┐
      │ SCORING / RESEARCH  │
      └──────────┬──────────┘
                 ▼
      ┌─────────────────────┐
      │ REPORTING / UI      │
      │ ALERTS / BACKTEST   │
      └─────────────────────┘
```

---

# 83. Definition of Done

The database architecture is considered ready for V1 implementation when:

- [ ] instrument identity is provider-independent;
- [ ] raw provider data is preserved;
- [ ] canonical daily OHLCV exists;
- [ ] raw and adjusted prices are separate;
- [ ] corporate actions are represented;
- [ ] provider reconciliation exists;
- [ ] data-quality events are persisted;
- [ ] point-in-time universe membership is represented;
- [ ] RS snapshots can be calculated across the full eligible universe;
- [ ] weekly data can be derived reproducibly;
- [ ] technical features are versioned;
- [ ] Trend Template results are stored at condition level;
- [ ] VCP patterns are stored separately from contractions and pivots;
- [ ] scan runs have configuration/data lineage;
- [ ] fundamental snapshots use `available_at`;
- [ ] historical as-of queries are supported;
- [ ] survivorship bias is addressed;
- [ ] look-ahead bias is structurally constrained;
- [ ] derived datasets are rebuildable;
- [ ] repositories isolate DuckDB/Parquet from domain logic;
- [ ] migrations are version controlled;
- [ ] backups are defined;
- [ ] canonical price and corporate-action tables are bitemporal/append-only;
- [ ] snapshot manifests with content hashes verify;
- [ ] adjusted prices are stored separately from raw;
- [ ] Trend Template stored with all 10 conditions and measurements;
- [ ] score components stored with raw measurements;
- [ ] fundamentals keyed by statement basis and revision;
- [ ] security master and surveillance history support historical universes;
- [ ] corporate actions stored per source and reconciled in `corporate_action_resolution`;
- [ ] adjustment factors carry source and calculation version;
- [ ] ISIN history maps to a permanent `instrument_id`.

---

# 84. Design Rule

The most important database rule is:

> **Never store only the final answer when the underlying measurement can be stored.**

For example, do not store only:

```text
VCP = TRUE
```

Store:

```text
T1 = 18.2%
T2 = 10.1%
T3 = 6.4%

ATR contraction = ...
Volume contraction = ...
Pivot = ...
Base depth = ...
```

Then derive:

```text
A_PLUS_VCP
```

from those measurements.

This makes the system:

- explainable;
- testable;
- backtestable;
- reproducible;
- configurable;
- suitable for future ML;
- resilient to strategy changes.

The database is therefore not merely a place to save scanner results. It is the **historical research foundation of the entire Minervini AI system**.