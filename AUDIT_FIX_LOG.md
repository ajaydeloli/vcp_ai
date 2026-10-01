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
