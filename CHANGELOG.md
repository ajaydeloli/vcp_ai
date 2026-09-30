# Changelog

All notable changes to the Institutional-Grade NSE VCP Scanner will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed (audit 2026-09-30 P0-3, gap safety net and unknown split ratios)
- `GapDetector` treats a gap as explained only by an *applied* split/bonus: status CONFIRMED / SINGLE_SOURCE / MANUAL_OVERRIDE and a present, finite, positive ratio (`domain.corporate_actions.explains_price_gap`). Before, any resolution on the ex-date (dividend, rights, PROVIDER_CONFLICT, or a split whose ratio NSE text could not be parsed) silenced the detector while the adjustment engine applied factor 1.0, so an unadjusted split passed through as a normal-looking crash.
- `corporate_action_events` also emits a `CORPORATE_ACTION_UNRESOLVED` event (`cause = ratio_unknown`, distinct event id) for every adjustable split/bonus without a usable ratio. It always blocks from the ex-date, cannot be hand-resolved, and clears when a later resolution carries a ratio. Producers (CA worker, `vcp quality scan`) pick it up automatically.
- Behavior change: instruments with an unparsed split/bonus ratio, or with a split-like gap on a dividend/conflict ex-date, now report `DATA_QUALITY_BLOCKED` and drop out of the universe and RS population. Test fixtures that used ratio-less CONFIRMED splits as "explanations" now carry a ratio.

### Fixed (audit P0-2, data-quality gate)
- Data-quality events are now persisted and enforced. New `data_quality_events` table and `DuckDBDataQualityRepository` (idempotent `sync_events`, human `resolve`, point-in-time `blocked_instruments`). Producers: the completeness check (`MISSING_CANDLES`, blocks from the first missing session), corporate-action reconciliation (`CORPORATE_ACTION_UNRESOLVED` per `PROVIDER_CONFLICT`, blocks from the ex-date, honours `conflict_blocks_signals`) and the gap detector (`UNEXPLAINED_GAP`, blocks only when split-like, per DATA_SPECIFICATION 18A). Why: conflicts, gaps and missing bars were modeled but never stored or consulted, so a symbol with unresolved adjustment data produced normal-looking signals. The gap detector was never invoked at all; it now runs after `vcp ingest corporate-actions` and via `vcp quality scan`.
- Consumers: `UniverseBuilder(quality_gate=...)` marks blocked instruments ineligible (`Data quality blocked: ...`); `RelativeStrengthEngine(quality_gate=...)` leaves them out of the ranking population; `TrendTemplateEngine(quality_gate=...)` returns the new status `DATA_QUALITY_BLOCKED` (checked before any data is read, all ten conditions stay NULL) and `trend_template_results.blocked_by` stores the flags. Behavior change: symbols with an open blocking event no longer reach PASS/FAIL, and RS `population_size` excludes them.
- CLI: `vcp quality scan | list [--all] | resolve EVENT_ID --by NAME --note TEXT`. Point-in-time: a run under `--data-snapshot-id` only sees events detected by that snapshot's `known_at`. Event ids are now deterministic (were random UUIDs). `DataQualityEvent` gained `trade_date`, `blocks_signal`, `dataset`, `status` and resolution fields (all defaulted).
- Safety rule: `vcp quality resolve` refuses `CORPORATE_ACTION_UNRESOLVED` events; a conflict clears only when its resolution is superseded (DATABASE_SCHEMA 17A). No command creates a `MANUAL_OVERRIDE` yet, so a standing conflict blocks until the feeds agree.

### Added (audit P1-1, provider instrument mapping)
- `provider_instruments` table and `DuckDBProviderInstrumentRepository` (validity-dated provider id to permanent `instrument_id`); `KiteProvider.get_provider_instruments()`; `data/ingestion/provider_mapping.py` syncs the dump through the shared `InstrumentResolver` (unresolved rows skipped, empty dump raises `ProviderError`).
- `vcp ingest market` syncs mappings before storing bars; Kite and Upstox candles carry `provider_instrument_id` into `raw_ohlcv` (internal-id fallback only for providers without a native id).
- Not done: Upstox mapping rows, backfill of older raw bars, live-Kite verification.

### Added (audit P0-3, survivorship: delisted securities)
- `NSEDelistedProvider` (`data/providers/nse_delisted.py`) reads NSE's official "List of Companies Delisted from NSE" workbook (link discovered from the NSE delisting page; `--delisted-file PATH` for a manual copy) and returns `SecurityRecord`s with `delisting_date`, `valid_to` and the delisting type. Standard-library XML parsing; no new dependency. Checked against the published file: 457 rows (2002-2026), 455 after collapsing two duplicated ISINs.
- `SecurityMasterIngestionWorker(delisting_provider=...)` ingests them into `security_master_history` (with `delisting_reason`, previously always NULL) next to the live listing. A delisted record is skipped and counted (`delisted_skipped`) if it could be confused with a live security: same instrument id held by a live security with a different or unknown ISIN, or the same (instrument, period) as a live row. `SecurityRecord` gained an optional `delisting_reason`.
- CLI: `vcp ingest security-master` now includes the delisted list by default. `--delisted-file PATH` uses a local copy; `--no-delisted` skips it (survivorship stays `BIASED`). A failed or unparseable download exits 1 with a hint, never an empty success.
- Effect: `survivorship_status` moves `BIASED` -> `PARTIAL`. Not `POINT_IN_TIME_COMPLETE`: NSE's list omits merger/amalgamation delistings (HDFC Ltd, Mindtree, the 2019-20 PSU-bank mergers), is thin before 2016, and has no listing date or series. Delisted names also still need price history (bhavcopy, unverified) before they can enter a universe. See DATA_SPECIFICATION §4A.

### Fixed (audit P0-1, point-in-time reproducibility)
- New data-snapshot boundary. `data_snapshots` table (`domain/snapshot.py`, `DuckDBSnapshotRepository`): a deterministic id per `known_at`, created idempotently. `AdjustedPriceBuilder(snapshot=...)` reads raw bars and corporate-action adjustments *as known at* `known_at` and stores the rows under that snapshot; without a snapshot it builds `LIVE` (unfrozen) as before. Why: derived data read "latest/current" rows, so a later price correction or corporate action silently changed earlier results.
- Schema: `daily_prices_adjusted.computed_from_snapshot_id` is now `NOT NULL DEFAULT 'LIVE'` and part of the primary key; `daily_prices_adjusted_current` picks one version per (instrument, snapshot). `data_snapshot_id` added to `technical_features_daily`, `weekly_prices`, `relative_strength_snapshots`, `weekly_context`, `trend_template_conditions` (all in the key) and `trend_template_results` (column). `relative_strength_snapshots` key now also includes `universe_snapshot_id` (a re-run over another universe no longer overwrites). `store.migrate()` rebuilds legacy tables and keeps their rows under `LIVE`.
- `DailyFeatureEngine`, `WeeklyAggregationEngine`, `RelativeStrengthEngine`, `DuckDBFeatureRepository` and `DuckDBTrendRepository` take `data_snapshot_id` (default `LIVE`). `DuckDBFeatureRepository.load_adjusted_closes` now reads the same source as the engines (one version per instrument within the snapshot), resolving the previous selection conflict. `load_relative_strength(..., universe_snapshot_id=None)` defaults to the newest universe snapshot.
- CLI: `vcp ingest adjusted-prices --known-at ISO_DATETIME` freezes a snapshot and builds under it; `vcp compute features|rs|trend-template --data-snapshot-id ID` (default `LIVE`, unknown ids are rejected). Trend scan ids embed a non-LIVE snapshot id. Behavior change: frozen data is never fed to a LIVE run, so compute commands on a DB built only with `--known-at` need `--data-snapshot-id`.
- Not done (see audit P1-5): per-dataset cutoffs, `snapshot_manifest` content hashes, `scan_runs`.

### Changed
- Universe defaults reconciled with `PROJECT_DESIGN.md` §14 (audit P1-4): `eligible_series` `[EQ, BE]` → `[EQ]`; `min_close_price` 10 → 20; `min_daily_turnover_inr` (20d average) 5,000,000 → 10,000,000; new `min_avg_traded_value_50d_inr` = 10,000,000 gate using a 50-day average. Universe `method_version` `1.0` → `1.1`. Why: config silently widened the research population versus the governing design. Snapshots built under `1.0` are not rewritten; rebuild to get the new population. SME series (SM/ST) are excluded by the EQ-only whitelist; ETFs are not identifiable from series alone and remain open.

### Fixed (audit P0-4, data completeness)
- Providers no longer turn failures into empty results: `NSECorporateActionProvider`, `NSESecurityMasterProvider`, `NSESurveillanceProvider` (ASM and T2T) and `UpstoxCorporateActionProvider` raise `ProviderError` on HTTP errors, network errors and unparseable/empty feeds; `KiteProvider` raises for an unmapped symbol. Why: an empty list read as "no corporate actions / no securities / no active flags", which left splits unadjusted and closed every open surveillance flag. Upstox 404 (no record for an ISIN) is still "no actions"; other per-ISIN failures are collected and raised together after the loop. `vcp ingest corporate-actions` and `vcp ingest security-master` print the error and exit 1.
- New daily-bar completeness check (`data/quality/completeness.py`): market sessions are observed from the stored cross-section (no holiday list), and an instrument missing a session between its first bar and the end of the window is INCOMPLETE. `IngestionWorker(completeness=...)` re-fetches interior holes once, records a `daily_ohlcv_gapfill` ingestion run, downgrades a still-incomplete run to PARTIAL and raises `MISSING_CANDLES` events (`worker.quality_events`). `vcp ingest market` runs a final verification pass after the loop and reports incomplete instruments and low-breadth dates. Config: `data.completeness.min_breadth` (0.5) and `min_active_instruments` (5). Events are not persisted yet (audit P0-2).
- `IngestionWorker` marks a run PARTIAL when a range longer than 7 days returns zero bars (backstop when completeness cannot be judged).

### Fixed (audit Phase 0, logging never wired)
- The `vcp` CLI now configures structured logging on every command (it never did; `configure_logging` was only called from tests, so all module loggers were silent below WARNING and unformatted). Precedence: CLI flags > `config/logging.yaml` > defaults. New global options (give before the command): `--log-level`, `--log-format {text,json}`, `--log-file PATH`. `logging.yaml` `log_file` was parsed but never used; it now also appends records to that file. Logs go to **stderr** so command output on stdout stays clean (`configure_logging` previously defaulted to stdout; nothing at runtime used it). A missing or invalid `logging.yaml`, or an unwritable log file, prints a warning and falls back to defaults instead of blocking the command. New `load_logging_config()` reads only `logging.yaml`, independent of the rest of the config. Why: ingestion, completeness and provider failures were logged but never visible.

### Removed
- `domain.market.CorporateActionRecord` (unused legacy duplicate of `domain.corporate_actions.CorporateAction`); the `PROJECT_DESIGN.md` provider sketch now names `CorporateAction`.

### Fixed
- `DailyFeatureEngine` (`features-1.0.0` → `features-1.1.0`): all windowed features (SMA 20/50/150/200, high/low 20/50/252, volume averages and ratios, ATR, rolling volatility) are now NULL until their window is full, per AGENTS.md rule 4 ("missing is not zero"). The first bar's `daily_return` and true range are NULL instead of 0 / `high - low`. Rows computed under `features-1.0.0` are not overwritten; recompute to get corrected values. Old vs new: partial-window values → NULL. Why: partial windows looked like real values to Phase 6/7 consumers.

### Documented
- `README.md` rewritten (audit L): setup, credentials the code actually reads, the pipeline order with per-step dependencies, logging flags, known limitations and research restrictions. `AI_AGENT_RULES.md` added. Known gap recorded in the README: no command populates the `instruments` table, so `vcp ingest market` / `corporate-actions` cannot run from an empty database.
- `TREND_TEMPLATE_SPECIFICATION.md` §4: prior lookback equals `sma_weeks`; missing as-of bar gives `INSUFFICIENT_DATA` (weekly stage) vs `DATA_NOT_READY` (Trend Template).
- `DATABASE_SCHEMA.md` §29 and §30: NULL-until-full rule for daily features; `weekly_context` table entry.

## [0.1.0] - 2026-09-28

### Added
- **Phase 0 — Architecture baseline delivery**:
  - Repository skeleton conforming to `PROJECT_DESIGN.md` §49.
  - Core domain models in `vcp_scanner.domain` (`market`, `trend`, `vcp`, `scoring`, `fundamentals`, `enums`, `errors`).
  - Strict StrEnum enumerations for classifications, lifecycle statuses, confirmation states, and error categories.
  - Provider protocols in `vcp_scanner.data.providers.base` (`MarketDataProvider`, `SecurityMasterProvider`, `CorporateActionProvider`, `SurveillanceProvider`, `FundamentalProvider`).
  - Repository protocols in `vcp_scanner.data.repositories.base` (`MarketDataRepository`, `UniverseRepository`, `ScanRepository`, `FundamentalRepository`).
  - Pattern and alert protocols in `vcp_scanner.patterns.base` (`PatternDetector`, `PatternConfirmer`) and `vcp_scanner.alerts.base` (`AlertChannel`).
  - Structured logging with JSON and text formatters in `vcp_scanner.infrastructure.logging`, supporting contextual field enrichment (`scan_id`, `run_id`, `symbol`, `component`, `duration_ms`).
  - Centralized version manifest in `vcp_scanner.versioning` covering package, strategy, algorithm, and data schema versions.
  - Configuration models and strict Pydantic validation in `vcp_scanner.config.models` implementing rules from `TREND_TEMPLATE_SPECIFICATION.md` and `SCORING_SPECIFICATION.md`.
  - Canonical deterministic configuration SHA-256 hash calculator and YAML loader in `vcp_scanner.config.loader`.
  - Production-ready YAML configurations under `config/`: `strategy.yaml`, `scoring.yaml`, `universe.yaml`, `data.yaml`, `monitoring.yaml`, `logging.yaml`.
  - Environment variables template in `.env.example`.
  - Command-line interface `vcp` in `vcp_scanner.cli` supporting `vcp version`, `vcp config validate`, and `vcp config hash`.
  - Complete Phase 0 test suite under `tests/unit/` covering domain models, config validation, protocol compliance, structured logging, CLI commands, and architectural boundary static analysis.
