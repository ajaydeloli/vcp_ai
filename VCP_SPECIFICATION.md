# VCP_SPECIFICATION.md

**Project:** Institutional-Grade NSE VCP Scanner  
**Version:** 1.1  
**Status:** Technical specification baseline (revised after design review)  
**Scope:** Deterministic VCP detection for V1; future ML confirmation interface included

---

## 1. Purpose

This document defines how the system represents, measures, detects, classifies, scores, monitors, and invalidates a Volatility Contraction Pattern (VCP).

The VCP engine is a **deterministic quantitative pattern engine**, not an LLM chart-reading system. Every production classification must be reproducible from the same data snapshot, configuration, and algorithm version.

Minervini's published material describes the VCP as a consolidation after a confirmed Stage 2 uptrend, characterized by tightening price action and contraction in volatility/supply near the right side of the base.

---

# 2. Core Principle

The detector must never decide that a VCP exists merely because a chart visually resembles one.

```text
OHLCV
  ↓
Swing Structure
  ↓
Base Detection
  ↓
Contraction Measurement
  ↓
Progressive Tightening
  ↓
Volatility + Volume Analysis
  ↓
Pivot Detection
  ↓
Classification
```

Detection, classification, ranking, and future prediction are separate problems.

---

# 3. Relationship to Trend Template

The Trend Template is a mandatory production gate.

```text
Universe
  ↓
Liquidity
  ↓
RS across full eligible universe
  ↓
Trend Template
  ↓
Weekly Stage-2 Context
  ↓
Daily VCP Detection
```

A VCP-like structure outside confirmed Stage 2 may be retained for research, but it must not receive `VCP` or `A_PLUS_VCP` production classification.

This follows the Minervini methodology described in published material: establish the Stage 2 uptrend first, then evaluate the VCP.

---

Stage 2 and the Trend Template are defined in TREND_TEMPLATE_SPECIFICATION.md.

# 4. Timeframe Hierarchy

### Weekly

Used for:
- Stage 2 context
- major trend
- base duration
- major resistance
- prior advance

### Daily

Used for:
- swing points
- contractions
- contraction depth
- range compression
- ATR contraction
- volume dry-up
- pivot
- current setup state

Intraday data is excluded from VCP detection in V1 and reserved for future breakout monitoring.

---

# 5. VCP Classification, Status and Confirmation

Three independent axes replace the earlier single "lifecycle" list:

```text
classification:  NONE < VCP_LIKE < VCP < A_PLUS_VCP        (from measurements)
status:          FORMING -> PIVOT_READY -> BREAKOUT
                 side states: FAILED, INVALIDATED
                 data states: INSUFFICIENT_DATA, DATA_NOT_READY, STALE_DATA
confirmation:    CONFIRMED | PROVISIONAL                   (§9A)
```

Production classifications: `VCP`, `A_PLUS_VCP`. `VCP_LIKE` is a watchlist/research classification. Every daily observation and every transition is persisted, never overwritten.

---

# 6. Required VCP Components

The detector evaluates:

1. prior advance;
2. base structure;
3. contraction sequence;
4. progressive tightening;
5. volatility contraction;
6. volume/supply contraction;
7. pivot quality.

These are measurements, not independent buy signals.

---

# 7. Prior Advance

Measure:

```text
prior_advance_return
prior_advance_duration
prior_advance_slope
```

Example configuration:

```yaml
vcp:
  prior_advance:
    enabled: true
    lookback_days: 120
    min_return_pct: 20
```

The minimum advance is a configurable research hypothesis, not a permanent universal rule.

---

# 8. Base Definition

A base is the broader consolidation containing the contraction sequence.

Required measurements:

```text
base_start
base_end
base_high
base_low
base_depth_pct
base_duration_days
```

```python
base_depth_pct = (base_high - base_low) / base_high * 100
```

A single pullback is not automatically a VCP.

---

## 8.1 Base and contraction segmentation (owner decisions 2026-10-01, Phase 6 spike)

The sections above define what a base and a contraction measure but not how to find them. V1 uses:

1. **Base start:** the highest confirmed swing high within the last `base.max_duration_days` bars, provided a prior advance of at least `prior_advance.min_return_pct` (20 %) occurred within the `prior_advance.lookback_days` (120) bars ending at that high (§7). The base ends at the as-of date (or at a breakout/invalidation, §45, §25).
2. **Contractions:** from a swing high to the lowest low before the next confirmed swing high; the next contraction starts at that swing high. Swings shallower than `swing.min_depth_pct` (2 %) or shorter than `swing.min_duration_days` (3) are merged into the surrounding contraction as noise (§23).
3. **Last contraction:** the one still in progress is provisional (§9A).
4. **Maximum base length:** `base.max_duration_days` = 130 bars (about 26 weeks). With the 120-bar prior advance this stays within the 253-bar data-quality block lifetime (DATA_SPECIFICATION 18A, audit P1-2); a longer base would require a longer lifetime, and configuration loading must enforce that relation.
5. **ATR** is the simple 14-bar average of true range as computed by the feature engine (`atr_14`), not Wilder's smoothing.

The prior-advance (§7) and invalidation (§25) keys join the §60 configuration contract with these defaults.

**Details fixed in the implementation (Phase 6 step 3, 2026-10-02; `patterns/vcp/segmentation.py`):**

- *Prior advance* = base high ÷ lowest low of the `lookback_days` bars ending at the base high − 1. If the highest swing high fails it, there is no base (no fallback to a lower high). With a shorter history a pass counts, a fail is `INSUFFICIENT_HISTORY`. On equal highs the earliest is the base start.
- *Closed contraction*: peak k to the lowest low before peak k+1, confirmed on peak k+1's confirmation date.
- *Noise* (item 2, §23; narrowed by Fix S1): a swing is noise if shallower than `swing.min_depth_pct`, or shorter than `swing.min_duration_days` bars **and** shallower than `swing.short_swing_max_depth_pct` (4 %), because sharp one- or two-bar moves are real swings. It applies to both swings of a contraction: the decline (peak k to its low) and the rally out of the low (to peak k+1), each measured as (high − low) ÷ high and in bars. A noise decline removes peak k (for T1, peak 2); a noise rally removes peak k+1. Repeated until stable.
- *Final contraction* (item 3, §9A): last peak to the lowest low since; confirmed once `swing.right_bars` bars follow that low without a lower low, else provisional; absent while that decline is itself noise.
- `duration_days` = bars from peak to low; base low and base duration run from the base start to the as-of bar.

**Golden dataset (§57):** the owner labels a blind sheet of symbol/date windows with charts drawn from Trend Template passers, without detector output, starting with about 20 examples per class; Phase 6 acceptance runs against those labels.

---

# 9. Swing Detection

Initial implementation:

```yaml
swing_detection:
  method: pivot_n_bar
  left_bars: 5
  right_bars: 5
```

A swing high is confirmed when its high is greater than or equal to the configured neighboring bars on both sides. A swing low is defined analogously.

Every swing stores:

```text
swing_date
confirmation_date
is_confirmed
```

Historical calculations must not use a swing before its confirmation date.

---

# 9A. Confirmation Lag and Provisional Patterns

With `right_bars = N`, a swing is known only N bars after it occurs, so the newest N bars are structurally unconfirmed. That is exactly the right-side tight area that A+ depends on. Rules:

1. A contraction is **confirmed** when both its swing high and swing low are confirmed as of `as_of_date`.
2. At most one **provisional contraction** is allowed: the one in progress, measured from the latest confirmed swing high to the running low since that high (subject to `swing.min_depth_pct` and `min_duration_days`). It is stored with `is_confirmed = false`.
3. A pattern is `CONFIRMED` only if every counted contraction is confirmed. Otherwise it is `PROVISIONAL`.
4. Classification is computed from measured values in both states (a provisional contraction counts as the final contraction) and stored with `confirmation_state`.
5. New-setup alerts and default ranking use `CONFIRMED` patterns. `PROVISIONAL` patterns appear on the watchlist unless `confirmation.include_provisional_in_ranking` is true.
6. Historical runs execute the detector as of each date, so provisional states are reproduced. They are never upgraded retroactively (§56).

```yaml
vcp:
  confirmation:
    allow_provisional_final_contraction: true
    include_provisional_in_ranking: false
```

Research items: compare forward outcomes for `PROVISIONAL` vs `CONFIRMED`; test `right_bars` 3 vs 5; evaluate an ATR-normalised zig-zag swing method (reserved key `swing.method: zigzag_atr`).

---

# 10. Contraction Definition

A contraction is a meaningful decline from a local swing high to a subsequent local swing low.

```python
depth_pct = (peak_price - trough_price) / peak_price * 100
```

Depth is measured on **adjusted** prices: `peak_price` = adjusted high at the swing high, `trough_price` = adjusted low at the swing low.

Each contraction stores:

```text
sequence_number
peak_date
peak_price
trough_date
trough_price
depth_pct
duration_days
atr_pct
range_pct
volume_ratio
confirmation_date
```

---

# 11. Contraction Sequence

Default configuration:

```yaml
vcp:
  contractions:
    min: 2
    max: 6
```

The maximum is a detection/classification control, not a claim that real VCPs can never contain more contractions.

Nested minor oscillations must be filtered as noise rather than automatically becoming additional contractions.

---

# 12. Progressive Tightening

Core relationship:

```text
D1 > D2 > D3 > D4
```

where `D` is contraction depth percentage.

Example:

```text
24% → 15% → 8% → 4%
```

is strongly progressive.

The raw ratios must be preserved, not only a Boolean pass/fail value.

---

# 13. Progressive Tightening Tolerance

Initial configuration:

```yaml
vcp:
  progressive_tolerance_pct: 10
```

A later contraction may be slightly larger than the previous one within configured tolerance, but materially larger contractions must be flagged.

The tolerance is **relative**: `D(n+1) <= D(n) x (1 + progressive_tolerance_pct/100)`. Persist each ratio `D(n+1)/D(n)` and `max_tightening_ratio`.

Store:

```text
tightening_ratio_1
tightening_ratio_2
...
tightening_consistency
```

---

# 14. Final Contraction

The latest completed contraction is the `final_contraction`.

Store:

```text
final_contraction_pct
final_contraction_duration
final_contraction_atr_pct
final_contraction_volume_ratio
```

Initial thresholds:

```yaml
classification:
  a_plus:
    max_final_contraction_pct: 8
  vcp:
    max_final_contraction_pct: 12
  vcp_like:
    max_final_contraction_pct: 15
```

These are research hypotheses and require out-of-sample validation.

---

# 15. Volatility Contraction

VCP detection must measure actual volatility, not only pullback depth.

Required metrics:

```text
ATR(14)
ATR_pct
rolling true range
rolling high-low range
rolling standard deviation
```

Example:

```python
atr_pct = ATR14 / close * 100
```

The detector compares earlier and later contraction volatility.

---

# 16. Range Compression

Measure:

```text
last_20d_range_pct
last_10d_range_pct
last_5d_range_pct
last_20d_ATR_pct
last_10d_ATR_pct
```

The right side should generally show decreasing range and/or ATR.

A setup with shrinking pullback depth but expanding daily volatility should score materially lower than one where both dimensions contract.

---

# 17. Volume Dry-Up

Required measurements:

```text
volume_5d_avg
volume_10d_avg
volume_20d_avg
volume_50d_avg
final_volume_ratio
contraction_volume_ratio
```

Example:

```python
volume_ratio_5_20 = avg_volume_5 / avg_volume_20
```

The detector evaluates volume **through the sequence**, not by requiring every individual bar to be below average.

Example:

```text
T1 = 1.10
T2 = 0.88
T3 = 0.62
```

shows stronger drying than a sequence where volume increases into the final contraction.

---

# 18. Selling-Pressure Measurements

Where reliable data exists, calculate:

```text
up-volume
down-volume
high-volume down days
high-volume up days
accumulation/distribution proxy
```

These are supporting measurements. They must not be described as proof of institutional accumulation.

---

# 18A. Operational Definitions of Boolean Criteria

Every `require_*` flag needs a measurable rule. Initial definitions (hypotheses, configurable):

| Criterion | Definition |
|---|---|
| `progressive_tightening` | for all n: D(n+1) ≤ D(n) × (1 + tolerance) AND D(last) < D(first) |
| `volume_dryup` | `final_volume_ratio` ≤ `volume.dryup_ratio` AND `final_volume_ratio` < first contraction's `volume_ratio`. Here `final_volume_ratio` = mean volume over the final contraction ÷ 50-day average volume at that contraction's start |
| `volatility_contraction` | with `volatility.measure: true_range` (default since Phase 6 step 4, §18B): `tr_contraction_ratio` = mean true range % of the final contraction's bars ÷ that of the first ≤ `volatility.contraction_ratio_max`. With `measure: atr` (the original rule): `atr_contraction_ratio` = mean ATR14% over the final contraction ÷ mean ATR14% over the first ≤ the same maximum. Both ratios are always stored |
| `tight_pivot` | `right_side_range_pct` = (max high − min low) ÷ max high × 100 over the last `right_side_window_days` bars ≤ `pivot.max_right_side_range_pct` |

Missing or suspect volume makes `volume_dryup` NULL/`INSUFFICIENT_DATA`, never a pass. `tight_pivot` measures compression only. Distance to pivot is a separate readiness test (§46).


# 18B. Measurement Windows and the Volatility Rule (Phase 6 step 4, 2026-10-02)

Gaps in §12–18A filled in `patterns/vcp/measurements.py` (owner allowed improvements with documentation, 2026-10-02):

- **Contraction window** = its decline bars, from the bar after the peak through the low (length = `duration_days`); the peak bar belongs to the advance.
- **Volume baseline** for a contraction = mean volume of the `volume.long_period` (50) bars before its peak (prior bars, like `volume_ratio_50`). Any missing volume in either window → NULL.
- **Volatility rule changed to unlagged true range.** ATR14 averages the last 14 bars, so over a short final contraction (39 % of finals on 2026-10-01 are ≤ 6 bars) it mostly measures earlier bars. On 2026-10-01 ATR and the bars disagreed on 230 of 875 bases: 160 passed on ATR although the final contraction's own bars were not calmer (APOLLO: ATR ratio 0.68 while its last 4 bars ranged 1.56× T1's; VGUARD 1.31×), 70 failed on ATR although the bars had calmed. Default `volatility.measure: true_range` decides on the bars' own mean true range %; `atr` restores the §18A rule. Both ratios are stored for research.
- **Right side**: the last `pivot.right_side_window_days` (10) bars; right-side volume ratio against the 50 bars before that window.
- **Range compression (§16)**: `last_N_range_pct` = (max high − min low) ÷ max high over the last N bars (20/10/5); `last_N_atr_pct` = mean ATR14% over the last N (20/10). **Volume (§17)**: `volume_avg_N` = mean of the last N bars including the as-of bar (5/10/20/50), `volume_ratio_5_20` = avg5 ÷ avg20.
- **Selling pressure (§18, supporting only)**: over the base, volume on up-close days ÷ volume on down-close days, and counts of up/down days with volume ≥ 1.5 × the mean of the 50 bars before them.
- **`tightening_consistency` (§13)** = share of consecutive contraction pairs where the depth shrinks.
- Criteria with fewer than two contractions (tightening, dry-up, volatility) are NULL, not FALSE.

---

# 19. Pivot Definition

The pivot is the resistance level associated with the top of the current tight area and used for future breakout monitoring.

Candidate sources:

1. base high;
2. latest meaningful confirmed swing high;
3. tight-right-side consolidation high;
4. relevant repeated resistance.

The detector generates candidates and selects a primary pivot deterministically.


# 19A. Pivot Candidates and Selection Rule (Phase 6 step 5, 2026-10-02)

§19 lists the sources but no selection rule, and §20 names `touches` and `rejection_count` without definitions. Implemented in `patterns/vcp/pivots.py` (owner allowed improvements with documentation, 2026-10-02):

- **Candidates**: `BASE_HIGH` (the base start); `SWING_HIGH` (the final contraction's peak, i.e. the last kept swing high); `RIGHT_SIDE_HIGH` (highest high of the last `pivot.right_side_window_days` bars); `REPEATED_RESISTANCE` (≥ 2 confirmed swing highs in the base, merged noise peaks included, within `pivot.level_tolerance_pct` (1.5 %) of the cluster's highest; level = that highest price, dated at the cluster's latest swing). A candidate at the same date and price as another is kept once, labelled BASE_HIGH > SWING_HIGH > RIGHT_SIDE_HIGH > REPEATED_RESISTANCE.
- **Touches** = visits to the zone `high ≥ P × (1 − level_tolerance)`: runs of consecutive bars from the base start to the as-of bar. **Rejections** = finished visits with no close above P (a visit still open at the as-of bar is not one).
- **`distance_to_close_pct`** = (P − close) ÷ close × 100 (negative when the close is above the pivot). **`right_side_tightness_pct`** = range of the bars after the pivot within the right-side window (NULL if none).
- **Primary** (first that applies): (1) right side tight (`tight_pivot_pass`) and its high ≥ close → the right-side high, the top of the tight area; (2) a contraction exists → the final contraction's peak; (3) the base high. Every candidate is stored (DATABASE_SCHEMA 33), so other rules can be tested later without re-detection.
- A close already above the primary pivot (60 of 1,198 bases on 2026-10-01, e.g. ABDL, whose unconfirmed 753.80 high of 09-25 is above its base high) is a status question (breakout / failed breakout), handled with the statuses in Phase 6 step 6.

---

# 20. Pivot Candidate

```python
@dataclass(frozen=True)
class PivotCandidate:
    price: float
    date: date
    source: str
    distance_to_close_pct: float
    touches: int
    rejection_count: int
    right_side_tightness_pct: float
```

---

# 21. Pivot Distance

```python
pivot_distance_pct = (pivot_price - close) / close * 100
```

This is a setup-readiness measurement, not a prediction.

Example interpretation:

```text
1.2%  close
3.5%  moderate
8.0%  distant
```

Exact thresholds remain configurable.

---

# 22. Pivot Tightness

Measure the price structure immediately below the pivot:

```text
right_side_range_pct
right_side_ATR_pct
right_side_volume_ratio
distance_from_pivot_pct
```

A low final contraction alone is insufficient if the right side remains structurally loose.

---

# 23. Nested Contractions and Noise

Minor fluctuations should not become contractions merely because a local high/low exists.

Initial filters:

```yaml
vcp:
  swing:
    min_depth_pct: 2.0
    min_duration_days: 3
    short_swing_max_depth_pct: 4.0
```

Where practical, movement significance should be normalized by ATR rather than relying only on fixed percentages.

---

# 24. Failed Contractions

A contraction can become invalid when:

- price materially breaks its contraction low;
- the base structure is destroyed;
- Trend Template fails;
- excessive volatility returns;
- a major distribution event occurs.

Failed structures must be retained for research.

---

# 25. VCP Invalidation

Initial invalidation configuration:

```yaml
vcp:
  invalidation:
    trend_template_failure: true
    base_low_break_pct: 2.0
    volatility_expansion_multiple: 2.0
```

Possible invalidation reasons:

```text
TREND_TEMPLATE_FAIL
BASE_STRUCTURE_FAIL
EXCESS_VOLATILITY
PIVOT_STRUCTURE_FAIL
DATA_QUALITY_FAIL
```

---

# 26. Current vs Historical Detection

The detector must accept an explicit `as_of_date`.

```python
detect_vcp(symbol="ABC", as_of_date="2022-06-15")
```

must not inspect any data after that date.

For historical signals, future-confirmed swings cannot be treated as known on their swing date.

---

# 27. Provisional Patterns

The current live contraction may be incomplete.

The system may emit:

```text
PROVISIONAL_VCP
```

but must distinguish it from:

```text
CONFIRMED_VCP
```

This distinction is mandatory for live monitoring and backtesting.

---

# 28. Classification

Production classifications (alerts, primary ranking):

```text
A_PLUS_VCP
VCP
```

Watchlist/research classification:

```text
VCP_LIKE
```

Status values are listed in §5 and are independent of classification.

Classification precedence:

```text
A+ VCP
  ↓
VCP
  ↓
VCP-like
  ↓
No VCP
```

A setup satisfying A+ criteria receives `A_PLUS_VCP`, not simultaneous contradictory classifications.

---

# 29. A+ VCP

Initial requirements:

```yaml
classification:
  a_plus:
    min_contractions: 3
    max_contractions: 6
    max_final_contraction_pct: 8
    require_progressive_tightening: true
    require_volatility_contraction: true
    require_volume_dryup: true
    require_tight_pivot: true
```

Interpretation:

```text
strict contraction sequence
+ strong right-side compression
+ drying volume
+ defined pivot
+ Stage 2
```

---

# 30. Standard VCP

Initial requirements:

```yaml
classification:
  vcp:
    min_contractions: 2
    max_contractions: 6
    max_final_contraction_pct: 12
    require_progressive_tightening: true
```

The standard class permits reasonable variations that do not satisfy A+ criteria.

**Owner decision 2026-10-02:** `require_tight_pivot` was removed from the VCP tier and kept for A+. VCP means the structure qualifies; readiness is the PIVOT_READY distance test (§46), and §18A's `tight_pivot` measures compression only. With it, a final contraction deeper than 5 % inside the last 10 bars could never be VCP although the tier allows 12 % (§61B calibration note).

---

# 31. VCP-Like

Initial requirements:

```yaml
classification:
  vcp_like:
    min_contractions: 2
    max_final_contraction_pct: 15
```

`VCP_LIKE` means the measured structure resembles a VCP but does not yet satisfy the stricter production definition. It is a research/watchlist state.

---

# 32. VCP Quality Vector

Store independent quality dimensions:

```text
contraction_quality
progressive_tightening_quality
volatility_quality
volume_quality
pivot_quality
base_quality
trend_quality
```

Each may be normalized to 0–100 for ranking and research.

---

# 33. VCP Score

Initial VCP-score hypothesis:

```yaml
vcp_score:
  contraction_sequence: 25
  tightening: 20
  final_contraction: 20
  volatility: 15
  pivot: 10
  base_structure: 10
```

These weights are research parameters and must be validated independently of the overall Setup Score.

Volume is **not** part of the VCP Score. `volume_quality` feeds the separate Volume Score (SCORING_SPECIFICATION §5), so it is counted once. Bounds and normalization: SCORING_SPECIFICATION §4.

---

# 34. Detection vs Scoring

Mandatory separation:

```python
pattern = detector.detect(...)
score = scorer.score(pattern)
```

Never use:

```python
if score > 75:
    pattern_exists = True
```

Detection determines whether the structural pattern exists. Scoring ranks already-qualified structures.

---

# 35. Trend Interaction

Trend Template remains a gate.

```text
Trend FAIL
    ↓
No production VCP
```

Trend quality may still affect the ranking of stocks that pass the gate.

---

# 36. Fundamental Independence

Fundamentals are completely outside the VCP detector.

Correct:

```text
VCP Detector
    ↓
VCP Classification
    ↓
Fundamental Support
```

Incorrect:

```text
Strong EPS growth
    ↓
VCP = true
```

---

# 37. Market Regime

Market context should be stored separately:

```text
index trend
market breadth
market regime
volatility regime
```

A weak market should not cause the VCP detector to lower its structural requirements. It should be recorded as context for later research.

---

# 38. Data Requirements

Minimum daily data:

```text
date
open
high
low
close
volume
```

Preferred:

```text
adjusted OHLC
corporate-action flags
delivery data where reliably available
```

Weekly data should be derived from validated daily data unless independently sourced weekly data is explicitly reconciled.

---

# 39. Missing Data

Missing candles must not become zero movement.

If required data is incomplete:

```text
VCP status = INSUFFICIENT_DATA
```

unless configured tolerance explicitly permits continuation.

---

# 40. Price Anomalies

Flag unexplained large overnight movements, initially around ±30%:

```text
large_gap
AND
no_matching_corporate_action
→ UNEXPLAINED_GAP (see DATA_SPECIFICATION §18A)
```

The detector must not automatically treat an anomalous candle as a valid contraction.

---

# 41. Liquidity Boundary

Liquidity is handled by the universe engine, not VCP detection.

```text
Universe eligibility
        ≠
Pattern structure
```

This separation prevents the VCP algorithm from becoming coupled to a particular market-universe policy.

---

# 42. Pattern Domain Object

Recommended model:

```python
@dataclass(frozen=True)
class VCPPattern:
    instrument_id: str
    as_of_date: date

    base_start: date
    base_end: date | None
    base_high: float
    base_low: float

    contractions: tuple[Contraction, ...]

    progressive_tightening: bool
    tightening_quality: float
    volatility_quality: float
    volume_quality: float
    pivot_quality: float

    pivot: PivotCandidate | None
    final_contraction_pct: float | None
    pivot_distance_pct: float | None

    classification: VCPClassification
    status: VCPStatus
    algorithm_version: str
```

---

# 43. Explainability Contract

Every result must expose its measurements.

Example:

```json
{
  "classification": "A_PLUS_VCP",
  "contractions": [
    {"depth_pct": 18.2},
    {"depth_pct": 10.1},
    {"depth_pct": 6.4}
  ],
  "progressive_tightening": true,
  "final_contraction_pct": 6.4,
  "volume_dryup": true,
  "atr_contraction": true,
  "pivot_distance_pct": 1.2
}
```

The frontend should render these facts directly rather than recomputing them.

---

# 44. Chart Annotation Contract

The charting layer receives explicit annotations:

```text
base_start
base_high
base_low
T1 peak/trough/depth
T2 peak/trough/depth
T3 peak/trough/depth
pivot
```

The UI must not independently rediscover the pattern.

---

# 45. Breakout Definition

Breakout monitoring is separate from VCP detection.

Initial concept:

```text
price > pivot
AND
volume / baseline_volume >= configured_multiplier
```

Example:

```yaml
breakout:
  min_volume_ratio: 1.5
```

A breakout must never be required retroactively for a VCP to exist.

---

# 46. Pivot-Ready State

A pattern becomes `PIVOT_READY` when:

```text
valid VCP classification
+ pivot exists
+ pivot distance <= configured threshold
+ acceptable data quality
```

Example:

```yaml
monitoring:
  pivot_ready:
    max_distance_pct: 3
```

---

# 47. Breakout Immutability

A breakout creates a separate event:

```text
VCPPattern
      +
BreakoutEvent
```

The original pre-breakout VCP record must not be rewritten based on the subsequent outcome.

---

# 48. Versioning

Every detector result stores:

```text
algorithm_version
config_hash
data_snapshot_id
```

Any structural algorithm change requires a new algorithm version.

---

# 49. Determinism

Given identical:

```text
data snapshot
configuration
algorithm version
```

the detector must produce identical output.

No random decisions, LLM decisions, or real-time external calls are allowed inside the deterministic detector.

---

# 50. Module Interfaces

The VCP engine must be decomposed rather than implemented as one monolithic function.

Recommended components:

```text
SwingDetector
BaseDetector
ContractionDetector
TighteningAnalyzer
VolatilityAnalyzer
VolumeAnalyzer
PivotDetector
VCPClassifier
VCPDetector
```

Example interfaces:

```python
class BaseDetector(Protocol):
    def find_candidates(...): ...

class ContractionDetector(Protocol):
    def detect(...): ...

class PivotDetector(Protocol):
    def detect(...): ...

class VCPClassifier(Protocol):
    def classify(...): ...
```

`VCPDetector` orchestrates these components.

---

# 51. Future ML Confirmer

Future ML operates after deterministic feature extraction:

```text
OHLCV
 ↓
Deterministic Measurements
 ↓
VCPFeatureVector
 ↓
ML Confirmer
 ↓
Ranking adjustment / confirmation
```

It cannot override:

```text
universe gate
Trend Template gate
data-quality gate
VCP structural gate
```

Potential output:

```text
confirmation_score
model_version
calibration_version
```

---

# 52. LLM Boundary

LLM may explain:

- why the setup qualified;
- which measurements are strongest;
- which conditions are incomplete;
- what changed since yesterday.

LLM may not decide:

```text
VCP = true
```

LLM receives structured facts and produces prose only.

---

# 53. ML Training Dataset

Historical observations can later become training samples.

Features may include:

```text
contraction depths
contraction ratios
duration
ATR contraction
volume contraction
pivot distance
RS
Trend score
base duration
base depth
market regime
```

Labels are generated only from future observations after the sample date.

Examples:

```text
breakout_within_20_days
forward_max_return_20d
```

---

# 54. Label Leakage Protection

Features must never include future:

```text
price
volume
contraction
pivot
breakout
```

A contraction not confirmed at the observation timestamp cannot be used as a confirmed historical feature.

---

# 55. Daily Re-Evaluation

The system recomputes the current pattern every trading day.

Example:

```text
Day 1 → VCP-like
Day 2 → VCP-like
Day 3 → VCP
Day 4 → VCP
Day 5 → A+ VCP
Day 6 → Pivot Ready
Day 7 → Breakout
```

Every daily observation is retained.

---

# 56. No Retroactive Classification

A successful breakout must not cause earlier observations to be rewritten.

If the scanner said `VCP_LIKE` on Day 8, it remains `VCP_LIKE` even if the stock breaks out successfully on Day 10.

This is essential for unbiased backtesting.

---

# 57. Golden Dataset

Create a manually reviewed benchmark:

```text
tests/fixtures/vcp/
├── confirmed_a_plus/
├── confirmed_vcp/
├── vcp_like/
├── non_vcp/
├── failed_vcp/
└── ambiguous/
```

Each fixture contains:

```text
symbol
as_of_date
expected_classification
review_notes
```

Every structural detector change must run the regression suite against this dataset.

Labeling protocol:

- Label from charts **without seeing detector output**; two reviewers where possible.
- Target ≥ 100 examples per class, accumulated over time.
- Hold out 30%, never used for threshold tuning.
- Exclude `ambiguous` examples from precision/recall.

---

# 58. Human Review

The system reduces manual chart review; it does not initially eliminate it.

UI actions:

```text
Confirm VCP
Reject VCP
Mark Ambiguous
```

Human labels must remain separate from algorithm labels:

```text
algorithm = A_PLUS_VCP
human_review = REJECTED
```

---

# 59. Failure Modes

The detector must explicitly handle:

### V-shaped recovery
Not automatically a VCP; requires genuine contraction structure.

### Wide-and-loose base
Normally fails or remains VCP-like.

### Flat base
May be constructive but is not automatically a VCP.

### Downtrend base
Trend Template failure prevents production VCP classification.

### Persistent distribution
Reduces volume/supply quality.

### Increasing volatility
Reduces VCP quality.

### Distant pivot
May remain a valid VCP but not `PIVOT_READY`.

---

# 60. Initial Configuration

```yaml
vcp:
  contractions:
    min: 2
    max: 6

  swing:
    left_bars: 5
    right_bars: 5
    min_depth_pct: 2.0
    min_duration_days: 3
    short_swing_max_depth_pct: 4.0

  progressive_tolerance_pct: 10

  volatility:
    atr_period: 14
    contraction_ratio_max: 0.80
    measure: true_range

  volume:
    short_period: 5
    medium_period: 20
    long_period: 50
    dryup_ratio: 0.70

  pivot:
    max_distance_pct: 3.0
    right_side_window_days: 10
    max_right_side_range_pct: 5.0
    level_tolerance_pct: 1.5

  confirmation:
    allow_provisional_final_contraction: true
    include_provisional_in_ranking: false

  prior_advance:
    enabled: true
    lookback_days: 120
    min_return_pct: 20

  base:
    max_duration_days: 130

  invalidation:
    trend_template_failure: true
    base_low_break_pct: 2.0
    volatility_expansion_multiple: 2.0

  breakout:
    min_volume_ratio: 1.5

classification:
  a_plus:
    min_contractions: 3
    max_contractions: 6
    max_final_contraction_pct: 8
    require_progressive_tightening: true
    require_volume_dryup: true
    require_volatility_contraction: true
    require_tight_pivot: true

  vcp:
    min_contractions: 2
    max_contractions: 6
    max_final_contraction_pct: 12
    require_progressive_tightening: true

  vcp_like:
    min_contractions: 2
    max_final_contraction_pct: 15
```

All values are initial hypotheses and must be empirically validated.

This block is the configuration contract: `config/strategy.yaml` and `config.models` (`VCPThresholdsConfig`, `ClassificationConfig`) adopt it verbatim, and a test parses the YAML above and requires it to validate (audit 2026-09-30 Fix 7). Validation also enforces `contractions.min <= max`, `short_period < medium_period < long_period`, ratios in `(0, 1]`, every tier's contraction counts inside `vcp.contractions`, and that a stricter tier never drops a `require_*` flag or loosens a limit of the tier below it. Omitted `require_*` flags mean "not required".

The `prior_advance`, `base` and `invalidation` keys were added in Phase 6 step 1 (§8.1). Validation also requires `base.max_duration_days >= contractions.min × swing.min_duration_days`, and counts the detector's lookback in the configuration's longest lookback: `base.max_duration_days + max(prior_advance.lookback_days − 1 (when enabled), volume.long_period, volatility.atr_period, swing.left_bars)` bars, as-of bar included (defaults 130 + 119 = 249). `data.quality.block_lifetime_bars` (253) must be at least the longest lookback, so a base of 135 bars, or a 125-bar prior advance, is refused unless the lifetime is raised.

---

# 61. Recommended Detection Pipeline

```python
def detect_vcp(symbol, as_of_date, config):
    data = load_daily_data(symbol, as_of_date)
    validate_data(data)

    weekly = build_weekly_context(data)
    if not weekly.stage2_context:
        return no_production_vcp("STAGE2_FAIL")

    swings = detect_confirmed_swings(data, config.swing)
    bases = find_candidate_bases(data, swings, weekly, config)

    candidates = []
    for base in bases:
        contractions = extract_contractions(base, swings, data, config)
        contractions = filter_noise(contractions, config)

        if len(contractions) < config.contractions.min:
            continue

        tightening = evaluate_progressive_tightening(
            contractions,
            config.progressive_tolerance_pct,
        )
        volatility = evaluate_volatility_contraction(
            data, contractions, config
        )
        volume = evaluate_volume_contraction(
            data, contractions, config
        )
        pivot = detect_pivot(
            base, contractions, data, config
        )

        classification = classify_vcp(
            contractions,
            tightening,
            volatility,
            volume,
            pivot,
            config,
        )

        if classification is not None:
            candidates.append(build_pattern(...))

    if candidates:
        return select_primary_pattern(candidates)   # §61A
    return no_vcp("NO_QUALIFYING_STRUCTURE")
```

The actual implementation should be decomposed into testable modules.

---

# 61A. Primary Pattern Selection

Every qualifying candidate base is persisted (`is_primary = false`). The primary pattern is chosen deterministically by, in order:

1. highest classification (`A_PLUS_VCP` > `VCP` > `VCP_LIKE`)
2. `CONFIRMED` over `PROVISIONAL`
3. most recent `base_end`
4. longer `base_duration_days`
5. earlier `base_start`


# 61B. Classification, Invalidation, Breakout and Status Rules (Phase 6 step 6, 2026-10-02)

Implemented in `patterns/vcp/classifier.py` and `patterns/vcp/detector.py`. Rules marked ★ were proposed to the owner with alternatives on 2026-10-02 11:30 IST and built as recommended.

- **Classification** (§28–31): the highest tier whose rules all hold (A_PLUS_VCP, VCP, VCP_LIKE, else NONE): contraction count in `[min_contractions, max_contractions or vcp.contractions.max]`, final depth ≤ `max_final_contraction_pct`, each required §18A criterion TRUE (NULL never satisfies), and for VCP/A_PLUS_VCP Trend Template PASS and weekly Stage 2. Unmet rules are reported per tier.
- ★ **Trend failure**: `invalidation.trend_template_failure` and Trend Template not PASS → INVALIDATED (`TREND_TEMPLATE_FAIL`); classification is still measured, capped at VCP_LIKE.
- ★ **Base structure**: with ≥ 2 contractions, close more than `base_low_break_pct` below the lowest low from the base start to the final contraction's peak → `BASE_STRUCTURE_FAIL`.
- ★ **Excess volatility**: mean true range % of the last `pivot.right_side_window_days` bars ≥ `volatility_expansion_multiple` × T1's → `EXCESS_VOLATILITY`.
- `PIVOT_STRUCTURE_FAIL` and `DATA_QUALITY_FAIL` are not raised in V1: data problems are data states, not invalidations.
- ★ **Breakout** (§45): the first close above the **structural pivot** (the final contraction's peak, else the base high) after its date, on volume ≥ `breakout.min_volume_ratio` (1.5) × the mean of the 50 bars before it. A close above without that volume is not a breakout. The structural pivot is used because a right-side pivot moves with every new bar (the breakout bar joins the window); breakouts of a right-side pivot are recorded by the daily run against the previous day's stored pivot (§47, Phase 6 step 7). `base_end` = the breakout date.
- ★ **Status precedence**: data state from the caller (DATA_NOT_READY, STALE_DATA, INSUFFICIENT_DATA) → INVALIDATED → BREAKOUT (close ≥ pivot) / FAILED (close back below) → PIVOT_READY (VCP or A_PLUS_VCP, primary pivot 0 – `pivot.max_distance_pct` above the close) → FORMING.
- **Breakout events across days** (§47, Phase 6 step 7, `patterns/vcp/monitor.py`): once a base (instrument + base start) has a breakout event, later days of that base are BREAKOUT while the close is at or above the event's pivot and FAILED below it. New events come from the detector's structural breakout (`STRUCTURAL`) or from a close above the previous scan date's stored primary pivot on ≥ `breakout.min_volume_ratio` × the prior 50-bar volume (`PRIOR_DAY_PIVOT`, which catches right-side-pivot breakouts). Data states and INVALIDATED are never overridden. Dates are therefore computed in order; a rerun of a date regenerates that date's events and ignores events detected later.
- **No bar on the as-of date** → STALE_DATA (signals need the as-of bar). Trend Template data statuses map to VCP data states: INSUFFICIENT_DATA → INSUFFICIENT_DATA; DATA_NOT_READY and DATA_QUALITY_BLOCKED → DATA_NOT_READY.
- **No base**: `INSUFFICIENT_HISTORY` → INSUFFICIENT_DATA; `NO_PRIOR_ADVANCE` / `NO_CONFIRMED_SWING_HIGH` → no pattern, no status.
- **Primary pattern** (§61A): one candidate base per date in V1, so it is primary; `select_primary` implements the §61A order for several.
- **Calibration note (2026-10-02)**: the VCP tier allows a final contraction up to 12 % but `tight_pivot` caps the last 10 bars' range at 5 %, so a final contraction deeper than 5 % inside the last 10 bars cannot be VCP. With gates forced open, 5–16 stocks per date are VCP/A+ while 135–195 miss VCP on the tight pivot alone (2026-06-15 … 10-01). Resolved by the owner on 2026-10-02 (option 2 of 3: keep as specified / A+ only / volatility-relative): the tight pivot is required for A+ only (§30).

---

# 62. Research Methodology

Thresholds must be treated as hypotheses.

Examples:

```text
A+ final contraction: 8 vs 10 vs 12%
Volume dry-up: 0.60 vs 0.70 vs 0.80
Pivot distance: 2 vs 3 vs 5%
```

Use:

```text
development period
validation period
out-of-sample period
walk-forward testing
```

Do not choose thresholds solely because they maximize historical return.

---

# 63. Evaluation Metrics

Detector metrics:

```text
detection count
classification distribution
breakout rate
false-positive rate
false-negative rate
```

Ranking metrics:

```text
top-decile forward return
rank correlation
information coefficient
hit rate
```

Trading metrics are a later layer:

```text
CAGR
max drawdown
Sharpe
Sortino
expectancy
profit factor
```

---

# 64. Data Lineage

For every VCP result, the system must answer:

```text
Which candles produced it?
Which provider supplied them?
Which corporate-action adjustments were applied?
Which universe snapshot was used?
Which Trend Template result was used?
Which configuration was active?
Which algorithm version produced it?
```

This is required for institutional-style reproducibility.

---

# 65. Definition of Done

The VCP engine is complete only when:

- swing detection is deterministic;
- swing confirmation timestamps exist;
- base detection is separated from contraction detection;
- contractions have measurable depth and duration;
- progressive tightening is measurable;
- volatility contraction is measurable;
- volume dry-up is measurable;
- pivot detection is deterministic;
- A+ / VCP / VCP-like classifications exist;
- thresholds are configurable;
- historical `as_of_date` operation works;
- no future data enters historical results;
- golden fixtures exist;
- regression tests exist;
- failure/invalidation states exist;
- measurements are persisted;
- algorithm/config/data versions are persisted;
- chart annotations can be generated directly from results;
- ML can later consume the feature vector without changing detection;
- LLM cannot override the detector.

---

# 66. Final Specification Principle

The VCP engine answers four separate questions:

### Q1 — Is the stock in the correct trend?

```text
Trend Template / Stage 2
```

### Q2 — Is there a meaningful contraction sequence?

```text
T1 → T2 → T3 → ...
```

### Q3 — Is the right side becoming tighter?

```text
price + volatility + volume
```

### Q4 — Is there a defined pivot?

```text
resistance + tight right side
```

The architecture is therefore:

```text
Stage 2
   +
Meaningful base
   +
Successive contractions
   +
Progressive tightening
   +
Volatility contraction
   +
Supply/volume contraction
   +
Defined pivot
   ↓
VCP candidate
   ↓
A+ / VCP / VCP-like
   ↓
VCP Quality Score
   ↓
Final Setup Ranking
```

The key rule is that **the detector establishes the pattern; the scorer ranks the pattern; future ML may estimate outcomes; the LLM explains the facts.**
