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
- `strategy_id` must equal the file name; a file for a strategy the code does not register is refused.
- If `vcp.yaml` is missing (test configs, older checkouts), VCP gets the default pointer below. Any other registered strategy without a file is simply not configured.
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
                    "config":  <resolved values of config/strategies/<id>.yaml,
                                without enabled and stage>
                }))
```

`enabled` and `stage` are left out of the hash: they decide what runs and which lists are shown, never a result.

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
- VCP keeps its own scan path (`vcp compute vcp`, = `vcp compute setups --strategy vcp`); its logic is not touched. The generic scan runner that calls `StrategyDetector`, and the protocol itself in `patterns/base.py`, are built in step 4 with the first new strategy, so they are designed against a real second detector rather than guessed now (decided in step 2, 2026-10-05).

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

**Result (2026-10-05 11:28–11:37 IST, `scripts/compare_results.py`): IDENTICAL.** Main-DB copies (30 Sep, 1 Oct): every compared table equal (e.g. 4,928 patterns, 469 scores, 474 labels). Research copies (10 weekly dates 2022-02-11 … 2026-09-30): 256,053 patterns, 900,900 contractions, 46,234 scores, 601,042 components, 46,241 labels, all scan-run hashes equal; development walk-forward default rule (12,523 events) and `--baseline` (42,155 events): metrics and events equal. Every migrated row has `strategy_id = 'vcp'`.

---

# 12. New strategies

Each new strategy gets its own section here **before** its code (steps 4–6): rules with every number as config, its tiers and grades, pivot and stop, its pattern-score sub-components and bounds, its dry-up measure, `details_json` keys, and worked examples. Starting values come from the plan's "Pattern rules (first draft)" table and are tuned on the development period only.

- §13 Flat / tight base (`flat_base`) — step 4
- §14 Three Weeks Tight (`three_weeks_tight`) — step 4
- §15 Cup and handle (`cup_handle`), double bottom (`double_bottom`) — step 5
- §16 High tight flag (`high_tight_flag`) — step 6, built after step 7 (§20); the only relaxed Trend Template (§5). If fewer than about 30 occur in 2022–2026 it stays a watch-list flag, not a tested strategy.

**Chart review (D6):** for each new strategy, before its first backtest is read, about 30 sample charts go to the owner in a labelling sheet like the VCP one (`vcp research labelling-sheet --strategy <id>`), mixed across grades and with some near-misses. The detector is changed (new version) until it agrees with the owner's eye; disagreements and their fixes are logged.

**Shared features (step 3):** see §12A.

## 12A. Shared base measurements (Multi-Strategy step 3, 2026-10-05)

Owner decision 2026-10-05 (option A of three): pure functions in `features/base_measures.py`, computed from the daily bars a detector already holds; **nothing is stored** and no table is added. Window lengths are per-strategy thresholds and base measures depend on each detector's base start, so precomputed columns would bake strategy settings into the shared feature table. VCP keeps its own copies of these formulas, so its results cannot move.

| Function | Definition | `None` when |
|---|---|---|
| `weekly_bars` | ISO weeks (Mon–Sun) of the daily bars up to the as-of bar: week end = last trading day, open = first day's open, close = last day's close, high/low = extremes, volume = sum. The as-of week uses only days up to the as-of bar and is `partial` Monday–Thursday (the weekly Stage rule). | volume: any day's volume missing |
| `weekly_close_range_pct(closes, N)` | (max − min) / min × 100 over the last N weekly closes | fewer than N closes; a close missing or ≤ 0 |
| `max_weekly_close_change_pct(closes, N)` | largest \|close / previous − 1\| × 100 over the last N week-to-week changes (N + 1 closes) | as above, with N + 1 |
| `prior_advance(high, low, end, lookback)` | high[end] / lowest low of the `lookback` bars ending at `end` − 1, × 100; earliest low on a tie; `window_complete` False with fewer bars (VCP §8.1 item 1) | `end` outside the series; a low ≤ 0 or missing |
| `base_extremes(dates, high, low, start, end)` | highest high and lowest low of bars start..end (earliest on a tie), depth % = (high − low) / high × 100, length in sessions and in ISO weeks | the window does not fit; a value missing |
| `volume_dryup_ratio(volume, recent, base=50)` | mean volume of the last `recent` bars / mean of the `base` bars before them | a window does not fit; any volume missing; base mean 0 |

`base_extremes.high` is the highest high in the window; VCP's `base_high` is the base-start bar's high. They differ after a breakout above the start (about 10 % of stored VCP patterns); a detector that means the start bar's high reads `high[start]`.

**Checked on real data** (copy of the research DB, 5 dates 2022-02-11 … 2026-09-30, every universe stock): `weekly_bars` equals the stored `weekly_prices` for all 386,220 completed weeks (open, high, low, close exact; volume to 1e-9 relative, the sum order differs); `prior_advance` equals VCP's stored `prior_advance_return_pct` exactly for all 5,283 patterns with one, and `base_extremes` gives VCP's base low and base length exactly.

---

## 12B. Scan path for new strategies (built in step 4)

`vcp compute setups --strategy <id> --as-of DATE` (dates in order, like VCP):

1. Read the Trend Template scan of the date (`trend-<date>-<scan hash12>`). Gate population: status PASS (§5). Instruments in a data state get a data-state result, as VCP does; nothing else is detected for them.
2. Load the adjusted daily bars up to the as-of bar (the strategy's `lookback_bars()`), in one query, from the scan's data snapshot. No bar on the as-of date → `STALE_DATA`.
3. Run the detector (`StrategyDetector`, §8, pure): candidate setups or a no-setup reason.
4. Breakouts (§10.1): read this strategy's events for the instrument; a base with an event is `BREAKOUT` while the close is ≥ its pivot and `FAILED` below; otherwise a new event is recorded on the first close above the pivot with volume ≥ 1.5 × the mean of the 50 bars before it. The pivots of §13–14 do not move once set, so the `PRIOR_DAY_PIVOT` path is not needed.
5. Write `strategy_setups`, `strategy_setup_status_history`, `strategy_breakout_events`, an immutable `scan_runs` row (type `SETUP`, `strategy_id`) and `strategy_scan_run_results`. A rerun of the scan id replaces its rows.

`vcp compute scores --strategy <id>` then scores the gate passers that have a primary setup (decision O4), and labels and backtests work unchanged (§10).

---

# 13. Flat / tight base (`flat_base`, `flat_base-1.0.0`) — signed off 2026-10-05

A stock in Stage 2 that, after an advance, moves sideways for at least five weeks within a shallow range (O'Neil's flat base; Minervini's tight base). Every number below is a config value under `detector:` and a starting hypothesis, tuned only on the development period.

## 13.1 Base

All on adjusted daily bars up to the as-of bar (index `t`).

1. **Base start `s`**: the bar with the highest high among bars `t − max_duration_days + 1` … `t − min_duration_days + 1` (earliest on a tie). So a base is at least `min_duration_days` (25 = 5 weeks) and at most `max_duration_days` (65 = 13 weeks) old.
2. **Base high** = `high[s]`; **pivot** = base high × (1 + `pivot_buffer_pct` / 100), buffer 0.1 %. The pivot is fixed for this base (it does not move with new bars).
3. **Base low** = lowest low of bars `s` … `t` (`base_extremes`, §12A). **Depth** = (base high − base low) / base high × 100.
4. **Prior advance** = `prior_advance(high, low, s, lookback = 120)` (§12A) ≥ `min_prior_advance_pct` (20). With fewer bars than the lookback a pass counts and a fail is `INSUFFICIENT_HISTORY`, as VCP.
5. **Not already gone**: if a close in `s+1` … `t` is above the pivot × (1 + `max_overshoot_pct` / 100) (3 %) and this base has no breakout event, there is no base (`MOVED_ABOVE_BASE`): the stock has left the range without a valid breakout. A close above the pivot by less than that, without breakout volume, leaves the base intact (as VCP: no volume, no breakout).
6. **Weekly closes**: `weekly_bars` (§12A) from bar `s` to `t`; **weekly close range** = `weekly_close_range_pct` over all of them (the as-of week partial if mid-week).
7. **Right side**: the last `right_side_days` (10) bars: range % = (max high − min low) / max high × 100, and their lowest low.

No base (no setup, the reason stored in `strategy_scan_run_results`): `INSUFFICIENT_HISTORY` (fewer than `lookback_bars()` bars), `NO_PRIOR_ADVANCE`, `MOVED_ABOVE_BASE`, `TOO_DEEP` (depth > the grade-1 limit).

## 13.2 Tiers and grades

| Tier | Grade | Rules (all must hold) |
|---|---|---|
| `FLAT_BASE_LIKE` | 1 | depth ≤ 20 %; length ≥ 20 sessions (4 weeks) |
| `FLAT_BASE` | 2 | depth ≤ 15 %; length ≥ 25 sessions; weekly close range ≤ 10 %; Trend Template PASS and weekly Stage 2 |
| `TIGHT_FLAT_BASE` | 3 | as grade 2, and depth ≤ 10 %, weekly close range ≤ 6 %, right-side range ≤ 5 %, volume dry-up ≤ 0.8 |

(The grade-1 length uses the same start search with `min_duration_days` = 20.) Unmet rules per tier are stored in `unmet_rules`, as VCP.

## 13.3 Status, pivot, stop

- Status (§6.2): data state → BREAKOUT / FAILED (events, §12B) → `PIVOT_READY` (grade ≥ 2 and the close 0 – 3 % below the pivot) → `FORMING`. Nothing is `INVALIDATED`: a deeper drop makes the base `TOO_DEEP` (no setup) or a lower tier.
- `stop_reference_price` = the right-side low (lowest low of the last 10 bars). (Decision F1, §14.9.)
- `base_end_date` = the breakout date after a breakout, else NULL. After a breakout the base is measured up to the day before it (depth, weekly closes, right side, dry-up): its shape is frozen and later bars only decide BREAKOUT / FAILED. `confirmation_state` = `CONFIRMED` (no swing confirmation lag: the start is at least 20 bars old).
- **Breakouts between scan dates**: the detector looks for the first breakout-volume close above the pivot anywhere after the base start, so weekly research scans do not miss a mid-week breakout (as VCP's structural breakout).

## 13.4 Score part (§9.2)

Pattern component `PATTERN`, sub-components (weights sum to 100; `linear(worst → best)` as SCORING_SPECIFICATION §2):

| Sub-component | Weight | Measurement | worst → best |
|---|---|---|---|
| `depth` | 30 | base depth % | 15 → 5 |
| `weekly_tightness` | 25 | weekly close range % | 10 → 3 |
| `right_side` | 20 | right-side range % | 8 → 2 |
| `length` | 15 | base length, weeks | 5 → 10 |
| `prior_advance` | 10 | prior advance % | 20 → 60 |

Dry-up measure: `volume_dryup_ratio(volume, recent = 10, base = 50)` at the as-of bar (§12A).

## 13.5 Stored details (`details_json`)

`weekly_close_range_pct`, `weeks`, `right_side_range_pct`, `right_side_low`, `prior_advance_low_date`, `closes_above_pivot` (count of closes above the pivot without a breakout), `pivot_buffer_pct`.

## 13.6 Worked example (synthetic)

A stock rises from 80 to a high of 120.00 (bar `s`), then trades 30 sessions between 104.00 and 119.50; weekly closes 117, 112, 116, 110, 114, 117, 118 (the last week partial); last 10 bars between 113.20 and 119.50; prior advance 50 %; volume of the last 10 bars 0.75 × the 50 before.
Depth (120 − 104) / 120 = 13.3 % → grade 2 limits pass; weekly close range (118 − 110) / 110 = 7.3 %; length 31 sessions (≥ 25), 7 weeks. Not grade 3 (depth > 10 %). **FLAT_BASE**, pivot 120.12, stop 113.20; a close of 118.00 is 1.8 % below the pivot → **PIVOT_READY**.

## 13.7 Config

```yaml
strategy_id: flat_base
algorithm_version: flat_base-1.0.0
enabled: false
stage: research
trend_gate: {mode: required}
detector:
  min_duration_days: 20          # the start search; FLAT_BASE itself needs 25
  max_duration_days: 65
  min_prior_advance_pct: 20
  prior_advance_lookback_days: 120
  pivot_buffer_pct: 0.1
  max_overshoot_pct: 3
  right_side_days: 10
  dryup_recent_days: 10
  dryup_base_days: 50
classification:
  flat_base_like:   {grade: 1, max_depth_pct: 20, min_duration_days: 20}
  flat_base:        {grade: 2, max_depth_pct: 15, min_duration_days: 25, max_weekly_close_range_pct: 10}
  tight_flat_base:  {grade: 3, max_depth_pct: 10, min_duration_days: 25, max_weekly_close_range_pct: 6,
                     max_right_side_range_pct: 5, max_dryup_ratio: 0.8}
ranking: {min_grade: 2, pivot_ready_max_distance_pct: 3}
scoring: {weights: {...}, bounds: {...}}   # §13.4
```

As built: `config/strategies/flat_base.yaml` (enabled false, stage research).

---

# 14. Three Weeks Tight (`three_weeks_tight`, `three_weeks_tight-1.1.0`) — signed off 2026-10-05; 1.1.0 after the chart review (§14.11)

Three weekly closes in a row within about 1–1.5 % of each other, after an advance: a continuation pattern that often forms after a breakout or in a strong uptrend (O'Neil). Weekly, so it uses `weekly_bars` (§12A).

## 14.1 Pattern

1. **Weeks used**: completed weeks only. Mid-week (as-of Monday–Thursday) the as-of week is left out; on a Friday it counts. (Decision T1, §14.9.)
2. **Candidate**: the 3 consecutive completed weeks ending `k` weeks ago, for k = 0 … `max_age_weeks` (2), most recent first; the first that meets grade 1 is the setup. So a 3WT stays a setup for 2 more weeks after it completes, while the stock waits to break out. (Decision T2, §14.9.)
3. **Tightness** = `max_weekly_close_change_pct(closes, 2)` over the 3 closes (each against the week before it, §12A); also stored: `weekly_close_range_pct(closes, 3)`.
4. **Tight weeks**: how many consecutive weeks up to the pattern's last week meet the grade-2 change rule (3 = classic, 4+ = "four weeks tight" and longer).
5. **Pattern high / low** = highest high and lowest low of the 3 weeks' daily bars (`base_extremes`); depth % as §6.1. `base_start_date` = first day of week 1; duration = its sessions.
6. **Prior advance** = `prior_advance(high, low, end, lookback = 120)` where `end` is the bar with the highest high from 20 sessions before week 1 to the end of week 3; ≥ `min_prior_advance_pct` (20).
7. **Pivot** = pattern high × (1 + 0.1 %); **stop_reference_price** = pattern low.

No setup: `INSUFFICIENT_HISTORY`, `NOT_TIGHT` (no candidate meets grade 1), `NO_PRIOR_ADVANCE`, `BELOW_PATTERN` (a close below the pattern low since week 3 ended: the pattern is broken, and an older candidate is not tried).

## 14.2 Tiers and grades

| Tier | Grade | Rules |
|---|---|---|
| `THREE_WEEKS_TIGHT_LIKE` | 1 | tightness ≤ 2.5 % |
| `THREE_WEEKS_TIGHT` | 2 | tightness ≤ 1.5 %; pattern depth ≤ 15 % (1.1.0); Trend Template PASS and weekly Stage 2 |
| `THREE_WEEKS_TIGHT_A` | 3 | tightness ≤ 1.0 %; pattern depth ≤ 6 %; dry-up ≤ 0.8 |

## 14.3 Status

Data state → BREAKOUT / FAILED (events) → `PIVOT_READY` (grade ≥ 2, close 0 – 3 % below the pivot) → `FORMING`. `confirmation_state` = `CONFIRMED` (completed weeks only).

## 14.4 Score part

| Sub-component | Weight | Measurement | worst → best |
|---|---|---|---|
| `tightness` | 35 | largest weekly close change % | 1.5 → 0.3 |
| `pattern_depth` | 25 | (pattern high − low) / high % | 10 → 3 |
| `prior_advance` | 20 | prior advance % | 20 → 60 |
| `tight_weeks` | 10 | consecutive tight weeks | 3 → 5 |
| `near_high` | 10 | % the pivot is below the 52-week high | 10 → 0 |

Dry-up measure: `volume_dryup_ratio(volume, recent = sessions of the 3 weeks, base = 50, end = last bar of week 3)`.

## 14.5 Stored details

`weekly_closes` (the 3), `max_close_change_pct`, `close_range_pct`, `tight_weeks`, `age_weeks` (k), `week_ends`.

## 14.6 Worked example (synthetic)

Weekly closes 100.00, 101.20, 100.60 (weeks ending Fri 11, 18, 25 Sep); daily highs/lows in them 97.80 … 102.30; prior advance 35 %; as-of Wednesday 30 Sep.
Changes: 1.20 % and 0.59 % → tightness 1.20 % ≤ 1.5 → **THREE_WEEKS_TIGHT** (not grade 3: > 1.0 %). Pivot 102.30 × 1.001 = 102.40; stop 97.80; depth 4.4 %. Close on 30 Sep 101.00 → 1.4 % below the pivot → **PIVOT_READY**. It stays a setup through the next two weeks (k = 1, 2) unless a close below 97.80, or until it breaks out.

## 14.7 Config

```yaml
strategy_id: three_weeks_tight
algorithm_version: three_weeks_tight-1.1.0
enabled: false
stage: research
trend_gate: {mode: required}
detector:
  max_age_weeks: 2
  min_prior_advance_pct: 20
  prior_advance_lookback_days: 120
  prior_high_window_days: 20
  pivot_buffer_pct: 0.1
  dryup_base_days: 50
classification:
  three_weeks_tight_like: {grade: 1, max_close_change_pct: 2.5}
  three_weeks_tight:      {grade: 2, max_close_change_pct: 1.5, max_depth_pct: 15}   # 1.1.0
  three_weeks_tight_a:    {grade: 3, max_close_change_pct: 1.0, max_depth_pct: 6, max_dryup_ratio: 0.8}
ranking: {min_grade: 2, pivot_ready_max_distance_pct: 3}
scoring: {weights: {...}, bounds: {...}}   # §14.4
```

As built: `config/strategies/three_weeks_tight.yaml` (enabled false, stage research). The detector needs 262 bars (the 52-week high for `near_high`).

## 14.10 First results (research copy, 2026-10-05; development period only)

Built in step 4 and run over all 248 weekly research dates (2022-01 … 2026-09) on a copy of the research DB (`data/tmp/ms4/res.duckdb`; the research DB itself is unchanged until the owner decides).

| | Flat base | Three Weeks Tight |
|---|---|---|
| Grade 2+ setup rows / distinct bases (2022–2026) | 3,433 / 1,638 | 5,647 / 3,468 |
| Grade 3 rows | 82 | 204 |
| Eligible (ranked) score rows | 3,195 | 5,444 |
| Same-day overlap with VCP's eligible setups | 46 % | 34 % |

Development walk-forward (2022-02-01 … 2024-06-30, rule `hold_s7`, 15 bps a side; survivorship PARTIAL):

| Strategy | Signals | Trades | Win | Avg | Profit factor | Portfolio CAGR / max DD |
|---|---|---|---|---|---|---|
| VCP (same copy, current code) | 6,690 | 1,803 | 29.3 % | +3.77 % | 1.74 | 6.8 % / −40.6 % |
| Flat base | 1,489 | 449 | 38.3 % | +5.58 % | 2.28 | 25.0 % / −16.5 % |
| Flat base, baseline (every setup with a pivot) | 8,748 | 1,675 | 32.5 % | +4.17 % | 1.87 | 18.9 % / −25.0 % |
| Three Weeks Tight | 2,985 | 783 | 32.4 % | +4.71 % | 1.98 | 15.4 % / −21.1 % |
| Three Weeks Tight, baseline | 7,440 | 1,535 | 32.2 % | +4.83 % | 2.00 | 13.3 % / −30.0 % |

Not yet read as an edge (§10.4): chart review (D6) comes first; several strategies are being compared (multiple testing); validation is unopened.

**For the chart review:** 49 % of grade-2+ Three Weeks Tight patterns are three closes drifting the same way (a steady trend of about 1 % a week also meets the close-change rule), and their median pattern depth is 10.5 % (25 %/75 %: 7.9 %/13.8 %): weekly closes are tight but the weeks' ranges often are not. A depth or close-range limit for grade 2 is a candidate change after the review.

## 14.11 Chart review (D6) and decisions (owner, 2026-10-05)

Answers: `reports/chart_review/<strategy>/review_answers_2026-10-05.csv` (30 windows each, development period).

- **Flat base: 27 yes, 3 partly, 0 no.** Geometry accepted; `flat_base-1.0.0` unchanged (R3). The two "earlier entry" notes (RVNL, UJJIVANSFB: a tight range breaking out inside the base) were found by Three Weeks Tight and VCP on those dates: multi-label covers them.
- **Three Weeks Tight: 23 yes, 4 partly, 1 unsure, 2 no.** Every negative answer with a note is a pattern that includes the sharp move into it, 16–32 % deep with a wide stop (NEULANDLAB 31.9, BLS 29.1, PGIL 28.2, AGARIND 24.7, FOODSIN 22.5, AUTOAXLES 16.4); the grade-2/3 charts marked yes are ≤ 13.9 % deep except ASHAPURMIN (15.6). **R1 (1.1.0): grade 2 needs pattern depth ≤ 15 %** (16 % would separate this sample perfectly but would be fitted to one chart). It removes 19 % of grade-2 rows (2022–2026; 2,678 distinct bases remain). The steady-drift concern of §14.10 was not borne out: 11 of the 15 same-direction charts were marked yes, and the others were rejected for depth.
- **R2: the owner's stop** ("the previous low or 8 % / 5 %, whichever is tighter") is tested as two exit rules, `hold_low8` and `hold_low5` (no target; stop = tighter of the setup's `stop_reference_price` × 0.995 and −8 % / −5 %; 60-session time exit), against `hold_s7` on the development period. Two more rule variants tried (§10.4).
- HAL note (avoid until it comes back to the pivot after a failed move): a trading rule, kept for the live-paper phase.

## 14.8 Overlap expected

A 3WT often sits inside a flat base or a VCP's final contraction. All are recorded (§7); the step-7 report measures how often they coincide.

## 14.9 Decisions for step 4 (owner, 2026-10-05: all as recommended)

| # | Question | Options | Decided |
|---|---|---|---|
| G1 | Lowest ranked grade for the new strategies | (a) 2: only full patterns are ranked and traded; grade 1 is stored for research and the `--baseline` · (b) 1, as VCP (VCP_LIKE is ranked) | (a): grade 1 here is loose by design; VCP stays as it is |
| F1 | Flat-base stop level | (a) right-side low (last 10 bars) · (b) base low | (a): the base low can be 15 % away; the engine's default rule (`hold_s7`) uses −7 % anyway, this only matters for `t20_low8` |
| F2 | Leaving the range without a breakout | (a) a close > 3 % above the pivot without breakout volume ends the base · (b) any close above the pivot ends it · (c) never | (a) |
| T1 | 3WT and the unfinished week | (a) completed weeks only · (b) the as-of week counts, setup `PROVISIONAL` mid-week | (a): a weekly pattern is judged on weekly closes |
| T2 | How long a 3WT stays a setup | (a) 2 weeks after it completes · (b) only the week after · (c) until broken | (a) |
| C1 | Chart review (D6) sample | ~30 per strategy from the development period: 12 grade 2, 8 grade 3 (or all if fewer), 6 grade 1, 4 near-misses (failed one rule by a small margin), stratified by year | as listed |

---

# 15. Cup and handle (`cup_handle-1.0.0`) and Double bottom (`double_bottom-1.0.0`) — signed off 2026-10-05

Two longer bases from O'Neil's published descriptions. Both are found on adjusted daily bars ending at the as-of bar `t`, with the shared measurements of §12A, the shared breakout rule (§10.1) and the scan path of §12B. Every number is a config value and a starting hypothesis, tuned on the development period only. The other rules carry over from §13: the pivot never moves, a breakout freezes the base's shape (it is measured up to the day before the breakout), and the move-away rule from decision F2 applies: a close more than 3 % above the pivot without breakout volume means no setup (`MOVED_ABOVE_BASE`).

## 15.1 Cup and handle (`cup_handle`)

A rounded, U-shaped correction after an advance. The right side climbs back near the old high, then a short, shallow pullback (the handle) forms in the upper half, on light volume. The buy point is the top of the handle.

**Points** (all indices into the daily bars; earliest bar on a tie):

1. **Right lip `R`** (the handle's start): the highest high among bars `t − max_handle_days + 1` … `t − min_handle_days + 1` (5 … 25 sessions ago), with no high after it above it. So the handle is 5 to 25 sessions long and stays below its start.
2. **Left lip `L`**: the highest high among bars `R − max_cup_days` … `R − min_cup_days` (35 … 325 sessions before `R`, i.e. 7 … 65 weeks), with no high between `L` and `R` above `R`'s high × (1 + `lip_tolerance_pct` / 100) (3 %): the cup stays below its rims.
3. **Cup bottom `B`**: the lowest low between `L` and `R`.
4. **Handle low `H`**: the lowest low from `R` to `t` (or to the day before a breakout).

**Measurements**

| Name | Definition |
|---|---|
| cup depth % | (high[L] − low[B]) / high[L] × 100 |
| cup length | `R − L` sessions (and ISO weeks) |
| right-lip gap % | (high[L] − high[R]) / high[L] × 100 (negative when the right lip is higher) |
| bottom share | share of the cup's bars (`L` … `R`) whose low is in the lowest quarter of the cup's range (≤ low[B] + 25 % × (high[L] − low[B])) |
| bottom position | (B − L) / (R − L): where the bottom sits in the cup, 0 = left lip, 1 = right lip |
| handle depth % | (high[R] − low[H]) / high[R] × 100 |
| handle position | (low[H] − low[B]) / (high[L] − low[B]): 0.5 = cup midpoint, 1 = left lip |
| handle dry-up | `volume_dryup_ratio(volume, recent = handle sessions, base = 50, end = last handle bar)` |
| prior advance % | `prior_advance(high, low, L, lookback = 120)` |

**Rounded, not V (the judgement call made measurable):** a U spends time near its low and has its low in the middle; a V touches the low once. Rule: bottom share ≥ `min_bottom_share` (0.15) **and** at least `min_bottom_sessions` (5) bars in the lowest quarter **and** bottom position between 0.15 and 0.85. (Decision C1, §15.4.)

**Tiers**

| Tier | Grade | Rules (all must hold) |
|---|---|---|
| `CUP_HANDLE_LIKE` | 1 | (1.1.0, all tiers: right lip at most 3 % above the left lip, else no cup) cup depth ≤ 50 %; handle depth ≤ 15 %; handle in the upper half (handle position ≥ 0.5); right-lip gap ≤ 15 % |
| `CUP_HANDLE` | 2 | cup depth 12 … 33 %; rounded (rule above); handle depth ≤ 12 %; handle position ≥ 0.5; right-lip gap ≤ 10 %; prior advance ≥ 30 %; Trend Template PASS and weekly Stage 2 |
| `CUP_HANDLE_A` | 3 | as grade 2, and handle depth ≤ 8 %, handle dry-up ≤ 0.8, handle low above the 50-day average close |

No setup: `INSUFFICIENT_HISTORY` (fewer bars than the shortest cup and handle need), `NO_HANDLE` (no right lip that the later bars stay below), `NO_CUP` (no left lip, or a high inside the cup above the rims), `NO_PRIOR_ADVANCE` (below the detector minimum of 20 %), `TOO_DEEP` (no tier met), `MOVED_ABOVE_BASE`.

**Pivot and stop:** pivot = high[R] × (1 + 0.1 %), the top of the handle. `stop_reference_price` = the handle low. `base_start` = the left lip's date, `base_end` = the breakout date. Status as §13.3.

**Score part** (`PATTERN`, weights sum to 100):

| Sub-component | Weight | Measurement | worst → best |
|---|---|---|---|
| `roundness` | 25 | bottom share | 0.15 → 0.40 |
| `handle_depth` | 25 | handle depth % | 12 → 4 |
| `handle_position` | 20 | handle position | 0.5 → 0.85 |
| `cup_depth` | 20 | cup depth % | 33 → 15 |
| `prior_advance` | 10 | prior advance % | 30 → 100 |

Dry-up measure for the shared volume score: the handle dry-up.

**Stored details:** `left_lip_date`, `bottom_date`, `right_lip_date`, `handle_low_date`, cup and handle depths, cup weeks, handle sessions, right-lip gap, bottom share and position, handle position, handle dry-up.

**History needed:** 325 + 25 + 120 + 10 = 480 bars (about two years). The research data starts in 2021, so cups longer than about a year can only be found from 2023 onwards. (Decision C3, §15.4.)

**Worked example (synthetic):** a stock rises 45 % to a left lip of 200 (bar `L`), falls over 6 weeks to a low of 160 (depth 20 %), spends 2 weeks between 160 and 170 (12 of 60 cup bars in the lowest quarter, below 170: bottom share 0.20, bottom position 0.45), climbs over 4 weeks to a right lip of 196 (gap 2 %), then drifts 8 sessions down to 186 on 0.7 × volume (handle depth 5.1 %, handle position (186 − 160) / 40 = 0.65). Handle low 186 above the 50-day average close (about 181). → **CUP_HANDLE_A**, pivot 196.20, stop 186; the close is 2.7 % below the pivot → **PIVOT_READY**.

## 15.2 Double bottom (`double_bottom`)

A W: a decline, a rally to a middle peak, a second decline that slightly undercuts the first low (shaking out weak holders), then a rally back. The buy point is the top of the middle peak.

**Points**

1. **Left high `L`**: the highest high among bars `t − max_base_days` … `t − min_base_days` (325 … 35 sessions ago).
2. **Second low `B2`**: the lowest low after `L` (the undercut is the lowest point of the base).
3. **Middle peak `M` and first low `B1`**: for each bar `m` between `L` and `B2`, its first low is the lowest low between `L` and `m`; `m` qualifies when it bounced at least the grade-1 minimum (8 %) above that low and the spacing below holds. `M` = the qualifying bar with the highest high (the latest on a tie); `B1` = its first low.
4. Spacing: at least `min_leg_days` (5) sessions between `L` and `B1`, `B1` and `M`, `M` and `B2`; at least 3 sessions after `B2` up to `t`.

**Measurements:** base depth % = (high[L] − low[B2]) / high[L]; undercut % = (low[B1] − low[B2]) / low[B1] × 100 (≥ 0); middle bounce % = (high[M] − low[B1]) / low[B1] × 100; middle-peak position = (high[M] − low[B2]) / (high[L] − low[B2]); right side = range % and low of the last 10 bars; prior advance % into `L` (lookback 120).

**Tiers**

| Tier | Grade | Rules |
|---|---|---|
| `DOUBLE_BOTTOM_LIKE` | 1 | depth ≤ 50 %; undercut 0 … 8 %; middle bounce ≥ 8 % |
| `DOUBLE_BOTTOM` | 2 | depth 15 … 35 %; undercut 0 … 5 % (the second low at or slightly below the first); middle bounce ≥ 10 %; middle peak below the left high; base ≥ 35 sessions; prior advance ≥ 30 %; Trend Template PASS and weekly Stage 2 |
| `DOUBLE_BOTTOM_A` | 3 | as grade 2, and a true undercut (> 0 %), right-side range ≤ 8 %, volume dry-up ≤ 0.8 |

No setup: `INSUFFICIENT_HISTORY`, `NO_W` (the points cannot be placed with the spacing), `NO_PRIOR_ADVANCE`, `TOO_DEEP`, `MOVED_ABOVE_BASE`.

**Pivot and stop:** pivot = high[M] × (1 + 0.1 %). `stop_reference_price` = the right-side low (last 10 bars), as decision F1 for flat bases; the second low is usually 15 %+ below the pivot. (Decision D2, §15.4.)

**Score part:** `right_side` 25 (right-side range % 8 → 2), `depth` 25 (35 → 15), `undercut` 15 (0 → 3: a small undercut is the textbook form), `middle_peak` 15 (middle-peak position 0.5 → 0.85), `prior_advance` 20 (30 → 100). Dry-up: last 10 bars vs the 50 before.

**Worked example (synthetic):** left high 300; first low 240 (bar 20 after `L`); middle peak 276 (bounce 15 %, position (276 − 237) / 63 = 0.62); second low 237 (undercut 1.25 %, depth 21 %); rally to a close of 270, last 10 bars 262 … 274 (range 4.4 %). → **DOUBLE_BOTTOM** (grade 3 also needs the dry-up), pivot 276.28, stop 262; the close is 2.3 % below the pivot → **PIVOT_READY**.

## 15.3 Overlap expected

A cup with a handle is often also a VCP (two contractions: the cup and the handle) or a flat base once the handle is long. A double bottom's right side can be a Three Weeks Tight. All are recorded (§7).

## 15.4 Decisions for step 5 (owner, 2026-10-05: all as recommended)

| # | Question | Options | Decided |
|---|---|---|---|
| C1 | How to tell a rounded cup from a V | (a) bottom share ≥ 0.15, ≥ 5 bars in the lowest quarter, bottom in the middle 70 % of the cup · (b) also require each side of the cup to last ≥ 3 weeks · (c) no rule, leave it to the chart review | (a): measurable and close to how a U looks; the chart review checks it |
| C2 | Cup without a handle | (a) handle required (5 … 25 sessions) · (b) also a separate tier for a cup without a handle, pivot at the left lip | (a): the handle is where the buy point and the tight stop are; a cup without a handle is often caught as a flat base or VCP |
| C3 | Longest cup | (a) 65 weeks (O'Neil), accepting that long cups appear only from 2023 in our data · (b) 40 weeks | (a) |
| D1 | Double bottom in this step | (a) build both now · (b) cup and handle first, double bottom after its chart review | (a): same building blocks; one rescan and one chart review round for both |
| D2 | Double-bottom stop | (a) right-side low (last 10 bars) · (b) the second low | (a), as for flat bases (F1) |
| G1′ | Ranked grades | grade 2+, as decided for flat base and 3WT (G1) | as G1 |
| C4 | Chart review | ~30 per strategy, development period, same mix as C1 of step 4 (12 grade 2, 8 grade 3, 6 grade 1, 4 near-misses) | as listed |

## 15.5 As built (step 5b, 2026-10-05) and three readings (owner, 2026-10-05)

Config: `config/strategies/cup_handle.yaml`, `double_bottom.yaml` (enabled false, stage research, `ranking.min_grade` 2); every number of §15.1–15.2 is a key there. Grade 3 repeats the grade-2 rules in its tier, as for the flat base. Lookback: cup 480 bars, double bottom 455.

Where §15.1 could be read two ways, the owner chose (2026-10-05):

- **A. Rim check:** no high between `L` and `R` above the **higher** rim × 1.03 (max(high[L], high[R])). Read literally (high[R] × 1.03), any cup whose right lip is more than about 3 % below the left would fail on the bars just after `L`, contradicting the 10–15 % right-lip gaps the tiers allow.
- **B. Above the handle start without a breakout:** point 1 is kept strict. A close more than 3 % above the pivot without breakout volume is `MOVED_ABOVE_BASE`; any other high above high[R] up to the handle's end is `NO_HANDLE` (F2's "a smaller close above the pivot leaves the base intact" does not apply to the cup: the handle stays below its start). A breakout less than 5 sessions after `R` (handle too short) is `NO_HANDLE`.
- **C. After a breakout:** `R` is searched 5–25 sessions back from the as-of bar, so a broken-out cup stays visible (BREAKOUT / FAILED) only until its breakout bar becomes the highest high of that window, about 4 sessions. Trades are unaffected: signals come from the scans before the breakout. A recorded breakout is reused only for the same left lip **and** the same pivot.

**Double bottom after a breakout (owner, 2026-10-05, option a):** its left high stays in the 35–325-session window long after a breakout, so on the first real-data check (6 dates, copy of the research DB) about 75 % of double-bottom setups were BREAKOUT/FAILED, grade 2+ breakouts a median 35 days old. Those are ranked, and the backtest engine enters on any breakout-volume close above the pivot (not only a cross), which would chase stocks 15–20 % above the middle peak. Rule: more than `max_days_after_breakout` (10) sessions after its breakout, a double bottom is no setup (`OLD_BREAKOUT`), the same two weeks a Three Weeks Tight stays a setup (T2). Rejected: (b) leave it and note it; (c) require a cross in the engine (changes every strategy's backtest; for step 7).

Other details fixed in code:

- Cup: handle sessions = bars `R` … handle end, `R` included (5 … 25). Bottom share counts bars `L` … `R`, both included. `base_start` = left lip, `base_duration_days` = `L` … handle end, `pivot_source` = `HANDLE_HIGH`. The 50-day average for grade 3 is of closes ending at the handle's last bar.
- Double bottom: the second low is the **latest** bar on a tie, so two equal lows are a W with a 0 % undercut (not grade 3, which needs a true undercut). The first low of a candidate middle peak `m` is the lowest low of `L+1` … `m−1` (earliest on a tie). After a breakout the points are measured up to the day before it; `pivot_source` = `MIDDLE_PEAK`.
- Stored details: cup `left_lip_date`, `bottom_date`, `right_lip_date`, `handle_low_date`, `cup_depth_pct`, `handle_depth_pct`, `cup_sessions`, `cup_weeks`, `handle_sessions`, `right_lip_gap_pct`, `bottom_share`, `bottom_sessions`, `bottom_position`, `rounded`, `handle_position`, `handle_dryup_ratio`, `handle_low`, `sma_close`, `prior_advance_low_date`, `pivot_buffer_pct`. Double bottom `left_high_date`, `first_low_date`, `middle_peak_date`, `second_low_date`, `first_low`, `middle_peak`, `second_low`, `undercut_pct`, `middle_bounce_pct`, `middle_peak_position`, `right_side_range_pct`, `right_side_low`, `base_sessions`, `prior_advance_low_date`, `pivot_buffer_pct`.
- Chart review sheet: charts of long bases start 40 bars before the base start (more than the usual 260 bars when needed), and the points are drawn as labelled dots (cup L, B, R, H; double bottom L, B1, M, B2).

## 15.6 First results (research DB, 2026-10-05; development period only)

Rescan of `data/golden_src.duckdb` (code 99224e0, 21:29–21:52 IST, under the daily-run lock, backup `golden_src_20261005_155936.duckdb`): both strategies over all 248 weekly dates, scores and labels (cup 15,176 observations, config `a7f96c6ec87e`; double bottom 3,063, `dd7107637b1b`). No errors.

| | Cup and handle 1.0.0 | Double bottom 1.0.0 |
|---|---|---|
| Grade 1 / 2 / 3 rows (2022–2026) | 12,140 / 2,583 / 453 | 2,178 / 810 / 75 |
| Grade 2+ distinct bases (summed by year) | 1,439 | 374 |
| Grade 2+ rows / bases, development period | 1,677 / 796 | 372 / 157 |
| Eligible (ranked) score rows | 2,943 | 801 |
| Grade 2+ median depth; length | 21.2 %; 75 sessions | 23.1 %; 58 sessions |
| Grade 2+ stop below the pivot (10/50/90 %) | 6.0 / 9.2 / 11.6 % | 7.2 / 13.2 / 20.3 % |
| Most common no-setup reasons | NO_HANDLE 14,785, TOO_DEEP 12,879, NO_CUP 2,455 | NO_W 27,725, OLD_BREAKOUT 10,966, TOO_DEEP 3,754 |
| Why grade 1 is not grade 2 (top) | not rounded 7,833, handle too deep 4,543 | undercut > 5 % 990, middle peak not below the left high 867 |

Same-day overlap of eligible setups: cup → VCP 22 %, → flat base 9 %, → 3WT 13 %, → double bottom 2 %; double bottom → VCP 54 %, → flat base 4 %, → 3WT 9 %, → cup 7 %. (VCP → cup 5 %, → double bottom 4 %.)

Development walk-forward (2022-02-01 … 2024-06-30, 15 bps a side; survivorship PARTIAL):

| Strategy | Rule | Signals | Trades | Win | Avg | PF | Portfolio CAGR / max DD |
|---|---|---|---|---|---|---|---|
| Cup and handle | hold_s7 | 1,626 | 476 | 31.3 % | +4.08 % | 1.83 | 18.7 % / −28.2 % |
| Cup and handle | hold_low8 | 1,626 | 465 | 33.8 % | +4.18 % | 1.79 | 16.1 % / −31.1 % |
| Cup and handle | hold_low5 | 1,626 | 496 | 23.0 % | +3.19 % | 1.79 | 17.7 % / −32.6 % |
| Cup and handle, baseline | hold_s7 | 8,391 | 1,565 | 29.6 % | +3.72 % | 1.74 | 22.2 % / −17.8 % |
| Double bottom | hold_s7 | 341 | 119 | 25.2 % | +1.56 % | 1.29 | 3.5 % / −18.1 % |
| Double bottom | hold_low8 | 341 | 119 | 31.1 % | +3.25 % | 1.60 | 8.6 % / −20.0 % |
| Double bottom | hold_low5 | 341 | 129 | 17.1 % | +0.61 % | 1.14 | 1.6 % / −16.8 % |
| Double bottom, baseline | hold_s7 | 1,516 | 492 | 30.7 % | +4.51 % | 1.91 | 21.2 % / −35.4 % |

Not read as an edge (§10.4): the chart review comes first, several strategies are compared, validation is unopened. For both, the graded setups do no better than every setup with a pivot (baseline).

**Found while checking the review-sheet marks (cup):** the right lip is usually well **above** the left lip. Grade 2+ right-lip gap: median −9.4 % (10/25/75/90 %: −27.6 / −17.3 / −3.9 / +1.0 %); 78 % of grade 2+ rows have the right lip more than 3 % above the left. §15.1 bounds the gap only from above (≤ 10 %), so a pullback in an uptrend that rallies past the old high and then pauses passes as a "cup with handle". The stock has already cleared the left-lip pivot; this is not O'Neil's form.

**Decision (owner, 2026-10-05, option a): `cup_handle-1.1.0`.** The right lip may be at most `max_right_lip_above_pct` (3 %, the rim tolerance) above the left lip; otherwise there is no cup (`NO_CUP`). Rejected: (b) a −5 % gap floor for grades 2–3 only; (c) no rule, left to the chart review. The 1.0.0 rows stay in the research DB under config `a7f96c6ec87e` as a version tried. Results of 1.1.0: §15.7.

## 15.7 Cup and handle 1.1.0 results (research DB, 2026-10-05; development period only)

Rescan of `data/golden_src.duckdb` (code bd595ab, 22:35–22:46 IST, under the daily-run lock after the 22:00 daily run finished; backup `golden_src_20261005_170507.duckdb`): cup_handle 1.1.0 over all 248 weekly dates, scores, labels (2,268 observations, config `e36477f23b36`). No errors.

| | Cup and handle 1.1.0 | (1.0.0) |
|---|---|---|
| Grade 1 / 2 / 3 rows (2022–2026) | 1,597 / 530 / 141 | 12,140 / 2,583 / 453 |
| Grade 2+ rows / distinct bases, development period | 325 / 201 | 1,677 / 796 |
| Eligible (ranked) score rows | 649 | 2,943 |
| Grade 2+ right-lip gap (10/50/90 %) | −2.5 / +0.5 / +4.9 % | −27.6 / −9.4 / +1.0 % |
| Grade 2+ median depth; length | 23.9 %; 74 sessions | 21.2 %; 75 sessions |
| Grade 2+ stop below the pivot (10/50/90 %) | 5.2 / 7.8 / 10.7 % | 6.0 / 9.2 / 11.6 % |
| Most common no-setup reasons | NO_CUP 34,828, NO_HANDLE 4,724, TOO_DEEP 4,171 | |
| Why grade 1 is not grade 2 (top) | cup deeper than 33 % 1,099, not rounded 576, handle too deep 362 | |

Same-day overlap of eligible setups: cup → VCP 44 %, → flat base 11 %, → 3WT 18 %, → double bottom 5 %.

| Rule | Signals | Trades | Win | Avg | PF | Portfolio CAGR / max DD |
|---|---|---|---|---|---|---|
| hold_s7 | 317 | 136 | 32.4 % | +4.63 % | 1.96 | 14.5 % / −14.9 % |
| hold_low8 | 317 | 135 | 35.6 % | +5.45 % | 2.07 | 15.5 % / −16.4 % |
| hold_low5 | 317 | 136 | 26.5 % | +3.85 % | 2.01 | 12.4 % / −12.2 % |
| baseline, hold_s7 | 1,032 | 401 | 29.9 % | +3.89 % | 1.78 | 14.6 % / −20.6 % |

Graded cups now do better per trade than the baseline (PF 1.96 vs 1.78), on 136 trades. Not read as an edge (§10.4): chart review first, several strategies compared, validation unopened. The review sheet was rebuilt from 1.1.0 (text sketches of the windows now show the right lip near the left lip).

## 15.8 Chart review (decision C4) and decisions (owner, 2026-10-05)

Answers: `reports/chart_review/<strategy>/review_answers_2026-10-05.csv` (30 windows each, development period; no notes given).

- **Cup and handle 1.1.0: 29 yes, 1 partly (FILATEX, nothing unusual in its numbers), 0 no.** Geometry accepted; `cup_handle-1.1.0` unchanged.
- **Double bottom 1.0.0: 16 yes, 13 partly, 1 no (SPANDANA).** By grade: grade 3 8 of 8 yes; grade 2 4 yes / 7 partly / 1 no; grade 1 and near-misses 4 yes / 6 partly. No measured value separates the grade-2 yes from the partly charts (right-side range: yes 13–19 %, partly 9–20 %; undercut, middle-peak position and leg spacing overlap); two partly answers are breakouts with the close 14–23 % above the pivot (GRSE, HNDFDS). Without notes on which mark was off, no rule was fitted. **Owner decision (option b): `double_bottom-1.0.0` accepted as is.** Rejected: (a) collect notes on the 14 partly/no charts first; (c) rank grade 3 only (about 75 rows in 2022–2026, too few trades).
- Both strategies go to step 7 (comparison, exit rule, validation once per strategy) with their current versions: cup_handle-1.1.0 (`e36477f23b36`), double_bottom-1.0.0 (`dd7107637b1b`).

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

---

# 20. Step 7 re-planned: entry, market regime and exits (signed off 2026-10-06)

After step 5 the owner judged every strategy's development results average (profit factor 1.7–2.3, win rate 25–38 %, median trade −7.3 % = most trades end at the stop; graded setups barely beat "every setup with a pivot"; the setup score does not rank, Phase 7). The likely gains are in the rules around the patterns, which are shared by all strategies, rather than in more pattern detectors. Step 7 is re-planned so these are tested first; High tight flag (step 6) moves after step 7.

## 20.1 Order

| Part | Content |
|---|---|
| 7a | This section (docs only) |
| 7b | Build: entry rules, regime filter and trailing exits in the event engine; `--entry`, `--regime` and the new `--rule` names on `backtest run` / `walk-forward`; tests. **Defaults stay as today** (entry `breakout`, regime `none`, rule `hold_s7`), so no stored result and no existing command changes |
| 7c | Development-period runs on the research DB (backtest runs only; no scan, score or label changes), staged as §20.5 |
| 7d | Report; owner picks one entry rule, one regime rule and one exit rule, the same for every strategy |
| 7e | The original step 7 with those rules frozen: comparison report and overlap, validation opened once per strategy (§10.4), signal ranking for portfolio slots, the combined list (§7), the owner's go-live decision |
| then | Step 6 High tight flag, step 8 daily run and per-strategy lists |

## 20.2 Entry (decision E: option c)

Today an entry is the first close above the pivot on volume ≥ 1.5 × the 50-session mean, within `watch_days` (20) sessions after the scan date. A stock already well above its pivot enters on its next busy day, far from the buy point.

New entry rule `cross_5` (name in config and on the command line):

1. the breakout-volume close above the pivot (as today), **and**
2. a real cross: the previous session's close is at or below the pivot, **and**
3. the close is at most `max_entry_extension_pct` (5 %) above the pivot (O'Neil's buy zone).

A day that fails 2 or 3 does not end the watch: a later session may still cross from below within `watch_days`. Exits, stops and costs are unchanged. `breakout` (today's rule) stays available.

## 20.3 Market regime (decision R: options a and b, tested separately)

Computed from our own universe and stored features only (no index data and no new provider; a Nifty index series, option d, would be a data-policy change and is left for later). For each trading day `d`, over the instruments that are universe members on `d` and have that day's adjusted close and features:

- **`breadth50`**: share of them whose close is above their 50-day average (`technical_features_daily.sma_50`). Regime **on** when ≥ 40 %.
- **`ew50`**: an equal-weight index of the universe (cumulative product of 1 + the mean of the members' daily returns, `daily_return`), on when its value is above its own 50-day average.

The regime only gates **new entries**: an entry on day `d` needs the regime on at `d`'s close (known at the same close the entry uses, so no look-ahead). A signal whose breakout happens on an "off" day does not enter that day; its watch continues. Open trades are never closed by the regime. Thresholds (40 %, 50 days) are fixed here and **not tuned**. `none` (today) stays available. The daily on/off series is computed when a backtest runs (nothing stored now); storing it for the daily run is a step-8 decision.

## 20.4 Exits (decision X: options a and b)

Two new trade rules beside `hold_s7` and `hold_low8` (the others stay available):

| Rule | Initial stop | Trailing exit | Time exit |
|---|---|---|---|
| `trail_e20` | −7 % below entry | the close below the 20-day EMA (`ema_20`), from the 5th session after entry; filled at that close | 120 sessions |
| `trail_s50` | −7 % below entry | the close below the 50-day average (`sma_50`), from the 5th session after entry | 120 sessions |

The −7 % stop is checked first each day (intraday low, as today); the trailing exit uses the close. A "half at +20 %" rule (option c) needs partial exits in the engine and is left for later.

## 20.5 How it is tested (decision T: option a)

Development period only (2022-02-01 … 2024-06-30), every strategy (VCP, flat base, 3WT 1.1.0, cup and handle 1.1.0, double bottom 1.0.0), portfolio and every-trade views as today. One stage at a time, each stage keeping the earlier stages' winners:

1. **Entry**: `breakout` vs `cross_5`, rule `hold_s7`, regime `none` (2 runs per strategy; `breakout` exists already).
2. **Regime**: `none` vs `breadth50` vs `ew50`, with the chosen entry and `hold_s7` (2 new runs).
3. **Exits**: `hold_s7`, `hold_low8`, `trail_e20`, `trail_s50`, with the chosen entry and regime (3 new runs).

About 7–9 runs per strategy. At each stage the owner chooses **one** option for all strategies, from the results pooled across strategies (trades, profit factor, average trade, portfolio CAGR and drawdown), not the best per strategy, to limit fitting to noise. Every variant is counted and reported (§10.4); validation stays unopened until 7e.

## 20.6 Decisions (owner, 2026-10-06: all as recommended)

| # | Question | Options | Decided |
|---|---|---|---|
| E | Entry | (a) real cross only · (b) close ≤ 5 % above the pivot only · (c) both | (c) `cross_5` |
| R | Market regime | (a) breadth ≥ 40 % above the 50-day average · (b) equal-weight universe index above its 50-day average · (c) both at once · (d) Nifty index from a new provider | (a) and (b), tested separately, fixed thresholds |
| X | Exits to add | (a) trail on the 20-day EMA · (b) trail on the 50-day average · (c) half at +20 %, trail the rest | (a) and (b) |
| T | Testing | (a) staged, one choice for all strategies on pooled results · (b) full grid (24 runs per strategy) | (a) |

Out of this round: signal ranking for portfolio slots (7e); fundamentals (needs a data-source decision; a later phase).

## 20.7 As built (step 7b, 2026-10-06)

- `vcp backtest run|walk-forward --entry breakout|cross_5 --regime none|breadth50|ew50 --rule …`; defaults `breakout`, `none`, `hold_s7` reproduce the stored development runs exactly (flat base: 449 trades, PF 2.28, CAGR 25.0 % / −16.5 %). Entry and regime are stored in each run's `settings_json` and printed with the run.
- **The trailing lines are computed from the trade's own adjusted closes in the engine**, not read from `technical_features_daily`: its `ema_*` columns are not computed (stored NULL, DATA_SPECIFICATION). `ema20`: alpha 2/21, seeded with the mean of the first 20 closes; `sma50`: mean of the last 50 closes, today included (same as the stored `sma_50`). The bars of a 120-session rule reach 250 calendar days after the period end (130 for 60 sessions).
- `trail_e20` / `trail_s50` exist only in the event engine (`outcomes.ENGINE_RULES`); the window outcome study keeps its six rules.
- Regime members for a day: the eligible members of the universe of the latest completed Trend Template scan (current scan config) on or before that day; before the first scan, the first scan's (warm-up of the 50-day index only). Days with fewer than 50 members are off. Computed per backtest from `technical_features_daily` (`sma_50`, `daily_return`, current features version) and the adjusted closes; nothing stored.
- Share of days "on" (research DB, 2022–2026): `breadth50` 68 / 79 / 78 / 47 / 66 %, `ew50` 62 / 76 / 78 / 45 / 63 % (2022 … 2026).

## 20.8 Development results (step 7c) and decisions (owner, 2026-10-06, step 7d)

Research DB, development period (2022-02-01 … 2024-06-30), 15 bps a side, survivorship PARTIAL; runs under the daily-run lock with a backup before each stage. 30 new runs (stage 1: 5, stage 2: 10, stage 3: 15); the `breakout` / `hold_s7` runs of steps 4–5 served as stage 1's baseline and stage 2's `cross_5` / `hold_s7` runs as stage 3's. Pooled = every trade of the five strategies together; means are over the five portfolios.

**Stage 1, entry** (hold_s7, no regime): `breakout` → `cross_5`: pooled trades 3,154 → 1,282, win 31.5 → 33.1 %, average 4.23 → 4.87 %, profit factor 1.87 → 2.03, mean portfolio CAGR 12.7 → 18.3 %; max drawdown lower for every strategy (VCP −40.6 → −24.5 %, CAGR 6.8 → 22.5 %). Cup and handle alone got worse (PF 1.96 → 1.67). **Decision: `cross_5`.**

**Stage 2, regime** (cross_5, hold_s7):

| Regime | Pooled trades, PF | Mean CAGR / max DD / Sharpe |
|---|---|---|
| none | 1,282, 2.03 | 18.3 % / −14.7 % / 1.18 |
| breadth50 | 1,144, 2.05 | 17.1 % / −12.4 % / 1.16 |
| ew50 | 1,115, 2.04 | 15.9 % / −12.6 % / 1.11 |

Per-trade quality unchanged; drawdowns about 2 points lower (VCP −24.5 → −17.3 %, 3WT −17.3 → −12.7 % with breadth50) for 1–2 points of CAGR. The development period was mostly a strong market (breadth50 on 68–79 % of days); the weak 2025 (47 %) is in validation. **Decision: `breadth50`**, as protection the development data barely tests.

**Stage 3, exits** (cross_5, breadth50):

| Rule | Pooled trades, win, avg, PF | Mean CAGR / max DD / Sharpe |
|---|---|---|
| hold_s7 | 1,144, 32.3 %, 5.02 %, 2.05 | 17.1 % / −12.4 % / 1.16 |
| hold_low8 | 1,132, 36.2 %, 6.00 %, 2.20 | 18.8 % / −13.8 % / 1.22 |
| trail_e20 | 1,191, 35.8 %, 1.91 %, 1.55 | 7.2 % / −15.8 % / 0.60 |
| trail_s50 | 1,147, 31.7 %, 4.32 %, 2.02 | 13.1 % / −14.9 % / 0.93 |

Trailing exits sell winners too early (trail_e20's trailing exits average +6.5 %); the best 10 % of trades give about 60 % of all gains, and hold_s7's 60-session time exits average +28 %. hold_low8's stop is in practice −8 % (the setup low is usually further), so fewer trades are stopped out. Per strategy, hold_low8 vs hold_s7 portfolio CAGR / max DD: flat base 31.1 / −15.9 vs 24.9 / −14.4 %; 3WT 26.7 / −14.5 vs 22.7 / −12.7 %; cup 11.3 / −10.3 vs 10.3 / −9.3 %; double bottom 10.6 / −8.5 vs 8.6 / −8.3 %; VCP 14.2 / −19.6 vs 19.1 / −17.3 % (the one strategy worse at portfolio level). **Decision: `hold_low8`.**

**As decided:** every strategy's backtests use `--entry cross_5 --regime breadth50 --rule hold_low8` by default (`config/backtest.yaml` `defaults`; the Phase 9 behaviour stays available as `--entry breakout --regime none --rule hold_s7`). The window outcome study keeps `hold_s7` as its default rule. Rejected: `ew50`, `none`; `trail_e20`, `trail_s50`, `hold_s7`.

**Parked idea (not tested, to avoid another development look now):** a time exit longer than 60 sessions, since the time exits carry the gains.

Not read as an edge yet (§10.4): validation is unopened; 7e opens it once per strategy with these frozen rules.

## 20.9 Validation, opened once per strategy (2026-10-06, step 7e)

Owner go 2026-10-06. Frozen rules `cross_5`, `breadth50`, `hold_low8`; validation period 2024-07-01 … 2026-09-30; look number 1 for every strategy (no earlier validation runs existed). Research DB, under the daily-run lock, backup `golden_src_20261006_055406`; code 987dc29.

| Strategy | Development: trades, PF, CAGR / max DD | Validation: trades, win, avg, PF | Validation portfolio: CAGR / max DD |
|---|---|---|---|
| VCP | 574, 2.15, 14.2 / −19.6 % | 317, 25.9 %, −0.62 %, 0.89 | −6.7 / −26.1 % |
| Flat base | 173, 2.73, 31.1 / −15.9 % | 104, 24.0 %, −1.76 %, 0.70 | −5.4 / −15.9 % |
| Three Weeks Tight | 284, 2.05, 26.7 / −14.5 % | 159, 27.7 %, −0.70 %, 0.87 | −5.7 / −22.9 % |
| Cup and handle | 69, 1.79, 11.3 / −10.3 % | 46, 37.0 %, +0.94 %, 1.19 | +2.7 / −11.5 % |
| Double bottom | 32, 2.82, 10.6 / −8.5 % | 26, 34.6 %, −0.82 %, 0.84 | −1.1 / −9.8 % |

**The development edge did not hold.** Every strategy is about flat to negative in validation; none reaches a profit factor of 1.2.

Market context (our equal-weight universe index, `breadth50` share of days on): development 2022-02 … 2024-06 **+93 %** (75 % on); validation 2024 H2 +6 % (71 %), 2025 −5.6 % (47 %), 2026 to September +7.5 % (66 %). The development period was a strong broad advance; validation was roughly flat with a sharp correction from late 2024. Validation trades by entry half-year (all strategies): 2024 H2 232 trades, 19 % winners, −2.1 % average (entries just before the correction, while breadth was still above 40 %); 2025 H1 58, +1.1 %; 2025 H2 146, −1.7 %; 2026 H1 129, 45 % winners, +4.2 %; 2026 H2 87, −4.1 % (some still open at the data end are not counted). The breakout edge measured on development looks largely like exposure to that advance; `breadth50` (a lagging 50-day measure) did not keep the strategies out of the late-2024 correction.

Validation is now spent for these versions (§10.4): changing a rule because of these numbers and re-running validation would fit to it. Any new idea is chosen on development only and judged on live paper (from 2026-10-01).


---

# 21. Monitoring phase: frozen strategies, paper tracking and reporting (signed off 2026-10-06)

After validation (§20.9) showed no edge outside the strong 2022–2024 advance, the owner decided (2026-10-06) to **stop strategy research** and move to reporting and monitoring. Live paper trading from 2026-10-01 (`config/backtest.yaml`, period `live_paper`) is now the only fair test. This section replaces Multi-Strategy steps 6 (High tight flag), 7e (ranking, combined list, go-live) and the open part of 8, and narrows PROJECT_DESIGN Phases 10–12 to what monitoring needs; Phase 13 (ML) and the API are deferred.

## 21.1 What is frozen

Until the review (§21.6), nothing below changes. A change to any of them is a new version, restarts that strategy's paper clock, and is logged.

| Item | Frozen value |
|---|---|
| Strategies | `vcp-1.1.0` (scan config `64da9482a769`), `flat_base-1.0.0` (`c4918be30855`), `three_weeks_tight-1.1.0` (`9ed3b8e7c31a`), `cup_handle-1.1.0` (`e36477f23b36`), `double_bottom-1.0.0` (`dd7107637b1b`) |
| Trade rules | entry `cross_5`, regime `breadth50`, exit `hold_low8`, watch 20 sessions, 10 positions, 15 bps a side (§20.8) |
| Scoring, labels | `scoring-1.0.0`, `labels-1.0.0` |
| Code | a git tag `paper-v1` on the commit that starts the paper ledger; later commits may fix bugs (logged, with a check that no paper result changes) or add reporting, never change a rule |

Data fixes (corporate actions, late bhavcopies) continue as today; they are logged and the review notes any that changed a paper trade.

## 21.2 Daily run (step M2)

After the VCP and score steps of each scan date, the daily run also runs, for each of the four other strategies: `compute setups --strategy ID`, `compute scores --strategy ID`; labels for all strategies as today. Their config files stay `stage: research`; a new value `stage: paper` marks the strategies being paper-tracked (all five), and the daily run runs every strategy whose stage is `paper` or `live`. The new scans cost about 1.5 s per strategy per date. The four strategies' scans for the scan dates since 2026-09-30 are back-filled once, in date order (point-in-time, so the same as if they had run each evening).

## 21.3 Paper ledger (step M3)

Every evening after the scans, for each paper strategy, the frozen rules are applied to that day's bar only (the event engine's daily step): new watch-list entries from today's eligible setups, today's entries (breakout close, `cross_5`, regime on), today's exits (`hold_low8` stop or the 60-session time exit). What is decided is **appended** to a ledger and never edited:

- one row per event (`WATCH`, `ENTRY`, `EXIT`, `WATCH_EXPIRED`, `SKIPPED_NO_SLOT`, `REGIME_OFF`), with the strategy, instrument, date, price, the setup and scan it came from, the rule set's name (`paper-v1`) and the code commit;
- the paper portfolio per strategy: 10 slots, equal size at entry, higher setup score first on a crowded day (as the engine);
- a day the daily run does not finish is caught up the next evening, in date order, before the new day.

At the review, the ledger is compared with `vcp backtest walk-forward --test` over the same months (a replay of the same rules on the stored scans). They should agree; any difference (usually a data fix) is explained.

## 21.4 Reports (step M4)

- **Daily report** (`reports/daily/<date>.html`, written by the daily run; plus one summary line in `data/logs/daily_runs.log`): market regime on/off with the breadth share and the equal-weight universe index against its 50-day average; per strategy the ranked list (grade 2+, or VCP's ranked tiers) with pivot, distance to pivot and stop; stocks on more than one list; today's paper entries and exits; open paper positions with open profit/loss and stop; the daily run's data health (steps, prices up to, surveillance lists, corporate-action warnings). Lists are a watch list, not trade instructions.
- **Weekly summary** (`reports/weekly/<ISO week>.html`, Fridays): per strategy since 2026-10-01: paper trades, win rate, average, profit factor, open positions, portfolio return and drawdown, against the equal-weight universe index; breadth trend; data issues of the week.
- Both are plain files on this PC (`\\wsl.localhost\Ubuntu\home\ubuntu\projects\vcp_ai\reports\…`); no server, API or alerts in this phase.

## 21.5 Monitoring of the system itself

The daily summary line already records failed steps. Added: the daily report flags (a) no new prices for 2 sessions, (b) a strategy with no scan on a scan date, (c) the paper ledger not updated for a session, (d) backups older than 7 days. Nothing pages or emails; the owner reads the report.

## 21.6 Review and success criteria (set now, before any paper result)

- **When:** the first weekly summary on or after **2027-04-01** (6 months), or later if fewer than 30 paper trades in total.
- **A strategy counts as working** only if, on its paper ledger: at least **30 closed trades**; profit factor **≥ 1.3**; average trade > 0 after costs; portfolio max drawdown no worse than **−20 %**; and a portfolio return above the equal-weight universe index over the same months. Fewer than 30 trades: not judged; the paper phase continues for that strategy up to 12 months.
- **Then:** the owner decides per strategy: real money (a separate decision, with position sizing), continue on paper, or retire. No rule is changed before this review.

## 21.7 Steps

| Step | Content |
|---|---|
| M1 | This section (docs only) |
| M2 | Daily run runs the five paper strategies; `stage: paper`; back-fill since 2026-09-30 (main DB, under the lock, backup first) |
| M3 | Paper ledger table (schema change: DATABASE_SCHEMA), the daily paper step, catch-up; tag `paper-v1` |
| M4 | Daily report and weekly summary; system checks of §21.5 |
| then | Monitoring only: the owner reads the reports; fixes are bugs only; review per §21.6 |

## 21.8 Decisions for the monitoring phase (owner, 2026-10-06: all as recommended)

| # | Question | Options | Decided |
|---|---|---|---|
| P1 | Which strategies are paper-tracked | (a) all five · (b) only the three with the most development trades (VCP, flat base, 3WT) | (a): it costs nothing, and the review needs each one's own record |
| P2 | How paper results are kept | (a) an append-only ledger written each evening, compared with a replay at review · (b) no ledger, only replay with `walk-forward --test` at review | (a): it freezes what was known each evening, so a later data fix cannot quietly change the record |
| P3 | Report format | (a) HTML files on this PC, daily and weekly · (b) also a published page (Artifact) updated each evening · (c) text only | (a): simplest; (b) can follow if you want it on your phone |
| P4 | Success criteria | as §21.6 · stricter (PF ≥ 1.5) · looser (PF ≥ 1.2) | as §21.6 |
| P5 | Paper start | (a) 2026-10-01, the four new strategies back-filled from their point-in-time scans · (b) the day M3 is deployed | (a): the scans are point-in-time, so back-filling a week is the same as having run it |

## 21.9 As built: paper ledger (step M3, 2026-10-06)

- `vcp paper update [--strategy ID] [--through DATE]` (the daily run's last step) and `vcp paper status`. Table `paper_events` (DATABASE_SCHEMA §49B), rule set `paper-v1`.
- Each evening the frozen rules (`config/backtest.yaml` defaults, 10 positions, watch 20 sessions, 15 bps) are replayed with the event engine from the paper start (2026-10-01) to the latest VCP-scored session, on the eligible setups scanned from 7 days before the start; open positions are kept in the replay (they hold their slot). Events: `WATCH`, `WATCH_EXPIRED` (no entry in 20 sessions, or replaced by a newer scan of the stock), `ENTRY` (with stop), `SKIPPED_NO_SLOT`, `EXIT` (with kind and return after costs), `DAY_CLOSED` (one per session, with the regime that day; this replaces the separate `REGIME_OFF` event of §21.3). Only events after the last `DAY_CLOSED` are appended; a recomputed difference before it is appended once as `DIVERGENCE`.
- First run on a copy of the main DB (2026-10-06): sessions 2026-10-01 and 10-05 written for all five strategies; `breadth50` off on both days (26–27 % of the universe above its 50-day average, as in the research DB), so no paper entries yet; a second run appended nothing.

