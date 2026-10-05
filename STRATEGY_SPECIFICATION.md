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
- §16 High tight flag (`high_tight_flag`) — step 6; the only relaxed Trend Template (§5). If fewer than about 30 occur in 2022–2026 it stays a watch-list flag, not a tested strategy.

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
