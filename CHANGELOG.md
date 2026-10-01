# Changelog

All notable changes to the Institutional-Grade NSE VCP Scanner will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documented (audit P1-10 second part: small corporate actions)
- `DATA_SPECIFICATION.md` §18A now records that the gap safety net cannot catch a missed bonus or split that moves the price by less than 30 % (for example 1:4), and why the threshold stays at 30 %: on the full NSE history a lower one would flag about 850 earnings, news and circuit drops. A second corporate-action source (Upstox, or a BSE feed) is the remedy.

### Fixed (same-day ratio action and demerger/rights; superseded readings)
- A rights issue or demerger on the same ex-date as a split or bonus is now derived from the prior close on the post-split/bonus scale. Before, the split/bonus was counted twice: AHLEAST's 2022-10-06 demerger + 1:2 bonus got factor 0.352 instead of 0.528, leaving a +50 % jump in its adjusted prices. The gap detector uses the same order.
- An older, ratio-less SPLIT/BONUS/RIGHTS reading of a record that the parser now stores as `UNMODELLED` (same instrument and ex-date) no longer raises a second, blocking event (QUINT 2026-08-25, BRITANNIA 2021-05-25); the record is reported once, as the unmodelled warning.

### Fixed (delisting of a company that listed again under a later ISIN)
- `vcp ingest security-master` no longer skips a delisting record whose instrument is held by the same issuer's equity under a later ISIN (DHFL, delisted 2021-09-29, INE202B01012 → PIRAMALFIN, INE202B01038). Like a relisting under the same ISIN, the delisting is kept as an earlier period of that instrument. A different issuer reusing the symbol is still refused.

### Added (audit P1-10: unmodelled corporate actions become warnings)
- NSE records for price-affecting actions the scanner does not model (capital reduction, merger or amalgamation, scheme of arrangement, a bonus of debentures, rights in CCPS/warrants/NCDs) are no longer dropped: they are stored as `UNMODELLED` actions and raise a non-blocking `CORPORATE_ACTION_UNMODELLED` warning on their ex-date (`vcp quality list`). The warning clears when a manual override covers that date, or a person resolves it. Prices are still not adjusted for them; the gap detector still blocks a split-like jump.
- Rights in non-equity securities (QUINT 2026-08-25, "Rights - 7 CCPS And 7 Warrants:40") are no longer read as an equity rights issue without a ratio, which blocked the stock.

### Changed (demerger factor edge rules; owner decision 2026-10-01)
- A demerger whose ex-date open is at or up to 1 % above the prior close now gets factor 1.0 instead of a blocking `factor_unknown` (DALMIASUG 2025-10-31). The plausibility floor is 0.02, not 0.05, so KESORAMIND's 2025-03-10 demerger (factor 0.04997) is applied.
- `config/manual_corporate_actions.yaml` accepts `DEMERGER` entries with `price_factor`, which wins over the ex-date open (for demergers with no usable ex-date bar).

### Added (audit P1-8d: verify a scan on demand)
- `vcp verify scan RUN_ID` rebuilds a recorded scan run on a copy of the database: it freezes the data at the run's cutoff, rebuilds the universe as known then (same deterministic id), RS and the Trend Template over that universe, and compares the results hash (exit 0 match, 2 mismatch with the differing instruments, 1 error). The copy is deleted unless `--keep`. A code commit different from the run's is reported. Without an id it lists recent runs.

### Added (audit P1-8c: scan-run records; owner decision 2026-10-01)
- Tables `scan_runs` and `scan_run_results`. Every `vcp compute trend-template` (so every evening scan of `vcp run daily`) inserts one immutable row: as-of date, data cutoff, data and universe snapshot ids, the scan's config hash and per-section hashes, the git commit and whether tracked files had uncommitted changes, algorithm versions, survivorship label, counts, a content hash of the verdicts, start and end times. The verdicts themselves are copied to `scan_run_results`, so a rerun cannot erase what an earlier run reported.
- The command prints the scan run id, the code commit and the results hash.

### Changed (audit P1-8b: deterministic universe ids, explicit universe for scans)
- Universe snapshot ids are derived from the as-of date, the knowledge cutoff, the config hash and the method version (`uv_YYYYMMDD_<10 hex>`), not random. Rebuilding with the same inputs replaces the stored snapshot instead of adding another.
- `vcp compute trend-template --universe-snapshot-id` evaluates a named snapshot (default: the latest for the date, as before) and prints which one it used; the Trend Template reads RS ranked over that same universe snapshot.

### Changed (audit P1-8a: per-section config hashes; owner decision 2026-10-01)
- Trend Template scan ids now hash only the sections that can change a result: `strategy`, `universe` and the data-quality gate (`data.quality`). Changing the log level, monitoring settings or storage paths no longer starts a new scan id. Existing scan ids change once with this release.
- A gated universe snapshot's `config_hash` now includes the gate settings, which change eligibility.
- `vcp config validate` prints the scan config hash and each section's hash.

### Added (audit P1-2c: trading absences; owner decision 2026-10-01)
- New data-quality flag `TRADING_ABSENCE`: a stock that misses 20 or more NSE sessions (`data.quality.absence_min_missed_sessions`; sessions = settled bhavcopy days) is blocked from its return until it has a full block lifetime (253 bars) of new history, so no feature window mixes prices from both sides of the absence. Detected by `vcp quality scan` and `vcp ingest corporate-actions`.
- The price jump on such a return is no longer a blocking unexplained gap; it stays recorded as a warning (`after_trading_absence_sessions`).

### Changed (audit P1-2b: data-quality blocks end; owner decision 2026-10-01)
- A dated blocking event (unexplained gap, corporate action without a usable factor, provider conflict) now stops applying once the stock has `data.quality.block_lifetime_bars` (253, new in `config/data.yaml`) of its own bars from the event date to the as-of date; the bad bar has then left every lookback. Before, a block lasted forever (BRITANNIA, PATINTLOG, SIGMAADV, TFL, UEL and KESORAMIND were blocked by events 385 to 1,329 bars old). Earlier as-of dates still see the block. Config loading refuses a lifetime shorter than the longest lookback (`ScannerConfig.longest_lookback_bars`).

### Fixed (audit P1-2a: debenture bonus)
- NSE's "Scheme Of Arangement- Bonus - 1 Debenture For 1 Equity Share Held" (BRITANNIA 2021) is no longer read as a share bonus without a ratio, which blocked BRITANNIA; it is reported as an unhandled record. The raw row already stored stays (raw data is immutable); the block it causes expires under P1-2b.

### Added (audit 2.7d: manual corporate-action overrides; owner decision 2026-10-01)
- `config/manual_corporate_actions.yaml`: hand-entered, evidenced corporate actions. `vcp ingest corporate-actions` (and so `vcp run daily`) loads all of them on every run as source `MANUAL`; reconciliation marks their (type, ex-date) `MANUAL_OVERRIDE` with the manual values. An invalid file aborts the run.
- First entries, verified against NSE record-date notices and bhavcopy prices: DTIL bonus 1:2 (2021-08-05), GICL split Rs 10 → Rs 5 and bonus 1:1 (2025-10-15), JSLL split Rs 10 → Rs 2 (2025-06-12).

### Changed (audit 2.7c: gap safety net window; owner decision 2026-10-01)
- An action now explains a gap when its ex-date falls after the previous bar and up to the gap bar, not only on the gap bar's own date. Illiquid stocks often do not trade on the ex-date, and their correctly adjusted splits, bonuses and rights still blocked them (DOLPHIN, UEL, TIL …).
- The action must also account for the gap's size: the previous close times the combined factor (split/bonus ratio, rights TERP) must land within `gap_pct` of the open. A long absence is not explained by an unrelated action inside it, and an ex-date action whose ratio does not fit the jump is now flagged. Such events record `residual_gap_pct_after_actions`.

### Fixed (audit 2.7b: share consolidations)
- An NSE "Consolidation Of Equity Shares From Re 1 … To Rs 10 …" record is now a reverse split, SPLIT (1, 10), instead of an unhandled record (VERTOZ 2025-06-25). Capital reductions still carry no ratio and stay reported as unhandled.

### Fixed (audit 2.7a: SME corporate actions)
- NSE corporate actions are fetched from both `index=equities` and `index=sme`. SME bonuses and splits (e.g. KSOLVES' 2021 bonuses) were missing, which left 34 blocking unexplained gaps on 31 active SME stocks. A failure of either feed aborts the run. The SME feed's internal number is no longer stored as an ISIN.

### Added (daily run)
- `vcp run daily` runs every evening and catches up after skipped evenings. Steps:
  1. security master with the ASM/GSM/T2T lists;
  2. NSE bhavcopy from the day after the last settled file;
  3. corporate actions for the last 60 days;
  4. adjusted prices and features;
  5. universe, RS and Trend Template for every session not scanned yet.
  It appends one summary line per run to `<db folder>/logs/daily_runs.log` and warns when today's ASM/GSM lists were not collected.
- Table `surveillance_collections` records each day a surveillance list was collected in full, including empty lists; it is seeded once from existing flags. A universe snapshot is POINT_IN_TIME_COMPLETE only if ASM and GSM were collected on its as-of date ("ASM list not collected on …").

### Changed (audit P0-4: point-in-time universe)
- The universe takes each date's series and trade-to-trade status from the NSE bhavcopy (`daily_series`) instead of today's EQUITY_L. Delisted, merged and renamed names take part on dates when they traded.
- The survivorship label is derived from the data: price coverage of a 380-day window, plus ASM and GSM collection dates. The reasons are stored in `universe_snapshots.survivorship_detail` and printed by `vcp ingest universe`. The `survivorship_coverage_verified` config field is removed. `UNIVERSE_METHOD_VERSION` is now 2.0.
- The NSE GSM list (`/api/reportGSM`) is collected with ASM and T2T on every security-master run.

### Changed (owner decision 2026-10-01: Upstox credentials rejected)
- An Upstox HTTP 401/403 (`ProviderAuthError`, e.g. an expired token) no longer aborts `vcp ingest corporate-actions`. The run continues NSE-only, nothing is escalated because of Upstox's silence, and the CLI prints a warning to refresh `UPSTOX_ACCESS_TOKEN`. Other Upstox failures still abort the run.

### Added (audit step 2.5: Kite as provisional bar and cross-check)
- `vcp ingest market --today` stores today's Kite bar as `PROVISIONAL` (`IngestionWorker.ingest_instrument(provisional=True)`, `save_daily(data_status=...)`).
- `--allow-provisional` on `vcp ingest adjusted-prices` and `vcp ingest universe`.
- `vcp verify kite-crosscheck` (`data/quality/kite_crosscheck.py`) explains every step in the Kite/ours close ratio by corporate action.

### Changed (audit step 2.5)
- **Breaking:** `vcp ingest market` needs a mode: `--today` (provisional bar) or `--kite-history` (Kite history, comparison only). Daily history comes from `vcp ingest bhavcopy`.
- `load_daily`, `load_daily_as_of` and the universe candidates leave PROVISIONAL bars out unless `include_provisional=True`.
- `save_adjusted_daily(prune_missing=True)` (used by full rebuilds) deletes rows of the same instrument, version and snapshot that the build no longer produces.
- `vcp verify kite-adjustment` only samples splits and bonuses (rights and demergers now also explain gaps, but have no ratio factor to test).
- The NSE rights parser also reads `Rights Issue a:b@ Premium ...` and `Prm Rs ...` (live variants).

### Added (audit step 2.4: rights issues and demergers)
- NSE corporate actions:
  - `Demerger` records now parse as `DEMERGER` (they were dropped as unhandled);
  - rights records carry their ratio `(a, b)` and issue price (face value + premium) in `cash_amount`.
- `derived_factor` / `factor_unknown` / `ExDatePrices` (domain) and `adjustment.engine.ex_date_prices`:
  - RIGHTS factor = TERP / prior close; the volume factor is its inverse;
  - DEMERGER factor = ex-date open (NSE special pre-open) / prior close; volume factor 1.0;
  - factors are derived only from raw bhavcopy bars.
  - `CALCULATION_VERSION` 1.2.
- `CorporateActionIngestionWorker(market=...)` derives these factors. The CLI passes the market repository.
- An underivable factor on raw prices is a blocking `CORPORATE_ACTION_UNRESOLVED` event (cause `factor_unknown`), raised by both the corporate-action worker and the quality scan.
- `tests/fixtures/corporate_actions/golden_derived_actions.json`: BHARTIARTL rights 2021, RELIANCE demerger 2023, ITC demerger 2025 (real NSE records and bhavcopy prices).

### Changed (audit step 2.4)
- The gap detector treats an adjustable rights issue or demerger as explaining its ex-date gap.
- Reconciliation uses the most recently seen record of a source for its values (a parser upgrade re-reads the same NSE record with more detail) and the earliest one for the grace period.

### Added (audit step 2.3: bhavcopy ingestion)
- `vcp ingest bhavcopy --start D [--end D] [--refresh] [--cache-dir]` (`BhavcopyIngestionWorker`) ingests NSE bhavcopy files day by day. It records the manifest and resolves identity. It writes final bars with `DuckDBMarketDataRepository.save_final_daily`, which is set-based and supersedes other providers' bars for the same session. It stops at the first PENDING or ERROR day, and re-runs are idempotent.
- `domain.market.FINAL_PRICE_SOURCE = "NSE_BHAVCOPY"`.

### Changed (audit step 2.3)
- `save_daily` no longer lets another provider (Kite) supersede a bhavcopy bar.
- `save_adjusted_daily` writes set-based through Arrow instead of `executemany`. Adjusting 5 stocks × 1,424 bars now takes 0.7 s instead of about 40 s, with identical values (D3).
- `classify_missing`: an earlier weekend day is NO_SESSION at once, so a Monday run is not held up by Saturday/Sunday 404s. Today's 404 is always PENDING.

### Added (audit step 2.2: identity for bhavcopy history)
- `data/ingestion/bhavcopy_identity.py` maps each day's bhavcopy rows to permanent instruments. It matches by known ISIN first, then by same symbol and same issuer when the ISIN changes after a face-value split. Otherwise it creates a new, inactive instrument, disambiguated as `NSE_EQ|SYMBOL#ISIN` when NSE reused the symbol. Re-runs replay the stored mapping.
- Tables `instrument_identifier_history` (symbol/ISIN periods) and `daily_series` (per-day instrument and series), plus `DuckDBIdentityRepository`.
- `identity.same_issuer_equity`; `mint_instrument_id(..., disambiguator=)`.
- Quality scan: `SYMBOL_MAPPING_UNCERTAIN` warning for ISIN changes that no split explains.
- `DuckDBStore.insert_rows`: bulk insert through Arrow. Writing one day (~3,400 rows) went from 24 s with `executemany` to 0.1 s.

### Changed (audit step 2.2)
- The bhavcopy parser skips non-company-equity ISINs (ETFs `INF...` and partly-paid `IN9...`, which trade in series EQ/BZ). On 2026-09-29 that is 350 ETF rows.

### Added (audit step 2.1: NSE bhavcopy as the raw price source)
- `data/providers/nse_bhavcopy.py`:
  - `NseBhavcopyProvider` downloads NSE capital-market bhavcopy files in both layouts (legacy up to 2024-07-05, UDiFF from 2024-07-08). Each zip is cached unchanged with its sha256, and requests are rate-limited (1 s).
  - `parse_bhavcopy` normalises both layouts, keeps equity series EQ/BE/BZ/SM/ST, and returns invalid or duplicate rows as rejects with reasons.
  - `classify_missing` decides what a 404 means: NO_SESSION or PENDING.
  - `get_trading_holidays` reads NSE's current-year holiday list.
- `domain/bhavcopy.py`: bhavcopy row, file status and manifest types.
- Table `bhavcopy_files` (manifest, one row per date and file hash) and `DuckDBBhavcopyRepository`.
- DATA_SPECIFICATION §21.2.
- Nothing is wired into ingestion yet (step 2.3).

### Fixed (Upstox corporate actions, found on live data)
- Upstox serves only about the last 12 months of corporate actions per ISIN. Its silence about an older NSE split/bonus was treated as evidence, so with a token configured every historical split/bonus would have become `PROVIDER_CONFLICT` after the 3-day grace period (blocked, factor withdrawn). `UpstoxCorporateActionProvider.coverage_start` records the earliest ex-date Upstox returned per instrument; the CA worker only counts Upstox's silence from that date (no records = no evidence).
- Upstox split ratios are share ratios (`"1:5"` for KOTAKBANK's face value 5 -> 1); they are now converted to the engine/NSE convention `(old face value, new face value)`. Before, every split reported by both sources was a ratio conflict. Bonus ratios are unchanged.

### Fixed (found by the first real-data run, audit 2026-09-30 Fix 5b)
- Same-day split and bonus (BAJFINANCE 2025-06-16: 1:2 split + 4:1 bonus) lost a factor: `save_adjustment` closes any row with the same (instrument, effective_date), so the bonus closed the split and the stored factor was 0.5 instead of 0.1. `AdjustmentEngine.compute_factors` now combines all actions of one ex-date into one factor (`resolution_id` = ids joined by `+`). The corporate-action worker also rebuilds an instrument's factors whenever they differ from what the engine computes now, so existing databases are repaired on the next `vcp ingest corporate-actions`.
- `--instrument` accepted only full ids in `compute features`, `compute trend-template`, `quality scan`, `quality list`, `ingest adjusted-prices` and the post-ingest quality scan of `ingest corporate-actions`; a symbol such as `RELIANCE` silently selected nothing (the quality scan checked 0 instruments). All now accept an id or a symbol, any case.
- Placeholder credentials copied from `.env.example` (`your_..._here`) or blank values now count as unset (`cli_pipeline.env_secret`). The placeholder Upstox token made `vcp ingest corporate-actions` fail with HTTP 401 instead of running NSE-only.

### Added (audit Fix 5b)
- `scripts/capture_golden_ca.py` and `tests/fixtures/corporate_actions/golden_actions.json`: 7 real splits/bonuses (IRCTC, TATASTEEL, NESTLEIND x2, RELIANCE, BAJFINANCE split+bonus, HDFCBANK), NSE bhavcopy raw prices vs Kite adjusted prices. `tests/regression/test_golden_corporate_actions.py` (49 offline checks).

### Fixed (`vcp auth kite` ignored .env)
- `vcp auth kite` now loads `--env-file` (default `.env`) before reading `KITE_API_KEY` / `KITE_API_SECRET`, like every other command; `--api-key`/`--api-secret` and variables already in the environment still take precedence. Before, it read only the process environment and failed with "Kite API Key and Secret are required" although both were in `.env`.
- `KiteAuthenticator` creates `.env` owner-only (0600) and tightens an existing group/world-readable one before writing the access token (audit P3: `.env` permissions).

### Changed (audit 2026-09-30 P1-7, VCP configuration contract)
- `vcp:` and `classification:` now follow VCP_SPECIFICATION §60 verbatim: nested `contractions`, `swing`, `volatility`, `volume`, `pivot`, `confirmation` blocks and per-tier `max_contractions` / `require_*` flags (`VCPThresholdsConfig`, `ClassificationConfig`, new `VCP*Config` sub-models). The flat keys `min_contractions`, `max_contractions`, `require_volume_dryup`, `require_tight_pivot` under `vcp:` are gone (`extra="forbid"` rejects them). New validation: volume periods ordered, ratios in (0, 1], tiers inside `vcp.contractions`, stricter tiers never looser. A test parses the §60 YAML from the spec and requires it to validate. No detector code yet (Phase 6).
- Behavior change: the configuration hash changes, so new `trend-template` scan ids differ from earlier ones; earlier rows are kept.

### Added (audit 2026-09-30 P1-6, RS tests)
- `tests/unit/test_rs_ranking.py`: known-answer ranking, weights/windows, ties, NULL-history and zero-close exclusion, staleness boundary, 1–98 rank range and monotonicity, and "RS unchanged when a stock leaves the Trend Template stage but not the population". TREND_TEMPLATE_SPECIFICATION §3 documents that `rs-1.0.0` ranks run 1–98.

### Changed (audit 2026-09-30 P1-3 / P1-4, architecture boundary and layout)
- RS ranking is now a pure function (`features.relative_strength.compute_rs_rows`) behind the new `RelativeStrengthRepository` Protocol; `DuckDBRelativeStrengthRepository` does only data access. `RelativeStrengthEngine(repository, calculation_version=None, config=None, quality_gate=None)` replaces `RelativeStrengthEngine(store, ..., data_snapshot_id=...)` (the data snapshot now binds the repository).
- Universe rules are a pure function (`data.universe.builder.evaluate_eligibility`) over `domain.universe.UniverseCandidate`; point-in-time SQL moved to `DuckDBUniverseRepository.load_universe_candidates` / `count_known_delistings` (new `UniverseInputRepository` Protocol). `UniverseBuilder(repository, config, ...)` replaces `UniverseBuilder(store, config, ...)`.
- SQL feature builders moved: `features.daily_features` -> `data.features.daily_features`, `features.weekly_aggregation` -> `data.features.weekly_aggregation`.
- Removed the empty duplicate packages `indicators/`, `trend/`, `universe/`, `data/normalization/`. PROJECT_DESIGN §6/§49 and AGENTS.md rule 3 describe the real layout.
- `test_architecture_rules.py` now scans `domain/`, `features/`, `data/universe/` (must contain code) plus future strategy packages, and forbids storage/ingestion/concrete-adapter imports as well as SDKs. The old version scanned only empty packages.
- No behavior change: new `tests/regression/test_rs_universe_golden.py` was recorded from the SQL implementations before the refactor and passes unchanged after it (RS statuses, returns, ranks, ties, staleness, NaN, gate exclusion; every universe exclusion reason; Kite price undo; survivorship label).

### Fixed (audit 2026-09-30 P1-1, pipeline could not start from an empty database)
- `vcp ingest security-master` now seeds `instruments` from NSE's current listing (`SecurityMasterIngestionWorker._sync_instruments`): upsert with ISIN-first canonical ids, so a symbol rename updates the existing instrument; instruments missing from a non-empty listing become `is_active = FALSE` (history kept); delisted-list records never create instruments. New stats `instruments_upserted`, `instruments_deactivated`. Why: nothing populated `instruments`, so `vcp ingest market` / `corporate-actions` stopped with "no instruments found".
- New `tests/integration/test_pipeline_e2e.py`: all eight pipeline commands through the real CLI from an empty database with synthetic providers.

### Fixed (audit 2026-09-30 P0-1, Kite history adjusted twice)
- Kite historical candles are adjusted by Zerodha for splits/bonuses (and rights, spin-offs, extraordinary dividends) as of the fetch time. The engine treated them as raw and applied NSE factors on top, so any history downloaded after a split was adjusted twice while history downloaded before it was right.
- `KiteProvider.get_capabilities().adjusted_prices` is now `True`; `domain.market.PROVIDER_ADJUSTED_SOURCES = {"KITE"}` (a test keeps the two in step).
- Adjustment is fetch-time aware (`CALCULATION_VERSION` 1.0 -> 1.1): a provider-adjusted bar gets only the factors of actions whose ex-date is after both its trade date and its IST fetch date (`daily_prices.known_from`, now carried on loaded candles as `ingested_at`). `apply_factors` and `build_adjusted_rows` share one factor rule.
- Universe minimum price uses the price that actually traded: the latest Kite close is divided by the factors Kite had applied. Traded value is unchanged (split scaling cancels).
- New read-only command `vcp verify kite-adjustment` classifies Kite's history around known splits/bonuses as ADJUSTED / RAW / INCONCLUSIVE (exit 0 / 2 / 1).
- Behavior change: rebuild adjusted prices (`vcp ingest adjusted-prices`) to get version `adj-1.1-*`; older `adj-1.0-*` rows stay for reproducibility. Docs: DATA_SPECIFICATION §21.1, PROJECT_DESIGN §10/§14A, DATABASE_SCHEMA §14, AGENTS.md rule 2, README.

### Fixed (audit 2026-09-30 P1-9, non-finite and non-positive values)
- `validate_ohlc` rejects NaN/inf prices or volume and any price <= 0. Rejected bars stay in `raw_ohlcv` for audit but never reach `daily_prices`. Why: NaN compared False with every ordering rule, so NaN bars looked valid.
- Trend Template: a non-finite input is stored as NULL and the result is `INSUFFICIENT_DATA`, not `FAIL`; the `close` extreme basis ignores a window containing a non-finite close. Weekly Stage returns `INSUFFICIENT_DATA` for a non-finite weekly close. RS stores non-finite returns as NULL, so the instrument is `INSUFFICIENT_DATA` and leaves the ranking population instead of sorting NaN above every value.

### Fixed (audit 2026-09-30 P0-2, reconciliation policy)
- Cash amounts are compared only when both sources report one (tolerance half a paisa). NSE's feed carries no parsed amount, so every dividend reported by both NSE and Upstox used to become `PROVIDER_CONFLICT`.
- Only SPLIT/BONUS conflicts block signals. DIVIDEND/RIGHTS conflicts are still recorded (`CORPORATE_ACTION_UNRESOLVED`, severity WARNING) but never gate the universe, RS or Trend Template.
- An NSE-only action past `secondary_grace_days` becomes `PROVIDER_CONFLICT` only if it is a split/bonus and the secondary source was actually queried for that instrument over a window containing the ex-date (`UpstoxCorporateActionProvider.queried_instrument_ids`, `ReconciliationEngine.reconcile(secondary_window=...)`). Without an Upstox token, or for an instrument without an ISIN, it stays `SINGLE_SOURCE` and keeps its factor, as the README always said. `conflict_fields = secondary_source_missing` marks the escalated case.
- Behavior change: existing conflicts are re-evaluated on the next `vcp ingest corporate-actions`; dividend-driven and unqueried NSE-only blocks clear, and withheld split factors come back. Why: the old rules systematically removed dividend payers from the universe and RS population.

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
