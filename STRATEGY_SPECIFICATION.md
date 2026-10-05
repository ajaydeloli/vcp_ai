# STRATEGY_SPECIFICATION.md

**Project:** Institutional-Grade NSE VCP Scanner
**Version:** 1.0 (Multi-Strategy phase step 1; signed off by the owner 2026-10-05)
**Owns:** the strategy registry, the setup record, per-strategy config and versioning, the multi-label rule, the split of the setup score, and how labels and backtests are keyed by strategy.
**Does not own:** the rules of each pattern (VCP: VCP_SPECIFICATION; new patterns: their own section here, added in steps 4–6), the Trend Template (TREND_TEMPLATE_SPECIFICATION), score bounds (SCORING_SPECIFICATION), tables and columns (DATABASE_SCHEMA §35A).

Owner decisions this spec follows (plan "Multi-Strategy Research Platform — Phase Plan", all accepted 2026-10-05):

| # | Decision |
|---|---|
| D1 | This phase runs now, before Phases 10–13. |
| D2 | Multi-label: every pattern match is its own setup record. |
| D3 | Trend Template required for every strategy. Only High Tight Flag gets a documented relaxation. |
| D4 | One ranked list per strategy. A combined list only after step 7. |
| D5 | First new strategies: Flat / tight base and Three Weeks Tight. |
| D6 | The owner reviews about 30 sample charts per strategy. |

---

# 1. Purpose

Run several base-pattern strategies on the same data, gates, scores, labels and backtests, so they can be compared fairly. VCP is strategy #1 and its stored results do not change.

A **strategy** is: a detector (finds setups), its config (thresholds), its pattern-quality score, and its ranking rule. Everything else is shared: prices, corporate actions, quality gates, universe, features, RS, Trend Template, weekly stage, the shared score parts, labels, the event engine and walk-forward periods.

Not in scope: new data sources, fundamentals (still skipped, decision F1), intraday entries, ML.

---

# 2. Terms

| Term | Meaning |
|---|---|
| `strategy_id` | Short lowercase id, `[a-z][a-z0-9_]*`, never starting with a digit (scan ids put a date after it). Fixed forever once used. |
| setup | One pattern match: one strategy, one instrument, one as-of date, one base. |
| primary setup | The one setup per (strategy, instrument, date) that is scored, labelled and traded. |
| gate population | The instruments a strategy may detect on, after its Trend Template rule (§5). |
| strategy scan | One run of one strategy's detector for one date, config and data snapshot. |
| strategy config hash | The hash that names every stored result of a strategy (§4.2). |

First ids: `vcp`, `flat_base`, `three_weeks_tight`. Reserved for later steps: `cup_handle`, `double_bottom`, `high_tight_flag`.

---

# 3. Strategy registry

## 3.1 Code side

`patterns/registry.py` holds one entry per strategy:

```text
strategy_id -> (detector factory, config model, ALGORITHM_VERSION, pattern scorer)
```

- Each detector lives in its own package, `patterns/<strategy_id>/`. VCP stays in `patterns/vcp/`.
- A strategy's `ALGORITHM_VERSION` is a constant in its package (VCP: `versioning.VCP_ALGORITHM_VERSION`). Tag form `<prefix>-MAJOR.MINOR.PATCH`, e.g. `vcp-1.1.0`, `flat_base-1.0.0`, `three_weeks_tight-1.0.0`.
- `version_manifest()` lists every registered strategy's version. Existing keys are unchanged; new keys are added as `<strategy_id>_algorithm_version`.

## 3.2 Config side

One file per strategy: `config/strategies/<strategy_id>.yaml`.

```yaml
strategy_id: flat_base
algorithm_version: flat_base-1.0.0   # must equal the code constant, or loading fails
enabled: false                       # true = the daily run executes it (step 8)
stage: research                      # research | paper | live (only the owner moves it, step 7)
trend_gate:
  mode: required                     # required | relaxed  (§5)
  waive: []                          # condition names; only allowed with mode: relaxed
detector: { ... }                    # the strategy's own thresholds (its spec section)
classification: { ... }              # its tiers (§6.3)
ranking:
  min_grade: 1                       # lowest grade that is ranked (§6.3)
  pivot_ready_max_distance_pct: 3.0
scoring:
  pattern:                           # the pattern-quality part (§9)
    weights: { ... }                 # sum = 100
    bounds:  { ... }                 # worst/best per sub-component
  dryup_measure: { ... }             # how this strategy measures base-end volume (§9.2)
```

- Unknown keys are refused at load (as for all config today).
- `strategy_id` must equal the file name.
- **VCP is the exception for now (§12.2):** `config/strategies/vcp.yaml` holds only `strategy_id`, `algorithm_version`, `enabled`, `stage`, `trend_gate` and `config_source: legacy`. Its thresholds stay in `strategy.yaml` (`vcp`, `classification`) and `scoring.yaml` (`components.vcp`), exactly as today.

## 3.3 Lifecycle

`stage` records how far a strategy has got: `research` (development backtests only), `paper` (live paper tracking, after the owner's step-7 decision), `live` (owner only). VCP starts as `paper` (live paper from 2026-10-01, config/backtest.yaml). The stage never changes a result; it only decides which lists the daily run shows.

---

# 4. Versioning and config hashes

## 4.1 What forces a new version

Same rules as today (AGENTS.md rule 7, VCP_SPECIFICATION §48):

- A change in detector logic → new `algorithm_version` of that strategy.
- A change of a threshold → new strategy config hash (automatic). Thresholds are hypotheses; a change is a strategy change and is logged (spec update, old/new/why).
- A change of the shared pattern-score rules (§9) → new `scoring_version` (all strategies).
- Strategies are versioned **independently**. Bumping `flat_base` never changes a VCP id or hash.

## 4.2 The strategy config hash

Today one hash names every result: `scan_config_hash` = SHA-256 over the section hashes of `strategy` (Trend Template, RS, stage, VCP, classification, scoring), `universe` and `gate`. The research history uses `64da9482a769…`.

Rule:

```text
vcp:            strategy_config_hash = scan_config_hash                       (unchanged formula)
other strategy: strategy_config_hash = sha256(canonical_json({
                    "scan":    scan_config_hash,
                    "strategy_id": <id>,
                    "algorithm_version": <tag>,
                    "config":  <resolved values of config/strategies/<id>.yaml>
                }))
```

- `scan_config_hash` itself does not change: the new strategy files are **not** part of `ScannerConfig.strategy`. So adding, editing or disabling a new strategy never forks Trend Template, RS or VCP scan ids.
- A new strategy's hash includes `scan_config_hash` because its results depend on the Trend Template scan, which is named by that hash.
- Known side effect: `scan_config_hash` includes the VCP thresholds, so a VCP threshold change also forks the other strategies' hashes. This already happens today to Trend Template scan ids. Splitting VCP out of the shared hash is left for a VCP major version (it would rename every stored VCP result).
- `vcp config hash` (CLI) stays as it is; a new `vcp config hash --strategy <id>` prints a strategy config hash.

## 4.3 Scan ids

| Result | VCP (unchanged) | Other strategies |
|---|---|---|
| Detector scan | `vcp-<date>-<hash12>` | `setup-<strategy_id>-<date>-<hash12>` |
| Score scan | `score-<date>-<hash12>` | `score-<strategy_id>-<date>-<hash12>` |

`<hash12>` is the first 12 characters of the strategy config hash; `-<snapshot>` is appended for a frozen snapshot, as today. The `strategy_id` rule in §2 keeps the two forms apart (a date never matches `[a-z]…`).

Code must stop deriving one scan id from another by string slicing (today: `'vcp-' || substr(score_scan_id, 7)`). Joins go through `strategy_id` + `as_of_date` + `config_hash` + `data_snapshot_id`.

---

# 5. Trend Template per strategy (D3)

- **Every strategy requires the Trend Template.** Production grades (§6.3) need Trend Template PASS **and** weekly Stage 2, as VCP does today (VCP_SPECIFICATION §3).
- `trend_gate.mode: required` → gate population = instruments with Trend Template status PASS.
- `trend_gate.mode: relaxed` is allowed **only** for `high_tight_flag`, and may only waive `above_52w_low` (TREND_TEMPLATE_SPECIFICATION §2, condition 8). Config load refuses any other strategy or condition. Gate population = instruments whose other nine conditions pass and whose data status is not a data state. The setup is stored with `trend_gate = RELAXED`.
- **VCP keeps its current behaviour**: it detects on every universe member and marks non-passers INVALIDATED (`TREND_TEMPLATE_FAIL`, VCP_SPECIFICATION §61B). New strategies detect only on their gate population, so they never store a trend-failed setup.
- RS, weekly stage and the Trend Template are computed once per date and shared.

---

# 6. The setup record

Every strategy writes the same record. Scoring, labels, the event engine and reports read only this record (plus the shared inputs), so they work for every pattern unchanged.

## 6.1 Fields

| Field | Type | Meaning |
|---|---|---|
| `setup_id` | text | `"st-" + sha256(scan_id, instrument_id, base_start)[:24]`. VCP keeps `vcp_pattern_id` (`vp-…`, same formula). |
| `strategy_id` | text | §2 |
| `scan_id` | text | the strategy scan (§4.3) |
| `instrument_id`, `as_of_date` | | |
| `is_primary` | bool | §6.4 |
| `base_start_date`, `base_end_date` | date | `base_end_date` NULL while forming; the breakout date after a breakout (as VCP) |
| `base_high`, `base_low` | price | adjusted |
| `base_depth_pct` | % | (base_high − base_low) / base_high × 100 |
| `base_duration_days` | sessions | from base start to the as-of bar (or base end) |
| `prior_advance_pct` | % | the run-up into the base, as the strategy defines it; NULL if not used |
| `pivot_price`, `pivot_date`, `pivot_source` | | the buy point; NULL = no pivot yet |
| `pivot_distance_pct` | % | (pivot − close) / close × 100 |
| `stop_reference_price` | price | the strategy's structural low for a stop (VCP: last contraction trough). The engine applies its own buffer. NULL = none. |
| `classification` | text | the strategy's own tier name (§6.3) |
| `grade` | int 0–3 | the tier mapped to a common scale (§6.3) |
| `status` | text | shared status list (§6.2) |
| `confirmation_state` | text | `CONFIRMED` / `PROVISIONAL` (e.g. a weekly pattern on a partial week) |
| `trend_gate` | text | `PASS` / `RELAXED` / `FAIL` (FAIL only for VCP, §5) |
| `weekly_stage2_pass` | bool | |
| `invalidation_reasons` | text | comma-separated, NULL when none |
| `unmet_rules` | JSON | unmet rules per tier (explainability, as VCP) |
| `breakout_event_id` | text | §10.1 |
| `details_json` | JSON | the strategy's own measurements (e.g. weekly close spread for 3WT). Raw values and thresholds, not verdicts only (AGENTS.md rule 6). |
| `algorithm_version`, `config_hash`, `data_snapshot_id`, `created_at` | | `config_hash` = strategy config hash |

Measurements that more than one strategy uses, or that a score or report reads, get a real column, not only JSON (DATABASE_SCHEMA §47: "JSON should not become the primary analytical schema"). Adding such a column is a schema change with a migration.

## 6.2 Status

The VCP status list is the shared list (VCP_SPECIFICATION §5): `FORMING`, `PIVOT_READY`, `BREAKOUT`, side states `FAILED`, `INVALIDATED`, data states `INSUFFICIENT_DATA`, `DATA_NOT_READY`, `STALE_DATA`. Same precedence as VCP_SPECIFICATION §61B: data state → INVALIDATED → BREAKOUT / FAILED → PIVOT_READY → FORMING.

- `PIVOT_READY`: grade ≥ 2, a pivot exists, and the close is 0 to `ranking.pivot_ready_max_distance_pct` below it.
- `BREAKOUT` / `FAILED`: from the shared breakout events (§10.1).
- Missing data is a data state, never `NONE` or a failed pattern (AGENTS.md rule 4).

## 6.3 Classification and grade

Each strategy names its tiers in its config, lowest first, and maps each to a grade:

| Grade | Meaning | VCP tier | Production? |
|---|---|---|---|
| 0 | measured, but no tier met | `NONE` | no |
| 1 | "-like": watch list / research | `VCP_LIKE` | no |
| 2 | standard pattern | `VCP` | yes |
| 3 | best form | `A_PLUS_VCP` | yes |

- A strategy may skip grades (e.g. Three Weeks Tight may have only grade 2).
- Grades 2–3 need Trend Template PASS (or RELAXED for High Tight Flag) and weekly Stage 2.
- **Eligible for ranking** = grade ≥ `ranking.min_grade` and status `FORMING`, `PIVOT_READY` or `BREAKOUT`. VCP: `min_grade: 1`, which is today's rule (`VCP_LIKE` or better, SCORING_SPECIFICATION §11).
- Grades let reports and filters work across strategies; a grade-2 flat base and a grade-2 VCP are not claimed to be equally good.

## 6.4 Primary setup

Each strategy may store several candidate bases per instrument and date. Exactly one is primary, chosen by VCP_SPECIFICATION §61A with grade in place of classification: highest grade → CONFIRMED over PROVISIONAL → most recent base end → longer base → earlier base start. Only the primary is scored, labelled and traded.

---

# 7. Multi-label rule (D2)

- Strategies run independently. A stock can have a primary setup in several strategies on the same date. **Each is its own record**, its own score row, its own label row and its own signal in that strategy's backtest.
- There is **no priority** between strategies and no strategy suppresses another. Nothing is merged or deduplicated at detection, scoring or labelling.
- Nested patterns are expected (a Three Weeks Tight inside a flat base, a flat base that is also a late-stage VCP). They are all recorded.

**Overlap is measured, not assumed.** Reports (step 7) show, per pair of strategies and per period:

- same-day overlap: share of eligible setups of A whose instrument also has an eligible setup of B on that date;
- same-base overlap: share whose base windows intersect (`base_start`..`base_end`/as-of);
- trade overlap: share of A's backtest entries that B also entered within 5 sessions.

A strategy whose trades are mostly another strategy's trades is not reported as a new edge.

**Combined list (D4):** none until the owner's step-7 decision. Then it is defined here (deduplication by instrument, which score wins, how one position per stock is kept across strategies). Until then the daily report shows one ranked list per strategy, and a stock's other strategies are shown as a note next to it.

---

# 8. Detector contract

```python
class StrategyDetector(Protocol):
    strategy_id: str
    algorithm_version: str
    def detect(self, ctx: StrategyContext) -> StrategyResult: ...
```

- `StrategyContext`: instrument, `as_of_date`, adjusted daily bars up to and including the as-of bar, weekly bars (partial week flagged), shared features at the as-of bar, the Trend Template verdict and weekly context. Nothing after `as_of_date` (AGENTS.md rule 1).
- `StrategyResult`: the candidate setups (§6.1 fields, `details_json`), or a no-setup reason (`NO_BASE`, `INSUFFICIENT_HISTORY`, …), or a data state.
- Pure and deterministic: same inputs → same output. No database, provider, clock or network access (AGENTS.md rule 3; the architecture test covers `patterns/<id>/`).
- `lookback_bars()` per strategy feeds `ScannerConfig.longest_lookback_bars` (data-quality block lifetime).
- VCP's existing `VCPDetector` is wrapped by an adapter; its logic is not touched.

---

# 9. Scoring split

The final score formula and weights stay as in SCORING_SPECIFICATION §1 (`scoring-1.0.0`). The VCP component becomes "the pattern component of the row's strategy".

```text
final = Σ(w_i × score_i) / Σ(w_i)   over available components
        i ∈ {trend, pattern, volume, rs, fundamentals}
weights (shared, scoring.yaml): trend 25, pattern 35 (today's "vcp" key), volume 15, rs 15, fundamentals 10
```

## 9.1 Shared parts (computed once per instrument and date)

| Component | Sub-components | Source |
|---|---|---|
| Trend | `high_proximity`, `sma200_slope`, `ma_stack_margin` | features |
| RS | `rs_rank` | RS snapshot |
| Volume | `up_down_volume`, `distribution` | adjusted bars |
| Fundamentals | — | NULL (F1), renormalized away |

## 9.2 Pattern-supplied parts

| Part | Who supplies it | VCP today |
|---|---|---|
| Pattern component | the strategy's own sub-components, weights (sum 100) and worst/best bounds, in its config | the six VCP sub-components, `scoring.yaml components.vcp` |
| Volume `dryup_quality` measurement | the strategy measures base-end volume against the 50-day average, its own way (`scoring.dryup_measure`); the bound stays shared | `final_volume_ratio` |

The volume component stays one component with the same three sub-components and weights; only where the dry-up number comes from is per strategy. Volume is still scored once (no double counting, SCORING_SPECIFICATION §4).

Normalization, NULL handling, renormalization, stored effective weights and the ranking percentile are exactly SCORING_SPECIFICATION §2 and §11, applied per strategy scan.

## 9.3 Rows and ranking (D4)

- **VCP:** unchanged. The score scan stores every Trend Template passer (owner decision 2026-10-03); passers without a VCP pattern get a NULL pattern part.
- **Other strategies:** the score scan stores every instrument of the gate population **that has a primary setup** of that strategy (any grade, including 0). Passers without one are not stored again: their trend, volume and RS parts are already in the VCP score scan of the same date and would be identical copies. (Decision O4, §19.)
- The ranking percentile is computed **within one strategy scan** and one confirmation state. Percentiles of different strategies are not comparable and are never merged (D4).
- Column names stay: `setup_scores.vcp_score` / `vcp_weight` hold the pattern score / weight of the row's strategy; `score_components.component = 'VCP'` for VCP rows and `'PATTERN'` for others. A view exposes them as `pattern_score` / `pattern_weight` (DATABASE_SCHEMA §35A). (Decision O5, §19.)

---

# 10. Breakouts, labels and backtests by strategy

## 10.1 Breakouts

- **Rule (shared):** first close above the pivot on volume ≥ `breakout.min_volume_ratio` (1.5) × the mean of the 50 bars before it (VCP_SPECIFICATION §61B). One rule for every strategy, so backtests differ only by which setups they trade.
- Events are immutable, one per (strategy, instrument, base start, config hash). VCP keeps `vcp_breakout_events`; other strategies write `strategy_breakout_events`; the view `breakout_events` unions them.
- For strategies whose pivot does not move (flat base high, 3WT high) the structural pivot is the pivot; the `PRIOR_DAY_PIVOT` path is only needed when a pivot can move.

## 10.2 Forward labels

- One label row per (strategy, instrument, as-of date, label version, data snapshot) observation, i.e. per score-scan row. Key gains `strategy_id`.
- The label itself does not change (`labels-1.0.0`): returns at 5–60 sessions, MFE/MAE from the as-of close; breakout fields use **that strategy's** primary pivot. Two strategies on the same stock and date therefore share the return fields and may differ in the breakout fields.

## 10.3 Backtests

- `vcp backtest run|walk-forward --strategy <id>` (default `vcp`). The event engine, exit rules (default `hold_s7`), costs, watch days and portfolio limits are unchanged.
- Signals: eligible primary setups of that strategy with a pivot; `--baseline` = every primary setup with a pivot, whatever its grade and status. The stop rule `stop_at_final_low` reads `stop_reference_price`.
- `--classes` takes the strategy's own tier names (VCP: as today).
- `backtest_runs` stores `strategy_id` and the strategy's `algorithm_version`.
- One position per stock at a time **within a run**. A run covers one strategy. The union run (step 7) is specified with the combined list.

## 10.4 Research discipline

Phase 9 rules, tightened (plan, "Research discipline"):

- Development period (2022-02-01 .. 2024-06-30) only for building and tuning.
- Validation (2024-07-01 .. 2026-09-30) opens only with `--validation`, **once per strategy version** (algorithm version + strategy config hash). Looks are counted per `strategy_id`; the count and the number of versions tried are printed with every report.
- A strategy's result is not read with fewer than about 30 development trades.
- Every strategy tried is reported, including failures.
- Live paper from 2026-10-01 is the real test; a strategy is judged after 6 months of it.

---

# 11. VCP as strategy #1

## 11.1 Mapping to the setup record

| Setup record | VCP source |
|---|---|
| `setup_id` | `vcp_patterns.vcp_pattern_id` |
| `base_*`, `pivot_*`, `classification`, `status`, `confirmation_state`, `invalidation_reasons`, `unmet_rules`, `breakout_event_id`, `weekly_stage2_pass`, `algorithm_version`, `config_hash`, `data_snapshot_id` | same-named `vcp_patterns` columns |
| `prior_advance_pct` | `prior_advance_return_pct` |
| `stop_reference_price` | `trough_price` of the highest `sequence_number` in `vcp_contractions` (today's `final_low`) |
| `grade` | NONE 0, VCP_LIKE 1, VCP 2, A_PLUS_VCP 3 |
| `trend_gate` | `PASS` if `trend_template_pass`, else `FAIL` |
| `details_json` | not stored: the VCP columns, `vcp_contractions` and `vcp_pivots` are the details |

VCP rows are **not copied**. The `setups` view (DATABASE_SCHEMA §35A) reads them from `vcp_patterns` in place. (Decision O2, §19.)

## 11.2 What does not change for VCP

- Detector code and `vcp-1.1.0`; `strategy.yaml` and `scoring.yaml` (their thresholds stay there, §3.2); `scan_config_hash` (`64da9482a769…` for the research history); scan ids `vcp-…` and `score-…`; the VCP tables and their keys; score values, percentiles and `results_hash`; labels; backtest metrics.
- Commands keep working: `vcp compute vcp` = `vcp compute setups --strategy vcp`; `vcp compute scores` = `--strategy vcp`; `vcp compute labels`, `vcp backtest …` default to `vcp`; `vcp scores list` shows the VCP list unless `--strategy` is given.

## 11.3 Step 2 exit: exact reproduction

Step 2 (framework, VCP only) merges only if a regression run shows **no difference**:

1. **Dates:** 2026-09-30 and 2026-10-01 on a copy of the main DB; 10 weekly dates spread over 2022-01..2026-09 on a copy of `golden_src.duckdb` (scan config hash `64da9482a769`).
2. **Baseline:** the old code (main at the step-2 branch point) rebuilds those dates on the copy first. Stored rows are not the baseline: the Phase 9 backtest report in the research DB is stale after Fix C11.
3. **New code** rebuilds the same dates on a second copy.
4. **Compared, row by row, floats exactly equal:** `vcp_patterns`, `vcp_contractions`, `vcp_pivots`, `vcp_status_history`, `vcp_breakout_events`, `vcp_scan_run_results`, `setup_scores`, `score_components`, `forward_labels`, and `scan_runs.results_hash`. Ignored: timestamps (`created_at`, `computed_at`, `started_at`, `completed_at`), `scan_run_id`, `code_commit`, `code_dirty`, and the new `strategy_id` column (checked separately to be `vcp`).
5. **Backtests:** `walk-forward` development period on the research copy, default rule and `--baseline`, old vs new code: `metrics_json` identical, and the event lists identical.
6. A pytest regression test pins the same comparison on a small synthetic fixture so it runs in the normal suite.

---

# 12. New strategies

Each new strategy gets its own section here **before** its code (steps 4–6): rules with every number as config, its tiers and grades, pivot and stop, its pattern-score sub-components and bounds, its dry-up measure, `details_json` keys, and worked examples. Starting values come from the plan's "Pattern rules (first draft)" table and are tuned on the development period only.

- §13 Flat / tight base (`flat_base`) — step 4
- §14 Three Weeks Tight (`three_weeks_tight`) — step 4
- §15 Cup and handle (`cup_handle`), double bottom (`double_bottom`) — step 5
- §16 High tight flag (`high_tight_flag`) — step 6; the only relaxed Trend Template (§5). If fewer than about 30 occur in 2022–2026 it stays a watch-list flag, not a tested strategy.

**Chart review (D6):** for each new strategy, before its first backtest is read, about 30 sample charts go to the owner in a labelling sheet like the VCP one (`vcp research labelling-sheet --strategy <id>`), mixed across grades and with some near-misses. The detector is changed (new version) until it agrees with the owner's eye; disagreements and their fixes are logged.

**Shared features (step 3):** measurements used by more than one strategy (weekly close range, prior advance %, base depth and length helpers) are computed once in `features/` / `data/features/`, not inside a detector. Their tables are specified in DATABASE_SCHEMA when step 3 starts.

---

# 17. Configuration validation (blocks a scan)

- Each file under `config/strategies/` loads into its strategy's model; unknown keys and a missing file for a registered strategy are refused.
- `algorithm_version` equals the code constant.
- `trend_gate.mode: relaxed` only for `high_tight_flag`, waiving only `above_52w_low`.
- Pattern weights sum to 100; every bound has `worst ≠ best`; sub-component names match the strategy's scorer exactly.
- Tier names are unique, grades rise with the tier order, `ranking.min_grade` is one of the strategy's grades.

# 18. Tests

- Registry: every registered strategy has a config file and matching version; unknown ids are refused.
- Hashes: `scan_config_hash` of the current config equals the pinned value; a change in a new strategy's file changes only that strategy's hash; a change in `strategy.yaml` changes all.
- Scan ids: VCP forms unchanged; new forms parse back to (strategy, date, hash).
- Setup record: VCP view rows equal the `vcp_patterns` projection on a fixture, including `stop_reference_price` and `grade`.
- Multi-label: two strategies on the same stock and date produce two setups, two score rows, two label rows; neither changes the other.
- Scoring: VCP final scores and components unchanged on known-answer fixtures; a strategy's pattern part cannot change another's score; volume `dryup_quality` takes the strategy's measure.
- Backtest: `--strategy` filters signals; validation looks counted per strategy.
- Architecture: `patterns/<id>/` imports no storage or provider code.
- The step-2 regression (§11.3).

# 19. Decisions (owner, 2026-10-05)

All five recommendations were accepted.

| # | Question | Decision | Why |
|---|---|---|---|
| O1 | Config hash of new strategies | Chained on `scan_config_hash` (§4.2) | No stored id changes; a global hash would fork every VCP id whenever any strategy changes |
| O2 | Where VCP setups live | `setups` view over `vcp_patterns`, no copy (§11.1) | VCP rows untouched by construction; no 256k-row backfill |
| O3 | VCP thresholds' file | Stay in `strategy.yaml` / `scoring.yaml`; `config/strategies/vcp.yaml` is a pointer (§3.2) | Moved at the next VCP major version |
| O4 | Score rows for new strategies | Only instruments with a primary setup (§9.3) | Every passer would store identical copies per strategy |
| O5 | Pattern score columns | Keep `vcp_score` / `vcp_weight` meaning "pattern score", plus the `setup_scores_v` alias view (§9.3) | VCP rows stay byte-identical |

Rejected options, for the record: O1 (b) a new shared hash without VCP, (c) one global hash; O2 (b) VCP rows copied into `strategy_setups`; O3 (b) move the VCP thresholds now; O4 (b) every gate passer; O5 (b) new `pattern_score` columns with a VCP backfill.
