# AUDIT_FIX_LOG.md

Record of fixes made in response to the independent Phase 0–5 audit of 2026-09-30
(`phase0-5-audit-2026-09-30.md`). Branch: `audit-fixes`, one commit per fix. Each fix is approved
by the project owner before work starts.

## Planned order

| # | Audit ID | Topic | Status |
|---|---|---|---|
| 1 | P0-3 | Gap safety net ignores non-applied actions; unknown split/bonus ratios block | Done |
| 2 | P0-2 | Reconciliation policy: dividends/rights and primary-only actions | Done |
| 3 | P1-9 | Reject NaN / zero / negative OHLC; NaN inputs are INSUFFICIENT_DATA | Done |
| 4 | P0-1 | Kite candles are provider-adjusted: stop double adjustment | Done; verified on live Kite data 2026-09-30 |
| 5 | P1-1 | Seed `instruments`; first real end-to-end run; real golden fixtures | Done (5a + 5b) |
| 6 | P1-3 / P1-4 | Architecture boundary test covers real packages; package layout | Done |
| 7 | P1-7 / P1-6 | VCP config shape per VCP_SPEC §60; real RS tests | Done |

---

## Fix 1 — P0-3: gap safety net and unknown split/bonus ratios

**Problem.** `GapDetector` treated a raw gap as explained if *any* corporate-action resolution
shared its ex-date, whatever its type or status. A split whose ratio could not be parsed from NSE
text (resolution with NULL ratio) got factor 1.0 from `AdjustmentEngine`, so its price jump stayed
in the adjusted series, and the same resolution silenced the gap detector. A dividend or a
`PROVIDER_CONFLICT` on the same date had the same effect. Result: an unadjusted split looked like a
genuine −50 %/−90 % crash to SMAs, 52-week lows, RS and (later) VCP depths, with no event raised.

**Change.**
- `domain/corporate_actions.py`: `PRICE_SCALING_ACTIONS`, `has_usable_ratio`, `ratio_unknown`,
  `explains_price_gap` (single definition shared by the detector and the event builder).
- `data/reconciliation/gap_detector.py`: only `explains_price_gap` resolutions explain a gap.
- `data/quality/events.py`: `corporate_action_events` adds a blocking
  `CORPORATE_ACTION_UNRESOLVED` event (`cause = ratio_unknown`) per adjustable split/bonus without
  a usable ratio; always blocks (not governed by `conflict_blocks_signals`); not hand-resolvable
  (existing `_SUPERSEDE_ONLY` rule); clears when a resolution with a ratio supersedes it.
- `DATA_SPECIFICATION.md` §18A: rule wording updated ("applied" split/bonus; unknown-ratio event).
- `CHANGELOG.md`: entry under Unreleased.

**Tests.**
- New: `test_quality_events.py` — non-adjusting actions (no/zero/NaN ratio, conflict, dividend,
  rights) do not explain a gap; applied split/bonus under CONFIRMED/SINGLE_SOURCE/MANUAL_OVERRIDE
  does; unknown-ratio event blocks even with `conflict_blocks_signals=False`, has its own id,
  cannot be hand-resolved; scanner end-to-end: unparsed split → gap + ratio events block, both
  clear once a ratio arrives. `test_gap_detector.py` — dividend does not explain a split-like gap.
- Updated fixtures (not weakened): three tests used ratio-less CONFIRMED/SINGLE_SOURCE splits as
  "explanations" or bystanders; they now carry a ratio, since a ratio-less split is exactly the
  case this fix makes visible.

**Behavior change.** Instruments with an unparsed split/bonus ratio, or a split-like gap on a
dividend/conflict ex-date, now become `DATA_QUALITY_BLOCKED` and leave the universe/RS population.

**Not done here.** Per-record parse failures in the NSE/Upstox parsers are still only logged
(audit P1-10); the 30 % threshold still misses small bonuses (e.g. 1:4); split-like tolerance is
still absolute. Revisit with Fix 2 / Fix 5.

**Verification.** Targeted: 79 passed (quality events, gap detector, CA pipeline, quality gate).
Full suite: 466 passed, 0 failed (was 447). `ruff check` clean, `ruff format --check` clean,
`mypy --strict src` clean (93 files).

---

## Fix 2 — P0-2: reconciliation policy (dividends, rights, NSE-only actions)

**Owner decisions (2026-09-30).**
- A: only splits/bonuses block when sources disagree; dividend/rights conflicts are warnings.
- B: an NSE-only split/bonus past the grace period escalates to `PROVIDER_CONFLICT` only when
  Upstox is configured *and* was actually queried for that instrument over a window containing
  the ex-date. Otherwise it stays `SINGLE_SOURCE`. Dividends/rights never escalate.

**Problem.** (1) NSE's parser never sets `cash_amount`, Upstox always does, and the engine compared
them with `!=`, so every dividend reported by both sources became `PROVIDER_CONFLICT`.
(2) Every NSE-only action became `PROVIDER_CONFLICT` 3 days after first sighting, including when no
Upstox token was configured (contradicting the README). (3) Every conflict, of any type, blocked
signals from its ex-date indefinitely and could not be hand-resolved, and conflicts withdrew split
factors. Net effect: dividend payers were progressively removed from the universe and the RS
ranking population (selection bias), and NSE-only splits lost their adjustment.

**Change.**
- `data/reconciliation/engine.py`: `_cash_equivalent` (compare only when both present,
  `abs_tol=0.005`); `reconcile(..., secondary_window=None)`; `_check_grace_period` escalates only
  price-scaling actions whose ex-date lies in `secondary_window`; `conflict_fields =
  secondary_source_missing` for that case.
- `data/providers/upstox_ca.py`: `queried_instrument_ids` (HTTP 200 or 404 per ISIN; no-ISIN
  instruments are not "asked").
- `data/ingestion/ca_worker.py`: reads the secondary provider's coverage (none for providers that
  don't report it, e.g. the no-token stand-in), canonicalises ids, passes `(start, end)` per
  covered instrument.
- `data/quality/events.py`: conflict events block only for SPLIT/BONUS; DIVIDEND/RIGHTS conflicts
  are WARNING and non-blocking.
- Docs: README (credential table, known limitations), DATA_SPECIFICATION §18A table + policy note,
  DATABASE_SCHEMA §17A, CHANGELOG.

**Tests.**
- New: engine — NSE-only past grace without coverage / with coverage outside the ex-date stays
  SINGLE_SOURCE; dividend/rights never escalate; dividend with cash on one side only is CONFIRMED;
  cash tolerance both ways. Worker — no-coverage split keeps its factor; dividend in both sources
  doesn't block; dividend cash conflict is a non-blocking warning; NSE-only dividend never blocks.
  Provider — `queried_instrument_ids` records 200/404 instruments and excludes no-ISIN ones.
- Updated (policy change, intent kept): three tests asserting "NSE-only past grace → conflict"
  now declare that Upstox was queried for the instrument (the only case where that rule now
  applies).

**Behavior change.** On the next `vcp ingest corporate-actions`, resolutions are re-evaluated:
dividend-driven and unqueried NSE-only conflicts become CONFIRMED / SINGLE_SOURCE, their events
close (SYSTEM), and withheld split factors are restored. Rebuild adjusted prices afterwards.

**Not done here.** No manual-override command yet; blocks are still open-ended in time (audit
P1-2); NSE dividend amounts are still not parsed (not needed for correctness now).

**Verification.** Targeted: 106 passed. Full suite: 477 passed, 0 failed. `ruff check`,
`ruff format --check`, `mypy --strict src` clean.

---

## Fix 3 — P1-9: non-finite and non-positive values

**Problem.** `validate_ohlc` only checked orderings. NaN compares False with everything, so a
NaN bar passed and reached `daily_prices`; zero and negative prices passed too. Downstream, a NaN
close or SMA turned Trend Template conditions into a definite `False` → `FAIL` (violating "missing
is not zero"), and NaN in RS sorted above every value in DuckDB, distorting all percentiles.

**Change.**
- `data/schema.py` `validate_ohlc`: rejects non-finite price/volume and any price <= 0. (Raw
  audit copy in `raw_ohlcv` unchanged; the bar never reaches canonical data.)
- `features/trend_template.py`: `_finite()` maps non-finite inputs to None before evaluation
  (stored as NULL, status `INSUFFICIENT_DATA`); `close` extreme basis returns None if its window
  contains a non-finite close.
- `features/weekly_stage.py`: non-finite weekly close → `INSUFFICIENT_DATA` with NULL measurements.
- `features/relative_strength.py`: non-finite returns become NULL (`isfinite` guard) → instrument
  is `INSUFFICIENT_DATA` and excluded from the population.
- Docs: DATA_SPECIFICATION §23, CHANGELOG.

**Tests.** New `tests/unit/test_non_finite_values.py` (32 cases): bad bars rejected with reasons;
valid/flat/zero-volume bars still pass; NaN bar never lands in `daily_prices`; each of 7 TT inputs ×
{NaN, +inf, −inf} gives `INSUFFICIENT_DATA` with no non-finite value stored; weekly NaN close →
`INSUFFICIENT_DATA`; RS with one NaN as-of close: that instrument NULL/INSUFFICIENT_DATA, the other
four ranked over a population of 4. No existing test changed.

**Not done here.** Existing NaN rows already in a live `daily_prices` are not purged (bitemporal;
they are now neutralised downstream). A later `vcp ingest market --force` supersedes them.

**Verification.** Full suite: 509 passed, 0 failed. `ruff check`, `ruff format --check`,
`mypy --strict src` clean.

---

## Fix 4 — P0-1: Kite history adjusted twice

**Owner decisions (2026-09-30).** Approach: *fetch-time aware* (keep Kite bars as received;
apply local factors only for actions after each bar's fetch date; rebuild true raw price for the
universe). Verification: code now, live check later via a new read-only command.

**Evidence.** Zerodha states Kite Connect historical prices are adjusted for bonuses, splits,
rights, spin-offs and extraordinary dividends (https://x.com/zerodha/status/1952292763929874868;
Kite forum discussion 5004). The code declared `adjusted_prices=False` and applied NSE factors on
top of every stored bar.

**Problem.** A bar fetched after an ex-date is already rescaled by Kite; applying the local factor
again halves (2:1) it a second time. A bar fetched before the ex-date is genuinely raw. So the
adjusted series depended on the download date, and the gap safety net (which reads stored bars)
could not see the error. The universe's minimum-price rule also ran on Kite-rescaled prices.

**Change.**
- `domain/market.py`: `PROVIDER_ADJUSTED_SOURCES = {"KITE"}`; `Candle.ingested_at` documented
  as fetch time.
- `data/providers/kite.py`: `adjusted_prices=True`.
- `data/repositories/duckdb_market_repository.py`: `load_daily` / `load_daily_as_of` return
  `known_from` as `Candle.ingested_at`.
- `data/adjustment/engine.py`: `CALCULATION_VERSION` 1.1; `provider_applied_through` /
  `_factor_threshold`; one `_factor_lookup(candle)` used by both `build_adjusted_rows` and
  `apply_factors`; public `single_factor`.
- `data/universe/builder.py`: `provider_undo` CTE divides the latest provider-adjusted close by
  the factors with ex-date in `(trade_date, fetch_date_IST]` known at `known_at`.
- New `data/quality/provider_adjustment.py` (`classify_adjustment`) and CLI
  `vcp verify kite-adjustment [--action SYMBOL:YYYY-MM-DD:SPLIT|BONUS:NUM:DEN] [--limit N]`
  (default: recent applied splits/bonuses from the DB). Read-only. Exit 0 = ADJUSTED, 2 = RAW,
  1 = inconclusive/error. Verdict requires the observed ex-date move within a quarter of the
  expected log jump of one hypothesis.
- Docs: DATA_SPECIFICATION §21.1 (new) and §18A note, PROJECT_DESIGN §10 and §14A,
  DATABASE_SCHEMA §14, AGENTS.md rule 2, README pipeline table, CHANGELOG.

**Tests.** New `tests/unit/test_provider_adjusted_history.py` (20): capability ↔ registry
invariant; Kite backfill after split not double-adjusted (was 25 → now 50); bars fetched before
the split adjusted locally; IST ex-date boundary (fetched on ex-date vs eve); raw sources
unchanged; `apply_factors` agrees; repository + builder end-to-end for backfill and
incremental-across-split; universe min-price uses true raw for KITE (150 eligible) but not for a
raw source (15 excluded); classifier verdicts; CLI exit codes 0/2 and bad spec. No existing test
changed.

**Operator action.** (1) `vcp auth kite`, then `vcp verify kite-adjustment --action ...` with 2–3
known splits/bonuses (large ratios). If it exits 2, stop and report. (2) Rebuild adjusted prices;
new rows are `adj-1.1-*`.

**Not done here.** The ex-date-morning boundary is assumed, not verified. Kite's rights/spin-off/
dividend adjustments are not undone for the price filter. Upstox candles are still treated as raw
(unverified). NSE bhavcopy as a true-raw source remains an option (see Fix 8).

**Verification.** Full suite: 529 passed, 0 failed. `ruff check`, `ruff format --check`,
`mypy --strict src` clean (94 files).

---

## Fix 5a — P1-1: seed `instruments`; offline end-to-end pipeline test

**Owner decisions (2026-09-30).** Source: NSE `EQUITY_L` through `vcp ingest security-master`.
Split: 5a (code, offline test) now; 5b (real run on ~20 names, `vcp verify kite-adjustment`,
5–10 real golden split/bonus fixtures) once `.env` credentials exist.

**Problem.** No command wrote the `instruments` table, but `vcp ingest market` and
`vcp ingest corporate-actions` iterate over it, so the pipeline could not start from an empty
database and had never run end-to-end.

**Change.**
- `data/ingestion/sm_worker.py`: `_sync_instruments` upserts the live (non-delisted) listing into
  `instruments` using the already-canonicalised ids (ISIN first → a symbol rename updates the
  existing instrument's symbol, same id), then marks instruments absent from the non-empty
  listing `is_active = FALSE`. Delisted-list records never create instruments. Stats
  `instruments_upserted` / `instruments_deactivated` (printed by the CLI).
- New `tests/integration/test_pipeline_e2e.py`: security-master → market → corporate-actions →
  adjusted-prices → features → universe → rs → trend-template through `vcp_scanner.cli.main`, from
  an empty DuckDB file; only NSE/Kite/Upstox providers are faked. Asserts 6 active instruments,
  prices for all, no blocking events, 6 eligible, 6 ranked, 60 condition rows, strong uptrend
  PASS, decliner FAIL, survivorship BIASED.
- Unit tests in `test_sm_worker.py`: seeding, rename keeps id, deactivation, delisted records
  don't create instruments.
- README: "Known gap" note replaced; step 2 description; test-layout sentence. CHANGELOG.

**Observation.** The e2e test takes ~50 s for 6 instruments × ~340 bars, mostly row-by-row
`save_daily` (audit P2-4). Expect a real 2,000-name backfill to be slow until that is batched.

**Still open (5b).** Real-data run, Kite verification, golden fixtures from real NSE payloads.

**Verification.** Full suite: 534 passed, 0 failed. `ruff check`, `ruff format --check`,
`mypy --strict src` clean.

---

## Fix 6 — P1-3 / P1-4: architecture boundary and package layout

**Owner decision (2026-09-30).** "Keep layout, enforce boundary": adopt the real layout in
PROJECT_DESIGN §6, delete the empty duplicates, move SQL indicator builders to the data layer,
make RS ranking and universe rules pure functions behind repositories, and point the boundary
test at the real packages. Requirement: identical RS and universe results before and after.

**Problem.** Strategy formulas (RS ranking, universe eligibility) and indicator builders were
raw DuckDB SQL inside `features/` and `data/universe/`, taking `DuckDBStore` directly (AGENTS
rule 3). `test_architecture_rules.py` scanned only `domain` and empty packages (`trend/`,
`indicators/`, `universe/`, ...), so it passed without checking anything. The empty packages
matched PROJECT_DESIGN §6, inviting a Phase 6 agent to write a second implementation there.

**Change.**
- Safety net first: `tests/regression/test_rs_universe_golden.py` + `tests/fixtures/
  rs_universe_golden.json`, **recorded from the old SQL implementations** (12 RS cases: ranks,
  ties, short history, stale, NaN, gappy sessions, gate exclusion, ineligible and price-less
  members; 13 universe cases: every exclusion reason, Kite price undo, PARTIAL label). After the
  refactor the fixture is byte-identical and the test passes (rel tol 1e-12 on floats).
- RS: `domain.trend.RSPriceInput` / `RSRow`; pure `features.relative_strength.compute_rs_rows`;
  `RelativeStrengthEngine(repository, ...)`; new Protocol `RelativeStrengthRepository`; new
  `data/repositories/duckdb_rs_repository.py` (fetch newest + lagged closes, upsert rows).
- Universe: `domain.universe.UniverseCandidate`; pure `evaluate_eligibility`; `UniverseBuilder(
  repository, ...)`; new Protocol `UniverseInputRepository`; SQL moved verbatim to
  `DuckDBUniverseRepository.load_universe_candidates` / `count_known_delistings`.
- Moved `features/daily_features.py`, `features/weekly_aggregation.py` → `data/features/`.
- Removed `indicators/`, `trend/`, `universe/`, `data/normalization/`.
- `tests/unit/test_architecture_rules.py` rewritten: scans `domain/`, `features/`,
  `data/universe/` (each must contain code) and `patterns/`, `scoring/`, `fundamentals/`; forbids
  SDKs/HTTP/DuckDB/pyarrow, `data.storage`, `data.ingestion`, `data.features`, `auth`, `cli`, and
  concrete `data.repositories.*` / `data.providers.*` (only the `*.base` Protocols allowed); a
  test asserts the removed packages stay removed; a self-test proves the checker catches the old
  violation.
- Call sites: `cli.py` (universe), `cli_pipeline.py` (RS, feature imports), 11 test modules
  (constructor changes only), `pyproject.toml` per-file ignore path.
- Docs: PROJECT_DESIGN §6 (real layout + rationale) and §49, AGENTS.md rule 3, AI_AGENT_RULES
  §8 note, CHANGELOG.

**Behavior.** None intended; proven by the golden regression.

**Verification.** Full suite: 543 passed, 0 failed. `ruff check`, `ruff format --check`,
`mypy --strict src` clean (92 files). Golden fixture unchanged (`cmp`).

---

## Fix 7 — P1-7 / P1-6: VCP configuration contract; RS tests

**Owner decisions (2026-09-30).** Adopt VCP_SPECIFICATION §60 exactly. RS rank ceiling (98, not
99, under `rs-1.0.0`): document and pin it; do not change the formula now.

**Problem.** (P1-7) `VCPThresholdsConfig` / `ClassificationConfig` were flat (`min_contractions`,
`require_volume_dryup`, ... under `vcp:`) with `extra="forbid"`, copied from PROJECT_DESIGN §46,
while VCP_SPECIFICATION §60 defines nested `swing` / `volatility` / `volume` / `pivot` /
`confirmation` blocks and per-tier `require_*` flags. The first Phase 6 agent would have had to
break one of them. (P1-6) The only RS test asserted `len(rows) == 2`.

**Change.**
- `config/models.py`: new `VCPContractionCountConfig`, `VCPSwingConfig`, `VCPVolatilityConfig`,
  `VCPVolumeConfig`, `VCPPivotConfig`, `VCPConfirmationConfig`; `VCPThresholdsConfig` composes
  them; `TierClassificationConfig` gains `max_contractions` and the four `require_*` flags;
  `ClassificationConfig` defaults = §60 and validates that a stricter tier is never looser (count,
  final contraction, required flags); `StrategyConfig` validates every tier lies inside
  `vcp.contractions`. Exported from `vcp_scanner.config`.
- `config/strategy.yaml`: §60 block verbatim.
- Docs: VCP_SPECIFICATION §60 note (contract + validation), PROJECT_DESIGN §46 (flat keys
  removed, points to §60), TREND_TEMPLATE_SPECIFICATION §3 (ranks 1–98 under rs-1.0.0), CHANGELOG.

**Tests.**
- `test_config.py`: shipped YAML and defaults equal §60 exactly; the YAML block *parsed out of
  VCP_SPECIFICATION.md* validates (so spec and code cannot drift silently); invalid blocks
  rejected (period order, ratio range, zero bars, negative distance, unknown key); A+ cannot drop
  a requirement the VCP tier has; tier counts must fit `vcp.contractions`. Updated one existing
  test to the nested key (`contractions={"min": 4, "max": 2}`), same intent.
- New `test_rs_ranking.py` (10): known-answer ranks (86/62/37/13), weights & windows, ties share
  the average position, all-equal → 50, NULL history and zero lagged close excluded without
  changing others' ranks, staleness boundary inclusive, ranks within 1–98 and monotonic for N up
  to 3,000, and the spec §7 invariance test (Trend Template evaluated on a subset leaves the RS
  table unchanged; removing a stock from the *population* does change ranks).

**Behavior change.** The configuration hash changes (new trend scan ids). No strategy output
changes: no detector exists yet; RS golden regression unchanged.

**Verification.** Full suite: 563 passed, 0 failed. `ruff check`, `ruff format --check`,
`mypy --strict src` clean.

---

## Status after Fixes 1–7 (2026-09-30)

Done: P0-1, P0-2, P0-3, P1-1 (code part), P1-3, P1-4, P1-6, P1-7, P1-9. Tests 447 → 563.

Still open from the audit (not started; each needs owner approval):

| Audit ID | Item | Blocks |
|---|---|---|
| P0-4 / P1-5 | Point-in-time universe: historical series (bhavcopy / sec_list history), `instrument_symbol_history`, delisted-name prices, GSM/ASM history, honest survivorship label | Phase 6B |
| P1-8 | `scan_runs`, `snapshot_manifest`, deterministic universe ids, explicit snapshot ids everywhere, per-section config hash | Phase 6B |
| P1-2 | Quality-gate blocks need an end date and bitemporal history; suspension ≠ data hole | Phase 6B |
| P1-10 | Surface per-record NSE/Upstox parse failures as events; small bonuses (< 30 %) missed by the gap net | — |
| P2-1…P2-6, P3 | EMA, ATR method, config-driven windows; staleness policy + trading calendar; provenance (`source_run_id`); batch `save_daily`; Parquet lake; duplicate hashes/versions; `scratch/`, `.env` perms | — |

---

## Verification — Fix 4 on live Kite data (2026-09-30)

Command (read-only, nothing written to the database):

```text
vcp verify kite-adjustment --action TATASTEEL:2022-07-28:SPLIT:10:1 \
    --action IRCTC:2021-10-28:SPLIT:10:2 --action RELIANCE:2024-10-28:BONUS:1:1
```

| Symbol | Action (ex-date) | Factor | Ex-date open / prev close | Verdict |
|---|---|---|---|---|
| TATASTEEL | 1:10 split (2022-07-28) | 0.10 | 1.023 | ADJUSTED |
| IRCTC | 1:5 split (2021-10-28) | 0.20 | 0.989 | ADJUSTED |
| RELIANCE | 1:1 bonus (2024-10-28) | 0.50 | 1.007 | ADJUSTED |

Exit code 0: "Kite history is adjusted, as the adjustment engine assumes."

Independent check on the raw Kite bars (script outside the repo): pre-action closes are on the
post-action scale — TATASTEEL ~87–90 (×10 ≈ ₹900 actually traded), IRCTC ~826 (×5 ≈ ₹4,130),
RELIANCE ~1,328 (×2 ≈ ₹2,656) — and the ex-date moves match contemporaneous reports (TATASTEEL
closed +4.8 % on the ex-date; IRCTC traded up to +19 % intraday). Volumes are rescaled too
(TATASTEEL pre-split 50–126 M shares/day). Conclusion: Kite returns split/bonus-adjusted prices
**and volumes**, so without Fix 4 any history downloaded today would have been adjusted twice.

Still unverified: whether a bar fetched on the ex-date morning (IST) is already adjusted (the
engine assumes yes). This needs a fetch on the morning of a future ex-date.

---

## Follow-up — `vcp auth kite` did not read `.env` (2026-09-30)

**Found while running Fix 4's live check.** `vcp auth kite` read `KITE_API_KEY` /
`KITE_API_SECRET` only from the process environment; unlike the other commands it never loaded
`--env-file`, so it failed with "Kite API Key and Secret are required" while both were set in
`.env`. Workaround used once: `set -a && source .env && set +a`. Owner asked for the fix.

**Change.**
- `cli.py`: the `auth kite` branch calls `load_dotenv(args.env_file)` before reading the
  variables (precedence: `--api-key/--api-secret` > existing environment > `--env-file`); the
  error names the env file; `--env-file` help says it is read as well as written.
- `auth/kite_auth.py`: a new `.env` is created with mode 0600 and an existing one readable by
  group/others is tightened to 0600 before the token is written (audit P3 item).
- CHANGELOG.

**Tests.** `tests/unit/test_kite_auth.py` (+5): credentials read from the env file (regression);
arguments and environment take precedence; clear failure naming the file when nothing is set;
new `.env` is 0600; an existing 0644 `.env` becomes 0600 with its content kept.
Manual check on the real `.env` with the variables removed from the shell: the command reached
the login-URL step (then stopped for lack of a request token); stored access token unchanged,
file mode 600.

**Verification.** Full suite: 568 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

---

## Fix 5b — first real-data run; golden corporate-action fixtures (2026-09-30)

**Owner decisions.** 20 stocks (RELIANCE, TATASTEEL, IRCTC, HDFCBANK, BAJFINANCE, NESTLEIND, TCS,
INFY, ICICIBANK, SBIN, LT, BHARTIARTL, ITC, HINDUNILVR, MARUTI, TITAN, SUNPHARMA, ASIANPAINT,
KOTAKBANK, AXISBANK); 2021-01-01 to 2026-09-29; separate git-ignored `data/fix5b.duckdb`;
golden = NSE bhavcopy vs Kite; small bugs fixed directly, big ones brought back for a decision.

### Run results (real data, every CLI step)

| Step | Result |
|---|---|
| `ingest security-master` | 2,592 listed securities → `instruments`; 115 delistings since 2021; 229 ASM + 274 T2T flags. NSE homepage returns 403 but the ASM API still answered. |
| `ingest market` (20) | 20/20 SUCCESS, 28,480 bars, completeness 20/20; 2,328 Kite tokens mapped |
| `ingest corporate-actions` | NSE feed reachable: 5 splits, 4 bonuses, 177 dividends, 1 rights; every split/bonus ratio parsed correctly (incl. KOTAKBANK 5:1 on 2026-01-14); 3 demergers reported as unhandled (RELIANCE, ITC, HINDUNILVR). 0 unexplained gaps, 0 conflicts, 0 blocks. |
| `ingest adjusted-prices` | 20 built, 28,480 rows; **all rows factor 1.0** (every bar fetched after its actions → Kite-adjusted; no double adjustment) |
| `compute features` | 28,480 daily rows, 6,020 weekly rows |
| `ingest universe` 2026-09-29 | 20/20 eligible; label PARTIAL |
| `compute rs` | 20 ranked |
| `compute trend-template` | 20 evaluated: 20 FAIL, 0 NULL (data sufficient). Best: TITAN (RS 96, Stage 2) fails only close > SMA50 |

**Independent recomputation.** From a fresh Kite fetch in plain Python (no DB, no engine):
TITAN's ten measurements/thresholds match the stored rows to 2.1e-16 relative, and all 20 RS ranks
are identical.

### Bugs found and fixed (small, per owner rule)

1. **Same-day split + bonus lost a factor** (BAJFINANCE 2025-06-16: 0.5 stored instead of 0.1;
   raw-sourced history would have been 5x too high). `compute_factors` now merges same-ex-date
   actions into one factor; the CA worker repairs stale factor sets on the next run. Verified
   on the real DB: BAJFINANCE factor 0.1.
2. **`--instrument SYMBOL` silently matched nothing** in six places (the post-ingest quality scan
   ran on 0 instruments). New `_matches` / `_resolve_instrument_args` accept id or symbol.
   Verified: quality scan now covers 20.
3. **`.env.example` placeholders used as credentials** (Upstox 401 aborted the CA ingest).
   `env_secret()` treats blank / `your_..._here` as unset.

Tests: `tests/unit/test_fix5b_bugs.py` (11), `tests/unit/test_env_secret.py` (8).

### Golden fixtures

`scripts/capture_golden_ca.py` → `tests/fixtures/corporate_actions/golden_actions.json` (REAL
data, sources recorded): IRCTC 2021-10-28, TATASTEEL 2022-07-28, NESTLEIND 2024-01-05, RELIANCE
2024-10-28, BAJFINANCE 2025-06-16 (split+bonus), NESTLEIND 2025-08-08, HDFCBANK 2025-08-26.
`tests/regression/test_golden_corporate_actions.py` (49): engine factors; raw bhavcopy shows the
jump (RAW) and Kite does not (ADJUSTED); raw × cumulative factor = Kite within 0.5 % (6/7 within
0.01 %); Kite bars fetched after the action are never re-adjusted; gap net flags the raw jump only
without the action; NSE subject formats parse to the verified ratios.

### Found, NOT fixed — need owner decisions

| # | Finding | Why it matters |
|---|---|---|
| D1 | Kite also adjusts history for **large ordinary dividends** (TATASTEEL ₹3.60 ≈ 2.2 %, 2024 and 2025) and demergers; our engine does not model them. Bars fetched before such an event differ from bars fetched after by that factor. | Incremental ingestion leaves ~2 % steps in stored history; VCP contraction depths (final contraction 3–8 %) are sensitive to that. Options: detect Kite re-adjustment on each incremental fetch (compare an overlap window) and re-fetch the instrument's history as new bitemporal versions; or move raw prices to NSE bhavcopy. |
| D2 | Kite trading symbols carry series suffixes (`GATECHDVR-BE`, `-SM`, `-SG`, ...): 7,831 dump rows unmapped. A stock moving EQ ↔ BE changes its Kite symbol. | BE/SME names cannot be ingested; a series move breaks the token mapping. Needs a symbol-normalisation rule in `provider_mapping`. |
| D3 | Throughput: `instruments` upsert 46 s for 2,592 rows; market ingest ~25 s per stock; adjusted build ~8 s per stock. | A 2,000-stock backfill ≈ 14 h + 4.5 h (audit P2-4). Batch inserts before a full-universe run. |
| D4 | Demergers (RELIANCE/Jio Financial 2023, ITC Hotels 2025, HINDUNILVR) are unhandled by the local engine. | Fine for Kite-sourced bars (already adjusted); wrong for any raw source; gap net only catches ≥ 30 %. |

**Verification.** Full suite: 635 passed, 0 failed (was 568). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

---

## Follow-up — Upstox coverage window and split-ratio convention (2026-09-30)

**Found** after the owner added Upstox credentials. Live responses for TATASTEEL, RELIANCE,
HDFCBANK, BAJFINANCE, KOTAKBANK: each returns only ~12 months of events (one 2026 dividend each;
none of their 2022–2025 splits/bonuses), and KOTAKBANK's face-value 5 → 1 split arrives as
`"1:5"`. Owner approved the fix.

**Problems.** (1) Fix 2 escalates an NSE-only split/bonus to `PROVIDER_CONFLICT` when Upstox was
asked and stayed silent; with Upstox's 12-month depth, every older split/bonus would have been
blocked and its factor withdrawn after 3 days. (2) Upstox split ratios are `old:new` shares, the
reverse of the engine/NSE `(old FV, new FV)`, so every split reported by both would conflict on
ratio.

**Change.**
- `data/providers/upstox_ca.py`: `coverage_start[instrument_id]` = earliest ex-date among *all*
  records Upstox returned (before the window filter); split ratio parts swapped on parse.
- `data/ingestion/ca_worker.py`: secondary window per instrument = `[max(start,
  coverage_start), end]`; instrument asked but with no records → no window (silence proves
  nothing); providers without `coverage_start` keep the full window (unchanged behavior).
- DATA_SPECIFICATION §18A note; CHANGELOG.

**Tests.** `tests/unit/test_upstox_coverage.py` (8, payload shapes from the live responses):
split `"1:5"` → (5, 1); bonus `"4:1"` unchanged; coverage from the earliest record even outside
the window; no records → no coverage; NSE+Upstox same split → CONFIRMED; 2022 split before
coverage stays SINGLE_SOURCE after grace (factor kept); split inside coverage that Upstox lacks
→ PROVIDER_CONFLICT; instrument with no Upstox records never escalates.

**Real-data verification** (`data/fix5b.duckdb`, token active): 221 actions (NSE 187, Upstox 34);
KOTAKBANK split CONFIRMED, 31 dividends CONFIRMED, historical splits/bonuses SINGLE_SOURCE,
0 conflicts, 0 blocking events. Simulated run 10 days later (past the grace period) on a copy with
live feeds: still 0 conflicts, 0 blocks, all 8 split/bonus factors kept.

**Verification.** Full suite: 643 passed, 0 failed (was 635). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Step 2.0 — spike: NSE bhavcopy as the raw price source (2026-09-30)

Read-only checks before any code (plan: bhavcopy = raw source of truth; Kite = provisional
today-bar + cross-check; equity series EQ/BE/BZ/SM/ST; demergers in scope). Scripts and cached
files live in `data/spike/` (git-ignored, not committed).

| # | Assumption | Result |
|---|---|---|
| 1 | Both formats parse to the same bars | **Yes.** 2026-09-29 bhavcopy vs Kite bars in `data/fix5b.duckdb`: 20/20 identical close and volume. |
| 2 | `PREVCLOSE` on an ex-date is exchange-adjusted (gives an implied factor) | **No.** 7/7 golden splits/bonuses and the RELIANCE demerger: ex-date `PREVCLOSE` = unadjusted prior close. **Plan 2.4 changed:** no implied factor from `PREVCLOSE`; factors keep coming from reconciled actions. |
| 3 | ISIN changes on face-value splits | **Yes, with a lag of 0–1 trading day** (NESTLEIND 2024, BAJFINANCE 2025 on the ex-date; IRCTC 2021, TATASTEEL 2022 the day after). `instruments.isin` is the *current* ISIN, so old files never match it: identity must follow symbol continuity, with an `isin_history`. |
| 4 | A 404 means holiday | **Ambiguous.** Holidays and weekends both return 404 (no body difference). NSE's `holiday-master` API works but lists the current year only. Past dates: a 404 once the archive is settled (T+3) = `NO_SESSION`. Current year: cross-check holiday-master, and treat an unexpected 404 as an error. |
| 5 | Sessions happen only on weekdays | **No.** Muhurat Sunday 2023-11-12 and special Saturday 2024-01-20 have files. The ingest must probe every calendar day, which is about 2,100 requests for 2021 to now. |
| 6 | Cost and size | About 1.2–1.5 s per file and 70–210 KB zipped. There are 2,000 (2021) to 3,700 (2026) rows per day; EQ 1,490 → 2,671, SM 62 → 384, BE ~200–290, ST up to 115, BZ 16–61. The rest are debt/ETF series (GB, GS, N*, TB …), which are excluded. |
| 7 | Kite history differs from raw only by splits/bonuses | **No, and this confirms D1.** Kite/raw close ratios on past dates are INFY 0.9789 and TATASTEEL 0.977 (dividends), ITC 0.8246 (ITC Hotels demerger, 2025-01), RELIANCE 0.4766 = 0.5 bonus × 0.953, and BHARTIARTL 0.9816 (2021 rights). Kite also rescales volume for demergers and rights. |
| 8 | Demerger factor can be derived | **Yes, from the ex-date special pre-open.** RELIANCE ex 2023-07-20 opened at 2580.00 against a 2841.85 prior close. 2841.85 − 2580 = 261.85, which is exactly JIOFIN's base price when it listed on 2023-08-21 (series BE). Factor = 0.9079. |

**Consequences for the plan.**
- **2.2 identity:** the primary link is symbol continuity, with ISIN changes recorded in `isin_history`. An ISIN change without a nearby split/FV action raises a WARNING.
- **2.3 ingestion:** probe every calendar day; `bhavcopy_files.status` ∈ {OK, NO_SESSION, ERROR}.
- **2.4 implied factor dropped:**
  - DEMERGER factor = ex-date open / prior close, taken from the special pre-open. This is only a *suggestion*; it must be CONFIRMED by NSE's announcement or set by MANUAL_OVERRIDE. The gap detector blocks until then.
  - RIGHTS is also in scope: raw prices now show rights gaps (BHARTIARTL 2021). Factor = theoretical ex-rights price / cum price.
- **2.6 migration:** expect adjusted-history changes for dividend residuals (now absent), plus new blocking gaps for demergers and rights (ITC 2025-01, RELIANCE 2023-07, BHARTIARTL 2021) until they are resolved.

## Step 2.1 — bhavcopy provider, raw cache and manifest (2026-09-30)

**Change.**
- New `domain/bhavcopy.py`: `BhavcopyRow`, `RejectedBhavcopyRow`, `ParsedBhavcopy`, `BhavcopyFileRecord`, `BhavcopyFormat`, `BhavcopyFileStatus` and `EQUITY_SERIES`.
- New `data/providers/nse_bhavcopy.py`:
  - URL and layout chosen by date;
  - one parser for both layouts;
  - series filter;
  - row rejects with reasons;
  - the whole file is refused on a wrong session date, missing columns or a non-zip payload;
  - `fetch_day` with an unchanged zip cache plus sha256, a `refresh` option and 1 s spacing;
  - `NOT_FOUND` on 404, `ProviderError` on any other failure;
  - `classify_missing` (holiday list, or at least 3 days old → NO_SESSION; else PENDING);
  - `get_trading_holidays`.
- Table `bhavcopy_files` (PK trade_date + sha256; missing-file entries use sha256 '' and are updated in place) and `DuckDBBhavcopyRepository` (`record_file`, `latest_file(s)`).
- DATA_SPECIFICATION §21.2; CHANGELOG.
- Not yet wired into any CLI or worker (that is 2.3).

**Tests.** New `tests/unit/test_nse_bhavcopy.py` (16) against two real NSE files trimmed to 11 rows each (`tests/fixtures/bhavcopy/`). They check:
- TATASTEEL's split ex-date bar is raw, and `PREVCLOSE` is unadjusted at 959.40;
- 2026-09-29 TITAN and TATASTEEL closes and volumes equal Kite's;
- the layout switch date and URLs;
- the series filter;
- wrong-date, missing-column and non-zip files are refused;
- duplicate, inverted, NaN, blank, zero and fractional-volume rows are rejected with reasons;
- 404 classification;
- the cache hit, refresh, 404, HTTP 500 and connection-error paths;
- rate-limit spacing;
- holiday parsing;
- manifest upsert and versioning.

**Verification.** Full suite: 659 passed, 0 failed (was 643). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Step 2.2 — identity: symbol continuity, ISIN history, delisted names (2026-09-30)

**Change.**
- `data/ingestion/bhavcopy_identity.py` is a pure resolver (state in, changes out). It works in the three steps below and is order-enforced: a day at or before the last resolved one must be replayed from `daily_series`, and a replay with new rows raises `IdentityOrderError`.
  1. A known ISIN goes to its instrument; a changed symbol or ISIN closes the open period and opens a new one.
  2. An unknown ISIN with the same symbol and the same issuer (ISIN characters 1–9) goes to the same instrument as an `ISIN_CHANGE`. A seeded EQUITY_L instrument with no history is adopted by symbol if its ISIN is the same issuer's or missing.
  3. Otherwise a new inactive instrument is created, disambiguated with `#ISIN` when NSE reused the symbol for another issuer.
- `data/identity.py`: `same_issuer_equity`; `mint_instrument_id(disambiguator=)`; `symbol_from_instrument_id` strips the disambiguator.
- Tables `instrument_identifier_history` (PK instrument_id + valid_from, `valid_to` exclusive) and `daily_series` (PK trade_date + symbol + series), plus `DuckDBIdentityRepository` (`load_state`, `load_day_mapping`, `load_periods`, and `apply`, which writes a day in one transaction).
- Quality: `identity_events` → `SYMBOL_MAPPING_UNCERTAIN`. It is a WARNING that never blocks, raised when an ISIN change has no SPLIT with ex-date between 7 days before and 1 day after it. It is wired into `QualityScanner` (optional `identity=`) and both CLI scan paths, which print the count.
- `DuckDBStore.insert_rows` does bulk inserts via Arrow (`INSERT` or `INSERT OR IGNORE`). This also points at D3: other repositories still use `executemany`, which is the likely cause of the ~8 s per stock adjust time (to check in 2.3).
- **Small fix found by the real-data check (per the owner rule):** series EQ also carries ETFs (`INF...` ISINs, 350 rows on 2026-09-29) and partly-paid shares (`IN9...`). They are now skipped; only `INE...` is kept (`EQUITY_ISIN_PREFIX`). Before the fix, 12 ETFs were minted with `#ISIN` ids because their ISINs change on unit splits.

**Real-data check** (`data/spike/identity_check.py`, not committed): 70 cached real days (2021-01-01, then 2021-10 to 2022-09, 2023, 2024–2026), seeded with the 2,592 EQUITY_L instruments from `data/fix5b.duckdb`.
- Results: 808 new inactive instruments (delisted names and SME stocks not in EQUITY_L), 269 ISIN changes, 148 symbol changes, 0 disambiguated IDs, 0 rejected rows.
- Spot checks match known events: ADANIGAS→ATGL, MAGMA→POONAWALLA and JUBILANT→JUBLPHARMA renames (2021); AFFLE, CESC and KPRMILL split ISIN changes (2021); TATASTEEL and IRCTC linked across their splits.

**Tests.**
- New `tests/unit/test_bhavcopy_identity.py` (13): seed adopts the old ISIN and then records the split ISIN change (TATASTEEL, real ISINs); IRCTC ISIN change with no seed; a symbol reused by another issuer gets `#ISIN`; the old company is not merged into today's holder of its symbol; a delisted name becomes inactive (daily_series keeps BE); symbol rename; DVR shares stay separate; order and replay rules; real files for 2022-07-28 and 2026-09-29 link TATASTEEL across the split; ISIN change explained vs unexplained.
- Plus 1 ETF-skip test in `test_nse_bhavcopy.py`.

**Verification.** Full suite: 673 passed, 0 failed (was 659). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Step 2.3 — bhavcopy ingestion and precedence over Kite (2026-09-30)

**Change.**
- New `data/ingestion/bhavcopy_worker.py` (`BhavcopyIngestionWorker`) and CLI `vcp ingest bhavcopy`. Per calendar day, in order:
  1. skip if the manifest has the day as OK (cached) or NO_SESSION;
  2. download or read the cache;
  3. classify 404s;
  4. parse;
  5. resolve (or replay) identity;
  6. pick one bar per instrument by series priority;
  7. write bars;
  8. mark the day OK last.
  PENDING or ERROR stops the run. An `ingestion_runs` row is written per run.
- `DuckDBMarketDataRepository.save_final_daily` (set-based, one transaction): an identical current bar stays; any other current bar is closed and the bhavcopy bar inserted. It returns the superseded counts per provider.
- `save_daily` guard: a non-bhavcopy bar never supersedes a current bhavcopy bar (Kite re-fetches are ignored for final sessions).
- `DuckDBStore.registered` (Arrow-backed view); `insert_rows` now uses it. `save_adjusted_daily` is set-based (D3).
- **Small fix (owner rule).** `classify_missing` would have held every Monday run on the weekend's 404s (PENDING for 3 days). An earlier weekend day is now NO_SESSION; today's 404 is PENDING.

**Real-data check** (copy `data/fix5b_bhav.duckdb` of the Fix 5b DB, `--start 2026-09-01`, not committed):
- 30 days in 32 s (21 sessions, 9 no-session): 63,783 bars, 0 rejects.
- All 400 Kite bars for the 20 stocks superseded. Every one had identical OHLCV to the bhavcopy bar (0 mismatches).
- 585 new inactive instruments and 7 identifier changes; a re-run is a no-op.

**D3 (throughput).**
- Ingest: 1 file per session covers ~3,000 stocks, about 1.5 s per day including download, where Kite needed about 25 s per stock.
- Adjust: 5 stocks took 0.69 s instead of about 40 s, and the rows are identical to the old code's (7,120 compared, 0 differences).

**Tests.**
- New `tests/unit/test_bhavcopy_worker.py` (6): order with a weekend as NO_SESSION and series priority; bhavcopy supersedes Kite and Kite never supersedes bhavcopy; re-run and refresh write nothing new; a recent missing weekday stops the run before later days (exit 0); a bad file is an ERROR and stops; future days are never requested.
- `test_classify_missing` extended with the weekend rules.

**Verification.** Full suite: 679 passed, 0 failed (was 673). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Step 2.4 — rights issues and demergers (D4) (2026-09-30)

**Owner decisions.** DEMERGER is applied automatically when NSE's feed lists it, with factor = ex-date special pre-open price / prior close. RIGHTS uses TERP from NSE's text. Anything that cannot be derived stays blocked.

**What NSE's feed says** (live, `data/spike/ca_samples.py`):
- Demergers arrive as subject `Demerger` (RELIANCE 2023-07-20, ITC 2025-01-06, SAREGAMA, STAR).
- Rights arrive as `Rights a:b @ Premium Rs X/-` plus `faceVal` (BHARTIARTL `1:14 @ Premium Rs 530/-`, face value 5).
- Variants seen: `21:20@`, `1:19.07`, `Premium Rs 0`, and `Rights 613:399` with no premium.

**Change.**
- `domain/corporate_actions.py`:
  - `PRICE_DERIVED_ACTIONS = {RIGHTS, DEMERGER}` and `ExDatePrices(prior_close, ex_open, raw)`;
  - `derived_factor` (TERP for rights with volume factor 1/pf, and 1.0 when the issue price ≥ P; ex-open/prior-close for demergers with volume factor 1.0, rejecting O ≥ P and pf < 0.05);
  - `factor_unknown` (only on raw prices);
  - `explains_price_gap` now includes adjustable RIGHTS/DEMERGER.
- `adjustment/engine.py`: `compute_factors(resolutions, ex_prices=None)`; `ex_date_prices(candles, ex_dates)` (raw only when both bars are `NSE_BHAVCOPY`); `CALCULATION_VERSION` 1.2.
- `ca_worker`: optional `market` (bars) → ex-date prices → factors and `factor_unknown` events. `QualityScanner` derives the same events from the bars it already loads, so both sync the same event set.
- `nse_ca`: `DEMERGER` is parsed (removed from the unhandled markers); rights ratio via `_RIGHTS_RE`; `rights_issue_price` = face value + premium. A rights record without a premium is listed in `unparsed_ratios`.
- **Reconciliation.** When one source has several records for one key, values come from the most recently seen record and the grace period from the earliest. After this parser upgrade, existing DBs hold both the old record (no ratio) and the new one for the same rights issue.
- **Policy change in an existing test.** A rights issue with an adjustable status now explains its gap (`test_quality_events`). A rights *conflict* still does not.

**Real-data check** (`data/fix24.duckdb`: a copy of the Fix 5b DB plus bhavcopy windows 2021-09-20..30, 2023-07-17..21 and 2025-01-01..07, then `ingest corporate-actions` for the three stocks):
- Resolutions: BHARTIARTL RIGHTS (1, 14, ₹535); RELIANCE and ITC DEMERGER, all SINGLE_SOURCE.
- Factors: BHARTIARTL 0.98157064 (volume 1.01877538, the same as Kite's volume ratio 1.01877); ITC 0.94601329; RELIANCE 0.90785932 (cumulative 0.45393 with the 2024 bonus).
- Adjusted series: RELIANCE and ITC ex-date open equals the adjusted prior close exactly (ratio 1.0000). For BHARTIARTL the ex-date open is 0.7 % above TERP, which is an ordinary market move.
- The 4 UNEXPLAINED_GAP events in that DB (RELIANCE) are an artifact of the test setup: short raw bhavcopy windows sit between Kite bars that were already halved for the 2024 bonus. **Consequence for 2.6:** the migration must backfill bhavcopy over the whole history in one continuous run, never in patches.

**Tests.** New `tests/unit/test_derived_factors.py` (22):
- parsing: the real BHARTIARTL, RELIANCE and ITC records, plus 3 rights text variants;
- math: TERP = Kite to 2e-5; demerger factors from the golden JSON; rights above market; 5 underivable cases;
- no factor and no block on Kite prices;
- `ex_date_prices` edge cases;
- the engine combining a demerger with a bonus (cumulative);
- `factor_unknown` blocks only on raw prices;
- worker end-to-end: bhavcopy bars give RELIANCE a factor of 0.907859;
- reconciliation using the newest record.
Plus 2 gap-detector tests in `test_quality_events.py`.

**Verification.** Full suite: 702 passed, 0 failed (was 679). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Step 2.5 — Kite as today's provisional bar and as a cross-check (2026-10-01)

**Change.**
- **Provisional bar.**
  - `vcp ingest market --today` → `IngestionWorker.ingest_instrument(provisional=True)` → `save_daily(data_status="PROVISIONAL")`. It always re-fetches today and skips the completeness check.
  - The bhavcopy bar supersedes it (`save_final_daily`), and the existing guard stops any Kite bar from superseding a bhavcopy bar.
- **Reads exclude PROVISIONAL by default.** `load_daily` / `load_daily_as_of` (`include_provisional=`), `load_universe_candidates` (`include_provisional=`), `AdjustedPriceBuilder(include_provisional=)` and `UniverseBuilder(include_provisional=)`.
  - The CLI flag `--allow-provisional` is on `ingest adjusted-prices` and `ingest universe`.
  - Full adjusted rebuilds use `save_adjusted_daily(prune_missing=True)`, so a provisional adjusted row disappears on the next build without the flag.
- **Kite history behind a flag.** `vcp ingest market` now requires `--today` or `--kite-history`; with neither it exits 1 and points to `vcp ingest bhavcopy`. The e2e test and the CLI date test now pass `--kite-history`.
- **Cross-check.** New `data/quality/kite_crosscheck.py` (pure) plus `vcp verify kite-crosscheck` (read-only, Kite fetched in provider-sized chunks). For each step in Kite/ours it finds the actions with ex-date in (t-1, t]:
  - DIVIDEND → INFO `KITE_DIVIDEND`;
  - DEMERGER → INFO `DEMERGER_METHOD`;
  - SPLIT, BONUS or RIGHTS → WARNING `FACTOR_MISMATCH`;
  - none → WARNING `UNEXPLAINED`;
  - a step that reverses the next day → WARNING `BAR_MISMATCH`;
  - a latest ratio ≠ 1 → WARNING `LEVEL_MISMATCH`.
  The default tolerance 0.2 % covers Kite's 2-decimal rounding.
- **Small fixes (owner rule).**
  - `vcp verify kite-adjustment` sampled any action that "explains a gap", which since 2.4 includes rights and demergers (factor 1.0 under a ratio test). It now samples splits and bonuses only.
  - The rights parser missed live variants `Rights Issue 4:17@ Premium Rs 390/-` and `Rights 7:10 @ Prm Rs 102/-`. After the fix, of all NSE rights records since 2021 only 6 remain unparsed: no premium in the text (4), CCPS/warrants (1) and `Rights 1:1` (1). Each is listed and blocked if raw prices show its ex-date.

**Real-data check** (`data/fix25.duckdb`: Fix 5b DB + bhavcopy 2026-04-01..2026-09-30 + NSE corporate actions for all 2,592 instruments + adjusted prices for 7 stocks).
- `vcp verify kite-crosscheck` over 2026-04-01..09-30, 125 common days each, exit 0:
  - INFY: INFO `KITE_DIVIDEND` step 0.97882 on the 2026-06-10 dividend;
  - ITC: INFO `KITE_DIVIDEND` step 0.97348 on the 2026-05-27 dividend;
  - HDFCBANK, KOTAKBANK, RELIANCE, TATASTEEL, TCS: no difference (their dividends were too small for Kite to adjust).
- `vcp ingest market --today` before the session opened (08:59 IST): 0 rows and SUCCESS. It fails safe.
- Live during the session (09:18 IST), `vcp ingest market --today` for TITAN, TATASTEEL and RELIANCE wrote 3 rows with `KITE`/`PROVISIONAL` (e.g. TATASTEEL open 184.70, last 185.14).
  - `ingest adjusted-prices --allow-provisional` for TITAN built 1,426 rows, including today.
  - The rebuild without the flag built 1,425 rows and left 0 adjusted rows for today (pruned).
- The full corporate-action run took 4 m 44 s: 9,378 records, 9,169 resolutions, 537 factors.
- The quality scan on this hybrid DB found 34 unexplained gaps and 27 blocks. They come from Kite-adjusted history before 2026-04-01 meeting raw bhavcopy after it, which 2.6 removes with a continuous backfill.

**Found, not fixed (needs owner decision).** An expired Upstox token (HTTP 401) aborts the whole `ingest corporate-actions` run. The run above was made NSE-only by blanking the token for that command.

**Tests.**
- New `tests/unit/test_provisional_bars.py` (6): hidden by default; intraday update, then superseded by the bhavcopy and protected from late Kite fetches; adjusted rows included only when allowed and pruned after; universe; worker re-fetch; CLI mode required.
- New `tests/unit/test_kite_crosscheck.py` (10).
- +2 rights variants in `test_derived_factors.py`.
- Interface dummy updated.

**Verification.** Full suite: 720 passed, 0 failed (was 702). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Follow-up — expired Upstox token no longer aborts corporate actions (2026-10-01)

**Owner decision:** warn and run NSE-only.

**Change.**
- New `domain.errors.ProviderAuthError(ProviderError)`; `UpstoxCorporateActionProvider` raises it on HTTP 401/403.
- `CorporateActionIngestionWorker` catches it from the secondary source only. It sets `secondary_unavailable`, uses no secondary actions and ignores `queried_instrument_ids` (silence proves nothing), and continues. `vcp ingest corporate-actions` prints a warning.
- Other secondary failures (HTTP 5xx, partial fetch) still abort, as before.

**Test.** `test_expired_upstox_token_runs_nse_only_without_escalating`: a split inside what would be Upstox's coverage stays SINGLE_SOURCE across the grace period, and its factor is kept.

**Verification.** Full suite: 721 passed, 0 failed (was 720). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Step 2.6 — first full rebuild found split histories; identity fix (2026-10-01)

**First rebuild** (`data/migr26_v1.duckdb`: copy of the Fix 5b DB + continuous bhavcopy 2021-01-01..2026-09-30 + all downstream steps; the report came from the first version of `scripts/migration_report.py`).
- Bhavcopy: 1,425 sessions, 674 no-session dates, 3,121,990 bars, 0 rejected; 28,480 Kite bars superseded; 1,071 new inactive instruments; 450 identifier changes. Time: 35 min.
- Corporate actions 5.8 min, adjusted prices 3.3 min (3,661 instruments), features 4.3 min, universe 29 s (1,327 eligible), RS 8 s, Trend Template 2 min (273 PASS).
- Kite vs ours for the 20 Fix 5b stocks: 15 identical. The rest are explained by Kite's dividend adjustments (INFY, ITC, TATASTEEL) and its demerger method (RELIANCE, ITC, HINDUNILVR's 2025-12-05 demerger).
  - TATASTEEL's 2021–22 Kite history drifts against the official NSE file (ratio 0.922 → 0.945 with ±0.3 % day-to-day noise; 32 small warnings). The NSE bhavcopy is the official record, so Kite's series is the noisy one.
- Trend Template for the 20 stocks: same pass/fail and the same weekly stage everywhere. Condition 10 (RS ≥ 70) now fails for 7 more stocks because RS is ranked against the whole eligible market, not 20 stocks.

**Bug found (step 2.2 identity).** 48 instruments had their history split in two, e.g. ANGELONE, SHRIRAMFIN, 360ONE, HEG, KPIGREEN, NAVA, PDSL and LINC.
- The cause: the stock changed symbol and later ISIN (split), and EQUITY_L seeds today's ID with today's ISIN. The old part was minted as a separate inactive instrument (e.g. `NSE_EQ|KPIGLOBAL`) because its ISIN and symbol matched no seed.
- NSE's corporate-action feed quotes the *old* ISIN, so actions landed on the old piece. KPIGREEN's 2025-01-03 1:2 bonus became an unexplained (blocking) gap on the new piece.

**Fix.**
- `bhavcopy_identity._same_company_by_symbol`:
  - continues an open period of the same issuer when the old symbol is not trading that day and the row's ISIN is a later issue (split and rename on one day);
  - adopts a seed whose ISIN is a later issue of the same issuer and whose symbol is not trading that day (the row is the seed's past);
  - treats several candidates as ambiguous, leading to a new instrument.
- Differential-voting shares (a later issue trading beside the ordinary shares) are never taken for a predecessor.
- `DuckDBInstrumentResolver.resolve` also checks `instrument_identifier_history` for former ISINs, before the security master.
- `scripts/migration_report.py`: uses the latest universe snapshot and the full Trend Template scan (the first version double-counted the copied 20-stock snapshot).

**Tests.** +3 in `test_bhavcopy_identity.py`: KPIGLOBAL → KPIGREEN with real ISINs gives one history and the old ISIN resolves to KPIGREEN; a same-day split and rename (SABTN → SABTNL) continues; DVR and same-day seed symbols are not adopted. Full suite: 724 passed, 0 failed. `ruff`, `mypy --strict src` clean.

**Second rebuild** started 11:38 IST on a fresh copy (`data/migr26.duckdb`), with results recorded below.

**Second rebuild** (`data/migr26.duckdb`, 11:38–12:06 IST): bhavcopy 12.5 min (from cache), then corporate actions 5.8 min, adjusted prices 3.3 min, features 4.2 min, universe 29 s, RS 8 s and Trend Template 2 min.
- **Fork fix confirmed:** 978 inactive instruments (was 1,071); identifier changes 543, of which 68 are ISIN+symbol changes on the same day. KPIGREEN, ANGELONE, SHRIRAMFIN, 360ONE, HEGAM and NAVA all have full 2021→ history, every split/bonus factor applied, and no gap events.
- **Universe and signals:** 1,330 eligible (was 1,327); Trend Template 272 PASS, 1,046 FAIL, 12 INSUFFICIENT_DATA.
- **Why unexplained gaps rose to 438** (from 379): +106 / −47. Most of the rise is rights entitlements (`ESSEN-RE`, `SILGO-RE1` …, ISIN security type 20, series BE/ST): successive issues were now chained into one instrument.
  - The rest are real price-scale events that the forks had hidden, e.g. AQYLON +1000 % on 2024-04-02 and ACL +1009 % on 2023-05-10. These are consolidations / capital reductions, which NSE lists but this scanner does not model yet. The gap net now blocks them correctly.
- **Small fix (owner rule):** the parser now keeps only ISIN security type `01` (equity shares). Rights entitlements and other non-share lines are skipped, counted as `"<series>:type<NN>"`. Test `test_rights_entitlements_are_skipped`. Full suite: 725 passed.
- **Blocking gaps in non-RE instruments:** 50 events on 44 active instruments, 76 on 62 inactive. Causes seen in samples:
  - unmodelled consolidations / capital reductions;
  - gaps after long suspensions (GOODYEAR, LANCER), which is audit P1-2;
  - SME bonuses/splits missing from NSE's `index=equities` feed (JSLL; `index=sme` returned only 9 records).

**Main database built** (owner approved; `data/vcp_scanner.duckdb`, fresh, code at cce2ec4, 12:15–12:44 IST):

| Step | Time | Result |
|---|---|---|
| Security master | 1.6 min | 2,593 listed stocks, 115 delisting records, 497 surveillance flags |
| Bhavcopy load | 12.3 min (cached files) | 1,425 sessions, 3,120,313 bars, 0 rejected; stopped cleanly at 2026-10-01 (PENDING); 775 inactive instruments; 501 identifier changes |
| Corporate actions | 5.7 min, NSE only (Upstox token expired; run continued with a warning) | 182 unexplained gaps (trial: 438); 136 blocking signals on 106 instruments, of which 44 active; 10 conflicts |
| Adjusted prices | 3.0 min | 3,363 instruments |
| Features | 3.9 min | 3,363 instruments |
| Universe 2026-09-30 | 27 s | 1,334 eligible |
| RS | 8 s | 1,334 ranked |
| Trend Template | 2.2 min | 265 PASS, 1,056 FAIL, 13 INSUFFICIENT_DATA |

The 1,677 bars fewer than the trial build are exactly the rights-entitlement rows, now excluded. Top RS-98 passes: BHAGYANGR, BIRLACABLE, CUPID, INDSWFTLAB, MOREPENLAB, RAYMOND, SETL, SHILPAMED, SIYSIL, TVSHLTD, WELCORP.

**Step 2 status:** complete. D1 (Kite dividend re-adjustment), D2 (Kite symbol suffixes; no longer needed for history), D3 (throughput: full market 2021→ in about 30 min) and D4 (demergers; rights too) are closed.

**Open follow-ups:**
- consolidations and capital reductions (reverse split) are unmodelled;
- SME corporate actions are missing from NSE's equities feed;
- suspension gaps block permanently (P1-2);
- the Upstox token needs refreshing;
- the survivorship label is still PARTIAL (P0-4).

## Fix P0-4 — point-in-time universe (2026-10-01)

**Owner decision:** ASM/GSM have no free history, so collect them from now; earlier snapshots are labelled PARTIAL with the reason.

**Change.**
- `DuckDBUniverseRepository.load_universe_candidates`:
  - new `day_series` CTE, giving the series on the last session on or before the as-of date (known at `known_at`, EQ preferred);
  - series = bhavcopy series, with today's EQUITY_L series only as the fallback;
  - exchange = NSE for bhavcopy names;
  - T2T = BE/BZ that day.
- `survivorship_evidence()`: bhavcopy manifest days settled in the window, first ASM/GSM collection dates and delistings. Pure `derive_survivorship()` in the builder; `SURVIVORSHIP_WINDOW_DAYS` = 380.
- `UniverseSnapshot.survivorship_detail` and the `universe_snapshots.survivorship_detail` column (migration adds it). The CLI prints the reason.
- Removed `UniverseConfig.survivorship_coverage_verified`.
- `UNIVERSE_METHOD_VERSION` 1.1 → 2.0; the RS/universe golden file was re-recorded, and only `config_hash` and `method_version` changed.
- `NSESurveillanceProvider` also collects GSM (`/api/reportGSM`; 77 stocks on 2026-10-01).

**Real-data check** (`data/p04.duckdb`, a copy of the main DB plus a security-master run):

| As of | Screened | Eligible | Eligible but gone today | Eligible though not EQ today | BE/BZ that day though EQ today | Label |
|---|---|---|---|---|---|---|
| 2022-06-30 | 2,049 | 831 | 28 (HDFC, MINDTREE, PEL, TV18BRDCST, SHRIRAMCIT, IDFC …) | 50 | 70 | PARTIAL: ASM and GSM history start 2026-10-01 |
| 2024-03-28 | 2,428 | 1,196 | 16 (IDFC, ISEC, GSPL, UJJIVAN …) | 61 | 237 | PARTIAL, same reason |
| 2026-09-30 | 3,363 | 1,335 | 0 | 1 | 0 | PARTIAL, same reason (the lists were first collected 2026-10-01) |

**Found, not fixed:** `sm_worker._accept_delisted` skips DHFL's delisting record (ISIN INE202B01012) because the bhavcopy instrument `NSE_EQ|DHFL` carries the same issuer's later ISIN (…038). Same-issuer ISINs should be treated as one security there. This is small, but the delisting date only feeds the survivorship count, and DHFL's prices are already present.

**Tests.**
- New `tests/unit/test_point_in_time_universe.py` (9):
  - series and T2T per date;
  - a delisted name screened while it traded;
  - `derive_survivorship` (5 cases);
  - the label from stored data, persisted with its reason;
  - missing bhavcopy days → PARTIAL.
- GSM in the provider tests (injected clock, GSM failure raises).
- `test_nse_delisted` uses the derived label.
- Positional `universe_snapshots` inserts in tests now include the new column.
- Full suite: 734 passed, 0 failed. `ruff`, `mypy --strict src` clean.

**To make future snapshots COMPLETE:** run `vcp ingest security-master` every trading day, so ASM/GSM history accumulates. From the first collected date onward, snapshots whose 380-day bhavcopy window is complete are POINT_IN_TIME_COMPLETE. This belongs in the daily evening run (next item).

## Daily run with catch-up and per-day surveillance tracking (2026-10-01)

**Owner question:** what does a skipped evening lose?
- Prices, T2T (from series), corporate actions, adjusted prices, features and scans can all be recomputed later. A late scan is a reconstruction, not what was seen live.
- The ASM/GSM lists cannot be recovered, because NSE publishes only the current list.

**Change.**
- New `src/vcp_scanner/daily.py` and `vcp run daily`. They chain the existing commands in order:
  1. security master (collects the lists);
  2. bhavcopy from the day after the last OK/NO_SESSION file;
  3. corporate actions from 60 days before the first new session;
  4. adjusted prices;
  5. features;
  6. universe, RS and Trend Template for each OK session after the last Trend Template date (only the latest on a first run).
- A failed step fails the run (exit 1), but later steps still run. Today's file being unpublished is not a failure.
- One summary line per run goes to `logs/daily_runs.log` next to the database.
- Table `surveillance_collections` (PK collected_on + flag_type). `SecurityMasterIngestionWorker` records each list it collected, including empty ones (`NSESurveillanceProvider.collected_flag_types`). The table is seeded once from existing flag rows.
- `survivorship_evidence` / `derive_survivorship`: ASM and GSM must be collected on the as-of date itself, otherwise "<list> list not collected on <date>".

**Tests.** New `tests/unit/test_daily_run.py` (4): first run scans only the latest session; skipped evenings are all scanned; step order and the bhavcopy start date; a failed step fails the run while later steps still run. Also an sm_worker collection test and the point-in-time tests for skipped evenings. Full suite: 740 passed. `ruff`, `mypy --strict src` clean.

**First real run** (main DB, 2026-10-01 13:57–14:05 IST, exit 0): collected the ASM, GSM and T2T lists; today's bhavcopy was not yet published (PENDING, normal); no new session to scan.
- **Scheduled** (owner approved): Windows Task Scheduler tasks "VCP daily run" (Mon–Fri 19:15 IST) and "VCP daily run late" (Mon–Fri 22:00, safety net for late NSE publication).
  - Both run `wsl.exe -d Ubuntu -u ubuntu -- bash -lc ~/projects/vcp_ai/scripts/daily_run.sh`.
  - Settings: run as soon as possible after a missed start; 2 h limit; a flock in the script prevents two runs at once.
  - Output goes to `data/logs/daily_run_<date>.out`; the summary goes to `data/logs/daily_runs.log`.
  - Verified by starting the late task manually; it launched `vcp run daily` in WSL.

## Step 2.7 — unblocking the 44 listed stocks held back by unexplained gaps (2026-10-01)

**Spike (read-only, 16:00–16:25 IST).** The 50 blocking `UNEXPLAINED_GAP` events on 44 active instruments in the main DB, checked against NSE's feeds (`index=equities` and `index=sme`, by date window and by symbol), the raw bhavcopy and the stored actions:

| Group | Events | Cause |
|---|---|---|
| A | 34 | SME bonus/split listed in `index=sme`, which we never fetched. 8 of them have no bar on the ex-date (illiquid SM stocks). |
| B | 7 | Action stored and prices correctly adjusted (DOLPHIN, DRCSYSTEMS, ESSENTIA, KEEPLEARN, TIL, UEL ×2), but no bar on the ex-date and `GapDetector` only accepted an ex-date equal to the gap bar's date. |
| C | 3 | In no NSE feed: DTIL 2021-08-05 bonus 1:2; GICL 2025-10-15 split Rs 10 → Rs 5 plus bonus 1:1; JSLL 2025-06-12 split Rs 10 → Rs 2 (record-date notices on nsearchives). |
| D | 1 | SETCO 2026-06-02: interim dividend Rs 13 on a Rs 28.62 share; the gap is genuine. |
| E | 5 | Long absences from NSE, no action in between (P1-2): BESTAGRO 259 days, GOODYEAR and GRAUWEIL 908, WATERBASE 1,027, LANCER 1,207. |

Consolidations and capital reductions caused none of the 44. The whole equities feed 2021–2026 has four such records: VERTOZ (consolidation Re 1 → Rs 10) and EASTSILK, MAXIND, MELSTAR (capital reduction, no ratio). Of the 41 open gaps of +100 % or more, only VERTOZ and EASTSILK have a feed record; most of the rest follow trading absences of 26–1,735 days (relistings, often after a resolution plan; P1-2).

**Owner decision:** fix A–C (SME feed, ex-date window with a residual check, consolidation parsing, a manual-override file); SETCO is closed as genuine with `vcp quality resolve`; E stays for P1-2.

### Fix 2.7a — NSE SME corporate-action feed

**Change.** `NSECorporateActionProvider` fetches `index=equities` and `index=sme` (`INDEXES`) with the same date window and merges them; a record listed in both (an SME stock that migrated) is kept once by its deterministic ID. A failure or a non-list answer from either feed raises `ProviderError`, naming the feed. The SME feed's `isin` field holds an internal number ("341033" for KSOLVES), so only values shaped like an ISIN are kept; otherwise the action resolves by symbol.

**Real-data check.** 2021-01-01..2026-09-30: `sme` has 907 records; the current parser reads 97 bonuses, 16 splits, 36 rights and 2 demergers from them, and fails only on two "RIGHTS 1:1" with no premium (reported as unparsed, as for the main board).

**Tests.** New `tests/unit/test_nse_sme_feed.py` (6), using real records (KSOLVES "BONUS 3:1/DIVIDEND", VCL split, DOLPHIN split): both feeds asked and merged with the right ratios; SME number not stored as ISIN; duplicate kept once; failure of either feed raises; non-list answer raises.

**Verification.** Full suite: 746 passed, 0 failed (was 740). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix 2.7b — share consolidations as reverse splits

**Change.** `NSECorporateActionProvider._parse_nse_action` reads a record mentioning a consolidation of shares as a SPLIT; `parse_ratio` already takes the two face values in order, so "From Re 1 Per Share To Rs 10 Per Share" gives (1, 10) and the adjustment engine's split formula gives price factor 10 and volume factor 0.1. A consolidation without two face values is kept and reported as an unparsed ratio. "Capital Reduction" (MAXIND, MELSTAR, EASTSILK) stays in `_UNHANDLED_MARKERS`: NSE's text has no ratio, so these need a manual override (2.7d).

**Real data.** One consolidation in NSE's equities feed 2021–2026: VERTOZ, ex-date 2025-06-25. Its open gap (+950 % on 2025-07-11, first bar after the ex-date) is explained once the detector looks past non-trading ex-dates (2.7c): 9.17 × 10 = 91.7 against an open of 96.28.

**Tests.** New `tests/unit/test_consolidation.py` (6) with the real VERTOZ, MAXIND and EASTSILK records: consolidation parsed as SPLIT (1, 10); engine factor (10, 0.1); consolidation without face values reported unparsed; capital reductions unhandled and reported; `parse_ratio` on the upper-case text.

**Verification.** Full suite: 752 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix 2.7c — gap safety net: ex-date window with a size check

**Owner decision (2026-10-01):** accept an ex-date between the bars, but only when the action accounts for the gap's size.

**Change.** `GapDetector.detect` no longer looks up the gap bar's date in the set of ex-dates. For each gap of at least `gap_pct` it takes the applied actions (`explains_price_gap`) with `previous bar < ex_date <= gap bar` and computes the residual `open / (previous close × F) − 1`:
- SPLIT/BONUS: `F` from the ratio, using `AdjustmentEngine.single_factor` so the detector and the adjusted prices agree (a consolidation's 10 included);
- RIGHTS: `F` = TERP factor (`derived_factor`) with the previous close as prior close;
- DEMERGER, or RIGHTS without an issue price: no factor can be computed from these two bars, so the gap counts as explained as before; the worker's `factor_unknown` event blocks the symbol when the adjustment engine cannot derive the factor either.

The gap is explained when `|residual| < gap_pct`. Otherwise the event carries `residual_gap_pct_after_actions`. Behaviour change on the ex-date itself: a split/bonus whose ratio does not fit the jump is now flagged (before, any applied split/bonus on that date silenced it).

**Why.** Seven of the 50 blocking events (DOLPHIN, DRCSYSTEMS, ESSENTIA, KEEPLEARN, TIL, UEL ×2) had correct actions and correctly adjusted prices; the stocks just did not trade on the ex-date. Eight of the SME events from 2.7a are the same case.

**Tests.** New `tests/unit/test_gap_window.py` (10) on real bhavcopy prices: DOLPHIN split, UEL bonus, TIL rights (TERP), VERTOZ consolidation and HECPROJECT bonus explained across non-trading ex-dates and flagged without the action; GOODYEAR's 908-day absence stays flagged with an unrelated 10:1 split inside it (residual +528 %); a misfitting ratio on the ex-date is flagged; an ex-date on the previous bar is outside the window; GICL's same-day split and bonus combine (the split alone leaves −47 %); a demerger without a derivable factor defers to its own event. Existing gap-detector and golden tests unchanged and passing.
- `test_quality_events.test_gap_is_explained_by_an_applied_split_or_bonus` used a 2:1 ratio for the bonus too, which predicts a third of the price, not half; it now uses bonus 1:1 and also asserts that the misfitting bonus 2:1 is flagged.

**Verification.** Full suite: 762 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix 2.7d — manual corporate-action overrides

**Owner decision (2026-10-01):** a version-controlled file in the repo.

**Change.**
- `config/manual_corporate_actions.yaml` and `data/providers/manual_ca.py`: `load_manual_entries` validates every entry with pydantic (symbol; optional ISIN of ISIN shape; type; ex-date; ratio > 0, split face values different; issue price for rights; evidence of at least 10 characters; `approved_by`; `entered_on`; no unknown fields; no duplicate symbol/type/ex-date). A missing file means no overrides; an invalid one raises `ConfigError`, which aborts `vcp ingest corporate-actions`.
- `ManualCorporateActionProvider` serves every entry on every run (an override added today for an old ex-date must reach the next daily run, which only asks for 60 days) as raw actions with source `MANUAL`, deterministic IDs and the evidence in `source_record_id`. It honours the instrument filter.
- `ReconciliationEngine`: a `MANUAL` action in a (type, ex-date) group gives `MANUAL_OVERRIDE` with the manual values; the feed records stay linked (`nse_action_id`, `upstox_action_id`).
- `CorporateActionIngestionWorker(manual_provider=…)`, wired in `run_corporate_actions` from `<config-dir>/manual_corporate_actions.yaml`; the CLI prints how many were applied.

**First entries** (all three checked against the notice and the bhavcopy):
- DTIL bonus 1:2, ex 2021-08-05 (board approval 2021-06-29; 521.15 × 2/3 = 347.4, open 330.00);
- GICL split Rs 10 → Rs 5 and bonus 1:1, ex 2025-10-15 (NSE notice GICL_24092025171948; ISIN change that day; 173.70 / 4 = 43.4, open 46.00);
- JSLL split Rs 10 → Rs 2, ex 2025-06-12 (NSE notice JEENASIKHO_24052025183909; ISIN change that day; 2250.95 / 5 = 450.2, open 460.00).

The notices give record dates; the ex-dates are the bhavcopy dates of the jump and the ISIN change (GICL's notice names 17 October, but the shares traded split from 15 October).

**Tests.** New `tests/unit/test_manual_corporate_actions.py` (15): the repository file's four entries; missing file; six invalid-entry cases, rights without issue price and duplicates raise; the provider serves entries outside the window with deterministic IDs and honours the filter; manual alone and manual against two disagreeing feeds give `MANUAL_OVERRIDE` with the manual ratio; end to end through the worker on DuckDB, JSLL gets price factor 0.2 from 2025-06-12.

**Verification.** Full suite: 777 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Follow-up 2.7c — a demerger never explains a gap up (small fix)

**Found in the rebuild:** UEL's +250 % gap on 2024-05-27 (non-blocking, after a 6-day absence) was closed because the 2024-05-22 demerger fell in its window and 2.7c defers demergers. A demerger or rights issue only ever lowers the price, so the deferral now applies only to gaps down; for a gap up the action counts with factor 1.0 and the residual is the whole gap. UEL stays blocked by its own `factor_unknown` event for that demerger (no bar on the ex-date), as before 2.7.

**Test.** `test_demerger_never_explains_a_gap_up` (UEL prices). Full suite: 778 passed, 0 failed; `ruff`, `mypy --strict src` clean.

### Real-data check of 2.7a–d (copy `data/fix27.duckdb`, 17:03–17:20 IST)

Copy of the main DB, then `vcp ingest corporate-actions --start 2021-01-01` (3.0 min, NSE only: Upstox token expired), adjusted prices (2.9 min), features (2.8 min), universe, RS and Trend Template for 2026-09-30 (2.4 min). Then the follow-up detector, `vcp quality scan`, and SETCO's gap closed as genuine with `vcp quality resolve` (owner decision), and universe/RS/Trend Template again.

| | Main DB | Copy |
|---|---|---|
| New raw actions | | 116 (111 SME, VERTOZ consolidation, 4 manual) |
| Unexplained gaps (all instruments) | 182 | 137 |
| Blocking gap events on active instruments | 50 on 44 | 5 on 5 |
| Instruments blocked by any cause (all) | 136 | 92 |
| Eligible 2026-09-30 | 1,334 | 1,343 |
| Trend Template PASS | 265 | 267 |

- **38 of the 44 are unblocked.** No instrument is newly blocked.
- **Still blocked (6):** the five long absences (BESTAGRO, GOODYEAR, GRAUWEIL, LANCER, WATERBASE; P1-2), and UEL. UEL's two gap events are resolved, but it was already blocked by a separate `CORPORATE_ACTION_UNRESOLVED` event (demerger 2024-05-22, `factor_unknown`: no bar on the ex-date), which 2.7 does not touch.
- **Newly eligible (9):** DCI, DOLPHIN, JSLL, NPST, TEMBO, THEJO, VIVIANA, WEL (unblocked by 2.7), and KABRAEXTRU, which comes from the P0-4 point-in-time series (the main DB's snapshot predates that fix), not from 2.7.
- **Trend Template:** new passes DOLPHIN, KABRAEXTRU, NPST, THEJO; ASIANHOTNR and UTTAMSUGAR drop out because their RS rank moves from 70 to 69 against the larger eligible set.
- SETCO is unblocked but stays ineligible (series BE).

### Applied to the main DB (owner approved; 17:26–17:37 IST, code at 2f1838a)

Backup first: `data/vcp_scanner.pre27.duckdb`. The run held the daily-run lock. Steps as on the copy: corporate actions from 2021-01-01 (3.0 min, NSE only), SETCO's gap closed as genuine (`vcp quality resolve`, by Ajay, note with the dividend arithmetic), adjusted prices, features, universe, RS and Trend Template for 2026-09-30.

Result, identical to the copy: 137 unexplained gaps, 92 instruments blocked by any cause (was 136), blocking gap events on active instruments 5 on 5 (was 50 on 44); 1,343 eligible; Trend Template 267 PASS, 1,063 FAIL, 13 INSUFFICIENT_DATA. **38 of the 44 unblocked**, none newly blocked. Still blocked: BESTAGRO, GOODYEAR, GRAUWEIL, LANCER, WATERBASE (long absences, P1-2) and UEL (demerger 2024-05-22 `factor_unknown`, pre-existing).

**Open after 2.7:** P1-2 long-absence gaps (5 active names; also most of the 41 gaps of +100 % or more); UEL's demerger factor (no bar on the ex-date); capital reductions (MAXIND, EASTSILK, MELSTAR) need manual entries with a ratio once the scheme documents are checked.

## Audit P1-2 — blocks that end, and absences that start a new history (2026-10-01)

**Spike (read-only, 17:45–18:00 IST).** Two separate problems:
1. **Blocks never end.** A blocking event applies from its date to every later as-of date. Six active stocks were blocked by events more than 253 bars old, outside every lookback: BRITANNIA (2021), PATINTLOG (2021), SIGMAADV (2021), TFL (2022), UEL (2024), KESORAMIND (2025).
2. **Feature windows bridge trading absences.** Every window counts rows, so a stock off NSE for 2½ years gets an SMA200 mixing 2023 and 2026 prices. 24 active stocks have an absence of 20+ missed sessions inside their last 253 bars; 7 were eligible on 2026-09-30 (KENNAMET, KIRLFER, KOVAI, NOVARTIND, PIRAMALFIN, SHIVAUM, WAAREEINDO), and KENNAMET and SHIVAUM passed the Trend Template on those windows. The count is the same for thresholds from 20 to 120 sessions.
   - 12 main-board names left the NSE bhavcopy after 2023-10-25 (FORCEMOT, GOODYEAR, GRAUWEIL, KENNAMET, KIRLFER, KOVAI, NOVARTIND, UDAICEMENT, WATERBASE …); FORCEMOT returned 2024-02-14, several on 2026-04-20. They look like BSE-listed names that stopped trading on NSE and later listed there directly (not verified from an official source). They kept trading elsewhere, so an action during the absence never reaches NSE's feed, and the return jump is not evidence of anything.
   - In 16 of 36 returns after 20+ missed sessions, NSE's PREVCLOSE on the return day is a new reference price rather than our last close.

**Owner decisions:** D1 an absence of ≥ 20 missed NSE sessions starts a new history; D2 a price-history block lasts 253 of the stock's bars (the longest lookback, from config); fix the debenture-bonus parse.

### Fix P1-2a — a bonus of debentures is not a share bonus

`_parse_nse_action` no longer takes a BONUS text that mentions debentures; `DEBENTURE` joins `_UNHANDLED_MARKERS`, so BRITANNIA's 2021 "Scheme Of Arangement- Bonus - 1 Debenture For 1 Equity Share Held" is reported as unhandled. (The misspelt "ARANGEMENT" slipped past the `ARRANGEMENT` marker.) The raw BONUS row already stored stays, since raw data is immutable; its block expires under P1-2b.

**Test.** `test_bonus_of_debentures_is_not_a_share_bonus` with the real record.

### Fix P1-2b — a block ends once the bad bar has left every lookback (D2)

**Change.**
- `QualityGateConfig` (`data.quality` in `config/data.yaml`): `block_lifetime_bars` = 253 and `absence_min_missed_sessions` = 20 (used by P1-2c). `ScannerConfig.longest_lookback_bars()` = max of RS and universe history minimums, RS's longest window + 1, 252 (52-week extremes), 200 + the SMA200 slope lookback, and (stage SMA + slope weeks) × 5; today 253. A lifetime below it fails config loading.
- `DuckDBDataQualityRepository(block_lifetime_bars=…)`: `blocked_instruments` counts the instrument's bars in `[trade_date, as_of]` (as known at `known_at` when given) and drops a dated event once that count reaches the lifetime. Undated events and the `None` default keep the old "forever" behaviour.
- Wired into every gate: `_quality_gate` (RS, Trend Template) and `vcp ingest universe`.
- DATA_SPECIFICATION 18A and DATABASE_SCHEMA 19.1 state the rule; the spec notes that Phase 6 must stay within the lifetime.

**Why 253 bars and "≥":** with 253 bars in `[event, as-of]` the longest window (253 bars ending at the as-of bar) starts at the event bar itself, so it never contains both sides of the jump.

**Tests.** New `tests/unit/test_block_lifetime.py` (6): the block ends exactly at the lifetime and still applies to earlier as-of dates; weekends do not count; no lifetime = forever; undated events never expire; point-in-time bars; config default equals the longest lookback, `None` allowed, 200 refused.

**Verification.** Full suite: 785 passed, 0 failed (this run also covers P1-2a). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix P1-2c — a trading absence starts a new history (D1)

**Change.**
- New flag `DataQualityFlag.TRADING_ABSENCE` and `data/quality/absence.py`: for each pair of consecutive bars, the NSE sessions strictly between them (settled bhavcopy days, `DuckDBMarketDataRepository.load_market_sessions`) are counted; at `data.quality.absence_min_missed_sessions` (20) or more, a blocking HIGH event is raised on the return bar with the missed-session count, both dates and the jump. Holidays never count; without a session calendar (no bhavcopy loaded) the check is skipped.
- `QualityScanner(sessions=…, absence_min_missed_sessions=…)` raises and syncs these events, and turns an `UNEXPLAINED_GAP` across the same two bars into a non-blocking warning (`after_trading_absence_sessions` in its context): the stock traded elsewhere or was suspended, and actions during the absence never reach NSE's feed, so the jump is not evidence of a missed action. Both CLI paths (`ingest corporate-actions`, `quality scan`) pass the calendar; the summaries print the count.
- The block ends through P1-2b: once the stock has 253 bars after its return, no window reaches back across the absence. The approved wording was "INSUFFICIENT_DATA until 253 bars"; it is implemented as a data-quality block (Trend Template status `DATA_QUALITY_BLOCKED`, flag `TRADING_ABSENCE`; universe reason "Data quality blocked") so that it reuses the gate, is point-in-time and needs no change to the feature SQL. The features themselves are still computed across the absence but are not used while the block applies.
- DATA_SPECIFICATION 18A and DATABASE_SCHEMA 19.1 updated.

**Tests.** New `tests/unit/test_trading_absence.py` (8): session counting; threshold 19 vs 20; holidays are not missed sessions; end to end with GOODYEAR's real prices (absence event on 2026-04-20, the −37 % gap kept as a warning, gate blocks until the lifetime's worth of bars); no calendar = old behaviour; sessions from settled, de-duplicated bhavcopy days.

**Verification.** Full suite: 792 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Real-data check of P1-2a–c (copy `data/p12.duckdb` of the main DB, 18:13–18:18 IST)

`vcp quality scan`, then universe, RS and Trend Template for 2026-09-30, with the P1-2 code (corporate actions not re-ingested: P1-2a only changes how new records are read).
- `quality scan`: 549 `TRADING_ABSENCE` events over the whole history (most on inactive or illiquid SME names, and most already expired by 2026-09-30); unexplained gaps unchanged at 137.
- Gate on 2026-09-30, active instruments: blocked 13 → 26.
  - **Unblocked (7):** BESTAGRO (returned 613 bars ago), BRITANNIA, KESORAMIND, PATINTLOG, SIGMAADV, TFL, UEL (events older than 253 bars).
  - **Newly blocked (20), all `TRADING_ABSENCE`:** AHLWEST, ANDREWYU, ARIHANT, BURNPUR, EASTSILK, EUROTEXIND, KALYANI, KENNAMET, KIRLFER, KOVAI, MBECL, NEUEON, NOVARTIND, PIRAMALFIN, SHIVAUM, SICAGEN, SWANDEF, VISHAL, VIVIMEDLAB, WAAREEINDO.
  - **Still blocked (6):** GOODYEAR, GRAUWEIL, LANCER, WATERBASE (now by `TRADING_ABSENCE`; their return gaps are warnings), DALMIASUG and QUINT (corporate actions without a factor, under 253 bars old).
- Eligible 1,343 → 1,337: out KENNAMET, KIRLFER, KOVAI, NOVARTIND, PIRAMALFIN, SHIVAUM, WAAREEINDO; in BRITANNIA.
- Trend Template 267 PASS either way: KENNAMET and SHIVAUM drop out (their windows spanned the absence); ASIANHOTNR and UTTAMSUGAR return (RS rank back to 70 against the smaller population).

### Applied to the main DB (owner approved; 18:19–18:23 IST, code at e7918b5)

`p12-blocks` fast-forwarded into `audit-fixes`. Backup `data/vcp_scanner.pre_p12.duckdb` (replaces `pre27`); the run held the daily-run lock. `vcp quality scan`, universe, RS and Trend Template for 2026-09-30. Results identical to the copy: 549 `TRADING_ABSENCE` events, 1,337 eligible, Trend Template 267 PASS / 1,057 FAIL / 13 INSUFFICIENT_DATA; blocked active instruments 26 (7 unblocked, 20 newly blocked by `TRADING_ABSENCE`, 6 still blocked).

**Still open:** DALMIASUG and QUINT (corporate actions without a factor; they expire 253 bars after their ex-dates unless fixed earlier); UEL-style demerger factors when the stock did not trade on the ex-date; capital reductions (MAXIND, EASTSILK, MELSTAR) need manual entries.

## Audit P1-8 — scan records and reproducibility (2026-10-01)

**Spike (read-only, 18:35–18:50 IST).** On a copy of the main DB a data snapshot was frozen at 2026-10-01 13:00 UTC and the 2026-09-30 universe, RS and Trend Template were rerun under it, twice. RS and all 1,337 Trend Template rows were identical (content hash) to the LIVE run of 18:23 IST, and the second frozen run matched the first: the computations are deterministic and the bitemporal inputs are complete enough to rebuild a past state. What is missing is lineage:
1. no record of a scan run: the TT results do not say which universe snapshot they used, the stored code version is the constant "0.1.0", and nothing keeps counts, times or a result hash; reproducing an old scan also needs the code that made it;
2. universe snapshot ids are random (`uuid4`), so every rebuild adds one (four for 2026-09-30), and Trend Template has no way to choose one: it takes the newest by `created_at`, so a rebuild "as known at" an earlier time would silently use a later live universe (the spike only worked because its cutoff was later than the live snapshot);
3. the TT scan id hashes the whole config, so a log-level change gives a new id (`a761043093fe` → `89c7ed5e2b8f`), while the universe hash leaves out `data.quality`, which changes eligibility;
4. rerunning a LIVE scan with the same date and config overwrites it, so what was seen on the evening is lost;
5. one frozen snapshot costs about 0.9 GB (adjusted prices ~0.1 GB, features ~0.8 GB), too much to keep every evening.

**Owner decisions (D1–D4):** an immutable `scan_runs` record per scan; deterministic universe ids and an explicit universe for TT; per-section config hashes; reproduce on demand (`vcp verify scan`), freezing only scans worth keeping.

### Fix P1-8a — per-section config hashes (D3)

**Change.** `config.loader.section_config_hashes` (strategy, universe, gate = `data.quality`) and `scan_config_hash` (hash of those three). `run_compute_trend_template` uses `scan_config_hash` for the scan id and stored `config_hash`. `UniverseBuilder(gate_settings=…)` folds the gate settings into the snapshot's config hash; `vcp ingest universe` passes them. `vcp config validate` prints the scan hash and the section hashes; `vcp config hash` still prints the full-config hash.

**Tests.** New `tests/unit/test_scan_config_hash.py` (4): the three sections; logging, monitoring and storage paths do not change the scan hash; a strategy, universe or gate change does; the universe hash includes gate settings when gated. Full suite: 796 passed, 0 failed; `ruff`, `mypy --strict src` clean.

### Fix P1-8b — deterministic universe ids, an explicit universe for Trend Template (D2)

**Change.**
- `universe.builder.universe_snapshot_id(as_of, known_at, config_hash, method_version)`: `uv_YYYYMMDD_` + the first 10 hex of a SHA-256 over those inputs (cutoff normalised to UTC, microseconds). `UniverseBuilder.build_snapshot` uses it instead of `uuid4`.
- `DuckDBUniverseRepository.save_snapshot` replaces a stored snapshot with the same id (same inputs) instead of failing or forking; new `latest_snapshot_id` and `snapshot_as_of`; `load_snapshot(as_of, snapshot_id=None)` loads a named snapshot and refuses one from another date.
- `vcp compute trend-template --universe-snapshot-id`; without it the latest snapshot for the date is used, as before, and its id is printed. `DuckDBTrendRepository(rs_universe_snapshot_id=…)` makes the Trend Template read RS ranked over exactly that universe; before, it took the RS row of whichever universe was created last.

**Tests.** New `tests/unit/test_universe_snapshot_ids.py` (4): the id is a function of its inputs (same instant in IST and UTC gives the same id; each input changes it); a rebuild with the same cutoff replaces, a later cutoff adds; load by id vs latest, wrong date refused; the trend repository reads RS of the named universe while "latest" would read another. Full suite: 800 passed, 0 failed; `ruff`, `mypy --strict src` clean.

### Fix P1-8c — immutable scan-run records (D1)

**Change.**
- New tables `scan_runs` and `scan_run_results` (DATABASE_SCHEMA 40.1) and `data/repositories/duckdb_scan_run_repository.py`: `ScanRun`, `verdict_row`, `results_hash` (SHA-256 over sorted verdict rows, RS rank rounded to 6 decimals), `DuckDBScanRunRepository.record/load/load_results/list_runs`. A run id is inserted once; a duplicate fails on the primary key.
- `versioning.code_state()`: the git commit and whether tracked files have uncommitted changes; `("unknown", None)` outside a checkout.
- `run_compute_trend_template` records a run after saving its results: data cutoff = start time for LIVE, the snapshot's `known_at` for a frozen snapshot; universe id and `created_at`; survivorship from the universe snapshot; `scan_config_hash` and the section hashes; `version_manifest()`; counts by status; `results_hash`. It prints the run id, commit and hash. The daily run gets a record for every scanned date with no change to `daily.py`.

**Tests.** New `tests/unit/test_scan_runs.py` (3): the hash ignores row order and float noise but not verdicts; record/load round trip and no overwrite; `code_state` returns a commit or `unknown` when git fails. The end-to-end pipeline test now reruns the Trend Template and checks two run records with the same hash, recorded fields, and that the hash equals the hash of both `scan_run_results` and `trend_template_results`.

**Verification.** Full suite: 803 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix P1-8d — `vcp verify scan`: reproduce on demand (D4)

**Change.** New `verify_scan.py` and the `vcp verify scan [RUN_ID] [--work-dir] [--keep] [--limit]` command:
1. load the run record and its verdicts from the main DB (read only), and warn when the running code commit (or a dirty checkout) differs from the run's;
2. copy the DB to the work dir;
3. on the copy: for a LIVE run, `ingest adjusted-prices --known-at <data_cutoff>` and `compute features` under that snapshot; `ingest universe --known-at <universe_cutoff>` (P1-8b gives the same id; a different id is reported, as for runs recorded before P1-8b); RS and Trend Template with `--universe-snapshot-id` and `--data-snapshot-id`;
4. compare the rebuilt run's `results_hash`: exit 0 on a match, 2 on a mismatch with up to 20 differing instruments, 1 on a failed step. The copy is deleted unless `--keep` (a frozen scan worth keeping).

Without an id it lists recent runs (id, data and universe snapshots, commit, dirty flag, hash).

**Tests.** The end-to-end pipeline test verifies its first run: exit 0 and "MATCH: 6 verdicts", the work dir is emptied, the main DB still has two runs; with the recorded hash altered the command exits 2 and reports the mismatch; the listing shows the run.

**Verification.** Full suite: 803 passed, 0 failed (the new checks extend the existing end-to-end test). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Real-data check of P1-8 on the main DB (19:28–19:42 IST, code at 5938668)

`p18-scan-runs` fast-forwarded into `main` after the evening run (19:15–19:27 IST, old code, "all steps OK", scanned 2026-10-01: 1,256 eligible, 203 PASS; the first POINT_IN_TIME_COMPLETE snapshot, since ASM/GSM were collected today, and 81 names excluded by those lists).
- With the new code, under the daily-run lock: universe for 2026-10-01 → `uv_20261001_e7fa4e2d1c` (deterministic), RS, Trend Template → scan run `run-20261001-20261001T135918870959Z`, scan id `trend-2026-10-01-4c3375441181` (section hashes), 1,256 evaluated, 203 PASS / 1,047 FAIL / 6 INSUFFICIENT_DATA (same as the evening run), commit `5938668`, clean checkout, results hash `40c25da8763380b1`.
- `vcp verify scan run-20261001-20261001T135918870959Z`: on a copy, froze the data at the run's cutoff (`snap-20261001T135918Z`), rebuilt features, the universe as known at its cutoff (**same id** `uv_20261001_e7fa4e2d1c`), RS and the Trend Template: **MATCH, 1,256 verdicts, hash `40c25da8763380b1`**. The copy (peak ~2.4 GB) was deleted; 10.8 minutes in all.

## Clean-up batch (2026-10-01, after P1-8)

### Fix C1 — demerger factor edge rules and hand-entered demerger factors

**Finding.** The plan assumed the blocked demergers had no bar on the ex-date. On real data only UEL did; the others did trade:
- KESORAMIND 2025-03-10 (cement business to UltraTech): open 10.23 over prior close 204.72 = 0.04997, rejected by the 0.05 plausibility floor;
- DALMIASUG 2025-10-31: open exactly at the prior close, 346.20, so "open not below prior close" made the factor unknown;
- UEL 2024-05-22: no bar on the ex-date and a +250 % jump on the next one, so nothing is derivable from prices;
- (KMSUGAR 2026-10-01 derived normally, 27.10 / 33.68 = 0.805.)

**Owner decision:** lower the floor to 0.02; an open at or up to 1 % above the prior close is factor 1.0; let the manual file carry a demerger price factor.

**Change.**
- `domain.corporate_actions`: `_MIN_DEMERGER_FACTOR` 0.05 → 0.02; `_DEMERGER_NO_CHANGE_BAND` = 1 %; `derived_factor` returns (1.0, 1.0) for an open within the band, and for a DEMERGER resolution with a usable ratio returns the hand-entered factor `num/den` (0 < f ≤ 1) before looking at prices.
- `manual_ca.ManualActionEntry.price_factor` (DEMERGER only, 0 < f ≤ 1; a DEMERGER entry needs it and no ratio), stored as the ratio `f:1`.
- The gap detector uses `derived_factor` for demergers too, so a hand-entered factor counts in the residual.
- DATA_SPECIFICATION (rights and demergers) updated.

**Tests.** New `tests/unit/test_demerger_rules.py` (10): KESORAMIND's real factor accepted; DALMIASUG's open at the prior close and +0.8 % give 1.0, +1.1 % stays unknown; a hand-entered factor wins without an ex-date bar; manual DEMERGER validation (ratio, cash or a factor above 1 refused; `price_factor` refused on other types); end to end through the worker, a manual factor 0.9 is stored for a demerger with no ex-date bar. `test_derived_factors` cases updated to the new edges (open 2 % above close; factor 0.01).

**Verification.** Full suite: 813 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix C2 — audit P1-10: unmodelled NSE actions become warning events

**Finding.** Unparsed split/bonus ratios and underivable rights/demerger factors were already surfaced (blocking `ratio_unknown` / `factor_unknown` events). What vanished was every price-affecting record the parser does not model: it returned `None` with only a log line. Also, rights in non-equity securities (QUINT 2026-08-25, "Rights - 7 Ccps And 7 Warrants:40") were read as an equity rights issue without a ratio and blocked the stock.

**Change.**
- `CorporateActionType.UNMODELLED` and `DataQualityFlag.CORPORATE_ACTION_UNMODELLED`.
- `NSECorporateActionProvider._parse_nse_action`: a record with an unhandled marker (capital reduction, amalgamation, merger, arrangement, debenture) or rights in CCPS/warrants/debentures/NCDs/preference shares (`_NON_EQUITY_RE`) is returned as an `UNMODELLED` action with the record text in `source_record_id`; it is still listed in `unhandled_records` for the CLI warning.
- `quality.events.unmodelled_action_events`: one non-blocking WARNING per `UNMODELLED` resolution and ex-date, unless a `MANUAL_OVERRIDE` resolution exists on that ex-date. Synced by the corporate-action worker and `QualityScanner` (`unmodelled_events` in the scan summary; `vcp quality scan` prints it). Not in `_SUPERSEDE_ONLY`, so `vcp quality resolve` can close it.
- The engine ignores the type (not price-affecting); reconciliation keeps it SINGLE_SOURCE.
- DATA_SPECIFICATION updated. P1-10's second part (small bonuses under the 30 % gap threshold) is not addressed here.

**Tests.** New `tests/unit/test_unmodelled_actions.py` (3, real records): EASTSILK's capital reduction kept as UNMODELLED with its text; QUINT's CCPS/warrant rights are UNMODELLED while a real equity rights record still parses as RIGHTS; the warning appears and clears once a manual action covers the date. `test_consolidation` updated (capital reductions and BRITANNIA's debenture bonus are now UNMODELLED actions instead of `None`).

**Verification.** Full suite: 816 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix C3 — DHFL's delisting record (same issuer, later ISIN)

**Finding.** `sm_worker._accept_delisted` skipped DHFL's delisting (INE202B01012, 2021-09-29) on every run: "id held by a live security (ISIN INE202B01038)". The live holder is PIRAMALFIN, the same issuer (Piramal's housing-finance company, the former DHFL) listed again in 2025 under a later ISIN; its 2021 history is DHFL's bars (P1-2 marked the 1,093-session absence).

**Change.** A delisting whose ISIN is the same issuer's equity as the live holder (`same_issuer_equity`) is accepted, as a relisting under the same ISIN already was; the database check for another live row ignores same-issuer ISINs the same way. A different issuer reusing the symbol is still refused (existing tests).

**Note.** The main DB also holds an older row for DHFL's delisting under the legacy id `NSE_EQ|DHFL`, written before the step 2.6 identity fix; it stays (history is not rewritten). The delisting count used for the survivorship evidence only tests for the presence of delisting data, so the second row changes no label.

**Test.** `test_same_issuer_relisted_under_a_later_isin_keeps_both_periods` (real ISINs): both periods stored, the live one not marked delisted.

**Verification.** Full suite: 817 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix C4 — same-day split/bonus with a demerger or rights issue; superseded readings

**Found in the real-data check of C1–C3** (copy `data/c1.duckdb`, 20:12–20:25 IST):
1. AHLEAST gained a blocking gap on 2022-10-06 once C1 let the gap detector compute demerger factors. The cause was older: that day has a demerger **and** a 1:2 bonus, and both the adjustment engine and (now) the detector derived the demerger factor from the unscaled prior close, so the bonus was counted twice. The stored factor was 0.352 (= 2/3 × 185/350.35) instead of 0.528 (= 185/350.35), and AHLEAST's adjusted series kept a +50 % jump (123.33 → 185.00). It is the only instrument in the main DB with a demerger or rights issue on the same day as a split or bonus.
2. QUINT and BRITANNIA kept their blocking `CORPORATE_ACTION_UNRESOLVED` events: their old ratio-less RIGHTS/BONUS readings stay stored (raw rows are immutable) next to the new UNMODELLED reading of the same record.

**Change.**
- `AdjustmentEngine.compute_factors`: per ex-date, ratio actions first, then rights/demergers derived with `rescale_prior_close(prices, pf)` (new in `domain.corporate_actions`: the prior close times the factor so far). `GapDetector._residual_gap` uses the same order.
- `corporate_action_events`: a ratio-less SPLIT/BONUS/RIGHTS (or an underivable RIGHTS/DEMERGER) on an ex-date that also has an UNMODELLED resolution raises no blocking event; the UNMODELLED warning covers that record.

**Tests.** In `test_demerger_rules.py`: AHLEAST's real prices give one factor 185/350.35 and volume factor 1.5; the gap detector raises nothing for that day. In `test_unmodelled_actions.py`: QUINT's old ratio-less RIGHTS blocks alone but not next to its UNMODELLED reading, which keeps one warning.

**Verification.** Full suite: 820 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Real-data check of C1–C4 (copy of the main DB, 20:12–20:42 IST)

Security master, corporate actions from 2021-01-01, adjusted prices, features, universe/RS/Trend Template for 2026-10-01, with the clean-up code.
- **DHFL:** `delisted_skipped` 1 → 0; its 2021 delisting is now also stored under PIRAMALFIN.
- **Events:** blocking `CORPORATE_ACTION_UNRESOLVED` 9 → 5 (DALMIASUG, KESORAMIND, QUINT and BRITANNIA cleared); 5 new `CORPORATE_ACTION_UNMODELLED` warnings (BRITANNIA 2021 debenture bonus, MAXIND 2022 and EASTSILK 2024 capital reductions, QUINT 2026 CCPS/warrant rights, SHAREINDIA 2023-02-28); KESORAMIND's 2025-03-10 factor 0.04997 is applied.
- **AHLEAST:** factor 0.52804 (was 0.35203); adjusted close 2022-10-04 185.00 against an ex-date open of 185.00 (was 123.33).
- **2026-10-01:** blocked active instruments 26 → 24 (DALMIASUG, QUINT unblocked; none newly blocked); eligible 1,256 → 1,257 (DALMIASUG); Trend Template 203 PASS either way.

### Applied to the main DB (20:43–20:56 IST, code at 0f5f9ae)

`cleanup-1` fast-forwarded into `main`. Backup `data/vcp_scanner.pre_c1.duckdb` (replaces `pre_p12`); the run held the daily-run lock. Security master, corporate actions from 2021-01-01, adjusted prices, features, universe/RS/Trend Template for 2026-10-01. Same result as the copy: universe `uv_20261001_95180a301f`, 1,257 evaluated, 203 PASS / 1,048 FAIL / 6 INSUFFICIENT_DATA, scan run `run-20261001-20261001T152358846215Z`, results hash `7769987bb8868a73` (identical to the copy's).

## Batch A — audit items Phase 6 relies on (2026-10-01, before Phase 6)

Read-only spike 21:00–21:15 IST; owner decisions 21:19 IST: (1) small bonuses: document and rely on a second source; (2) `volume_ratio_N` excludes the current bar, ATR and EMA documented; (3) one staleness rule in NSE sessions, tiered by use.

### Fix A1 — audit P1-10 second part: small actions below the gap threshold (documented, no code change)

**Spike** (main DB, NSE bhavcopy 2021-01-01 → 2026-10-01). 1,772 opening gaps of −12 % to −30 %; 1,468 within 1 % of a small-integer ratio. Removing market-wide days (2025-04-07 alone had 364) and gaps explained by actions in the window left 1,058; 134 sit exactly at a −5/−10/−20 % circuit and 855 are neither at a circuit nor a locked bar. A random 25 of those 855 had no NSE corporate-action record within ±10 days (SPANDANA, TARIL, CHENNPETRO …: earnings and news drops). 29 small gaps were explained by small bonuses the NSE feed does carry.

**Decision.** Keep `gap_pct` 30. A ratio test cannot tell a 1:4 bonus from a −20 % circuit, so a lower threshold (or a warning tier) would add about 850 false flags. The defence is a second source: the Upstox cross-check (owner to refresh the token) and, if still needed, a BSE feed.

**Change.** `DATA_SPECIFICATION.md` §18A "Known limitation: small actions below `gap_pct`", with the numbers above and the effect of a miss (an unadjusted drop under 30 %: a false contraction risk for VCP detection, not a false Trend Template pass).

### Fix A2 — audit P2-1: feature definitions (`features-1.2.0`)

**Found.** `volume_ratio_20/50` divided today's volume by an average that included today, so a spike diluted itself (a 5× day after flat volume read 4.17, not 5.0). Nothing reads the ratios yet; VCP volume dry-up and breakout volume (Phase 6) will. `ema_10/20/50` are never computed (NULL), and ATR's smoothing was documented only in the VCP spec.

**Change.**
- `volume_ratio_N` = volume(t) / mean of the N bars before t (windows `p_20`, `p_50`: `N PRECEDING AND 1 PRECEDING`); needs N prior bars. `volume_avg_N` still includes the current bar.
- Version `features-1.2.0` as one constant, `domain.features.FEATURES_CALCULATION_VERSION`, used by the feature engine and by the Trend Template's feature reads (was the literal "features-1.1.0" in both). After the deploy, `vcp compute features` writes 1.2.0 rows next to the old 1.1.0 rows; readers ask for 1.2.0.
- `DATA_SPECIFICATION.md` §39A "Feature Definitions" (windows, SMA, simple ATR(14), highs/lows, volume averages and ratios, returns, volatility; EMAs not computed); `DATABASE_SCHEMA.md` technical_features_daily note.

**Tests** (`test_daily_features.py`): the ratio equals volume over the mean of the 20 (50) prior bars, NULL until N prior bars exist; a 5× spike reads exactly 5.0; rows carry the current version.

**Verification.** Full suite: 823 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean. Real-data check: together with A3 (below).

### Fix A3 — audit P2-2: one staleness rule, in NSE sessions

**Found.** Three unrelated staleness rules: the spec (§26) proposed `max_market_staleness_trading_days: 1`; RS used `strategy.rs.max_staleness_days` = 4 **calendar** days; the universe used `universe.max_staleness_days` = 30 calendar days; the Trend Template needs a bar on the as-of date. Calendar days over-count long weekends and holidays and under-count nothing else, and the universe's 30 days let a stock that missed ~20 sessions stay eligible. Spike on 2026-10-01: all 1,257 eligible names had 0 missed sessions; the stale-excluded names had 22 to 1,419.

**Change** (owner decision 2026-10-01: tiered, in sessions).
- `data.quality.staleness` (`StalenessConfig`): `universe_max_missed_sessions` 5, `rs_max_missed_sessions` 1. Signals keep the fixed rule "a bar on the as-of session" (`DATA_NOT_READY`). The old `max_staleness_days` keys are removed (a config that still sets them is refused).
- Missed sessions = sessions in (last bar, as-of] (`domain.sessions.missed_sessions_since`). The calendar (`data.repositories.session_calendar.load_session_calendar`) is the settled-OK bhavcopy days known at the cutoff: the universe's `known_at`, and for RS the universe snapshot's `created_at`, so `vcp verify scan` counts against the calendar the run saw. Without any known bhavcopy it falls back to the dates of the raw bars known then.
- `UniverseBuilder` takes `staleness` (default: `gate_settings.staleness`), includes it in the snapshot config hash, and reports "Stale: last trade D, N NSE sessions before AS_OF (max M)". `RelativeStrengthEngine` takes `staleness`; `compute_rs_rows` takes the calendar.
- `DATA_SPECIFICATION.md` §26 rewritten; `config/data.yaml` gains the block.
- Universe snapshot ids and the scan config hash change once (new config content).

**Tests.** `test_staleness_sessions.py` (session arithmetic incl. holidays and weekends; calendar as known at a cutoff, NO_SESSION and future days excluded; fallback to bar dates; RS calendar as known when the universe was built; defaults; old keys refused); RS ranking: one missed session ranked, two not, a holiday not counted; universe: the limit comes from `data.quality.staleness`. The RS/universe golden record was re-recorded: only the stale member's exclusion text and the snapshot config hash changed (eligibility and every RS value identical).

**Verification.** Full suite: 830 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Real-data check of A2–A3 (copy `data/batcha.duckdb` of the main DB, 21:33–21:40 IST, code at 6dbebbf)

Features for all 3,365 instruments (3,123,370 rows written as `features-1.2.0` next to the 1.1.0 rows), universe, RS and Trend Template for 2026-10-01.
- **Features:** every column other than the volume ratios is identical between 1.1.0 and 1.2.0 (0 of 3,123,370 rows differ in SMA200, ATR14 or 252-day high). On 2026-10-01, `volume_ratio_20` is on average 1.2 % higher; the largest reads 150.5 (it was capped near 17.7, i.e. under N = 20, when the spike was in its own average).
- **Universe:** 1,257 eligible, the same set (none lost or gained). "Stale" exclusions 198 → 217: 19 names that missed 6 to 21 sessions are now stale, all of them already ineligible for another reason. New id `uv_20261001_e80803370f` (config content changed).
- **RS:** 1,257 PASS, 0 rank differences.
- **Trend Template:** 203 PASS / 1,048 FAIL / 6 INSUFFICIENT_DATA; results hash `7769987bb8868a73`, identical to the main DB's run.

### Applied to the main DB (owner approved; 21:41–21:48 IST, code at 52953f1)

`batch-a` fast-forwarded into `main` and pushed. Backup `data/vcp_scanner.pre_batcha.duckdb` (replaces `pre_c1`); the run held the daily-run lock. Features (all instruments, `features-1.2.0`), universe, RS and Trend Template for 2026-10-01. Same result as the copy: universe `uv_20261001_0385a05ca4`, 1,257 eligible, RS 1,257 PASS, Trend Template 203 PASS / 1,048 FAIL / 6 INSUFFICIENT_DATA, scan run `run-20261001-20261001T161603895333Z`, results hash `7769987bb8868a73` (unchanged).

## Fix U1 — Upstox rate limits failed the 22:00 corporate-actions step (2026-10-01/02)

**Found.** The 22:00 IST daily run on 2026-10-01 was the first with a valid Upstox token (refreshed 21:36). It sent 2,593 per-ISIN requests in about 8 minutes (~5.4/s, ~320/min); 30 instruments got HTTP 429 after three retries (first: CONCOR), and any failure aborted the whole step, so none of that run's corporate actions were saved ("FAILED: corporate actions"). Prices, features and the 19:15 scan were unaffected. Upstox documents 25/s, 250/min and 1,000 per 30 minutes per API.

**First attempt (commit 7854d49, not deployed):** 0.25 s pacing (240/min) and tolerating up to 5 % failures. Its live check on a copy (23:53–00:12 IST) stalled: Upstox now answered every request with HTTP 429 ("UDAPI10005 Too Many Request Sent", confirmed by a single probe at 00:12 and 00:13), i.e. the 30-minute limit is enforced, and urllib3's 429 retries with backoff would have stretched the run over hours. The copy run was stopped and the copy deleted.

**Change** (owner decision 2026-10-02 00:15 IST: budget + rotation).
- Pacing 1.9 s (~950 per 30 min).
- Per-run budget `data.corporate_actions.secondary_max_requests_per_run` = 800 (~25 min): instruments with an NSE split/bonus/rights/demerger in the window first, then the least recently checked (`select_secondary_instruments`, deterministic). New table `secondary_ca_checks (provider, instrument_id, checked_at)` (DATABASE_SCHEMA), written for the instruments actually queried.
- HTTP 429 is no longer retried by urllib3: one wait (Retry-After, capped 60 s), one retry, then stop asking for the run (`rate_limited`, warning, step OK). Server errors: 3 retries, backoff 2/4/8 s.
- Up to 5 % other failures tolerated (`failed`, warning); more fail the step.
- Correctness rests on audit P0-2's coverage rule: only instruments in `queried_instrument_ids` count as asked, so an unasked or failed instrument is never escalated by Upstox's silence; earlier Upstox observations stay stored and are reconciled with every later run.
- `vcp ingest corporate-actions` prints how many instruments Upstox was asked about, and warnings for rate-limit stops and failures. `DATA_SPECIFICATION.md` §18A; `tests/conftest.py` skips the pacing pause in unit tests.

**Tests.** `test_upstox_budget.py`: selection order (NSE action first, never checked, then oldest; ties by id; all within budget); the worker rotates AAA → BBB → CCC while EEE (an NSE split) is asked every run, and records the checks; no budget asks everyone; the check log upserts; one 429 waits Retry-After and continues; a second 429 stops without failing and asks nothing more; Retry-After capped at 60 s. `test_provider_failures.py`: 1 server error in 40 tolerated and reported; a zero share raises; pacing spaces requests.

**Verification.** Full suite: 841 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean. Two tests that stub `_build_ca_providers` take the new config argument.

### Fix U2 — Upstox's zero amount made every rights issue a conflict (found in the U1 live check)

**Found.** Live check of U1 on a copy of the main DB (00:27–00:56 IST, 800 of 2,593 instruments asked in 25.5 minutes, no rate limit hit): the first Upstox data ever stored (269 rows) moved 254 resolutions from SINGLE_SOURCE to CONFIRMED, but every one of the 9 rights issues it saw became PROVIDER_CONFLICT on `cash_amount` (NATCOPHARM 2:21 at 750, CENTEXT, GENESYS, SHANTIGOLD, DUCON, KSHITIJPOL, VHLTD, JAYKAY, RATNAVEER). Upstox sends `amount: 0.0` on splits, bonuses and rights (all 18 such rows), and the parser read it as an issue price of 0. A PROVIDER_CONFLICT rights issue gets no adjustment factor, so these stocks would have lost their TERP factors. The resulting events are warnings, not blocks. (The tenth new conflict, DTIL's 2026-08-12 dividend that only Upstox reports, is a genuine one-source dividend and only a warning.)

**Change.** `UpstoxCorporateActionProvider._parse_upstox_action`: an amount of 0 or less is "not reported" (None), so reconciliation compares cash amounts only when both sources give one (its existing rule).

**Test.** `test_upstox_coverage.py::test_zero_amount_is_not_reported_and_rights_are_confirmed`: the Upstox rights row has no amount, and NSE 2:21 at 750 plus Upstox 2:21 reconcile to CONFIRMED.

**Verification.** Full suite: 842 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Real-data check of U1–U2 (copy `data/upstox.duckdb` of the main DB, 02:02–02:31 IST, code at 6a5d259)

Two earlier attempts (01:26, 01:57) were cut short by power failures on the owner's PC; the third ran to completion. `vcp ingest corporate-actions --start 2026-08-02` with the live Upstox token:
- Upstox asked about 800 of 2,593 instruments (23 with an NSE action first) in 26.8 minutes; no HTTP 429; `secondary_ca_checks` holds the 800.
- 269 Upstox observations stored (the first ever). Resolutions: 263 SINGLE_SOURCE → CONFIRMED, including all 9 rights issues that U1 alone turned into conflicts (NATCOPHARM, CENTEXT, GENESYS, …); 187 adjustment factors (179 without U2: the rights TERP factors are back).
- One new PROVIDER_CONFLICT: DTIL's 2026-08-12 dividend reported only by Upstox (warning, no price effect).
- Blocking data-quality events 235 → 235 (none new, none cleared); no change for any of the 2026-10-01 eligible stocks.

### Applied to the main DB (owner approved; 03:04–03:42 IST 2026-10-02, code at 88fe296)

`upstox-pacing` fast-forwarded into `main` and pushed at 02:33; the apply waited 30 minutes for Upstox's window after the copy check. Backup `data/vcp_scanner.pre_upstox.duckdb` (replaces `pre_batcha`); the run held the daily-run lock. Corporate actions from 2026-08-02 with Upstox (800 of 2,593 asked in 29.5 minutes, no HTTP 429), adjusted prices, features, universe/RS/Trend Template for 2026-10-01. Data-quality scan: 6 unresolved corporate-action conflicts, 631 blocking signals (as on the copy). Universe `uv_20261001_0e1271d5ce`, 1,257 eligible; RS 1,257; Trend Template 203 PASS / 1,048 FAIL / 6 INSUFFICIENT_DATA, scan run `run-20261001-20261001T220957675027Z`, results hash `7769987bb8868a73` (unchanged).

## Fix B1 — health check and rolling backups before the daily run (2026-10-02)

**Why.** Two power failures on the owner's PC during the night of 2026-10-01 interrupted the Upstox checks. DuckDB survived (writes are all or nothing; the main DB opened with all data), but nothing backed the database up automatically: every backup so far was taken by hand before a fix, and rebuilding means re-downloading five years of NSE data. Owner decision 2026-10-02: back up before every daily run.

**Change.**
- `vcp_scanner.backup`: `check_database` (opens the file and runs a query; with `checkpoint` folds the WAL in first), `backup_database` (health check with checkpoint, disk-space check, copy to `<name>_<YYYYMMDD_HHMMSS>.duckdb.partial`, fsync, open-and-check the copy, rename, keep the newest `keep`, remove stale `.partial` files), `list_backups`, `restore_hint`.
- `vcp run daily` step 0: check, then back up to `<db folder>/backups/` (newest 3). A failure stops the run before any step, prints the newest backup and the restore command, and logs `FAILED: database check/backup`. New flags `--backup-dir`, `--backup-keep`, `--no-backup`.
- `README.md` "Daily run, backups and recovery".

**Tests** (`test_backup.py`): healthy file passes, garbage and missing files fail; five backups keep the newest three and the copy opens with its data; an uncheckpointed insert is in the backup; a leftover `.partial` is removed and never listed; a damaged database is refused, the restore hint names the good backup, and that backup is not rotated out; `run_daily` on a damaged database runs no step and logs the failure.

**Verification.** Full suite: 848 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

**Real-data check** (main DB, 03:51 IST, under the daily-run lock): health check passed instantly; checkpoint, copy, fsync, verification and rename of the 1.96 GB database took 9.4 s, giving `data/backups/vcp_scanner_20261001_222137.duckdb` (UTC timestamp). 918 GB free.

## Clean-up batch 2 (2026-10-02; owner decisions 02:55 IST: RS rank to 99 in rs-1.1.0, NSE user-agent kept but configurable, `--api-secret` removed)

Order: C7, C9, C10, C6, C5, C8 (C8 last: the only one that changes scan results). P2-4 (performance) deferred: the full rebuild is a few minutes and the daily run is incremental. P2-5 (Parquet lake) dropped for now: DuckDB covers storage and snapshots.

### Fix C7 — audit P2-6: one snapshot lookup, one config hash

**Found.** `cli_pipeline._latest_snapshot_id` duplicated `DuckDBUniverseRepository.latest_snapshot_id`; `UniverseBuilder._hash_config` serialised config with its own `json.dumps` instead of `compute_config_hash`, which the scan hash uses.

**Change.** `vcp compute rs` uses the repository's lookup (the CLI copy is gone). The universe hash is `compute_config_hash({universe, staleness, gate})[:8]`, the same canonical serialisation as the scan hash. Universe snapshot ids change once (the hash's bytes changed, not the settings it covers); scan config hashes and all results are unchanged. The RS/universe golden record was re-recorded: only `snapshot.config_hash` changed (c2d256fc → 2179f3e8).

**Tests** (`test_scan_config_hash.py`): the universe hash equals the shared canonical hash of its sections; the CLI has no private snapshot lookup.

**Verification.** Full suite: 850 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean. Real-data check: with C9 and C10 (below).

### Fix C9 — audit P3-1: secrets off the command line, one configurable NSE User-Agent, no tracked scratch files

**Found.** `vcp auth kite --api-secret` put the Kite secret into shell history; five NSE providers each hard-coded a browser User-Agent (two different Chrome versions); `scratch/` (personal notes and scripts) was committed.

**Change** (owner decisions 2026-10-02).
- `--api-secret` is refused with exit 2 and a pointer to `KITE_API_SECRET` in `.env` or the environment; the value is not echoed. (`--api-key` stays: the key is not a secret.)
- `data/providers/nse_http.py`: one `DEFAULT_NSE_USER_AGENT` (Chrome 124), `nse_user_agent()` / `set_nse_user_agent()`; all five NSE providers use it. `data.nse_user_agent` in `config/data.yaml` (null = default) is applied by the CLI at start-up. The module documents why a browser string is sent. **Deviation from the plan:** no contact suffix. A live probe (03:59 IST) with `... Safari/537.36 vcp-scanner (+https://github.com/ajaydeloli/vcp_ai)` timed out on NSE's corporate-actions API while the plain string got HTTP 200, so adding it would break the daily run.
- `scratch/` removed from git (`git rm --cached`; the files stay on disk) and added to `.gitignore`.

**Tests** (`test_hygiene.py`): every NSE provider sends the configured string; blank falls back to the default; the CLI applies `data.nse_user_agent` from the config folder; `--api-secret` is refused without echoing the value; `scratch/` is ignored.

**Verification.** Full suite: 855 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix C10 — audit P3-1: the split-like test separated nothing; every unexplained down gap blocks

**Found.** `GapDetector._is_split_like` blocked a gap when its open/prev-close ratio was within 3 percentage points of some p/q (p < q <= 10), first match. The audit item was the absolute tolerance. A first fix (relative 3 %, closest match) was checked on a copy (04:03–04:08 IST): it stopped 42 of the open blocking gap events from blocking, including recent -47 % to -65 % gaps (CHAVDA 2026-09-24, AILIMITED 2026-08-26, PSRAJ, VMARCIND …) that could be missed splits. Measuring it showed the real problem: the candidate ratios are so dense that of the 83 open unexplained down-gaps, 81 were within the old tolerance of one (relative 3 %: 38; 5 %: 69; 6 %: 79). The test never separated missed splits from crashes; it blocked almost every deep drop by accident.

**Decision** (owner, 2026-10-02 04:1x IST): every unexplained down gap of `gap_pct` (30 %) or more blocks until an action is added or a person marks it genuine (`vcp quality resolve`); up gaps only warn, as before. The first fix was discarded, not committed.

**Change.** `_is_split_like` returns "blocks" for every down gap and names the nearest p/q only when it is within `split_like_tolerance_pct` (now relative, `|ratio/(p/q) - 1|`) as a hint (`context.suspected_ratio`, "Close to a q:p split/bonus"). Context key `is_split_like` kept for existing readers. DATA_SPECIFICATION §18A, config comment.

**Tests** (`test_gap_detector.py`): a -88 % drop blocks with no ratio named; an up gap does not block; 0.1025 → 10:1, 0.66 → 3:2, 0.50 → 2:1.

**Verification.** Full suite: 857 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Real-data check of C7, C9, C10 (copy `data/c2.duckdb` of the main DB, 04:44–04:49 IST, worktree code)

`vcp quality scan`, universe, RS and Trend Template for 2026-10-01:
- **C10:** blocking signals 631 → 633. Two open gap events start blocking, both deep crashes the old ratio list missed: GOLDSTAR 2023-01-23 (-94.2 %) and ISHAN 2024-01-25 (-96.5 %). Both are older than the 253-bar block lifetime, so neither blocks today. No gap event stopped blocking.
- **C7:** universe `uv_20261001_266d79117c` (new id, as expected), 1,257 eligible, 8 blocked (unchanged).
- **Results:** RS 1,257; Trend Template 203 PASS, results hash `7769987bb8868a73` (unchanged).

### Fix C6 — audit P1-2b: a stock absent from a settled bhavcopy is not missing data

**Found.** `CompletenessChecker` (Kite path, `vcp ingest market`) observes sessions from the cross-section and reports every session without a bar as missing, raising a blocking `MISSING_CANDLES` event. A suspended or untraded stock has no bar on those days by design. The daily run uses the NSE bhavcopy path, so the main DB has no such events today, but `vcp ingest market` would raise them.

**Change.** A session whose NSE bhavcopy settled OK lists every stock that traded; a stock without a bar on such a day did not trade on NSE, so it is not counted missing (long absences are `TRADING_ABSENCE`, §18A). Sessions without a settled bhavcopy are checked as before.

**Tests** (`test_completeness.py`): a day absent from a settled bhavcopy is COMPLETE; a hole on a day without settled bhavcopy is still reported.

**Real-data check** (main DB read-only, 04:51 IST; window 2025-10-01 → 2026-10-01, 248 observed sessions, 3,207 instruments): old rule 626 instruments INCOMPLETE with 34,118 "missing" bars (suspensions, untraded SME and BE days, e.g. GOODYEAR 2025-10-01 → 2026-04-17); new rule 0.

**Verification.** Full suite: 859 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Fix C5 — audit P1-2a: data-quality event history; the point-in-time gate reads it

**Found.** `data_quality_events` holds one row per event. When the system reopened an event it had closed, the upsert set `resolved_at = NULL`, so a backtest asking "was this stock blocked on day X, as known then?" saw it blocked through the interval where it had been unblocked. A change of `blocks_signal` (e.g. C10) was not dated either.

**Change.**
- New table `data_quality_event_history` (system-time intervals per event state; DATABASE_SCHEMA 19.1). `sync_events` compares each event's (status, blocks_signal, resolved_by) before and after and records every change at `at`; `resolve` records the human resolution. A change at or before the current interval's start replaces it (no overlaps).
- `blocked_instruments(known_at=...)` checks the interval containing the cutoff (OPEN and blocking) instead of `detected_at`/`resolved_at`; without a cutoff it reads the current row as before.
- `migrate()` seeds history once for events that have none (OPEN from `detected_at`, RESOLVED from `resolved_at`); reopen cycles from before C5 cannot be recovered.

**Tests** (`test_event_history.py`): open → system-cleared → reopened is unblocked only in between; a `blocks_signal` change is dated; a human resolution is dated and a refresh does not reopen it; unchanged state adds no history; migrate seeds once (idempotent) and the gate uses it; an older knowledge time replaces the current interval.

**Verification.** Full suite: 865 passed, 0 failed (before a one-line mypy fix in `resolve`; mypy then clean). `ruff check`, `ruff format --check` clean.

**Real-data check** (copy `data/c5.duckdb` of the main DB, 04:56–05:10 IST): `migrate()` seeded the 802 existing events (747 OPEN, 55 RESOLVED) into 857 history intervals (747 open, 55 OPEN-then-RESOLVED pairs). Universe/RS/Trend Template for 2026-10-01 with the history gate: 1,257 eligible, 8 blocked, 203 PASS, results hash `7769987bb8868a73` (unchanged). `vcp verify scan` of that run (frozen data, universe rebuilt as known at its cutoff through the history gate): **MATCH**, same universe id.

### Fix C8 — audit P3-1: RS rank reaches 99 (`rs-1.1.0`; owner decision 2026-10-02)

**Found.** `rs-1.0.0` ranks `pct = (count_below + 0.5·count_equal) / N`, where `count_equal` includes the instrument itself, so the strongest stock's `pct` is `(N − 0.5)/N` and ranks run 1–98, not Minervini's 1–99 (documented quirk, audit 2026-09-30 Fix 7).

**Change.** `rs-1.1.0` (new default, `strategy.rs.version`): `pct = (count_below + 0.5·(count_equal − 1)) / (N − 1)` over the other instruments (N = 1: 1.0), `rs_rank = 1 + floor(98·pct)`: weakest 1, strongest 99, ties share. `rs-1.0.0` stays selectable to re-run old scans (`RSConfig.version` is a Literal of the two). `RS_ALGORITHM_VERSION`, the Trend Template's default RS version, `config/strategy.yaml` and TREND_TEMPLATE_SPECIFICATION §3 updated. RS rows are stored per version, so old `rs-1.0.0` rows stay as they were. This is a strategy change: scan config hashes and scan ids change.

**Tests** (`test_rs_ranking.py`): known answer for four stocks (99/66/33/1); range 1–99 and monotonic for 2–3,000 stocks; a single stock ranks 99; ties share 50; `rs-1.0.0` still tops at 98; an unknown version is refused. Golden RS/universe record re-recorded: only the 9 RS rows' `rs_percentile`/`rs_rank` changed.

**Verification.** Full suite: 868 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

**Real-data check** (copy `data/c8.duckdb` of the main DB, 05:13–05:16 IST, 2026-10-01): of 1,251 ranked stocks, 1,226 keep their rank, 13 move up 1 and 12 move down 1 (the old formula's self-count shifted ranks by up to half a step; the top rank is now 99, was 98). Trend Template: 203 PASS / 1,048 FAIL / 6 INSUFFICIENT_DATA, **no stock changes status**; results hash `8bdc231dc0f6fb4c` (was `7769987bb8868a73`; the hash includes `rs_rank`).

### Clean-up batch 2 applied to the main DB (owner approved; 05:53–05:58 IST 2026-10-02, code at 3e5beac)

`cleanup-2` fast-forwarded into `main` and pushed. Backup `data/backups/vcp_scanner_20261002_002345.duckdb` (B1's rolling backups, under the daily-run lock). `migrate()` seeded the event history; `vcp quality scan` (C10: 2 more blocking events, both expired; 633 blocking signals; history 859 intervals = 857 seeded + the 2 flips); universe `uv_20261001_0d8e467c70`, 1,257 eligible; RS `rs-1.1.0`, top rank 99; Trend Template 203 PASS / 1,048 FAIL / 6 INSUFFICIENT_DATA, scan run `run-20261001-20261002T002649655400Z`, results hash `8bdc231dc0f6fb4c` (identical to the copy). The merge removed the untracked-now `scratch/` files from the main checkout (C9 untracks them); they were copied back from the worktree and are ignored by git.

## Fix C11 — capital reductions as their own class; reviewed MAXIND, EASTSILK, UEL (2026-10-02)

**Owner research** (2026-10-02): MAXIND 2022 was a tender-based reduction, up to 20 % of the shares cancelled at Rs 85 each (5,37,86,261 → 4,30,29,009, the cap of 1,07,57,252 cancelled); EASTSILK 2024 an IBC resolution-plan reduction (old equity extinguished, 50,00,000 new Rs 2 shares to the resolution applicant); MELSTAR 2024 similar (public holding cancelled; 27,93,661 shares after); UEL 2024 a demerger under the resolution plan, record date 2024-05-22; DTIL's 2026-08-12 dividend reported only by Upstox stays a warning. Decision: capital reduction is its own corporate-action class, never a price adjustment, always a warning for manual review; UEL gets a reviewed demerger with factor 1.0.

**Data check** (main DB, read-only): MAXIND and EASTSILK had UNMODELLED NSE records and open warnings; EASTSILK also has a blocking `TRADING_ABSENCE` on its 2025-08-18 return under a new ISIN (expired by the block lifetime). **MELSTAR has no NSE corporate-action record and its bars end 2024-09-02** (it left the universe as stale), so no entry was made: there is no ex-date to record it on. UEL's 2024-05-22 demerger had an open blocking `factor_unknown` event (expired): the price rose from 43.80 (2024-05-21) to 153.30 (2024-05-27, upper-circuit relisting), so no demerged value can be measured.

**Change.**
- `CorporateActionType.CAPITAL_REDUCTION`. The NSE parser stores "Capital Reduction" records under it (with the record text) instead of UNMODELLED. It is in no ratio or derived-factor set, so it never adjusts prices or explains a gap.
- `unmodelled_action_events` raises one warning per capital reduction (same event id as before), `context.price_adjustment = NONE`, `manual_review = PENDING | DONE` (`MANUAL_OVERRIDE` = reviewed; the warning stays). An older UNMODELLED reading of the same NSE record is reported once. `corporate_action_events` treats both as superseding older ratio-less readings.
- `ManualActionEntry`: `CAPITAL_REDUCTION` with required `reduction_kind`, optional `shares_before`, `shares_after`, `cash_amount` (consideration per cancelled share), no ratio; these fields are refused on other types. The stored record text starts with `[kind; price adjustment NONE; shares ...; Rs ... per cancelled share]`.
- `config/manual_corporate_actions.yaml`: reviewed MAXIND and EASTSILK capital reductions; UEL demerger `price_factor: 1.0` with the reasoning.
- DATA_SPECIFICATION §18A.

**Tests** (`test_capital_reduction.py`, updated `test_unmodelled_actions.py`, `test_consolidation.py`, `test_manual_corporate_actions.py`): NSE capital-reduction records get the new type; manual entries take details but never a ratio and need `reduction_kind`; an unreviewed reduction warns once (old UNMODELLED reading folded in), PENDING, never adjusts; a reviewed one is MANUAL_OVERRIDE, still warns, marked DONE; the project file holds the three entries.

**Verification.** Full suite: 872 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

**Real-data check** (copy `data/cr.duckdb` of the main DB, 06:08–06:20 IST; corporate actions from 2021-01-01 with NSE only, then adjusted prices, features, universe/RS/Trend Template for 2026-10-01):
- MAXIND 2022-07-26 and EASTSILK 2024-11-22: new CAPITAL_REDUCTION resolutions are MANUAL_OVERRIDE; each has one open, non-blocking warning "Capital reduction ... prices are not adjusted (it is not a split); reviewed by hand", `manual_review = DONE` (the old UNMODELLED readings stay stored and are folded in).
- UEL 2024-05-22: DEMERGER MANUAL_OVERRIDE (factor 1.0); its blocking `factor_unknown` event is resolved. Unresolved corporate-action conflicts 6 → 5, blocking signals 633 → 632 (that event had already expired by the block lifetime).
- Adjustment factors 834 → 834 (no price changes). Trend Template: 1,257 eligible, 203 PASS, results hash `8bdc231dc0f6fb4c` (unchanged).

**Small fix found in the check.** `vcp ingest corporate-actions` printed "Upstox asked about 800 of 2,593 instruments" even when no Upstox token was set (the rotation selected instruments for the no-op provider). It now prints that line only when a token is used.

## Phase 6 step 1 — VCP configuration additions and domain model (2026-10-02)

Build order step 1 of Phase 6 (owner decisions 2026-10-01, VCP_SPECIFICATION §8.1). Worktree `vcp_ai_p6s1`, branch `p6-step1-config-domain`.

**Configuration** (`config.models`, `config/strategy.yaml`, VCP_SPECIFICATION §60):
- `vcp.prior_advance` (`enabled: true`, `lookback_days: 120`, `min_return_pct: 20`), §7 and §8.1 item 1.
- `vcp.base.max_duration_days: 130`, §8.1 items 1 and 4.
- `vcp.invalidation` (`trend_template_failure: true`, `base_low_break_pct: 2.0`, `volatility_expansion_multiple: 2.0`), §25. Only the keys and defaults: the rules that read the last two are written with the status logic (step 6).
- `VCPThresholdsConfig.lookback_bars()` = `base.max_duration_days + max(prior_advance.lookback_days − 1, volume.long_period, volatility.atr_period, swing.left_bars)`, as-of bar included: the base, then before its oldest bar (a candidate base-start high) the prior-advance window (sharing the high's bar), the 50-bar volume average and ATR at the base start (prior bars), and the swing's left bars. Defaults 130 + 119 = **249**. `ScannerConfig.longest_lookback_bars()` includes it, so the existing check `block_lifetime_bars >= longest lookback` (253) now refuses a 135-bar base or a 125-bar prior advance (§8.1 item 4). The longest lookback stays 253 (RS/universe history).
- `base.max_duration_days >= contractions.min × swing.min_duration_days`.

**Domain model** (`domain/vcp.py`, `domain/enums.py`), per §9, §9A, §10, §20, §42 and DATABASE_SCHEMA §31–33:
- New `Swing` (kind, swing date, adjusted price, confirmation date; `known_on(as_of)` is the no-look-ahead test of §26). New enums `SwingKind`, `PivotSource` (§19's four sources), `InvalidationReason` (§25's five).
- `Contraction` now has the schema's fields (sequence number, peak/trough dates and adjusted prices, depth, duration, ATR%, range%, volume ratio, confirmation date; `is_confirmed`). `PivotCandidate` has §20/§33's (price, date, source, distance to close, touches, rejections, right-side tightness).
- `VCPPattern` has every §31 measurement: base duration, prior-advance return, final volume ratio and dry-up pass, ATR contraction ratio and pass, right-side range and tight-pivot pass, five quality fields, pivot candidates, Trend Template and weekly Stage 2 verdicts, invalidation reasons, `config_hash`, `is_primary`. Values derivable from other fields are properties, so they cannot disagree: contraction count, first and final contraction depth, tightening ratios D(n+1)/D(n) and their maximum, base depth, pivot distance. Scan id and data snapshot id are attached at persistence (step 7).
- Constructors refuse structural contradictions: sequence numbers not 1..n, overlapping or out-of-base contractions, a provisional contraction that is not the last (§9A.2), a confirmation state that does not match the contractions (§9A.3), `VCP`/`A_PLUS_VCP` without Trend Template PASS and weekly Stage 2 (§3, §35; `VCP_LIKE` is allowed outside, as research), a data status with a classification (§5), `INVALIDATED` without a reason, a depth that does not match its prices, a pivot that is not among the candidates. Thresholds stay out of the domain.

**Hash effect.** New strategy keys change `section_config_hashes()["strategy"]`, so Trend Template scans made with this code get a new scan config hash and scan id (as with C8); verdicts are unchanged. `vcp verify scan` of an older run prints its existing "current config differs" warning and still compares results.

**Tests:** `test_vcp_config.py` (defaults, lookback 249, 134-bar base accepted and 135 refused at lifetime 253, longer lifetime or none accepts it, disabled prior advance, the longer pre-base input wins, base must hold the minimum contractions, invalid values refused, hash changes); `test_vcp_domain.py` (derived values, every invariant above, swing confirmation); `test_config.py` §60 contract and `test_domain.py` updated to the new shapes.

**Verification.** Full suite: 901 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

**Real-config check** (08:40 IST): the shipped `config/` loads; VCP lookback 249, longest lookback 253, block lifetime 253. Scan config hash `e910a99876740931` (main) → `f3fa1ec152b2c87d` (this step), as expected. This step reads and writes no database data, so no DB copy check applies; the first copy check comes with the swing detector (step 2).

## Phase 6 step 2 — swing detector with confirmation dates (2026-10-02)

Worktree `vcp_ai_p6s2`, branch `p6-step2-swings`.

**Change.** `patterns/vcp/swings.py`: `detect_swings(dates, highs, lows, as_of, left_bars, right_bars)` and `SwingDetector` (bound to `vcp.swing`), method `pivot_n_bar` (VCP_SPECIFICATION §9, §9A, §26).
- A swing high is a bar whose adjusted high is ≥ each of the `left_bars` highs before and `right_bars` highs after it; a swing low likewise with lows. Bars are the instrument's own sessions; nothing is filled.
- `confirmation_date` = the date of bar `i + right_bars`. A bar with fewer than `left_bars` bars before it is never a swing.
- Only bars dated on or before `as_of` are read, or even validated. Confirmed swings as of D are therefore exactly the full-history swings with `confirmation_date <= D`.
- *Pending* swings: in the newest `right_bars` bars, a bar that satisfies the rule against every bar seen so far is reported separately with `confirmation_date = None`; a later bar may cancel it. Never used as confirmed.
- Ties follow §9 literally (`>=` both sides): a plateau of equal highs gives a swing on each plateau bar. Merging them belongs to noise filtering in segmentation (§23, step 3). An outside bar can be both a swing high and a swing low (HIGH listed first).

**Tests** (`test_vcp_swings.py`): peak and trough with their confirmation dates; no swing without the left bars; confirmation waits for the right bars and the newest bars are pending; a pending swing cancelled by a higher bar; equal highs; outside bar; bars after `as_of` (even a malformed one) are never read; **as-of property**: 20 random series × 4 left/right settings, at every date the as-of result equals the full history filtered by confirmation date; config binding; input validation; empty/short series.

**Real-data check** (09:50 IST, read-only on `data/backups/vcp_scanner_20261002_030518.duckdb`, the 08:35 copy of the main DB; the 1,257 instruments with a 2026-10-01 Trend Template verdict, their last ≤ 249 adjusted bars):
- All 1,257 detected in 3.0 s, 0 errors. Per stock: confirmed highs 6–22 (median 15), confirmed lows 8–40 (median 15), pending 0–3 (median 1).
- As-of property on real data: 100 random stocks × each of their last 60 dates: **0 mismatches**.
- 256 pairs of equal swing highs within a week (plateaus) across the universe: step 3's noise merge must handle them.
- Spot checks (ABDL, ACE, ACMESOLAR, last 60 bars): highs and lows alternate as on the charts; confirmation dates are 5 sessions later, skipping the NSE holiday in mid-September as expected.

**Verification.** Full suite: 991 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Phase 6 step 3 — base and contraction segmentation (2026-10-02)

Worktree `vcp_ai_p6s3`, branch `p6-step3-segmentation`. Interpretations of §8.1 sent to the owner at 10:00 IST before coding; two refinements found while testing are marked (R1, R2).

**Change.** `patterns/vcp/segmentation.py`: `segment_base(dates, highs, lows, as_of, config)` returns a `BaseSegmentation` (base start/high/low, duration, prior-advance return and its low date, contractions as `ContractionSegment`, merged peak dates) or a `NoBaseReason` (`NO_CONFIRMED_SWING_HIGH`, `NO_PRIOR_ADVANCE`, `INSUFFICIENT_HISTORY`), plus the swings used. Rules (VCP_SPECIFICATION §8.1, details paragraph added):
- Base start = highest confirmed swing high in the last 130 bars (earliest on a tie) with a prior advance ≥ 20 % = base high ÷ lowest low of the 120 bars ending at it − 1; otherwise no base (no fallback). A short history that passes counts; one that fails is `INSUFFICIENT_HISTORY`.
- Peaks = base start + every later confirmed swing high. Closed contraction k = peak k → lowest low before peak k+1, confirmed on peak k+1's confirmation date.
- Noise (§23), repeated until stable: a decline < 2 % or < 3 bars removes its peak (T1 keeps the base start and drops peak 2). **R1:** the rally out of a low is tested too: a bounce < 2 % or < 3 bars removes the next peak. Without it, a 1 % bounce in the middle of a decline split T1 into two deep contractions (e.g. 10 % then 12 %) and broke tightening. This follows §8.1's "swings shallower than…" (a swing is any leg between turning points).
- Final contraction = last peak → lowest low since; none while that decline is noise. **R2:** it is confirmed once `right_bars` (5) bars follow the low without a lower low, not when the low is a formal swing low: in the real check 6 finals stayed provisional only because their low came 3 bars after the peak and the left-side window reached back into the previous rally's lower lows (NRAIL, EMIL, DOLLAR).
- `confirmation.allow_provisional_final_contraction = false` drops a provisional final.

**Tests** (`test_vcp_segmentation.py`, 27): classic three-contraction base (peaks, lows, depths, confirmation dates, prior advance and its window); provisional final and the config switch; R2 right-side confirmation; no final contraction while pressing the high; R1 bounce merged; shallow decline merged; equal highs (earliest is the base start, twin merged); no prior advance and the disabled switch; short history pass/fail; no swing high; an older higher high outside the window ignored; segments build valid domain `Contraction`s; 15 random series × many as-of dates: identical with bars after as-of removed, invariants (depth ≥ 2 %, ≥ 3 bars, ordered, only the final provisional, inside the base).

**Real-data check** (read-only on the 08:35 backup copy, as of 2026-10-01, default config, last 249 bars):
- 1,257 stocks in 0.7 s: 1,198 bases, 59 `NO_PRIOR_ADVANCE`. Contraction counts 0:19, 1:304, 2:211, 3:185, 4:157, 5:148, 6:110, 7+:64. Median base 56 bars, 22.5 % deep; median 1 merged peak per base.
- Trend Template PASS (203): all have a base; counts 0:11, 1:126, 2:34, 3:14, 4:9, 5:7, 6:2; median base 17 bars, 15.1 % deep (passers sit near their highs).
- Provisional finals: 943, all with the low in the last 5 bars (the late-September decline); after R2 none are provisional for any other reason (was 949).
- As-of: 100 stocks × 29 dates, identical with later bars removed: 0 mismatches.
- Spot checks (ABDL 18.5 → 10.7 → 8.4 %; APOLLO 19.8 → 8.4 → 15.6 %; ADFFOODS, ARFIN) match the swings and the price history.
- Rough preview only (classification is step 6): of 875 bases with ≥ 2 contractions, 606 end ≤ 15 % and 213 also tighten within 10 %; among passers 66, 58, 38.

**Verification.** Full suite: 1,018 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Phase 6 step 4 — VCP measurements (2026-10-02)

Worktree `vcp_ai_p6s4`, branch `p6-step4-measurements`. Owner, 2026-10-02: gaps or better logic than the documents may be improved, with documentation. The improvements are in VCP_SPECIFICATION §18B (new) and marked **I1–I3** here.

**Change.** `patterns/vcp/measurements.py`: `measure(series, base, config) -> VCPMeasurements` over a `PriceSeries` (adjusted high/low/close, volume with `None` for missing, the feature engine's `atr_pct_14`) and the step-3 `BaseSegmentation`.
- Per contraction (`ContractionMeasures`, DATABASE_SCHEMA 32): `atr_pct`, `tr_pct`, `range_pct`, `volume_ratio`.
- Tightening (§12–13): ratios D(n+1)/D(n), max ratio, `tightening_consistency` (share of shrinking pairs; §13 named it without a definition), `progressive_tightening` per §18A (relative tolerance and D(last) < D(first)).
- Volatility (§15–16, 18A): `atr_contraction_ratio`, `tr_contraction_ratio`, `volatility_contraction_pass`; `last_N_range_pct` (20/10/5), `last_N_atr_pct` (20/10).
- Volume (§17, 18A): `final_volume_ratio`, `volume_dryup_pass`, `volume_avg_N` (5/10/20/50), `volume_ratio_5_20`.
- Right side (§18A, 22): range, ATR%, volume ratio over the last 10 bars; `tight_pivot_pass`.
- Selling pressure (§18, supporting): up/down-volume ratio and high-volume (≥ 1.5× prior 50-bar mean) up/down day counts over the base.
- Missing inputs give `None`, never a pass; criteria needing two contractions are `None` with one.

**I1 — contraction window and baseline.** §18A says "over the final contraction" and "50-day average at that contraction's start" without saying which bars. Window = the decline bars (after the peak through the low, = `duration_days`); baseline = the 50 bars before the peak (prior bars, as `volume_ratio_50`).

**I2 — volatility decided on the contraction's own bars.** §18A compares mean ATR14% over the final and first contractions. ATR14 averages 14 bars, and 39 % of final contractions on 2026-10-01 are ≤ 6 bars, so it mostly measures earlier bars. On 2026-10-01 the two disagree on 230 of 875 bases: 160 pass on ATR while the final contraction's own bars were not calmer (APOLLO: ATR ratio 0.68 but its last 4 bars ranged 1.56× T1's; VGUARD 1.31×, PRECAM 1.11×), 70 fail on ATR although the bars had calmed. New key `vcp.volatility.measure` (`true_range` default, `atr` = the §18A rule); both ratios are always stored. Owner decision 4 (ATR = the feature engine's simple `atr_14`) still holds for every ATR value.

**I3 — tight pivot left as specified.** Only 14 of 1,198 bases had a 10-bar range ≤ 5 % on 2026-10-01 (mid-decline). On 2026-06-15 / 07-15 / 08-14 / 09-15: 21/941, 58/1,027, 69/1,113, 45/1,190 (median right-side range 9.5–10.5 %). Tight right sides are rare, which is the point of the criterion; the threshold is left for the golden dataset (step 8) to calibrate.

**Tests** (`test_vcp_measurements.py`, 19): tightening ratios and consistency; the progressive rule at, above and below the relative tolerance, zero tolerance, and last ≥ first; volume dry-up through the sequence (pass, above 0.70, not below T1) with exact baselines; missing volume → `None`; ATR and TR per contraction against hand computation; the volatility switch at four caps; missing ATR → the ATR rule cannot pass, the TR rule still decides; right-side and range-compression windows; selling-pressure counts; one contraction → sequence criteria `None`; bars after as-of ignored; a series that does not match the segmentation is refused. `test_config.py` §60 contract and strategy.yaml include `measure`.

**Real-data check** (read-only on the 08:35 backup, 2026-10-01, features-1.2.0, default config): 1,198 bases measured in 1.0 s.
- All bases: progressive tightening 231 / 644 / 323 (True/False/None = fewer than 2 contractions); volume dry-up 400 / 475 / 323; volatility 352 / 523 / 323 (442 / 433 under the ATR rule); tight pivot 14 / 1,184. Medians: ATR ratio 0.80, TR ratio 0.85, final volume ratio 0.65, right-side range 10.5 %, up/down volume 0.98.
- Trend Template passers (203): progressive 38 / 28 / 137; dry-up 38 / 28; volatility 22 / 44 (ATR rule 29 / 37); tight pivot 1.
- No criterion was `None` for missing data on any base with ≥ 2 contractions.
- Spot checks: ABDL depths 18.5 → 10.7 → 8.4 %, volume 1.09 → 0.75 → 0.61 (dry-up), bars' range 3.78 → 3.37 %; APOLLO loosens (8.4 → 15.6 %) and its last contraction is the most volatile (TR% 6.2 vs 3.98): now fails volatility.

**Verification.** Full suite: 1,037 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Fix S1 — sharp one- or two-bar swings are not noise (2026-10-02, found in Phase 6 step 5)

**Found.** Spot-checking pivots, ABDL's 9.3 % rally from 584.30 (2026-08-21) to 643.90 (08-25) had been merged as noise, losing a real peak and its contraction (643.90 → 586.55, 8.9 %). §8.1 item 2 merges swings "< 2 % deep **or** < 3 days long". Traced on 2026-10-01 (1,257 stocks): of 966 noise merges, **921 were on duration alone** (522 two-bar, 399 one-bar swings) with a median size of 7–8 %, 403 of them ≥ 8 %; only 4 merges were shallow (< 2 %), because the 5-bar swing rule already removes small wiggles. The duration test was deleting sharp shakeouts and fast rallies, not noise.

**Change** (owner allowed improvements with documentation, 2026-10-02). New key `vcp.swing.short_swing_max_depth_pct` (default 4.0, about one day's average range; must be ≥ `min_depth_pct`): a swing is noise if it is shallower than `min_depth_pct`, **or** shorter than `min_duration_days` bars **and** shallower than `short_swing_max_depth_pct`. 100 restores the plain rule. Applies to declines, rallies and the final-contraction check (`segmentation._small`). VCP_SPECIFICATION §23/§60 YAML, `strategy.yaml`, the §60 contract test updated; §8.1 rule text below.

**Tests:** a 9 % two-bar rally keeps its peak (and the old rule, via 100, merges it); a 3 % two-bar dip is still merged; threshold below `min_depth_pct` refused; the random-series invariant is now "depth ≥ 2 %, and ≥ 3 bars or ≥ 4 %".

**Real-data check** (read-only on the 08:35 backup, 2026-10-01): 1,198 bases as before; median merged peaks per base 1 → 0; contraction counts shift up (1: 304 → 261; 7+: 64 → 210); bases with no contraction 19 → 0 (their final 1–2-bar drops of ≥ 4 % now count). Trend Template passers with ≥ 2 contractions 66 → 79; rough tightening preview 38 → 44. ABDL: T1 18.5 %, T2 10.7 %, T3 8.4 %, **T4 8.9 %** (was missing). As-of check 100 × 29: 0 mismatches.

**Verification.** Full suite: 1,040 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Phase 6 step 5 — pivot candidates and selection (2026-10-02)

Worktree `vcp_ai_p6s5`, branch `p6-step5-pivots` (after Fix S1 above, found in this step's spot checks).

**Change.** `patterns/vcp/pivots.py`: `select_pivots(series, segmentation, measurements, config) -> PivotSelection` (all candidates + the primary), producing domain `PivotCandidate`s. Definitions and the selection rule are new VCP_SPECIFICATION §19A (spec gaps filled: §19 had no selection rule, §20 no definition of touches/rejections). New key `vcp.pivot.level_tolerance_pct` (1.5) in the §60 contract and `strategy.yaml`.

**Tests** (`test_vcp_pivots.py`, 10): classic base → final contraction's peak when the right side is loose (at a 3 % cap) and the right-side top when it is tight (5 %); a flat tight area → its top; repeated resistance at 150/149 with 2 touches and 2 rejections on the base high; no contraction → base high; duplicates keep the structural label; touch/rejection counting (finished vs open visit, close above, consecutive bars = one visit); no base, mismatched series, determinism.

**Real-data check** (read-only on the 08:35 backup, 2026-10-01, with Fix S1): 1,198 bases in 1.1 s. Primary pivot: final contraction's peak 930, base high 259, right-side high 9 (tight right sides are rare, step 4 I3). Candidates per base 1–7 (median 3); 561 bases have repeated resistance. Primary distance: median 10.3 % (all), 6.1 % (passers); within 0–3 %: 60 (all), 25 (passers); close above the pivot: 60 (all), 35 (passers). Spot checks: AJANTPHARM pivot 3,630 (1.4 % away, 6 touches, 4 rejections); ALIVUS 1,446 (5.2 %); ASKAUTOLTD 678.75 with repeated resistance at the base high 687.70. **For step 6:** ABDL (pivot 643.90, close 679.45, unconfirmed new high 753.80 above the base high 711.70) and AJANTPHARM (traded to 3,758.90 above its 3,630 pivot on 09-30, closed back below) need breakout / failed-breakout statuses.

**Verification.** Full suite: 1,050 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Phase 6 step 6 — classifier, invalidation, breakout, status, primary pattern (2026-10-02)

Worktree `vcp_ai_p6s6`, branch `p6-step6-classifier`. Status and invalidation rules were sent to the owner with alternatives at 11:30 IST (★ recommendations built; owner may still change them). Rules are in new VCP_SPECIFICATION §61B.

**Change.**
- `patterns/vcp/classifier.py`: `classify` (tiers per §28–31 with per-tier unmet rules), `invalidation_reasons` (TREND_TEMPLATE_FAIL; BASE_STRUCTURE_FAIL = close > 2 % below the base low before the final contraction; EXCESS_VOLATILITY = last-10-bar true range ≥ 2 × T1's), `find_breakout` (first close above the structural pivot on ≥ 1.5 × prior 50-bar volume), `decide_status` (data state → INVALIDATED → BREAKOUT/FAILED → PIVOT_READY → FORMING).
- `patterns/vcp/detector.py`: `VCPDetector.detect(instrument, series, as_of, trend_template_pass, weekly_stage2_pass, data_state)` → `VCPDetection` (domain `VCPPattern` with all measurements and pivot candidates, plus the segmentation, measurements, pivots, classification detail and breakout for explainability); `select_primary` (§61A order). Quality fields stay NULL (scoring phase).
- `pivots.PivotSelection.structural`: the final contraction's peak (else base high). **Design point found while testing:** a right-side pivot is the highest high of the last 10 bars, so the breakout bar itself always joins it and a stateless as-of run can never see a close above it. Breakouts are therefore measured against the structural pivot; right-side-pivot breakouts are recorded by the daily run against the previous day's stored pivot (§47, step 7).
- New key `vcp.breakout.min_volume_ratio` (1.5) in the §60 contract and `strategy.yaml`; `VCPInvalidationConfig` docstring points at §61B.

**Tests** (`test_vcp_classifier.py`, 13): classic tight base → A_PLUS_VCP, PIVOT_READY, CONFIRMED, right-side pivot 0–3 % away; Trend Template fail → VCP_LIKE + INVALIDATED (and FORMING with the switch off); missing weekly Stage 2 → VCP_LIKE; no dry-up → VCP with the single unmet A+ rule; final 13 % → VCP_LIKE; breakout on 2× volume → BREAKOUT with `base_end`, then a close back below → FAILED; close above without volume → not a breakout; base-low break and the 2 % allowance; excess volatility and its multiple; data states, a non-data state refused, NO_PRIOR_ADVANCE and INSUFFICIENT_DATA; too many contractions; §61A ordering; determinism and as-of safety.

**Real-data check** (read-only on the 08:35 backup; 2026-10-01; gates from scan `trend-2026-10-01-e910a9987674`): 1,257 detections in 3.4 s.
- All: no pattern 65 (59 NO_PRIOR_ADVANCE, 6 INSUFFICIENT_DATA); classification VCP_LIKE 532, NONE 660; status INVALIDATED 991 (TREND_TEMPLATE_FAIL 989, BASE_STRUCTURE_FAIL 309, EXCESS_VOLATILITY 3), FORMING 154, BREAKOUT 32, FAILED 15.
- Trend Template passers (203): VCP_LIKE 66, NONE 137; BREAKOUT 32, FAILED 15, FORMING 154, INVALIDATED 2 (base structure); **no VCP or A_PLUS_VCP**. All 66 VCP_LIKE passers miss VCP on `require_tight_pivot`; 25 also on tightening, 11 on final depth.
- Gates forced open (structure only), 2026-06-15 / 07-15 / 08-14 / 09-15 / 10-01: VCP + A+ = 5 / 11 / 16 / 10 / 1; VCP_LIKE short of VCP on the tight pivot alone: 138 / 170 / 195 / 135 / 143.
- **Calibration finding (left as specified, raised with the owner):** the VCP tier allows a 12 % final contraction but `tight_pivot` requires the last 10 bars within 5 %, so any final contraction deeper than 5 % that lies inside the last 10 bars cannot be VCP. Recorded in §61B for the golden dataset to settle.

**Verification.** Full suite: 1,063 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Decision T1 — tight pivot required for A+ only (owner, 2026-10-02 11:50 IST)

**Context.** Step 6's real-data check found no VCP or A+ among 2026-10-01's 203 passers: every VCP_LIKE passer missed VCP on `require_tight_pivot` (last 10 bars within 5 %), which contradicts the VCP tier's 12 % final-contraction allowance. Options given: (1) keep and calibrate with the golden dataset, (2) require it for A+ only, (3) volatility-relative limit. **Owner chose option 2.**

**Change.** `classification.vcp.require_tight_pivot` removed (default config, `strategy.yaml`, VCP_SPECIFICATION §30 and §60; §61B note resolved). A+ keeps it. The tier validator still holds (A+ may be stricter than VCP). `test_config.py`: the §60 contract and the "A+ cannot drop a VCP requirement" test now use `require_progressive_tightening`; `test_vcp_classifier.py`: a loose right side is VCP with the single unmet A+ rule.

**Real-data check** (read-only on the 08:35 backup, 2026-10-01, scan `…e910a9987674` gates): passers VCP 37, VCP_LIKE 29, NONE 137, A+ 0. VCP statuses: PIVOT_READY 6 (AJANTPHARM 1.4 %, INOXINDIA 1.0 %, MARINE 2.4 %, MBAPL 1.6 %, …), FORMING 20, BREAKOUT 5 (ABDL, GALAXYSURF, GREAVESCOT, LLOYDSENT, …), FAILED 5, INVALIDATED 1. The 29 VCP_LIKE miss on tightening (25) or final depth (11).

**Verification.** Full suite: 1,064 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

## Phase 6 step 7 — VCP persistence, `vcp compute vcp`, breakout events, daily-run step (2026-10-02)

Worktree `vcp_ai_p6s7`, branch `p6-step7-persistence`. Plan sent to the owner at 12:00 IST.

**Change.**
- **Tables** (`duckdb_store.py`, DATABASE_SCHEMA new §34A): `vcp_patterns` (§31 + pivot source, TR ratio, invalidation reasons, unmet rules per class as JSON, breakout event id), `vcp_contractions` (§32 + TR%), `vcp_pivots` (§33 + `is_structural`), `vcp_status_history` (§34), `vcp_breakout_events` (§43/§47), `vcp_scan_run_results` (frozen verdicts of each VCP run). Additive migration.
- **`patterns/vcp/monitor.py`** — breakouts across days (VCP_SPECIFICATION §61B): an existing event of the same base decides BREAKOUT/FAILED against its pivot; new events come from the detector's structural breakout or from a close above the **previous scan date's stored primary pivot** on ≥ 1.5× volume (catches right-side-pivot breakouts, the step-6 design point). Data states and INVALIDATED are not overridden.
- **`data/repositories/duckdb_vcp_repository.py`**: bulk `load_series` (last N adjusted bars + `atr_pct_14`, missing volume as `None`), `prior_patterns`, `events` (detected **before** the date), `save_scan` (one transaction; replaces the scan id's rows, that date's history, and events detected on or after the date), status history (a row when class or status differs from the previous scan date), verdict rows and `vcp_results_hash`.
- **`cli_vcp.py`** — `vcp compute vcp --as-of DATE [--instrument] [--data-snapshot-id]`: gates from `trend-<date>-<hash12>` of the same config; TT data statuses → VCP data states; no bar on the as-of date → STALE_DATA; scan id `vcp-<date>-<hash12>`; immutable `scan_runs` row (type `VCP`, counts by class/status, breakout events, results hash) + `vcp_scan_run_results`. Prints a note when later VCP scan dates exist (they need a rerun in order).
- **Daily run**: a `VCP <date>` step after each Trend Template step.
- **`vcp verify scan`** refuses non-Trend-Template runs (it would have compared a TT rebuild with a VCP hash); `list_runs` takes `scan_type`, and the rebuild picks the TREND_TEMPLATE run.
- README command table, VCP_SPECIFICATION §61B (breakout events across days, STALE_DATA, data-state mapping).

**Bugs found while testing (fixed before commit):**
1. A rerun of a date read back its own breakout event as history, produced no new one, and then deleted it: events are now read only if detected *before* the date.
2. **Copy check:** computing 2026-09-30 after 2026-10-01 failed with a duplicate breakout event id (1 Oct had already recorded the same base's event). Recomputing a date now deletes events detected on or after it (they were built on the old history) and the command lists the later dates to rerun in order. The failed run had rolled back completely (one transaction).

**Tests:** `test_vcp_monitor.py` (7: no history; structural event; an existing event decides later days, BREAKOUT/FAILED; other base / future events ignored; prior-day-pivot breakout only with volume and same base; invalidated passes through; deterministic id), `test_vcp_repository.py` (5: series loading with missing volume; rows, relations, unmet JSON, first-day history; second-day breakout event, linked pattern, history transition, prior pattern, rerun replaces; recomputing an earlier date removes later events and a rerun in order restores them; verdict rows/hash), `test_pipeline_e2e.py` (compute vcp from an empty DB: run record, frozen verdicts match the hash, rerun replaces rows with an identical hash, verify refuses a VCP run), `test_daily_run.py` (VCP step after Trend Template).

**Real-data check** on a copy (`data/p6s7.duckdb`, copied 12:06 IST under the daily-run lock), code at this branch:
- Trend Template under the new config hash `7de9afd37f3a`: 2026-10-01 results hash **`8bdc231dc0f6fb4c`, identical to the main DB** (new keys change the scan id, not the verdicts). 2026-09-30 first came out DATA_NOT_READY (1,324) because that date's RS had only `rs-1.0.0` rows; after `compute rs` 267 PASS / 1,057 FAIL / 13 INSUFFICIENT_DATA.
- VCP 2026-10-01 alone: 1,257 considered, 1,192 patterns, VCP 37 / VCP_LIKE 495 / NONE 660; BREAKOUT 32, FAILED 15, FORMING 148, PIVOT_READY 6, INVALIDATED 991; 47 new events; rerun → identical hash `c7aaf621f686543c` (~40 s per date).
- In order, 09-30 then 10-01: 09-30 VCP 45, 62 events; 10-01 VCP 37, BREAKOUT 32, FAILED 16, FORMING 147, PIVOT_READY 6, **7 new events** (the rest read from 09-30), hash `4dc554690d22b1e0`. 10-01 history transitions include BREAKOUT→FAILED 9, FORMING→BREAKOUT 4, FORMING→PIVOT_READY 4. Spot checks: ABDL breakout 2026-09-16 over 643.90 (as in step 5); SONACOMS broke out over 813.15 on 09-29 and is FAILED on 10-01 against that event's pivot although a later swing high (837) now exists (§47).

**Verification.** Full suite: 1,076 passed, 0 failed. `ruff check`, `ruff format --check`, `mypy --strict src` clean. Not yet applied to the main DB (owner to approve).

### Phase 6 steps 1–7 applied to the main DB (owner approved; 12:29–12:35 IST 2026-10-02, code at d39a1bd)

Under the daily-run lock: backup `data/backups/vcp_scanner_20261002_065920.duckdb` (1.96 GB); `p6-step7-persistence` fast-forwarded into `main` and pushed; `compute rs` 2026-09-30 (`rs-1.1.0`, 1,337 rows; that date only had `rs-1.0.0`); Trend Template under config hash `7de9afd37f3a`: 2026-09-30 267 PASS / 1,057 FAIL / 13 INSUFFICIENT_DATA, hash `482d2509e9c17b21`; 2026-10-01 203 / 1,048 / 6, hash **`8bdc231dc0f6fb4c`** (unchanged verdicts); `compute vcp` 2026-09-30 (VCP 45, 62 breakout events, hash `e34dacce0e6632f4`) then 2026-10-01 (VCP 37: PIVOT_READY 6, BREAKOUT 32, FAILED 16, FORMING 147; 7 new events; hash `4dc554690d22b1e0`). All four hashes identical to the copy check. The copy `data/p6s7.duckdb` was deleted afterwards.

## Phase 6 step 8 — golden-dataset harness and blind labelling sheet (2026-10-02)

Worktree `vcp_ai_p6s8`, branch `p6-step8-golden`. Plan sent to the owner at 12:40 IST. Definitions are in VCP_SPECIFICATION §57 ("Implementation").

**Change.**
- `research/golden.py`: `GoldenFixture` and its JSON format (bars embedded, offline), `load_fixtures` (folder must match label), `split_for` (fixed 30 % holdout by hash), `run_fixture`, `agrees` (failed_vcp judged on FAILED/INVALIDATED status, ambiguous excluded), `evaluate` → agreement, per-class and production precision/recall, confusion; `format_report`.
- `research/labelling.py` + `_sheet_template.py`: `collect_candidates` (Trend Template passers with weekly Stage 2 on the scan dates of the current config; detector run only to stratify), `sample` (six strata, per-stratum cap, instrument diversity, deterministic, shuffled), `write_outputs` (`candidates.json`, hidden `key.json`, `labelling_sheet.html`: canvas charts with 50/150-day averages and volume, label dropdown + notes, progress in localStorage, Download CSV; symbol and date hidden by default to avoid hindsight), `import_labels` (CSV → fixtures; unknown label or id refused).
- `cli_research.py`: `vcp research golden [--split] [--record]`, `vcp research labelling-sheet --from --to [--per-stratum] [--seed] --out`, `vcp research import-labels --candidates --labels [--fixtures]`.
- `tests/regression/test_vcp_golden.py`: per-fixture baseline check (fails on any changed answer) and the development/holdout report; skipped while no fixtures exist.
- README "Golden dataset" commands; CHANGELOG.

**Tests** (`test_vcp_golden.py`, 6): holdout share ~30 % and fixed; JSON round trip, files, folder/label mismatch, unknown label; agreement, ambiguous exclusion, production and per-class precision/recall, confusion, report text; failed_vcp judged on status (a volume breakout then a close back below → FAILED); sampling balance, per-instrument limit and spacing, determinism; the sheet and candidates carry no detector output, the key does; CSV import with notes containing commas, blank labels skipped, unknown label refused. `test_pipeline_e2e.py`: sheet from the e2e scan, CSV import, `golden --record` report.

**Verification.** Full suite: 1,082 passed, 2 skipped (golden regression, no fixtures yet). `ruff check`, `ruff format --check`, `mypy --strict src` clean.

### Phase 6 step 8 follow-up — near-A+ stratum; the sheet generated (2026-10-02 13:46–14:00 IST)

**Historical scans for the sheet** (on a copy, `data/golden_src.duckdb`; the main DB is untouched): universe / RS / Trend Template for the last session of each month 2024-10 .. 2026-09 (24 dates, 12:45–13:45 IST, ~2.5 min each), config `7de9afd37f3a`.

**Finding: no A+ in two years.** Over 3,711 passer windows (weekly Stage 2) the detector gives VCP 539, VCP_LIKE 622, NONE 2,523, no pattern 27, **A_PLUS_VCP 0**. Of the 539 VCPs, 518 fail A+'s tight-pivot rule (last 10 bars within 5 %), 377 have fewer than 3 contractions, 279 fail volatility contraction, 244 a final contraction > 8 %, 235 volume dry-up; 51 miss exactly one A+ rule (42 the tight pivot). The tight pivot is the binding A+ constraint; the owner's labels will calibrate it.

**Change.** New hidden stratum `NEAR_A_PLUS` (a VCP missing exactly one A+ rule) so candidate A+ charts reach the sheet even when the detector finds none. Test: `test_near_a_plus_stratum`. Full suite: 1,083 passed, 2 skipped; ruff, format, mypy clean.

**Sheet**: `data/labelling/` (not in git): `labelling_sheet.html`, `candidates.json`, hidden `key.json`; seed 20261002, 25 per stratum.

## Phase 6 validation — mark check (A) and outcome study (B) (2026-10-03)

Worktree `vcp_ai_p6s9`, branch `p6-validation`.

**Decision (owner, 2026-10-03).** Labelling charts blind was hard for the owner, and he was concerned that wrong labels would bias the detector. Phase 6 acceptance moves from a labelled golden set to:
- **A**: a mark check of the detector's geometry;
- **B**: an outcome study.

The golden harness stays, and labelling is optional. Spec: VCP_SPECIFICATION §57 (acceptance basis) and §62 (outcome study).

**Change.**
- `research/outcomes.py` with `vcp research outcomes --from --to --split [--variant NAME:SECTION.KEY=VALUE,...] [--csv]`. It works out the scan-date outcome (+10 % before −7 % within 60 sessions; a bar touching both counts as LOSS) and a breakout trade (first close above the pivot within 20 sessions on ≥ 1.5× the 50-bar volume; exit at +10 %, −7 % or 60 sessions; censored without 60 sessions after entry). It reports Wilson CIs and excess over same-date passers by group: A+, near-A+, VCP, VCP-like, none. Development and validation are reported separately, and variants run on development only.
- `research/review.py` with `vcp research review-sheet --from --to [--seed] --out`. It draws a quota sample (A+ 4, near-A+ 8, VCP 12, VCP-like 10, failed/invalidated 5, none 5), reruns the detector, and computes marks (base start, peaks and troughs with depth, primary pivot, a plain-words verdict). It writes `review_windows.json` and `review_sheet.html`, with answers yes / partly / no / unsure plus notes.
- `_sheet_template.py` now takes `__HELP__` and `__OPTIONS__` and draws marks when a window has them; the labelling sheet is unchanged.
- README, CHANGELOG.

**Tests.**
- `test_vcp_outcomes.py` (6):
  - win, loss and none, including a bar that touches both;
  - returns and excursions;
  - censoring;
  - Wilson interval;
  - excess against same dates;
  - breakout-trade volume rule;
  - trade statistics.
- `test_vcp_review.py` (3):
  - quotas and determinism;
  - marks sit on chart bars at the stated prices;
  - files contain no leftover placeholders;
  - no-pattern marks.

**Verification.** Full suite: 1,092 passed, 2 skipped (golden regression, no fixtures). The first run caught one issue: the snapshot guard test flagged the outcome query's literal `'LIVE'` filter, so it was changed to a bound parameter. `ruff check`, `ruff format --check`, `mypy --strict src` clean.

**Real-data run** on the copy `data/golden_src.duckdb`: 24 month-end scans, 2024-10 .. 2026-09, split 2025-09-30.

Scan-date win rate (excess over same-date passers):

| Group | Development | Validation |
|---|---|---|
| VCP | 41 % (−3), n 145 | 33 % (−4), n 199 |
| VCP-like | 48 % (+4), n 254 | 38 % (+1), n 239 |
| Near-A+ | 56 % (+11), n 16 | 40 % (+7), n 25 |
| None | 43 % (−1), n 967 | 41 % (+1), n 915 |

Breakout trades:

| Group | Development (trades, win %, expectancy) | Validation (trades, win %, expectancy) |
|---|---|---|
| VCP | 91, 26 %, −2.4 % | 104, 36 %, −1.0 % |
| VCP-like | 163, 47 %, +0.8 % | 130, 47 %, +0.8 % |
| None | 412, 35 %, −1.1 % | 386, 47 %, +1.1 % |

Variants (development only):
- ATR volatility measure: neutral.
- Old noise rule: near-A+ 33 % against 56 % (weak support for S1).
- Tight pivot for VCP: n 3, too few to judge.
- A+ right side 7.5 % / 10 %: A+ n 5 / 9, win rate 40 % / 33 % (no support for loosening).

Checked and rejected: the idea that "descending bases cause the failures". Descending-base VCP breakouts won 37 %, other VCPs 25 %.

**Result.** The VCP class shows no edge so far. **No threshold was changed**: this is a strategy decision for the owner, and the sample is small.

**Mark-check sheet**: `data/review/` (not in git), 40 windows, seed 20261003.

### Phase 6 validation follow-up — mark-check answers, larger sample, exit rules (2026-10-03 10:40–12:00 IST)

**Mark check (A)** (owner's answers in `data/review/review_answers.csv`): 32 yes, 6 partly, 2 unsure, 0 no out of 40. The detector's geometry is sound, so the weak outcome results do not come from bad marks.

Owner's note on window 30 (M&M 2024-11-29, depths 7.4 → 17.1 → 9.1 %, peaks 3222.1 / 3220.3): "T1 and T2 highs are almost the same, so counting should start from T2." Checked against the other answers:
- Merging *every* pair of equal highs would also change windows the owner marked "yes" (MAHABANK 67.74 / 67.70, SBIN, INDIANB, TORNTPHARM).
- The narrower version is consistent with all the answers: merge only when the next high is within tolerance **and** the next pullback is deeper.
- This is a detector rule change, so it is **pending an owner decision**; nothing has been changed.

**Larger sample (B).** Month-end universe, RS and Trend Template scans for 2022-01 .. 2024-09 were added to the copy `data/golden_src.duckdb` (33 dates, 10:40–11:52 IST; main DB untouched; 2022-01 all INSUFFICIENT_DATA). The development period is now 2022-02 .. 2025-09 with 8,409 windows; validation is unchanged.

**Exit rules** (code commit "Outcome study: fixed exit rules"): `TRADE_RULES` t10_s7 (default), t20_s7, t20_low8, hold_s7. Validation is shown only for the default rule or for one rule named with `--validate-rule`. The CSV has one trade column per rule. The e2e test now covers `review-sheet` and `outcomes`.

**Results** (development; full output in `data/review/outcomes_2022_2026.txt`):

Scan-date win rate (excess over same-date passers):

| Group | Win rate |
|---|---|
| VCP | 45.6 % (−0.6), n 888 |
| VCP-like | 47.5 % (+1.0), n 1,348 |
| Near-A+ | 51.9 % (+1.7), n 81 |
| None | 47.0 % (−0.2), n 6,084 |
| A+ | n 2 |

Breakout-trade average:

| Group | Trades | t10_s7 | t20_s7 | t20_low8 | hold_s7 |
|---|---|---|---|---|---|
| VCP | 588 | +0.25 % | +0.94 % | +1.05 % | +2.43 % |
| VCP-like | 873 | +0.98 % | +1.54 % | +1.63 % | +2.67 % |
| None | 3,017 | +0.74 % | +1.56 % | +1.62 % | +2.98 % |

The `noise_old` variant gives about the same results.

By year, the regime dominates. With hold_s7, VCP and the other passers both made about +9 % in 2023, and both were negative in 2022, 2024 and 2025.

**Disclosure.** That by-year table was computed from the CSV, so its 2025-Q4 and 2026 rows include validation windows under hold_s7: VCP +2.9 %, other passers +7.6 % for 2026. Strictly, the "look once" for hold_s7 on validation has therefore been used informally. It points the same way: no VCP edge.

**Conclusion.**
- On 2022–2025 NSE data the VCP class does not pick better setups than other Trend Template passers.
- The exit rule and the market regime matter far more than the class.
- No threshold or rule was changed.
- Owner decisions are pending: (1) the equal-high merge rule; (2) how to use the VCP class from here on (descriptive or chart-review filter versus an edge); (3) which exit rule, if any, to adopt.

## Decision T2 — research default exit rule hold_s7; VCP class as shortlist (owner, 2026-10-03 ~12:00 IST)

The owner chose three things:
- **"Let winners run" is the default exit rule** of the research tools: `outcomes.DEFAULT_RULE = "hold_s7"`. This affects `trade_ret`, the `trades / twin% / exp%` columns and the validation view. `breakout_trade` gained a `rule` argument.
- **The VCP class is a chart-review shortlist, not an edge.** Work moves on to Phase 7 scoring.
- **The equal-high merge is tested first** as a setting that is off by default (next entry).

The detector and the production config are unchanged.

Tests: `test_vcp_outcomes.py` uses `rule="t10_s7"` for the original checks and asserts that the default is hold_s7.

## Equal-high merge — research setting, tested on development (2026-10-03 12:00–12:10 IST)

**Origin.** The owner's mark-check note on M&M 2024-11-29 (see the Phase 6 validation follow-up). Owner decision: "test first".

**Change.**
- New keys `vcp.swing.merge_equal_highs` (default **false**) and `vcp.swing.equal_high_tolerance_pct` (1.5).
- `segmentation._merge_equal_highs` runs after the noise merge. While peak k+1 is within the tolerance of peak k **and** its pullback is deeper, peak k+1 is removed; removed peaks go to `merged_peak_dates`.
- Spec §8.1 details and the §60 YAML; `strategy.yaml`; the §60 contract test.
- `vcp research outcomes --scan-config-hash` reuses Trend Template scans made under an earlier hash, because new keys change the scan config hash.

**Tests.**
- M&M-like path: 7 → 17 → 9 % becomes 17 → 9 %; the default is unchanged.
- A tightening pair with equal highs (14 → 6 %) stays as two contractions.
- A high outside the tolerance is never merged.

**Real-data run** (development 2022-02 .. 2025-09, copy DB, scans under `7de9afd37f3a`, exit rule hold_s7):
- Variant `eqhigh` (1.5 %) changes the class of 468 of 10,546 windows (4.4 %): 171 VCP-like → VCP, 172 VCP-like → none, 87 none → VCP-like, 23 VCP-like → near-A+, 2 → A+.
- M&M 2024-11-29 becomes VCP (it was VCP-like), as the owner expected.

| Group | Current | eqhigh | eqhigh3 (3 %) |
|---|---|---|---|
| VCP | n 888, win 45.6 %, trade avg +2.4 % | n 1,024, 45.0 %, +2.8 % | n 1,122, 45.1 %, +3.0 % |
| Near-A+ | n 81, 51.9 %, +2.2 % | n 96, 52.1 %, +4.2 % | n 118, 49.2 %, +4.3 % |
| None | trade avg +3.0 % | +2.9 % | |

**Result.** Neutral to slightly positive, and well within noise. It matches the owner's chart reading and harms nothing measurable.

**Not merged.** Any merge changes the config hash and therefore the scan ids, so it needs an owner decision (switch on or keep off) and a main-DB rerun of the recent Trend Template and VCP scans under the daily-run lock.

## Decision T3 — equal-high merge switched on, `vcp-1.1.0` (owner, 2026-10-03 ~12:20 IST)

The owner chose "switch on + rerun".

**Change.**
- `vcp.swing.merge_equal_highs` default and `strategy.yaml` set to **true**; spec §60 and the contract test updated.
- `VCP_ALGORITHM_VERSION` `vcp-1.0.0` → **`vcp-1.1.0`** (the default detection changed).
- The scan config hash changes from `7de9afd37f3a` to **`64da9482a769`**, so scan ids change. Older scans stay under the old id.

**Real-data check on a copy of the main DB** (12:16–12:22 IST):
- Trend Template verdicts are unchanged. 30 Sep: 267 PASS, results hash `482d2509e9c17b21`. 1 Oct: 203 PASS, `8bdc231dc0f6fb4c`.
- VCP 30 Sep: 47 VCP (was 45), hash `b84c05af39efb2ad`.
- VCP 1 Oct: 40 VCP (was 37), hash `94ff3fc2dc695124`.
- A rerun of 1 Oct gives an identical hash.

**Tests.** The segmentation tests now run with the merge on by default; the "off" case is explicit. Full suite: 1,095 passed, 2 skipped. ruff, format and mypy clean.

### Equal-high merge applied to the main DB (owner approved; 12:22–12:28 IST 2026-10-03, code at 8fc9d86)

Under the daily-run lock:
- Database check passed; backup `data/backups/vcp_scanner_20261003_065217.duckdb` (1.97 GB).
- `p6-equal-highs` fast-forwarded into `main` and pushed.
- Trend Template under `64da9482a769`: 30 Sep 267 PASS, hash `482d2509e9c17b21`; 1 Oct 203 PASS, `8bdc231dc0f6fb4c`.
- VCP: 30 Sep 47 VCP, `b84c05af39efb2ad`; 1 Oct 40 VCP, `94ff3fc2dc695124`.
- All four hashes are identical to the copy check. The copy was deleted.

**Note.** The 2026-10-03 09:05 IST catch-up run (when the PC came back on) stopped during its backup step. It left `data/backups/vcp_scanner_20261003_033533.duckdb.partial` (318 MB) and no "end" line or summary. The main DB check passed afterwards. There were no new prices to miss (2 Oct was a holiday; 3 Oct is a Saturday).

## Phase 7 step 1 — component scores (2026-10-03)

Worktree `vcp_ai_p7s1`, branch `p7-step1-components`. Plan sent to the owner on 2026-10-03 around 12:40 IST.

**Owner decisions:**
- Scores apply to the ranked list per the spec; research also scores all Trend Template passers.
- Weights and bounds stay as in the spec.

**Change.**
- `scoring/components.py`:
  - `linear`;
  - `score_component` (NULL sub-components dropped, component renormalized over the available ones);
  - `trend_score`, `vcp_score` (volatility ratio of the configured measure; no volume input), `volume_score`, `rs_score`;
  - bar measurements `measure_up_down_volume` and `measure_distribution`.
- `config/models.py`: sub-component names are validated (`_expect_names`). The config hash is unchanged.
- SCORING_SPECIFICATION §11 records the implementation details; CHANGELOG.

**Gap found and fixed.** The scoring config accepted any sub-component name, so a typo would have silently dropped a sub-component. It is now refused at load.

**Verification.** Full suite: 1,102 passed, 2 skipped. ruff, format and mypy clean.

**Tests** (`test_scoring_components.py`, 7):
- `linear` mapping, clipping and smaller-is-better bounds;
- trend known answer (54.0), points and max points, NULL renormalization, all-missing and NaN cases;
- VCP known answer (50), best-end clipping, no volume parameter;
- volume and RS known answers, including `min_rs_rank` 99;
- up/down volume (ratio, too few bars, zero volume, no-down cap);
- distribution days (count, 1.5× threshold, day excluded from its own average, too few bars, missing volume);
- misspelt name refused;
- determinism.

## Phase 7 step 2 — final score and ranking percentile (2026-10-03)

Worktree `vcp_ai_p7s2`, branch `p7-step2-final`.

**Change.**
- `scoring/final.py`:
  - `final_score`: weighted average over the non-NULL components; a NULL or absent component is flagged and its weight renormalized away; effective weights sum to 100.
  - `ranking_percentiles`: per group (confirmation state); tie-aware; a group of one gets 100.
- SCORING_SPECIFICATION §11 records the step-2 details; CHANGELOG.

**Verification.** Full suite: 1,106 passed, 2 skipped. ruff, format and mypy clean.

**Tests** (`test_scoring_final.py`, 4):
- the full weighted average;
- NULL fundamentals renormalized and flagged (an absent component equals NULL; a fundamental score of 0 stays 0);
- other NULL components;
- nothing available gives a NULL final score;
- percentile: top 100, bottom 0, ties shared, groups kept separate, NULL scores get no percentile.

## Phase 7 step 3 — scores stored, `vcp compute scores`, daily-run step (2026-10-03)

Worktree `vcp_ai_p7s3`, branch `p7-step3-persist`.

**Change.**
- **Tables** `setup_scores` and `score_components` (DATABASE_SCHEMA §35 implementation note), created by `migrate()`.
- **`scoring/engine.py`**:
  - `SetupInputs` and `PatternInputs`;
  - `score_setup` (the four components plus the final score);
  - `score_scan`, which ranks only eligible setups. Eligible means VCP_LIKE or better **and** FORMING, PIVOT_READY or BREAKOUT; invalid or failed patterns are scored but not ranked (decided 2026-10-03, see SCORING_SPECIFICATION §11).
- **`data/repositories/duckdb_score_repository.py`**: `load_inputs` (Trend Template PASS rows, the primary VCP pattern, features now and 21 sessions earlier, 76 bars) and `save_scan` (replaces the scan; bulk insert), plus `score_results_hash`.
- **`cli_scores.py`**: `vcp compute scores --as-of`. It refuses to run without both the Trend Template and VCP scans, and records a `SCORE` scan run.
- **`daily.py`** runs a "scores" step after VCP.
- README, spec §11 and CHANGELOG updated.

**Tests.**
- `test_scoring_engine.py` (4):
  - full known answer, including the volume measurements;
  - a passer without a pattern is scored but not ranked;
  - eligibility and per-state ranking (INVALIDATED, NONE and no-pattern are not ranked);
  - storage round trip, a rerun replaces the rows, NULL sub-component rows, order-independent hash.
- `test_daily_run.py`: the step list now ends with `compute scores`.
- e2e: scores for the scan date; a rerun gives the same hash; row count equals the passers; no percentile on ineligible rows; a missing scan is refused.

**Verification.** Full suite: 1,110 passed, 2 skipped. ruff, format and mypy clean. **Not merged yet**: the daily run would start writing scores into the main DB, so this waits for the owner-approved apply (plan step 6).

**Real-data check on a copy of the main DB** (13:07 IST; about 1 s per date):
- 30 Sep: 267 passers scored, 78 ranked; top score 77.4.
- 1 Oct: 203 scored, 54 ranked; top 75.1. A rerun gives an identical hash (`189ee38cce1dc6d1`).
- Sub-component coverage on 1 Oct: tightening and volatility are present for 78 of 203 (they need two or more contractions); everything else for all 203.

**Calibration note for step 5.** The spec bound for `pivot` (right-side range 8 % → 2 %) gives almost every setup about 0: the median right-side range is 11.8 %, and the average normalized value is 2.2. `contraction_sequence` averages 13.5 among all passers. These are spec values, left unchanged (owner decision); the step-5 outcome check will show whether they matter.

## Phase 7 step 4 — `vcp scores list` / `vcp scores explain` (2026-10-03)

Worktree `vcp_ai_p7s4`, branch `p7-step4-explain` (on top of step 3; not merged until the main-DB apply).

**Change.**
- `cli_scores_view.py` reads the current config's score scan for a date (the latest when no date is given).
- `list` shows the ranked setups; `--all` adds unranked passers; `--limit` caps the rows.
- `explain SYMBOL`:
  - each component's score × effective weight = contribution;
  - for every sub-component: raw value, 0–100 value, points of max, and the configured bounds;
  - flags and ranking context.
- Symbols are case-insensitive, and an instrument id also works. Registered in `cli.py` as `vcp scores`; README and CHANGELOG updated.

**Verification.** Full suite: 1,110 passed, 2 skipped. ruff, format and mypy clean.

**Tests (e2e).** `list --all` output; `explain` of a scored stock (lowercase symbol, sub-components, points, the FUNDAMENTALS_UNAVAILABLE flag); an unknown symbol is refused.

**Real-data look** (copy of the main DB, 1 Oct):
- Top ranked setup: SIYSIL, score 75.1, VCP pivot-ready.
- Its explanation adds up by hand: 19.9 + 25.9 + 13.2 + 16.1 = 75.1.

## Phase 7 step 5 — score outcome check (2026-10-03 13:21 IST)

Worktree `vcp_ai_p7s5`, branch `p7-step5-study` (on top of step 4).

**Change.**
- `research/outcomes.collect_windows(scoring=...)` attaches each window's final score, its eligibility and the component scores. The inputs are the same bars, features and RS rank the production scorer reads, plus `scoring.engine.pattern_inputs` from the in-memory detection.
- `research/score_study.py`: per-date quintiles, Spearman IC with t-statistic, report.
- `vcp research score-outcomes --from --to --split [--scan-config-hash]`: development for the final score and the four components; validation for the final score only.
- Spec §11 (step 5 results), README, CHANGELOG.

**Verification.** Full suite: 1,113 passed, 2 skipped. ruff, format and mypy clean.

**Tests** (`test_score_study.py`, 3): Spearman (perfect, inverse, too few, no spread); quintiles are formed within each date, so the regime is balanced across buckets; trade averages; IC; missing keys and censored windows are left out; too few windows on a date.

**Results** (research copy; scans `7de9afd37f3a`, detector vcp-1.1.0, `scoring-1.0.0`; full output in `data/review/score_study_2022_2026.txt`):
- Development, all passers: final score IC −0.003 (t −0.2); trend −0.002; VCP **+0.037 (t 2.3)**; volume −0.015; RS −0.033 (t −1.8).
- Ranked setups only: final score IC −0.011.
- Validation (one look, final score only): all passers +0.010; ranked −0.059.
- Breakout-trade averages rise from Q1 +1.7 % to Q5 +3.4 % (all passers, development), but not monotonically.

**Conclusion.** Scores are reproducible and explainable (the Phase 7 acceptance), but the spec score has **no predictive value** for 60-session outcomes on 2022–2025 NSE data. Only the VCP-shape component carries a small signal. No weight was changed; this goes to the owner.

### Phase 7 applied to the main DB (owner approved; 13:30–13:32 IST 2026-10-03, code at 6af5bc5)

Under the daily-run lock:
- **Copy check first:** 30 Sep 267 scored / 78 ranked, hash `0ca05805c3466143`; 1 Oct 203 / 54, `189ee38cce1dc6d1`. The copy was deleted.
- **Backup** (database check passed): `data/backups/vcp_scanner_20261003_080104.duckdb` (1.98 GB).
- **Merge:** `p7-step5-study` (steps 3–5) fast-forwarded into `main` and pushed.
- **Main DB:** `compute scores` for 30 Sep and 1 Oct. Both hashes are identical to the copy check.

From the next session on, the daily run scores every new date after VCP detection.

**Owner decisions (2026-10-03).** Keep `scoring-1.0.0` as an explainable ordering, with no tuning on 44 month-end dates. Next is Phase 8 (fundamentals); re-check the score after that.

## Decision F1 — fundamentals skipped for now (owner, 2026-10-03 ~13:45 IST)

The owner was offered NSE filings (recommended), a paid vendor, or skipping, and chose **skip**. The history question ("from 2025 only") does not apply while fundamentals are skipped.

**Effect.** No fundamentals are ingested. `fundamental_score` stays NULL, so every setup score is flagged `FUNDAMENTALS_UNAVAILABLE` and the 10 % weight is shared among the technical components (SCORING_SPECIFICATION §1). Nothing in code or config changes. Work moves to Phase 9 (backtesting).

**Findings kept for when fundamentals are picked up** (read-only probes on 2026-10-03; scripts in `~/vcp_spike/p8_probe*.py`):
- **Old feed:** `api/corporates-financial-results?index=equities&symbol=X&period=Quarterly|Annual` lists filings up to Jan 2025. Each has `broadCastDate` (the point-in-time timestamp), a consolidated / non-consolidated flag, revision fields and an XBRL link (`nsearchives.nseindia.com/corporate/xbrl/...xml`). Tags include RevenueFromOperations, ProfitLossForPeriod, ProfitOrLossAttributableToOwnersOfParent, Basic EPS, ProfitBeforeTax, FinanceCosts, DepreciationDepletionAndAmortisationExpense, and DebtEquityRatio (only some companies). Contexts `OneD` = quarter, `FourD` = year to date. History goes back to 2007.
- **New feed:** `api/integrated-filing-results?index=equities&symbol=X` covers filings from Feb 2025 (SEBI integrated filing), with `broadcast_Date` and an inline-XBRL HTML link (`.../corporate/ixbrl/INTEGRATED_FILING_INDAS_..._iXBRL_WEB.html`). Latest for SIYSIL: the June 2026 quarter, broadcast 30-Jul-2026 22:41.
- **Not usable as history:** `api/results-comparision?symbol=X` is a summary that includes the debt/equity ratio, but it is not point-in-time.
- **Gaps:** ROE needs equity, which is filed only half-yearly (statement of assets and liabilities), so expect ROE and debt to be missing for many stocks.
- **Cost:** about 26,000 files for 2021–2026, roughly 8 hours at 1 request per second.

## Phase 9 step 1 — scans about 5× faster (2026-10-03)

Worktree `vcp_ai_p9s1`, branch `p9-step1-speed`. Owner decisions for Phase 9 (2026-10-03):
- **Weekly** historical scans for the backtest.
- **Walk-forward periods:** development 2022-02 .. 2024-06; validation 2024-07 .. 2026-09; the true out-of-sample test is **live paper tracking from Oct 2026**, because 2025-10 .. 2026-09 has already been looked at in outcome checks.

**Found.** One historical date on the research copy took about 160 s:

| Step | Time |
|---|---|
| Universe | 18 s |
| RS | 7 s |
| Trend Template | 93–105 s |
| VCP | 29 s |
| Scores | 1 s |

Weekly 2022–2026 would have taken about 11 hours. Two causes:
1. **pandas probing.** DuckDB's Python client tries `import pandas` for every bound parameter it converts. pandas is not installed, so each attempt was a failed import that searched the whole path: 327,690 failed imports in one Trend Template scan (cProfile; import-hook count).
2. **Row-by-row writes.** `executemany` sends one statement per row. It accounted for 13 of 24 stack samples (about 40 s) in `save_trend_template_results`, which writes 1,080 results and 10,800 conditions.

**Change.**
- `duckdb_store._mark_pandas_absent()`: when pandas is not installed, `sys.modules["pandas"] = None`, so each probe fails at once. Nothing in the project uses pandas, and an installed pandas is left alone.
- `DuckDBStore.upsert_rows(insert_sql, rows)` runs an existing `INSERT INTO t (cols) VALUES (?, …) [ON CONFLICT …]` statement as one set-based insert from an Arrow view. The conflict handling is unchanged. It refuses other shapes, and parameters after VALUES.
- Used for: Trend Template results, conditions and weekly context; RS snapshots; universe memberships; `scan_run_results`; `vcp_scan_run_results` (now with an explicit column list).

**Result** (2023-06-23, research copy):

| Step | Before | After |
|---|---|---|
| Universe | 17.3 s | 1.0 s |
| RS | 7.3 s | 0.9 s |
| Trend Template | 93.5 s | 22.3 s |
| VCP | 26.9 s | 7.8 s |
| Scores | 1.0 s | 0.9 s |
| **Total** | **146 s** | **33 s** |

Weekly 2022–2026 now takes about 2.3 hours.

**Equivalence.**
- Identical results hashes old vs new: Trend Template `02841b3398789508`, VCP `a0a2bba794e2038b`, scores `f47dd4f182a1c759`; Trend Template 2023-06-16 `998b7019692f9d2b` in two runs each way.
- RS rows are identical.
- Universe memberships: the same eligibility for all 2,202. Seven rows differ only in the 16th significant digit of `avg_traded_value`. That comes from DuckDB's parallel summation order, which varies from run to run, not from the write path.

**Verification.** Full suite: 1,116 passed, 2 skipped, in 135 s (was 186 s). ruff, format and mypy clean.

**Tests** (`test_store_upsert_rows.py`, 3): upsert inserts and updates exactly like `executemany`; other statement shapes are refused; pandas is marked absent.

## Phase 9 step 2 — forward labels (2026-10-03)

Worktree `vcp_ai_p9s2`, branch `p9-step2-labels`.

**Change.**
- `backtest/labels.py`: the `forward_labels` function, `LABEL_VERSION = labels-1.0.0`.
- Table `forward_labels` (DATABASE_SCHEMA §49A).
- `data/repositories/duckdb_label_repository.py`:
  - `pending`: scored observations of the config without a complete label; the pivot comes from the primary pattern of the matching VCP scan;
  - bulk bar load;
  - set-based upsert.
- `cli_labels.py` (`vcp compute labels`).
- The daily run adds a "forward labels" step once after the per-date scans.
- PROJECT_DESIGN §39 implementation note, README, CHANGELOG.

**Verification.** Full suite: 1,120 passed, 2 skipped. ruff, format and mypy clean. Not merged until the main-DB apply (plan step 7): the daily run would start writing labels.

**Tests.**
- `test_forward_labels.py` (4):
  - returns and excursions, completion, no pivot;
  - partial labels while bars are missing;
  - breakout needs the volume (1.4× is not enough, 2× is), failure versus holding, failure window still open;
  - no breakout after 20 sessions; a missing volume cannot confirm a breakout.
- `test_daily_run.py`: the steps end with `compute labels`.
- e2e: one label per scored passer, none complete (no later bars).

## Phase 9 steps 3–5 — event engine, walk-forward periods, bias checks (2026-10-03)

Worktree `vcp_ai_p9s3`, branch `p9-step3-engine` (on top of step 2; not merged until the main-DB apply).

**Change.**
- **Step 3**
  - `backtest/engine.py`: `run_signals` (watch, breakout on volume, exit rule, one position per stock, newer scan replaces a watch; events ENTRY_SIGNAL / BREAKOUT / STOP / EXIT_SIGNAL / TIME_EXIT / INVALIDATION / OPEN_AT_END) and `run_portfolio` (maximum positions, equal size, higher score first, daily mark-to-market).
  - `backtest/metrics.py`: trade and equity statistics.
  - Tables `backtest_runs` and `backtest_events`; `duckdb_backtest_repository.py` (signals from stored score scans and primary patterns; final contraction low from `vcp_contractions`; bars; save).
  - `vcp backtest run`.
- **Step 4:** `backtest/periods.py` and `config/backtest.yaml` (outside the scan hash) with the owner's periods and guards; `vcp backtest walk-forward [--validation] [--test]`.
- **Step 5**
  - `backtest/lookahead.py`: `vcp backtest lookahead-check --as-of D [--variant prices|corporate-actions]` (temporary copy, truncate, rebuild, compare Trend Template, VCP and scores; exit code 2 on differences).
  - `backtest/bias.py`: `vcp backtest bias-report`.
- PROJECT_DESIGN §38/§40/§41 implementation notes, DATABASE_SCHEMA §48, README, CHANGELOG.

**Tests.**
- `test_backtest_engine.py` (5):
  - breakout entry needs volume, and the scan-date bar is not an entry; target exit;
  - the stop wins when stop and target hit on the same bar; costs; no-breakout invalidation after 20 sessions;
  - a newer scan replaces the watch, signals are skipped while in a position, time exit after 60 sessions;
  - open at the end is not a trade;
  - portfolio slots, score priority and equity.
- `test_backtest_periods.py` (3): the shipped yaml equals the defaults (the owner's decision); overlap, order and unique names are checked; months.
- `test_lookahead_compare.py` (1): differences counted per table; float noise below 6 decimals ignored.
- e2e: `backtest run`, `walk-forward` (validation and test hidden), `lookahead-check` IDENTICAL with the copy removed afterwards, `bias-report`.

**Verification.** Full suite: 1,129 passed, 2 skipped. ruff, format and mypy clean.

**Addition:** `--baseline` (`backtest run` / `walk-forward`) trades every passer whose primary pattern has a pivot, whatever its class or status. It runs the same engine and rules, as the comparison for the VCP classes.


## Phase 9 step 6 — first backtest report (2026-10-03 16:27–19:19 IST)

**Weekly history scans** on the research copy `data/golden_src.duckdb` (main DB untouched):
- 248 weekly dates (last session of each week, 2022-01 .. 2026-09), current config `64da9482a769`, run 16:27–19:01 IST without errors.
- Then `compute labels`: 46,238 observations labelled, 43,043 complete.

Full output: `data/review/backtest_report_2026-10-03.txt`.

**Development period (2022-02 .. 2024-06)**, default exit rule hold_s7, 15 bps per side, watch 20 sessions, breakout on 1.5× volume:

| Signals | Trades | Win % | Avg trade | Profit factor | Portfolio (10 slots) CAGR / max DD / Sharpe |
|---|---|---|---|---|---|
| VCP classes (VCP-like, VCP, A+) | 1,803 | 29.3 % | +3.77 % | 1.74 | 6.8 % / −40.6 % / 0.42 |
| VCP + A+ only | 1,023 | 29.6 % | +4.24 % | 1.84 | 13.5 % / −25.5 % / 0.73 |
| Baseline: every passer with a pivot | 3,422 | 29.4 % | +3.89 % | 1.77 | 28.0 % / −24.3 % / 1.28 |

Exit rules on the VCP classes (per trade):

| Rule | Avg trade | Profit factor |
|---|---|---|
| t10_s7 | +0.93 % | 1.25 |
| t20_low8 | +2.18 % | 1.44 |
| hold_s7 | +3.77 % | 1.74 |

**Reading.**
- Per trade, breakouts from Trend Template passers had a positive edge in this period under hold_s7 (profit factor about 1.75). The VCP classes add nothing over the baseline. VCP + A+ is slightly better (+4.2 % against +3.9 %), within noise.
- The **portfolio numbers are fragile.** With 10 slots only about 240 of 1,800–3,400 trades are taken, and which ones depends on arrival order. The baseline's 28 % CAGR against the VCP classes' 6.8 %, from near-identical per-trade numbers, is luck of selection, not a property of the signals.
- Letting winners run beats fixed targets (consistent with the outcome study).
- Validation and live paper were not looked at.

**Bias checks.**
- **Look-ahead** (`lookahead-check --variant prices`): 2022-09-30, 2023-06-23 and 2024-06-28 were rebuilt from copies with all later prices deleted and all features recomputed. **Identical** Trend Template (1,030 / 1,077 / 1,275 rows), VCP (962 / 903 / 1,194) and scores (172 / 246 / 311). Phase 9 acceptance met: historical scans use only information available at the time.
- **Corporate actions** (`--variant corporate-actions`, 2023-06-23): later corporate actions deleted and adjusted prices rebuilt; result **identical**. A later split or bonus rescales price and volume together, so ratios and traded value are unchanged. 18.7 % of observations (2,300 of 12,183 ranked setups) have a later adjustment, with no effect seen on the checked date.
- **Survivorship:** every scan date is PARTIAL, because ASM/GSM surveillance history only starts on 2026-10-01. Prices come from bhavcopies, which include delisted stocks.
- **Period looks:** development 5; validation 0.

### Phase 9 applied to the main DB (owner approved; 19:47–19:48 IST 2026-10-03, code at 81f3d20)

Owner decisions:
- apply steps 2–6;
- keep the backtest history in the research copy `data/golden_src.duckdb` (main stays lean with live scans).

Under the daily-run lock:
- **Copy check:** `compute labels` gave 470 observations, all still open (the dates are 30 Sep and 1 Oct). Bias report: survivorship PARTIAL for 30 Sep and POINT_IN_TIME_COMPLETE for 1 Oct (ASM/GSM collection started on 1 Oct); no later corporate-action adjustments. The copy was deleted.
- **Backup** (database check passed): `data/backups/vcp_scanner_20261003_141759.duckdb` (1.98 GB).
- **Merge:** `p9-step3-engine` (steps 2–6) fast-forwarded into `main` and pushed.
- **Main DB:** tables `forward_labels`, `backtest_runs` and `backtest_events` created; 470 labels written, all open.

From the next trading day the daily run updates labels after scoring.

## Fix C11 — phantom bonus adjustments from preference-share bonuses (2026-10-04)

**Reported by the owner:** SIYSIL ranked #1 on 1 Oct, but on the chart its price was below the 50-day average, so it should not have passed the Trend Template.

**Cause.** NSE publishes bonuses of preference shares in the same corporate-action feed as equity bonuses. "Scheme Of Arrangement - Bonus Ncrps 4:1" (SIYSIL, ex 2026-08-21, plus a duplicate 3:1 record), "Bonus Ncrps 4:1" (TVSMOTOR, ex 2025-08-25) and "Bonus Ncrps 46:1" (TVSHLTD, ex 2026-09-08) were parsed as equity bonuses. Factors 0.2, 0.2 and 0.0213 divided every earlier price, although the stock price never changed. On 1 Oct SIYSIL's stored SMA50 was 370.26 against a close of 531.55 (real SMA50 580.55), so all Trend Template rules passed. Nothing checked that a large split/bonus factor actually appears in the raw prices; the gap net only covered the opposite case (a raw gap with no action).

**Scope (main DB before the fix).** 746 price adjustments with |factor − 1| > 5 %; 713 confirmed by the raw gap, 32 not. The severe unconfirmed ones (factor ≤ 0.7): SIYSIL, TVSMOTOR, TVSHLTD, DVL 2021-08-05 (NSE "Bonus 1:2", 0.667) and TPHQ 2023-04-18 (0.471). Main DB: SIYSIL passed on 30 Sep and 1 Oct (score 75.1, rank 1); TVSHLTD passed on both dates (64.5, not ranked). Research copy: SIYSIL 27 passes (6 ranked), TVSMOTOR 105 (25), TVSHLTD 70 (27).

**Fix (owner choice: parser fix + safety net).**
- `nse_ca.py`: a bonus subject that names a non-equity instrument (NCRPS, CCPS, CRPS, OCRPS, NCCRPS, NCPS, RPS, OCPS, CPS, preference shares, warrants, debentures, NCDs) is stored as UNMODELLED (warning), never as BONUS.
- `domain/corporate_actions.py`: `ratio_unconfirmed()` (a factor outside (0.7, 1/0.7) must show in raw_open(ex) / raw_close(prev)) and `superseded_by_unmodelled()` (an UNMODELLED reading on the same instrument and ex-date replaces an older BONUS/SPLIT reading).
- `adjustment/engine.py`: superseded readings are skipped; an unconfirmed factor is withheld (1.0) unless a MANUAL_OVERRIDE exists. `ca_worker.py` and `quality/scanner.py` now load ex-date prices for split/bonus dates too.
- `quality/events.py`: one CRITICAL `CORPORATE_ACTION_UNRESOLVED` event per ex-date with cause `ratio_unconfirmed`, blocking signals from the ex-date until resolved.
- Spec: DATA_SPECIFICATION §18A "Phantom-action safety net".

**Tests.** `tests/unit/test_fix_c11_phantom_actions.py` (5 tests: parser, ratio check, engine withholding/applying, supersede, one event for duplicate records). Full suite 1,134 passed, 2 skipped; ruff, format, mypy clean.

**Verification (copy of the main DB, 20:25–21:02 IST).** CA re-ingest from 2021, adjusted prices, features, scans for 30 Sep and 1 Oct, labels.
- SIYSIL 1 Oct: close 531.55, SMA50 580.55, SMA200 578.40 → Trend Template FAIL on both dates; no longer scored as eligible. TVSHLTD: close 11,886 vs SMA50 13,648.52 → FAIL. TVSMOTOR stays FAIL (SMA50 4,222.01 vs close 4,021).
- DVL's 0.667 factor withheld; one blocking `ratio_unconfirmed` event (2021-08-05) for review. TPHQ's factors stay applied (no usable raw bars around its ex-date; a 0.50-rupee stock outside the universe).
- Large adjustments: 746 → 741. Trend Template passes 30 Sep 267 → 265, 1 Oct 203 → 204. New 1 Oct top: MWL 72.1, GLAND 70.0, UFLEX 66.0, TFCILTD 65.7, MBAPL 64.8.

### Fix C11 applied (2026-10-04/05, code 355be64)

- **Main DB** (21:03–21:40 IST, under the daily-run lock; backup `data/backups/vcp_scanner_20261004_153355.duckdb`): CA re-ingest from 2021, adjusted prices, features, scans for 30 Sep and 1 Oct, labels. Result identical to the copy check: SIYSIL and TVSHLTD FAIL on both dates, DVL has one blocking `ratio_unconfirmed` event, Trend Template passes 265 (30 Sep) and 204 (1 Oct), 1 Oct top: MWL, GLAND, UFLEX, TFCILTD, MBAPL.
- **Research copy** `data/golden_src.duckdb` (21:43–00:51 IST; backup `data/backups/golden_src_pre_c11.duckdb`): same rebuild, then all 248 weekly scans (config 64da9482a769) rerun in date order, then labels. No errors.
  - Scans are point-in-time, so a phantom factor only distorted scans after its ex-date. SIYSIL passes 27 → 22 (the 5 from Aug–Sep 2026 removed; last pass now 2025-01-24). TVSHLTD 70 → 66 (Sep 2026 removed; last pass now 2026-04-17). TVSMOTOR 105 → 105: its passes were genuine, so the earlier "105 affected" count overstated the impact.
  - Total Trend Template passes 46,238 → 46,234 out of 284,738 rows. Stored backtest results from Phase 9 step 6 are stale only for those few setups; rerun the report when convenient.

## Multi-Strategy phase step 1 — strategy spec and schema plan (2026-10-05)

**Plan:** "Multi-Strategy Research Platform — Phase Plan" (Claude Docs, 2026-10-04). Owner accepted D1–D6 on 2026-10-05: phase now, before Phases 10–13; multi-label; Trend Template for every strategy (only High Tight Flag may waive the 52-week-low rule); one ranked list per strategy until step 7; Flat base and Three Weeks Tight first; about 30 charts reviewed per strategy.

**Docs only, no code or data change.**
- `STRATEGY_SPECIFICATION.md` (new): strategy registry (`patterns/registry.py`, `config/strategies/<id>.yaml`), per-strategy versions and config hash, scan ids, Trend Template per strategy, the setup record with a common 0–3 grade, the multi-label rule and overlap measures, the detector contract, the scoring split (pattern part and dry-up measure per strategy, the rest shared), breakouts/labels/backtests keyed by strategy, validation looks counted per strategy version, and the exact-reproduction test that step 2 must pass.
- `DATABASE_SCHEMA.md` §35A (new): `strategy_setups`, `strategy_setup_status_history`, `strategy_breakout_events`, `strategy_scan_run_results`; `strategy_id` on `setup_scores`, `score_components`, `forward_labels` (key rebuilt), `backtest_runs` (+ `algorithm_version`), `scan_runs`; views `setups`, `breakout_events`, `setup_scores_v`; `DATA_SCHEMA_VERSION` 1 → 2. §49A points to it.
- `AGENTS.md`: spec table row.

**Owner decisions on the open points (2026-10-05), all as recommended:** O1 new strategies' config hash chained on `scan_config_hash`; O2 VCP setups read through a view over `vcp_patterns`, no copy; O3 VCP thresholds stay in `strategy.yaml` / `scoring.yaml`; O4 new strategies store score rows only for stocks with a setup; O5 `vcp_score` / `vcp_weight` keep their names and mean "pattern score", with an alias view.

**Checks on the research DB (read-only, 2026-10-05).** The draft `setups` view over `vcp_patterns` returns 256,053 rows (255,772 with a stop level). Joining `setup_scores` to it by strategy, date, config hash, snapshot and primary flag finds the same 46,070 rows as today's scan-id slicing join. DuckDB 1.5.6 adds a column with a default to existing rows and can then set it NOT NULL.

**Noted for later.** The research DB has 46,243 `forward_labels` rows against 46,234 `setup_scores` rows; the 9 extra are probably left from the Fix C11 rescans. To be checked when the Phase 9 report is rerun.

## Multi-Strategy phase step 2 — strategy framework, VCP only (2026-10-05)

**Owner go:** 2026-10-05 ("start step 2"). Built as specified in STRATEGY_SPECIFICATION (decisions O1–O5).

**Code.**
- `domain/strategy.py` (new): strategy id rule, `VCP_GRADES`, `RANKED_STATUSES`, `detector_scan_id` / `score_scan_id` (VCP forms unchanged).
- `config/models.py`: `StrategyFileConfig`, `TrendGateConfig` (relaxed gate only for `high_tight_flag`, waiving only `above_52w_low`). `config/strategies.py` (new): loads `config/strategies/*.yaml` against the registry; `strategy_config_hash` (VCP = `scan_config_hash`; others chained on it, without `enabled`/`stage`).
- `patterns/registry.py` (new): `REGISTRY` with `vcp` (version, tiers → grades, `min_grade` 1, pattern scoring). `config/strategies/vcp.yaml` (new): legacy pointer.
- `scoring/engine.py`: `PatternScoring` protocol, `VCPPatternScoring`, `shared_components`; `score_setup` / `score_scan` take the strategy's scoring (default VCP). `scoring/final.py`: the pattern component may be named `VCP` or `PATTERN`.
- `data/storage/duckdb_store.py`: §35A DDL (`strategy_id` columns, four `strategy_*` tables, views `setups`, `breakout_events`, `setup_scores_v`); `_migrate_strategy_dimension`; `_rebuild_table` can fill columns that are new. `versioning.DATA_SCHEMA_VERSION` 2.
- Repositories: scores, labels, backtests and scan runs write `strategy_id`; labels and backtest signals read the pivot and stop through `setups` joined on strategy, date, config hash, snapshot and primary flag (no more scan-id slicing).
- CLI: `--strategy` on compute scores/labels, backtest run/walk-forward, scores list/explain; `compute setups`; `config hash --strategy`; validation looks counted per strategy.
- `scripts/compare_results.py` (new): row-by-row comparison of two databases.

**Deviation from the spec, decided while building (spec §8 updated):** the generic `StrategyDetector` protocol and scan runner are built in step 4 with the first new detector; VCP keeps its own scan path. The spec's planned extra indexes are not created (DuckDB zone maps and keys suffice; DATABASE_SCHEMA §35A.4 updated).

**Tests.** `tests/unit/test_strategy_framework.py` (registry, files, hashes incl. the pinned `64da9482a769`, scan ids, scoring split, CLI refusals) and `tests/unit/test_strategy_schema.py` (migration from the old layout, fresh schema, `setups` view, labels and signals equal to the old slicing joins, no leaks between strategies on the same stock, date and config hash); `tests/unit/_legacy_ddl.py` holds the old layouts. Full suite 1,169 passed, 2 skipped; ruff, format, mypy clean.

**Exit test (STRATEGY_SPECIFICATION §11.3), 11:28–11:37 IST, on copies only.** Old code (main 5251638) and new code each rebuilt Trend Template, VCP and scores for 30 Sep and 1 Oct on a copy of the main DB and for 10 weekly dates (2022-02-11, 2022-08-19, 2023-02-24, 2023-09-01, 2024-03-07, 2024-09-06, 2025-03-13, 2025-09-19, 2026-03-27, 2026-09-30) on a copy of the research DB, recomputed the labels of those dates, and ran the development walk-forward (default rule and `--baseline`). `compare_results.py`: **IDENTICAL** for every table (research: 256,053 patterns, 900,900 contractions, 795,517 pivots, 46,234 scores, 601,042 components, 46,241 labels, 360,566 Trend Template rows), all scan-run hashes, both backtests (metrics and 12,523 / 42,155 events). All migrated rows carry `strategy_id = 'vcp'`. A control run (main copy vs research copy) reports differences, so the comparison is not vacuous.

**Note.** Research labels went 46,243 → 46,241 in both copies: recomputing the 10 dates dropped 2 labels whose score rows no longer exist (left from the Fix C11 rescans). The other 7 such orphans remain; harmless, to be cleaned when the Phase 9 report is rerun.

**Not yet applied** to the main or research DB: the schema migration runs on the first open with the new code, so the merge waits for the owner's go (backup, daily-run lock).


### Multi-Strategy step 2 applied (2026-10-05 12:03–12:04 IST, code 2d8fc43)

Owner go 2026-10-05. Under the daily-run lock (`~/vcp_spike/ms2_apply.sh`):
- **Backups** (health-checked, keep 3): `data/backups/vcp_scanner_20261005_063329.duckdb`, `data/backups/golden_src_20261005_063338.duckdb`.
- **Merge:** `ms-step2-framework` fast-forwarded into `main` (5251638 → 2d8fc43) and pushed.
- **Migration** (`DuckDBStore.migrate()`), main and research DB: `setup_scores`, `score_components`, `forward_labels`, `backtest_runs` rebuilt with `strategy_id` (every row `vcp`); `scan_runs.strategy_id` = `vcp` for VCP and SCORE runs (main 6 + 4, research 501 + 499), NULL for Trend Template runs; new tables and views created; `setups` shows 4,928 (main) and 256,053 (research) rows.
- **Check:** row counts of the six key tables unchanged in both databases (main: 469 scores, 6,097 components, 474 labels; research: 46,234 / 601,042 / 46,243, 5 backtest runs). `vcp scores list` for 1 Oct shows the same ranking as before (MWL 72.1, GLAND 70.0, UFLEX 66.0, TFCILTD 65.7, MBAPL 64.8).

## Multi-Strategy phase step 3 — shared base measurements (2026-10-05)

**Owner decisions (2026-10-05):** start step 3; store nothing (option A of three: in-memory functions / new feature columns / a new weekly table). The plan's exit "feature rebuild on a copy matches row counts" is replaced by a real-data consistency check, since nothing is rebuilt.

**Code.** `features/base_measures.py` (new): `weekly_bars`, `weekly_close_range_pct`, `max_weekly_close_change_pct`, `prior_advance`, `base_extremes`, `volume_dryup_ratio` (definitions in STRATEGY_SPECIFICATION §12A). Pure: no storage, clock or provider access. VCP code untouched.

**Tests.** Full suite 1,189 passed, 2 skipped; ruff, format, mypy clean. `tests/unit/test_base_measures.py` (20 tests on hand-built series: ISO weeks, partial as-of week, holiday weeks, missing volume, tightness windows, ties, short windows, bad inputs, and the shared prior advance / base low / length equal to VCP's segmentation on a synthetic base).

**Real-data check** (copy `data/tmp/ms3/res.duckdb` of the research DB, 5 dates 2022-02-11, 2023-04-06, 2024-06-07, 2025-08-08, 2026-09-30, `~/vcp_spike/ms3_check.py`): `weekly_bars` from the adjusted daily bars equals the stored `weekly_prices` for all 386,220 completed weeks (OHLC exact, volume within 1e-9 relative: the SQL and Python sums add in a different order). `prior_advance` equals VCP's stored `prior_advance_return_pct` exactly for all 5,283 patterns with one; `base_extremes` gives the same base low and base length, and the base-start high equals VCP's `base_high`. In 516 of them (10 %) a later bar is above that high (a breakout): documented, since `base_extremes.high` is the window's highest high.

**Not changed:** the database (nothing written; the check ran read-only on a copy), VCP results, stored features.

## Multi-Strategy phase step 4a — Flat base and Three Weeks Tight spec (2026-10-05)

STRATEGY_SPECIFICATION §12B (scan path for new strategies), §13 Flat / tight base (`flat_base-1.0.0`), §14 Three Weeks Tight (`three_weeks_tight-1.0.0`): rules, tiers and grades, status, pivot and stop, score parts, stored details, worked examples, config. Owner signed off 2026-10-05 with every recommendation: G1 only grade 2+ ranked for the new strategies; F1 flat-base stop = right-side low (10 bars); F2 a close > 3 % above the pivot without breakout volume ends a flat base; T1 Three Weeks Tight on completed weeks only; T2 a 3WT stays a setup for 2 weeks after it completes; C1 chart-review sample of ~30 per strategy (12 grade 2, 8 grade 3, 6 grade 1, 4 near-misses, spread over the years). Docs only.

## Multi-Strategy phase step 4b — Flat base and Three Weeks Tight built (2026-10-05)

**Owner go:** 2026-10-05 ("go with all your recommendations and build step 4").

**Code.**
- `domain/strategy.py`: `Setup`, `Breakout`, `StrategyResult`. `patterns/strategy_base.py` (new): `StrategyDetector` protocol, `StrategyContext`, `DailyBars`, `find_breakout` (first breakout-volume close above the pivot, so weekly scans do not miss mid-week breakouts), `setup_status`.
- `patterns/flat_base/detector.py`, `patterns/three_weeks_tight/detector.py` (new): STRATEGY_SPECIFICATION §13, §14, on the shared measurements of §12A.
- `config/models.py`: typed settings (`FlatBaseSettings`, `ThreeWeeksTightSettings`, tiers, `RankingConfig`, `PatternScoringConfig`); `config/strategies/flat_base.yaml`, `three_weeks_tight.yaml` (enabled false, stage research).
- `patterns/registry.py`: entries build a `StrategyRuntime` (grades, min grade, pattern scoring, detector) from the strategy file; every file's settings are validated at load.
- `scoring/engine.py`: `GenericPatternInputs`, `FilePatternScoring` (pattern component `PATTERN`, the setup's dry-up ratio, grade >= min grade).
- `data/repositories/duckdb_strategy_repository.py` (new) and `cli_setups.py` (new): the §12B scan path (`vcp compute setups --strategy ID`): gate = Trend Template PASS, bars, detector, breakout events, status history, `scan_runs` type SETUP, run results. Scores read the primary setups (`strategy_setups`, measures in `details_json`); labels and backtests unchanged apart from reading the runtime's tiers.
- `research/strategy_review.py` (new), `vcp research review-sheet --strategy ID`: decision C1 sample from stored setups; the sheet now also draws a stop line and the breakout day.
- `cli_scores_view.py`: `PATTERN` component and its bounds in `scores explain`.

**Bug found and fixed before merge:** Three Weeks Tight counted "tight weeks" against the grade-3 change limit (1.0 %) instead of grade 2's (1.5 %), so a grade-2 pattern could show 1 tight week. Fixed, with a test; the strategy was rescanned.

**Tests.** `test_flat_base.py` (9), `test_three_weeks_tight.py` (9), `test_strategy_repository.py` (3), `test_strategy_review.py` (2), registry and settings tests in `test_strategy_framework.py`, and the end-to-end pipeline test now runs both strategies (setups, scores, labels, baseline backtest) and checks VCP's rows are untouched. Full suite 1,213 passed, 2 skipped; ruff, format, mypy clean.

**Research rescan** (copy `data/tmp/ms4/res.duckdb` of the research DB, code ad1a359 + the tight-weeks fix; 13:47–14:17 IST): both strategies over all 248 weekly dates, in order, then scores and labels; no errors. Counts, first development backtests and the Three Weeks Tight observation (steady drifts, wide weekly ranges) are in STRATEGY_SPECIFICATION §14.10.

**Chart review sheets (D6)** for the development period: `reports/chart_review/flat_base/review_sheet.html` and `reports/chart_review/three_weeks_tight/review_sheet.html` (30 windows each: 12 grade 2, 8 grade 3, 6 grade 1, 4 near-misses, 2022–2024).

**Not changed:** the main DB and the research DB (the rescan is on a copy; whether it replaces the research DB is the owner's call); VCP code and results.

### Step 4 rescan made the research DB (2026-10-05 15:01 IST, owner choice A)

Under the daily-run lock (`~/vcp_spike/ms4_apply.sh`):
- **Check before replacing:** `compare_results.py` research DB vs the rescan copy: every VCP and Trend Template table identical (256,053 patterns, 900,900 contractions, 795,517 pivots, 71,350 status rows, 8,166 breakout events, 360,566 Trend Template rows); the copy only adds 30,616 score and label rows (`flat_base` 16,876, `three_weeks_tight` 13,740), 30,616 strategy setups, their scan runs, and 6 development backtest runs (the step-4 comparison: 2 VCP, 2 per new strategy). The research DB was last written at 12:04 IST (step-2 migration), before the copy was made (13:47), so nothing was lost.
- **Backup** (health-checked): `data/backups/golden_src_20261005_093106.duckdb`; the copy passed its health check; then it replaced `data/golden_src.duckdb`. `golden_src_pre_c11.duckdb` kept.
- The rescan was made with the step-4 branch code before the final squash (13d437e for flat_base, ad1a359 for three_weeks_tight); their `src` and `config` equal 4596549.

## Multi-Strategy phase step 4c — chart review and its changes (2026-10-05)

**Chart review (D6)** of the step-4 sheets by the owner (answers in `reports/chart_review/*/review_answers_2026-10-05.csv`; summary in STRATEGY_SPECIFICATION §14.11): Flat base 27 yes / 3 partly / 0 no; Three Weeks Tight 23 yes / 4 partly / 1 unsure / 2 no, every negative with a note being a pattern that includes the sharp move into it (16–32 % deep). The steady-drift concern raised in §14.10 was checked against the answers and dropped (11 of 15 same-direction patterns marked yes).

**Owner decisions (2026-10-05):** R1 Three Weeks Tight 1.1.0, grade 2 needs pattern depth ≤ 15 %; R2 exit rules `hold_low8` / `hold_low5` for a development comparison; R3 Flat base unchanged. Code 60a6622 (tests: deep 3WT pattern capped at grade 1; `hold_low8` stop is the tighter of the setup low × 0.995 and −8 %). Full suite 1,217 passed, 2 skipped; ruff, format, mypy clean.

**Correction to the step-4 apply entry:** the Flat base rescan ran with 13d437e, whose `src` differs from 4596549 only in the Three Weeks Tight detector (the tight-weeks fix), so Flat base results are the same as with 4596549; the Three Weeks Tight rescan ran with ad1a359, identical in `src` and `config` to 4596549.

**Research DB** (`data/golden_src.duckdb`, 19:58–20:09 IST, under the daily-run lock, after tonight's daily run had finished at 19:51; backup `data/backups/golden_src_20261005_142803.duckdb`): Three Weeks Tight 1.1.0 over all 248 weekly dates (new strategy config hash 9ed3b8e7c31a; the 1.0.0 rows stay under df6cc5b0202a as a variant tried), scores and labels (13,740 observations), then development walk-forwards for 3 exit rules x 3 strategies (9 runs):

| Strategy | Rule | Trades | Win | Avg | PF | Portfolio CAGR / max DD |
|---|---|---|---|---|---|---|
| VCP | hold_s7 | 1,803 | 29.3 % | +3.77 % | 1.74 | 6.8 % / −40.6 % |
| VCP | hold_low8 | 1,767 | 32.6 % | +4.10 % | 1.76 | 9.3 % / −36.5 % |
| VCP | hold_low5 | 1,954 | 22.1 % | +3.02 % | 1.74 | 12.4 % / −37.7 % |
| Flat base | hold_s7 | 449 | 38.3 % | +5.58 % | 2.28 | 25.0 % / −16.5 % |
| Flat base | hold_low8 | 438 | 42.7 % | +6.48 % | 2.43 | 21.1 % / −16.5 % |
| Flat base | hold_low5 | 476 | 30.7 % | +4.86 % | 2.34 | 24.4 % / −15.9 % |
| Three Weeks Tight 1.1.0 | hold_s7 | 647 | 34.2 % | +5.01 % | 2.07 | 13.9 % / −23.3 % |
| Three Weeks Tight 1.1.0 | hold_low8 | 634 | 37.4 % | +5.48 % | 2.10 | 18.8 % / −22.6 % |
| Three Weeks Tight 1.1.0 | hold_low5 | 677 | 27.3 % | +4.61 % | 2.21 | 18.1 % / −19.6 % |

(Three Weeks Tight 1.0.0 with hold_s7: 783 trades, +4.71 %, PF 1.98, CAGR 15.4 % / −21.1 %.) Development only, survivorship PARTIAL. No rule dominates: `hold_low8` mostly ends up a −8 % stop (the setup low is usually further away), so it trades a slightly wider stop for a higher win rate; `hold_low5` cuts losers sooner but wins less often. The default rule stays `hold_s7`; the exit rule is revisited in step 7 with all strategies side by side.

## Multi-Strategy phase step 5a — Cup and handle and Double bottom spec (2026-10-05)

STRATEGY_SPECIFICATION §15: `cup_handle-1.0.0` (left lip, rounded cup 7–65 weeks, right lip, handle 5–25 sessions in the upper half; measurable rounded-vs-V rule; pivot = handle high, stop = handle low) and `double_bottom-1.0.0` (W with a slight undercut; pivot = middle peak, stop = right-side low), with tiers, score parts, worked examples. Owner signed off 2026-10-05 with every recommendation: C1 rounded rule (bottom share ≥ 0.15, ≥ 5 bars in the lowest quarter, bottom in the middle 70 %); C2 handle required; C3 cups up to 65 weeks; D1 both strategies built now; D2 double-bottom stop = right-side low; G1′ grade 2+ ranked; C4 chart review ~30 per strategy, same mix as step 4. Build continues in a new chat (owner request). Docs only.


## Multi-Strategy phase step 5b — Cup and handle and Double bottom built (2026-10-05)

**Owner go:** 2026-10-05 ("A–C as recommended, go ahead"; then option (a) for old double-bottom breakouts).

**Code.**
- `patterns/cup_handle/detector.py`, `patterns/double_bottom/detector.py` (new): STRATEGY_SPECIFICATION §15.1, §15.2, on the shared measurements of §12A and the step-4 contract (`find_breakout`, `setup_status`). New helpers `highest` / `lowest` in `patterns/strategy_base.py`.
- `config/models.py`: `CupHandleSettings`, `DoubleBottomSettings` (detector settings, tiers, ranking, scoring, with checks); `config/strategies/cup_handle.yaml`, `double_bottom.yaml` (enabled false, stage research, `ranking.min_grade` 2). Both registered in `patterns/registry.py`. Lookback: cup 480 bars, double bottom 455.
- Readings of §15.1 chosen by the owner before the build (§15.5): A rim check against the higher rim; B any high above the handle start before a breakout is `NO_HANDLE` (a close > 3 % above the pivot: `MOVED_ABOVE_BASE`); C cup points found from the as-of bar, so a broken-out cup stays visible about 4 sessions.
- **Found on the first real-data check and decided (option a):** a double bottom kept its base long after a breakout (about 75 % of its setups were BREAKOUT/FAILED, grade 2+ breakouts a median 35 days old). They are ranked, and the backtest engine enters on any breakout-volume close above the pivot, so stale Ws would have opened trades 15–20 % above the pivot. Now a double bottom is no setup more than `max_days_after_breakout` (10) sessions after its breakout (`OLD_BREAKOUT`). Options (b) note only and (c) a cross rule in the engine (all strategies; step 7) were rejected.
- `research/strategy_review.py`, `research/_sheet_template.py`: long bases get a longer chart (from 40 bars before the base start), and the stored points are drawn as labelled dots (cup L, B, R, H; double bottom L, B1, M, B2). The summary line shows cup/handle and undercut/bounce measures.

**Tests.** `test_cup_handle.py` (13: the §15.1 worked example → CUP_HANDLE_A, PIVOT_READY, pivot 196.20, stop 186; grade 2 without dry-up; a V cup not rounded → grade 1; no Stage 2 → grade 1; INSUFFICIENT_HISTORY, NO_HANDLE, MOVED_ABOVE_BASE, NO_CUP, NO_PRIOR_ADVANCE, TOO_DEEP; breakout freezes the handle then FAILED; STALE_DATA; lookback 480). `test_double_bottom.py` (13: the §15.2 worked example → DOUBLE_BOTTOM, PIVOT_READY, pivot 276.28, stop 262; grade 3 with dry-up; equal lows not a true undercut; no Stage 2 → grade 1; INSUFFICIENT_HISTORY, NO_W, NO_PRIOR_ADVANCE, TOO_DEEP, MOVED_ABOVE_BASE; breakout freezes then FAILED; OLD_BREAKOUT after 10 sessions; STALE_DATA). `test_strategy_review.py`: long-base chart and point marks. The e2e test runs all four new strategies (setups, scores, labels, baseline backtest). Unknown-strategy examples in `test_strategy_framework.py` now use `ascending_base`.

**Real-data check** (copy `data/tmp/ms5b/res.duckdb` of the research DB; 6 weekly dates 2022-06-24 … 2026-09-25; about 1.5 s per date): cup and handle 10–129 setups per date (grade 2+ about 15–20 %, mostly FORMING); double bottom after option (a) 2–36 per date (grade 2+ 23 of 71 rows). No errors.

**Not changed:** the main DB and the research DB (step 6 of the build plan asks the owner first); VCP code and results; no schema change.

### Step 5b research DB rescan, walk-forwards and review sheets (2026-10-05 21:29–22:05 IST, owner go)

- **Research DB** `data/golden_src.duckdb`, under the daily-run lock (`~/vcp_spike/ms5b_research.sh`, code 99224e0); backup `data/backups/golden_src_20261005_155936.duckdb` (keep=3; `golden_src_20261005_063338` rotated out, `golden_src_pre_c11` kept). cup_handle then double_bottom over all 248 weekly dates in date order (setups, scores), then labels: cup 15,176 observations (config a7f96c6ec87e), double bottom 3,063 (dd7107637b1b). No errors; about 11 minutes per strategy.
- **Counts, overlap and development walk-forwards** (`~/vcp_spike/ms5b_stats.py`, `ms5b_wf.sh`; 8 runs: 3 exit rules + baseline per strategy, development period only): STRATEGY_SPECIFICATION §15.6. Graded setups did no better than the baseline for either strategy; the double bottom is weak (119 trades, PF 1.29 with hold_s7).
- **Chart-review sheets** (decision C4; development period; 12 grade 2, 8 grade 3, 6 grade 1, 4 near-misses each): `reports/chart_review/cup_handle/review_sheet.html`, `reports/chart_review/double_bottom/review_sheet.html`. Marks checked against the setups (`~/vcp_spike/ms5b_marks.py`): every window has its 4 points in order, base start = L, pivot = R / M high × 1.001, cup stop = H; charts 243–367 bars. Shapes checked as text sketches (`ms5b_spark.py`): the double bottoms look like Ws.
- **Finding (cup):** most cups have the right lip well above the left lip (grade 2+ median 9.4 % above; 78 % more than 3 % above), because §15.1 bounds the right-lip gap only from above. Reported to the owner with options; the cup sheet is not sent for review until decided.
- The main DB was not touched; VCP, flat base and 3WT rows unchanged.

## Multi-Strategy phase step 5b — Cup and handle 1.1.0 (2026-10-05)

**Owner decision** (option a, after the §15.6 finding that 78 % of grade 2+ cups had the right lip more than 3 % above the left): a cup's right lip may be at most `max_right_lip_above_pct` (3 %, the rim tolerance) above its left lip, else `NO_CUP`. `cup_handle-1.1.0`, new config hash e36477f23b36; the 1.0.0 rows (a7f96c6ec87e) stay in the research DB as a version tried.

**Code:** `CupHandleDetectorConfig.max_right_lip_above_pct`, the check in `patterns/cup_handle/detector.py`, `config/strategies/cup_handle.yaml`. **Test:** right lip 2 % above the left is still a cup, 5 % above is `NO_CUP`. Full suite 1,245 passed, 2 skipped; ruff, format, mypy clean.

**Real-data check** (copy `data/tmp/ms5b/res.duckdb`, 3 dates): setups per date 14 / 15 / 8 (1.0.0: 39 / 129 / 83); grade 2: 6 / 4 / 1.

### Cup and handle 1.1.0 applied to the research DB (2026-10-05 22:35–22:46 IST)

- A 22:00 IST daily run (late slot; "all steps OK", ended 22:34) held the lock; the watcher `~/vcp_spike/ms5b_cup11_chain.sh` waited, then ff-merged bd595ab and, under the lock, backed up (`data/backups/golden_src_20261005_170507.duckdb`; keep=3 rotated out `golden_src_20261005_093106`), rescanned cup_handle 1.1.0 over all 248 dates (setups, scores), labels (2,268), and ran 4 development walk-forwards (3 rules + baseline). No errors. Results: STRATEGY_SPECIFICATION §15.7.
- Review sheet rebuilt from 1.1.0: `reports/chart_review/cup_handle/review_sheet.html` (30 windows, the C4 mix); marks checked (`ms5b_marks.py`: 4 points in order, pivot = R × 1.001, stop = H; charts 260–369 bars). Shapes eyeballed as text sketches: L at the prior peak, R back near it.
- The 1.0.0 rows (a7f96c6ec87e) and their 4 backtest runs stay as a version tried.

## Multi-Strategy phase step 5c — chart review of Cup and handle and Double bottom (2026-10-05)

**Chart review (C4)** of the step-5b sheets by the owner (answers in `reports/chart_review/{cup_handle,double_bottom}/review_answers_2026-10-05.csv`, no notes; analysis `~/vcp_spike/ms5b_answers.py`; summary in STRATEGY_SPECIFICATION §15.8): Cup and handle 1.1.0 29 yes / 1 partly / 0 no; Double bottom 1.0.0 16 yes / 13 partly / 1 no, with every grade-3 chart marked yes and grade 2 mostly partly, but no measured value separating yes from partly.

**Owner decisions (2026-10-05):** cup_handle-1.1.0 unchanged; double_bottom-1.0.0 accepted as is (option b). No code, config or database change. Step 5 is complete; next is step 6 (High tight flag).

## Multi-Strategy phase step 7a — step 7 re-planned: entry, market regime, exits (2026-10-06)

The owner judged every strategy's development results average and asked whether there is scope for improvement. Recommended and signed off (2026-10-06, all as recommended): test the rules shared by every strategy before more patterns. STRATEGY_SPECIFICATION §20: E entry `cross_5` (a real cross of the pivot, close at most 5 % above it); R market regime from our own universe, `breadth50` (≥ 40 % of members above their 50-day average) and `ew50` (equal-weight universe index above its 50-day average), tested separately, fixed thresholds, entries only; X trailing exits `trail_e20` and `trail_s50` (−7 % initial stop, 120-session time exit); T staged testing (entry, then regime, then exits), one choice for all strategies on pooled development results. Defaults stay as today until the owner's 7d decision. High tight flag (step 6) moves after step 7. Docs only.

## Multi-Strategy phase step 7b — entry rules, market regime and trailing exits built (2026-10-06)

**Owner go:** 2026-10-06 ("yes start 7b"). STRATEGY_SPECIFICATION §20.2–20.4, as built §20.7.

**Code.**
- `backtest/engine.py`: `EngineConfig.entry` (`breakout` default, `cross_5`), `max_entry_extension_pct`, `regime` (day → on/off; None = no filter); `moving_average` (`ema20`, `sma50` of the trade's own closes); `TRAIL_EXIT`; a rule's own horizon.
- `research/outcomes.py`: `TradeRule.trail`, `trail_after`, `horizon`; `ENGINE_RULES` = the six rules + `trail_e20`, `trail_s50` (the outcome study is unchanged).
- `backtest/regime.py` (new): `breadth50`, `ew50` from per-day universe aggregates. `DuckDBBacktestRepository.breadth` (the aggregates), `bars(after_days=…)`.
- `cli_backtest.py`: `--entry`, `--regime`; the new rules; stored in `settings_json`, printed.

**Found while building:** `technical_features_daily.ema_20` is never computed (all NULL), so the 20-day EMA is computed in the engine from the trade's closes (§20.7).

**Tests.** `test_backtest_rules.py` (9: defaults unchanged; cross needs the previous close at or below the pivot; the 5 % limit; an off day delays entry, never closes a trade, a missing day is off; moving averages; trailing exit after 5 sessions; −7 % stop first; 120-session horizon; breadth and ew regimes); the e2e test runs `cross_5` with both regimes and `trail_e20`, and checks the breadth query. Full suite 1,254 passed, 2 skipped; ruff, format, mypy clean.

**Real-data check** (copy `data/tmp/ms7b/res.duckdb`, flat base, development period): defaults reproduce the stored step-4c run exactly (449 trades, PF 2.28, CAGR 25.0 % / −16.5 %). One run of each option (not yet results; the staged runs are 7c): `cross_5` 183 trades, PF 2.30, CAGR 26.0 % / −14.4 %; `breadth50` 428, 2.25, 22.4 % / −14.7 %; `ew50` 427, 2.24, 18.6 % / −14.5 %; `trail_e20` 479, 1.53, 11.6 % / −20.1 %; `trail_s50` 450, 2.35, 15.8 % / −18.3 %. Regime on-share by year in §20.7.

**Not changed:** stored scans, scores, labels, backtests; the main and research DBs; no schema change.

## Multi-Strategy phase steps 7c–7d — staged development runs and the rule choice (2026-10-06)

**7c** (research DB, under the daily-run lock, `~/vcp_spike/ms7c_stage.sh`, report `ms7c_report.py`; code c2d65dc): stage 1 entry `cross_5` (5 runs, 10:51 IST, backup `golden_src_20261006_052113`), stage 2 regimes `breadth50`, `ew50` (10 runs, 11:07, backup `golden_src_20261006_053633`), stage 3 exits `hold_low8`, `trail_e20`, `trail_s50` (15 runs, 11:13, backup `golden_src_20261006_054255`; keep=3 rotated out the 142803, 155936 and 170507 backups). Development period only; no errors; nothing but backtest runs added. Results: STRATEGY_SPECIFICATION §20.8.

**7d owner decisions (2026-10-06, each as recommended):** entry `cross_5`, regime `breadth50`, exit `hold_low8`, the same for every strategy, chosen on pooled results.

**Code (step 7d):** `config/backtest.yaml` `defaults` (entry, regime, rule) read by `vcp backtest run|walk-forward` when the command line names none; `BacktestDefaults` validates the names. The engine's own defaults and the outcome study's `DEFAULT_RULE` (hold_s7) are unchanged. Test: the shipped defaults and refused names. Full suite 1,255 passed, 2 skipped; ruff, format, mypy clean. **Real-data check** (copy of the research DB): `vcp backtest walk-forward --strategy flat_base` with no options reproduces the stage-3 run (173 trades, PF 2.73, CAGR 31.1 % / −15.9 %).

## Multi-Strategy phase step 7e — validation opened once per strategy (2026-10-06 11:24 IST)

Owner go ("yes, open validation now"). `vcp backtest walk-forward --validation` per strategy with the frozen defaults (cross_5, breadth50, hold_low8), under the daily-run lock (`~/vcp_spike/ms7c_stage.sh "--validation"`, code 987dc29), backup `data/backups/golden_src_20261006_055406.duckdb` (keep=3 rotated out 052113). Look number 1 for all five strategies; each run also re-ran the development period (identical to the 7c runs). No errors.

**Result:** the development edge did not hold. Validation profit factors 0.70–1.19, portfolio CAGR −6.7 … +2.7 %; full table and market context in STRATEGY_SPECIFICATION §20.9 (development was a +93 % broad advance of the equal-weight universe; validation roughly flat with a correction from late 2024). Reported to the owner with options; no rule changed.

## Monitoring phase M1 — plan signed off (2026-10-06)

After the validation result (§20.9) the owner decided to stop strategy research and move to reporting and monitoring. STRATEGY_SPECIFICATION §21 (signed off 2026-10-06, P1–P5 all as recommended): all five strategies frozen at their current versions with the 7d rules; the daily run runs them (`stage: paper`), back-filled since 2026-09-30; an append-only paper ledger each evening, checked against a replay at review; daily and weekly HTML reports with system checks; review from 2027-04-01 with criteria fixed now (≥ 30 closed paper trades, PF ≥ 1.3, average > 0, max drawdown no worse than −20 %, return above the equal-weight universe index). Replaces Multi-Strategy steps 6, 7e (ranking, combined list, go-live) and the open part of 8. Docs only.

## Monitoring phase M2 — the daily run executes the paper strategies (2026-10-06)

**Code:** `daily.py` `paper_strategies` (every strategy other than VCP whose file says `enabled: true`, registry order); after each scan date's VCP scores, `compute setups` and `compute scores --strategy ID` per strategy; after VCP's labels, `compute labels --strategy ID`. An invalid strategy file is a failed step (`strategy config`) and VCP still runs. `config/strategies/{flat_base,three_weeks_tight,cup_handle,double_bottom}.yaml`: `enabled: true`, `stage: paper` (neither is in the config hash: the strategy hashes are unchanged). **Tests:** the daily-run chain with the four strategies; a bad strategy file. Full suite 1,256 passed, 2 skipped; ruff, format, mypy clean.

**Real-data check** (copy `data/tmp/m2/main.duckdb` of the main DB; `~/vcp_spike/m2_backfill.sh`): the four strategies over the main DB's three scan dates (2026-09-30, 10-01, 10-05) in date order, then labels; config hashes equal the frozen ones; grade 2+ setups per date: flat base 21–23, 3WT 19–20, cup 3, double bottom 5–6; no errors; about 20 s in all.

### M2 applied to the main DB (2026-10-06 12:40–12:41 IST)

Under the daily-run lock (`~/vcp_spike/m2_apply.sh`): health check and backup `data/backups/vcp_scanner_20261006_071026.duckdb` (keep=3: `vcp_scanner_20261004_153355` rotated out), then the four paper strategies back-filled over the main DB's scan dates 2026-09-30, 10-01, 10-05 in date order (setups, scores), then their labels (flat base 298, 3WT 234, cup 26, double bottom 41 observations, all still open). Same counts as the check on the copy; no errors. Then 8e5b3f9 was ff-merged, so the 19:15 daily run scans all five strategies from 2026-10-06.

## Monitoring phase M3 — paper ledger built (2026-10-06)

**Owner go:** 2026-10-06 ("first finish M3"). STRATEGY_SPECIFICATION §21.3, as built §21.9; DATABASE_SCHEMA §49B.

**Code.**
- `paper/ledger.py` (new): `derive_events` (the frozen rules replayed with the event engine into `WATCH`, `WATCH_EXPIRED`, `ENTRY`, `SKIPPED_NO_SLOT`, `EXIT`, `DAY_CLOSED`), `plan_update` (append only after the last `DAY_CLOSED`; a recomputed difference before it becomes one `DIVERGENCE`). `paper/status.py`: closed / open / skipped / divergences.
- `backtest/engine.py`: `run_signals(include_open=True)` returns positions still held at the last bar as `OPEN` trades, which never free a portfolio slot (backtests unchanged: default False).
- `paper_events` table (append-only; `DuckDBPaperRepository` only inserts, `INSERT OR IGNORE` on a deterministic event id); `DATA_SCHEMA_VERSION` 3.
- `cli_paper.py`: `vcp paper update|status`; `daily.py`: `paper update` after the labels.

**Tests.** `test_paper_ledger.py` (6: entry with stop, open then stop exit, watch expiry; an open position keeps its slot; append only after the last closed day; a data fix is one divergence, not repeated; the repository appends and never updates or deletes; the summary); daily-run chain; e2e runs `paper update` / `status`. Full suite 1,262 passed, 2 skipped; ruff, format, mypy clean.

**Real-data check** (copy `data/tmp/m3/main.duckdb` of the main DB): sessions 2026-10-01 and 10-05 written for all five strategies (VCP 211 rows, flat base 53, 3WT 66, cup 12, double bottom 23: watch-list entries and expiries); `breadth50` off on both days (26–27 % above the 50-day average; the research DB agrees), so no paper entries yet; a second run appended nothing.

### M3 applied to the main DB (2026-10-06 13:14 IST)

68fa391 ff-merged; under the daily-run lock (`~/vcp_spike/m3_apply.sh`): health check, backup `data/backups/vcp_scanner_20261006_074435.duckdb` (keep=3: `vcp_scanner_20261005_134501` rotated out); the first open with the new code created `paper_events` (schema version 3); first `vcp paper update`: sessions 2026-10-01 and 10-05 for all five strategies (365 rows: watch-list entries and expiries; regime off both days, so no entries), same as on the copy. Git tag **`paper-v1`** on 68fa391 (pushed): the freeze point of §21.1. From the 19:15 run the daily run appends each new session.

## Dashboard D0 — spec proposed, awaiting sign-off (2026-10-06)

The owner asked for a web dashboard calling an API, after his mockup. FRONTEND_SPECIFICATION §67 (proposed, decisions W1–W4 awaiting the owner; recommended: FastAPI + Next.js, a serving copy of the database refreshed by the daily run, the dashboard page only in v1, dark theme): read-only, local (127.0.0.1), API v1 endpoints, the page layout, what is out of v1 (NIFTY quotes, sector / market-cap filters, writes, alerts, remote access). Replaces the static reports of STRATEGY_SPECIFICATION §21.4 (M4). The owner will continue the dashboard in a new session. Docs only.

## Dashboard D0 — §67 signed off (2026-10-06)

Owner decisions W1–W4, each as recommended: FastAPI + Next.js (TypeScript, Tailwind, TanStack Query, Lightweight Charts, Zod); a serving copy `data/serving/vcp_serving.duckdb` refreshed atomically as the daily run's last step, the API opening only that copy read-only; v1 is the dashboard page only; dark theme. FRONTEND_SPECIFICATION §67 is no longer "proposed". Next: D1 (serving copy step, API v1, `vcp api serve`, tests on a fixture database). Docs only; no code, database or strategy change.

## Dashboard D1 — serving copy and read-only API (2026-10-06)

Owner go after the §67 sign-off. FRONTEND_SPECIFICATION §67.3 and §67.9.

**Code.**
- `serving.py` (new): `refresh_serving_copy` (checkpoint, copy to `.partial`, open check, atomic rename; the previous copy stays on any failure). `daily.py`: last step with `--serving-copy` (**off by default**: the owner is asked before the daily run writes it); a failed copy is a failed step.
- `api/` (new, FastAPI): `models` (Pydantic, `as_of` and `data_time` on every response, missing = null), `db` (read-only serving copy, re-opened when the file is replaced), `context` (paper strategies, config hashes, tiers, from `config/`), `queries` and `reports` (the SQL), `app` (GET `/api/v1/...`, CORS for the local page only). `cli_api.py`: `vcp api serve` (127.0.0.1:8000). `pyproject.toml`: `fastapi`, `uvicorn`; `httpx` for the tests.

**Found while testing, fixed:** DuckDB keeps one database instance per file path in a process, so re-connecting to a replaced file while the old connection was open kept serving the old data. `ServingDb` now waits until no request uses the old copy, closes it, then opens the new one (test: the new request waits for one in flight and then sees the new file). FastAPI could not resolve a local type alias for the `date` parameter and silently ignored `?date=`; the alias is module-level now (a test covers dates).

**Tests (+37; 1,299 passed, 2 skipped; ruff and mypy --strict clean).** `tests/unit/test_serving.py` (7: readable copy, replaced with new data, a failed refresh keeps the old copy, the daily run writes it only when asked and last, a failed copy fails the run); `tests/api/` (30) on a synthetic database built with the real schema and paper repository: every endpoint with exact values, the five config hashes equal the frozen ones, null instead of 0, all routes GET only and the two databases byte-identical after requests, 503 without a copy, warnings of §21.5, hot replacement.

**Real-data check** (copy `data/tmp/d1/main.duckdb` of the main DB; 2026-10-06 14:28 IST): serving copy of 2.23 GB in 9 s; `vcp api serve` on 127.0.0.1 answered status, summary, market (250 days), setups, paper and activity with 200 in 0.03–0.28 s; POST gave 405; the copy replaced under the running server was picked up without a restart. Numbers agree with the ledger: five strategies at the frozen hashes, 207 Trend Template passers and 61 ranked VCP setups on 2026-10-05, breadth 26–27 % on 10-01 and 10-05, regime off, no paper trades. `company` is null for most stocks (no names stored); the status warnings list was empty.

**Not changed:** the main DB and the research DB (the check ran on a copy), every strategy, rule, scan, score, label and the ledger; the daily run's default behaviour (the serving step is off until the owner agrees).

## Dashboard D2 — the web page (2026-10-07)

Owner go "start D2. you can do some minor changes if needed." FRONTEND_SPECIFICATION §67.4, §67.6 and §67.10.

**Code.** `frontend/` (new): Next 15 app with the `/dashboard` page against the API of D1; Zod schemas for every response (`src/lib/schemas.ts`), TanStack Query hooks (`api.ts`), formatting and chart-series helpers (`fmt.ts`, `chartData.ts`), the panels of §67.4. `scripts/dashboard.sh` (new) starts `vcp api serve` and the web server. Minor API change: `GET /api/v1/search?q=&limit=` (`SearchHit`, `SearchResponse`, `queries.search_symbols`) for the top-bar search. `.gitignore`: node_modules, .next and test output.

**Found while testing, fixed:** acronyms in labels ("A plus VCP", "Rs rank") are now upper-cased by `sentence`; unmet rules are shown per tier; the details grid no longer clips at narrow widths; daily-run activity rows carry the prefix "Daily run:".

**Tests.** 62 frontend tests (contract, format and chart helpers, components, page), `tests/api/test_contract_samples.py` (the committed sample responses equal what the API returns), `test_search_finds_symbols_and_company_names`. Frontend `npm run check` (tests, tsc, eslint) and `npm run build` pass; Python suite 1,301 passed, 2 skipped; ruff and mypy --strict clean.

**Real-data check** (copy of the main database, 2026-10-06 IST, browser pane): the page renders real data (1,271 universe, 207 Trend Template passers, 74 grade 2+ setups, 3 breakouts, regime off at 27.0 % breadth, no paper trades); chart for GLAND with pivot 3,008.00 and stop 2,841.10; the API answered in 0.03 to 0.3 s.

**Not changed:** the main and research databases, every strategy, rule, scan, score, label and the ledger; the daily run (the serving copy stays off until the owner agrees).

## Dashboard D2.1 — equal card heights and launcher fix (2026-10-07)

Owner review of the page: the setups card was much shorter than the chart card, and the three bottom cards (recent activity, market overview, paper trading) had different heights.

**Fix.** `ui.tsx` `Card` is a flex column. At xl width the setups card and the activity card fill their grid cell (absolute inset-0, list scrolls inside) so the chart card and the market card set the row height; the market and paper cards stretch. Below xl the layout is unchanged (stacked, lists limited to 520 / 288 px). Measured in the browser pane at 1536 px: setups and chart 1,198 px each; activity, market and paper 420 px each.

**Also fixed:** `scripts/dashboard.sh` started `vcp` without the project's venv on PATH, so the API did not start ("vcp: command not found"); it now calls `.venv/bin/vcp`.

**Tests:** frontend 62 passed (no logic changed). **Not changed:** any data, strategy, rule, scan, score, label or the ledger.

