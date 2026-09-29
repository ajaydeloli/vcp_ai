# TREND_TEMPLATE_SPECIFICATION.md

**Project:** Institutional-Grade NSE VCP Scanner
**Version:** 1.0
**Status:** Specification baseline (new; closes design-review gap)
**Owns:** Trend Template conditions, Relative Strength (RS) formula, weekly Stage 1–4 classification
**Versions defined here:** `trend-1.0.0`, `rs-1.0.0`, `stage-1.0.0`

All numeric values are **initial hypotheses** (see PROJECT_DESIGN §69) and live in configuration.

---

# 1. Inputs and Data Readiness

- Canonical **adjusted** daily OHLC (`daily_prices_adjusted`), as-of `as_of_date`, no later data.
- Warm-up: at least `252 + sma200_slope_lookback_days` trading days of history.
- If history is insufficient, or data-quality gates fail, the result is `INSUFFICIENT_DATA` / `DATA_NOT_READY`. It is never `FAIL`, and RS is never set to 0.

---

# 2. Conditions

All ten conditions are always evaluated and stored (no short-circuiting), so results stay explainable.

| # | Name | Rule | Config |
|---|---|---|---|
| 1 | `close_above_sma150` | close > SMA150 | |
| 2 | `close_above_sma200` | close > SMA200 | |
| 3 | `sma150_above_sma200` | SMA150 > SMA200 | |
| 4 | `sma200_rising` | SMA200(t) > SMA200(t − L) | `sma200_slope_lookback_days` L = 21 |
| 5 | `sma50_above_sma150` | SMA50 > SMA150 | |
| 6 | `sma50_above_sma200` | SMA50 > SMA200 (implied by 3 + 5; kept for explainability) | |
| 7 | `close_above_sma50` | close > SMA50 | |
| 8 | `above_52w_low` | close ≥ low252 × (1 + 0.25) | `min_above_52w_low_pct` = 25 |
| 9 | `near_52w_high` | close ≥ high252 × (1 − 0.25) | `max_below_52w_high_pct` = 25 |
| 10 | `rs_rank_min` | rs_rank ≥ threshold | `min_rs_rank` = 70 (stricter research: 80) |

- SMAs use adjusted **close**. `high252` / `low252` use `extreme_basis` (`high_low` default: adjusted high/low over 252 sessions; `close` alternative).
- Minervini describes a 1-month minimum for a rising SMA200 and prefers 4–5 months. L = 21 is the floor. Test 63 and 105 as research variants.
- Each condition is stored as `{condition_id, name, measurement, threshold, passed}` (DATABASE_SCHEMA `trend_template_conditions`).
- `trend_template_pass = all 10 passed`. A fail means no production `VCP` / `A_PLUS_VCP` classification (research retention allowed).

---

# 3. Relative Strength (`rs-1.0.0`)

The RS formula was missing from the merged design. It is reinstated from the older design document (40/20/20/20 weights). **Confirm before implementation.**

```text
R_n      = adj_close(t) / adj_close(t − n) − 1
rs_raw   = 0.40·R_63 + 0.20·R_126 + 0.20·R_189 + 0.20·R_252
```

- **Population:** every instrument eligible in the universe snapshot at `as_of_date` (liquidity/price filters applied) **and** with ≥ 253 adjusted closes. Instruments without enough history get `rs_raw = NULL`, status `INSUFFICIENT_DATA`, and are excluded from the population. They are never scored 0.
- **Rank:** `pct = (count_below + 0.5·count_equal) / N`, then `rs_rank = 1 + floor(98 · pct)` (integers 1–99). Ties share the average position.
- RS is computed **before** the Trend Template gate, over the full population (PROJECT_DESIGN §16).
- Stored in `relative_strength_snapshots` with the four component returns, `rs_raw`, `rs_rank`, `population_size`, `calculation_version`.
- Any change to windows, weights or population is a new `rs_algorithm_version`.

---

# 4. Weekly Stage Classification (`stage-1.0.0`)

Weekly bars are derived from canonical daily bars using the exchange calendar (DATA_SPEC §10). The in-progress week is included as a partial bar built from days ≤ `as_of_date` and flagged `is_partial_week`. This does not look ahead.

```text
sma_w      = SMA(weekly close, 30)
slope_pct  = (sma_w(t) / sma_w(t − 4w) − 1) × 100
prior_pct  = (sma_w(t − 4w) / sma_w(t − 30w) − 1) × 100
```

The prior lookback (30w in `prior_pct`) equals `sma_weeks`; there is no separate config key, so the config hash is unchanged. To research a different prior lookback, add a new key at that time with a version bump.

Decision order (first match wins):

| Stage | Rule |
|---|---|
| 4 (decline) | close < sma_w AND slope_pct < −flat_band |
| 2 (advance) | close > sma_w AND slope_pct > +flat_band |
| 3 (top) | \|slope_pct\| ≤ flat_band AND prior_pct ≥ prior_advance_min |
| 1 (base) | \|slope_pct\| ≤ flat_band AND prior_pct < prior_advance_min |
| TRANSITION | anything else, e.g. close on the wrong side of a moving SMA |

- `weekly_stage2_pass = (stage == 2)`. TRANSITION is not Stage 2.
- Known trade-off: a Stage 2 stock that dips below its 30-week SMA fails Stage 2 until it recovers. Accepted for V1 and measured in research.
- Stored: `weekly_stage`, `sma_w`, `slope_pct`, `prior_pct`, `is_partial_week`.
- A missing as-of bar yields `INSUFFICIENT_DATA` for the weekly stage, while the Trend Template result uses `DATA_NOT_READY`. No new enum value is added for this.

---

# 5. Configuration

```yaml
trend_template:
  required: true
  min_rs_rank: 70
  stricter_rs_rank: 80
  sma200_slope_lookback_days: 21
  min_above_52w_low_pct: 25
  max_below_52w_high_pct: 25
  extreme_basis: high_low          # high_low | close

rs:
  version: rs-1.0.0
  windows_days: [63, 126, 189, 252]
  weights: [0.40, 0.20, 0.20, 0.20]   # must sum to 1.0
  min_history_days: 253

stage:
  sma_weeks: 30
  slope_lookback_weeks: 4
  flat_band_pct: 0.5
  prior_advance_min_pct: 10
```

Config validation: RS weights sum to 1.0; `min_rs_rank` in [1, 99]; `stricter_rs_rank ≥ min_rs_rank`.

---

# 6. Trend Score

The Trend Score (0–100 ranking component, not a gate) is defined in SCORING_SPECIFICATION §3. It is computed only for stocks that passed the gate.

---

# 7. Required Tests

- Each condition: pass, fail and exact-boundary fixtures.
- RS: known-answer ranking on a synthetic universe; ties; NULL-history exclusion; result unchanged when a non-passing stock is removed from the Trend Template stage but not from the population.
- Stage: one fixture per stage plus TRANSITION and a partial week.
- Look-ahead: results at date T are identical when data after T is deleted.
- Determinism: same snapshot + config gives the same `trend_template_conditions` rows.
