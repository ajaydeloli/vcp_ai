# SCORING_SPECIFICATION.md

**Project:** Institutional-Grade NSE VCP Scanner
**Version:** 1.0
**Status:** Specification baseline (new; closes design-review gap)
**Version tag:** `scoring-1.0.0`

Scoring **ranks** setups that the deterministic detector has already qualified. It never decides whether a pattern exists (VCP_SPECIFICATION §34) and is **not a probability** (PROJECT_DESIGN §4.3). All bounds and weights are hypotheses, configurable and versioned.

---

# 1. Final Setup Score

```text
final = Σ(w_i × score_i) / Σ(w_i)      over components with a non-NULL score_i
```

Default weights: trend 25, vcp 35, volume 15, rs 15, fundamentals 10 (sum = 100, fundamentals ≤ `fundamentals_max_weight` = 15).

- Scores exist only for setups that passed the universe, data-quality and Trend Template gates and have classification ≥ `VCP_LIKE`.
- If `fundamental_score` is NULL (§7), the score is computed over the remaining weights, renormalized. It is flagged `FUNDAMENTALS_UNAVAILABLE` and `weights_renormalized = true`. Missing fundamentals are never scored as 0 or as neutral.
- **Ranking percentile** = percentile of `final` among the primary patterns of the same scan, computed separately for `CONFIRMED` and `PROVISIONAL` states.

# 2. Normalization

```text
linear(x, worst, best) = clip( (x − worst) / (best − worst) × 100, 0, 100 )   # works for best < worst
```

Each sub-component stores: raw measurement, normalized 0–100, weight within its component, points, max points (`score_components`, DATABASE_SCHEMA §35).

# 3. Trend Score

| Sub-component | Weight | Measurement | worst → best |
|---|---|---|---|
| `high_proximity` | 40 | % below 52-week high | 25 → 0 |
| `sma200_slope` | 30 | (SMA200(t) / SMA200(t−21) − 1) × 100 | 0 → 8 |
| `ma_stack_margin` | 30 | (SMA50 / SMA200 − 1) × 100 | 0 → 20 |

# 4. VCP Score

Weights per VCP_SPECIFICATION §33: contraction_sequence 25, tightening 20, final_contraction 20, volatility 15, pivot 10, base_structure 10.

**Volume is deliberately excluded** from the VCP Score and scored once, in the Volume Score. This fixes the earlier double counting of volume dry-up.

Sub-component measurements and bounds:

| Sub-component | Measurement | worst → best |
|---|---|---|
| contraction_sequence | number of contractions | 2 → 4 |
| tightening | `max_tightening_ratio` (largest D(n+1)/D(n)) | 1.10 → 0.60 |
| final_contraction | `final_contraction_pct` | 15 → 3 |
| volatility | `atr_contraction_ratio` | 1.0 → 0.5 |
| pivot | `right_side_range_pct` | 8 → 2 |
| base_structure | `base_depth_pct` | 40 → 15 |

# 5. Volume Score

| Sub-component | Weight | Measurement | worst → best |
|---|---|---|---|
| `dryup_quality` | 50 | `final_volume_ratio` | 1.0 → 0.40 |
| `up_down_volume` | 25 | up-volume / down-volume over 50 sessions | 0.8 → 1.5 |
| `distribution` | 25 | count of high-volume down days in last 25 sessions | 4 → 0 |

A high-volume down day: close < previous close and volume > `high_volume_multiple` × 50-day average volume (default 1.5). Missing or zero-suspect volume gives a NULL sub-component (never a dry-up).

# 6. RS Score

`rs_score = linear(rs_rank, worst = trend_template.min_rs_rank, best = 99)`.
Since every scored stock has `rs_rank ≥ min_rs_rank`, this spreads the passing range over 0–100.

# 7. Fundamental Score

Computed only after technical qualification. Cannot change gates or classification.

| Sub-component | Weight | Measurement | worst → best |
|---|---|---|---|
| eps_yoy | 30 | EPS YoY % | 0 → 50 |
| eps_qoq | 20 | EPS QoQ % | 0 → 50 |
| sales_yoy | 20 | sales YoY % | 0 → 30 |
| eps_acceleration | 10 | change in EPS YoY growth (pp) | 0 → 15 |
| margin_expansion | 10 | operating margin change (pp) | 0 → 3 |
| roe | 5 | ROE % | 10 → 25 |
| debt | 5 | debt-to-equity | 1.0 → 0 |

- Missing metrics are dropped and the remaining sub-weights renormalized. If `availability_score < min_availability` (default 0.5), `fundamental_score = NULL`.
- Growth is computed only between periods of the same `statement_basis` (DATABASE_SCHEMA §36). Only observations with `available_at ≤ as_of_date` are used.
- Hard filters (negative trailing EPS, insufficient data) are stored as `fundamental_hard_gate_pass`. They can only remove a setup from actionable lists. They never upgrade one.

# 8. Configuration

```yaml
scoring:
  version: scoring-1.0.0
  weights: {trend: 25, vcp: 35, volume: 15, rs: 15, fundamentals: 10}
  fundamentals_max_weight: 15
  fundamentals_min_availability: 0.5
  high_volume_multiple: 1.5
  # sub-component weights and worst/best bounds per §3–§7 live under scoring.components.*
```

# 9. Validation (config load, blocks scan start)

- Component weights sum to 100; fundamentals ≤ cap; every sub-component weight set sums to its component total.
- `worst ≠ best` for every bound.
- Any weight or bound change creates a new `scoring_version`.

# 10. Tests

Known-answer fixtures per sub-component; boundary clipping; NULL-fundamentals renormalization; a check that VCP Score is unchanged when volume inputs change; determinism (same inputs give the same score); config rejection cases.

---

# 11. Implementation notes (Phase 7, 2026-10-03)

**Owner decisions (2026-10-03).**
- Weights and bounds start exactly as above (`scoring-1.0.0`). Any change is asked first.
- **Scope:** the ranked list follows §1 (classification ≥ `VCP_LIKE`). Research additionally scores *every* Trend Template passer, to test whether the trend, volume and RS components rank outcomes on their own; the VCP class showed no edge (VCP_SPECIFICATION §62).

**Details fixed in step 1** (`scoring/components.py`):
- **NULL sub-components.** A missing or non-finite measurement makes its sub-component NULL. It is stored with `normalized = NULL` and 0 points, and never counts as 0 or as neutral.
- **Component score.** Points over the max points of the *available* sub-components, × 100 (the §7 renormalization, applied to every component). A component with none available is NULL, and §1 renormalizes over the rest. Points = normalized × weight ÷ 100; max points = weight.
- **Trend.** `high_proximity` = (52-week high − close) ÷ 52-week high × 100, with the 52-week high as `high_252` (includes the as-of bar). `sma200_slope` uses SMA200 21 sessions earlier.
- **VCP volatility.** The ratio of the configured `vcp.volatility.measure`: true range by default, the same measure the detector's volatility test uses (VCP §18B). With `measure = atr` it is the spec's `atr_contraction_ratio`.
- **Up/down volume.** Over the last 50 sessions; each session is up or down against the previous close, and unchanged sessions are ignored. A missing or zero volume in the window gives NULL. With no down volume the value is capped at 10.
- **Distribution day.** Close below the previous close on volume above `high_volume_multiple` × the mean of the 50 sessions *before* that day (the day itself excluded). Counted over the last 25 sessions; needs 75 bars with valid volume, otherwise NULL.
- **RS.** If `min_rs_rank` = 99, the worst bound becomes 98 so the bound stays valid.
- **Config.** Sub-component names must be exactly those of §3–§7; a misspelt key is refused at load instead of silently dropping a sub-component (§9).

