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
| 5 | P1-1 | Seed `instruments`; first real end-to-end run; real golden fixtures | 5a done; 5b pending (needs credentials) |
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
| P1-1 (5b) | Real run on ~20 names; `vcp verify kite-adjustment`; 5–10 real golden split/bonus fixtures | Phase 6 golden dataset (needs `.env` credentials) |
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
