# VCP Institutional-Grade Scanner — Project Design Specification

**Document:** `PROJECT_DESIGN.md`  
**Version:** 1.1 (revised after design review; see REVIEW_FIXES.md)  
**Status:** Architecture baseline / implementation-ready specification  
**Primary market:** NSE India equities  
**Primary methodology:** Mark Minervini / SEPA-inspired Trend Template + Volatility Contraction Pattern (VCP)  
**Primary use:** Personal research and semi-automated decision-support  
**Deployment target:** Standalone modular monolith, initially local/Ubuntu server, container-ready  
**Primary historical market-data provider:** Zerodha Kite Connect  
**Future provider:** Dhan API  
**Storage:** DuckDB + Parquet  
**Development model:** AI-agent-assisted, test-first, phase-gated

---

## 1. Executive Summary

This project is a production-oriented quantitative stock scanner designed to identify, measure, rank, monitor, and alert on **Minervini-style VCP setups within confirmed Stage 2 uptrends**.

The central design decision is:

> **Technical structure is the source of truth. Fundamentals are secondary supportive evidence.**

A stock cannot become a valid VCP merely because its fundamentals are strong. The deterministic technical engine must first establish the required Trend Template / Stage 2 context and VCP structure. Fundamental information may then improve or reduce the ranking of an already technically qualified setup.

The system is deliberately designed as a **modular monolith**, not as a premature microservice system. Every major domain has an explicit interface and dependency boundary so that individual components can later be replaced or extracted without rewriting the scanner.

The architecture is provider-neutral from day one:

```text
MarketDataProvider
    ├── LocalStoreProvider
    ├── KiteProvider
    └── DhanProvider (future)
```

The local historical dataset is the operational source of truth. External providers are ingestion/fallback sources.

The initial product stops at:

```text
Scan → Rank → Explain → Monitor → Breakout Alert
```

Trade execution, position sizing, and exit management are deliberately separate future modules.

---

# 2. Goals

## 2.1 Primary goals

1. Detect technically valid VCP structures.
2. Require Minervini-style Stage 2 / Trend Template confirmation.
3. Detect VCPs hierarchically:
   - weekly context
   - daily contraction structure
4. Classify setups into:
   - A+ VCP
   - VCP
   - VCP-like / Watchlist
5. Produce transparent numerical measurements for every decision.
6. Produce separate component scores:
   - Trend
   - VCP
   - Volume
   - RS
   - Fundamentals
7. Produce a configurable **Final Setup Score** used for ranking, not probability.
8. Support provider switching without changing scanner logic.
9. Maintain point-in-time datasets for unbiased research/backtesting.
10. Support 10+ years of historical data where available.
11. Prevent look-ahead and survivorship bias.
12. Provide reproducible scan runs through configuration hashing.
13. Support future ML confirmation without coupling ML to the deterministic detector.
14. Support future intraday breakout monitoring.
15. Provide detailed explainable reports suitable for human review.

---

# 3. Non-Goals for V1

The following are explicitly outside the V1 core:

- Automatic order placement
- Position sizing
- Stop-loss execution
- Portfolio optimization
- Fully autonomous trading
- LLM-based VCP detection
- Computer-vision-only chart interpretation
- High-frequency trading
- Intraday VCP detection
- Fundamental-analysis-heavy ranking
- Broker-specific business logic inside strategy code

These may be added later as independent modules.

---

# 4. Core Design Principles

## 4.1 Technical-first

The system must never use fundamentals to turn a technically invalid setup into a valid setup.

Correct:

```text
Technical Gates
      ↓
VCP Detection
      ↓
Technical Qualification
      ↓
Fundamental Support
      ↓
Ranking
```

Incorrect:

```text
Strong fundamentals
      ↓
Compensate for failed VCP
```

---

## 4.2 Deterministic engine is authoritative

V1 VCP detection is deterministic and measurable.

Future ML may:

- confirm a detected pattern,
- modify ranking,
- estimate historical outcome likelihood after calibration,

but it must not override mandatory technical gates.

Future interface:

```python
class PatternConfirmer(Protocol):
    def confirm(
        self,
        context: PatternContext,
    ) -> ConfirmationResult:
        ...
```

---

## 4.3 Ranking score is not probability

The Final Setup Score is a **ranking score**.

It must never be described as:

> "There is a 78% probability this stock will break out."

until a separate probability-calibration system has been trained and validated against strictly forward outcomes.

The system should use terminology such as:

- Setup Score
- Technical Score
- VCP Score
- Fundamental Support Score
- Ranking Percentile

Probability is a separate future model.

---

## 4.4 Point-in-time correctness

Every research decision must be reproducible using only information available at the decision timestamp.

This applies to:

- OHLCV
- corporate actions
- universe membership
- liquidity
- RS ranking
- fundamentals
- classification
- scoring
- labels
- ML training datasets

---

## 4.5 Raw measurements before scores

Every scored feature must preserve its raw measurement.

Example:

```text
final_contraction_pct = 7.8
volume_dryup_ratio = 0.61
pivot_distance_pct = 1.7
rs_rank = 86
eps_yoy_growth = 31.4
```

Then:

```text
measurement → normalization → score → classification
```

Never store only the final score.

---

## 4.6 Configuration over hard-coding

Strategy parameters must live in configuration.

Examples:

- Trend Template thresholds
- RS thresholds
- VCP contraction thresholds
- volume thresholds
- liquidity thresholds
- scoring weights
- classification thresholds
- data freshness limits
- fundamental thresholds

Safety constraints that protect the strategy architecture may be hard-coded.

Example:

```yaml
fundamentals_max_weight: 15
```

The implementation must reject a configuration attempting to assign fundamentals more than the permitted maximum.

---

# 5. High-Level Architecture

```text
                         ┌─────────────────────┐
                         │   External Sources   │
                         │                     │
                         │ Kite / Dhan / etc. │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   Ingestion Layer   │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Raw Data Lake       │
                         │ Parquet             │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ Normalization Layer │
                         │ OHLCV / CA / Symbol │
                         └──────────┬──────────┘
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   DuckDB Repository │
                         └──────────┬──────────┘
                                    │
                 ┌──────────────────┼──────────────────┐
                 │                  │                  │
                 ▼                  ▼                  ▼
          Universe Engine     Indicator Engine    Fundamental Engine
                 │                  │                  │
                 └──────────┬───────┴──────────┬──────┘
                            │                  │
                            ▼                  │
                    Trend Template            │
                            │                  │
                            ▼                  │
                    Weekly Context             │
                            │                  │
                            ▼                  │
                     Daily VCP Engine          │
                            │                  │
                            └────────┬─────────┘
                                     ▼
                              Scoring Engine
                                     │
                                     ▼
                            Classification Engine
                                     │
                                     ▼
                              Report Engine
                                     │
                        ┌────────────┴────────────┐
                        ▼                         ▼
                    Dashboard               Alert Engine
                                                  │
                                                  ▼
                                        Breakout Monitoring

Future:
                              ┌──────────────────┐
                              │ ML Confirmer     │
                              │ LLM Explanation  │
                              │ Backtest Engine  │
                              └──────────────────┘
```

---

# 6. Module Boundaries

The project shall use strict modules:

```text
src/vcp_scanner/
├── config/
├── domain/
├── data/
│   ├── providers/        adapters behind Protocols in providers/base.py
│   ├── repositories/     Protocols in repositories/base.py + DuckDB implementations
│   ├── ingestion/        provider -> raw/canonical workers
│   ├── adjustment/       corporate-action factors -> adjusted prices
│   ├── reconciliation/   NSE/Upstox reconciliation, gap safety net
│   ├── quality/          completeness, data-quality events, verification
│   ├── features/         SQL feature builders (SMA/ATR/volume/volatility, weekly bars)
│   ├── storage/          DuckDB catalogue, Parquet archiver
│   └── universe/         universe rules (strategy code, DB-free)
├── features/             strategy engines: RS ranking, Trend Template, weekly Stage
├── patterns/             VCP detector (Phase 6)
├── scoring/
├── fundamentals/
├── research/
├── backtest/
├── reporting/
├── monitoring/
├── alerts/
├── ml/
├── api/
└── infrastructure/
```

Layout amended by audit 2026-09-30 Fix 6 to match the implementation. The earlier `universe/`, `indicators/`, `trend/` and `data/normalization/` packages were empty and have been removed so no agent writes a second implementation there: universe rules live in `data/universe/`, indicator builders in `data/features/`, and RS / Trend Template / weekly Stage in `features/`. Indicator *computation* is storage-side SQL (data layer); every *strategy decision* (eligibility, RS ranking, the ten conditions, Stage, later VCP and scoring) is pure Python over domain objects and repository Protocols. `tests/unit/test_architecture_rules.py` enforces this for `domain/`, `features/`, `data/universe/`, and `patterns/`, `scoring/`, `fundamentals/` as they gain code.

Recommended dependency direction:

```text
domain
  ↑
config

data ───────────────┐
universe             │
indicators           │
trend                │
patterns             ├──> scoring ──> reporting
fundamentals         │
research/backtest ───┘

monitoring ──> alerts

api ──> application services

infrastructure ──> implementations
```

Core strategy modules must not import:

- broker SDKs
- Streamlit
- FastAPI
- LLM SDKs
- database-specific code

They operate on domain objects and repository/provider interfaces.

---

# 7. Domain Model

Core entities:

```text
Instrument
TradingSymbol
UniverseSnapshot
Candle
CorporateAction
DataQualityReport
TrendTemplateResult
WeeklyContext
Contraction
VCPPattern
Pivot
VolumeProfile
FundamentalSnapshot
TechnicalScore
FundamentalScore
SetupScore
Classification
ScanRun
MonitoringState
BreakoutEvent
AlertEvent
```

---

# 8. Market Data Provider Architecture

## 8.1 Provider interface

```python
class MarketDataProvider(Protocol):

    def get_instruments(self) -> list[Instrument]:
        ...

    def get_historical_daily(
        self,
        instrument: Instrument,
        start: date,
        end: date,
    ) -> list[Candle]:
        ...

    def get_historical_intraday(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        ...

    def health_check(self) -> ProviderHealth:
        ...

    def get_capabilities(self) -> ProviderCapabilities:
        ...

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        ...  # optional; used for interim breakout polling (Phase 12)
```

The scanner must never call Kite or Dhan SDK methods directly.

---

# 9. Provider Priority

V1:

```text
1. Local DuckDB/Parquet
2. Kite Connect
3. Dhan
```

The local dataset is the source of truth.

External provider calls are responsible for:

- initial backfill
- missing-data repair
- incremental updates
- recovery
- validation

Provider fallback must not silently mix incompatible datasets.

Every fetched record should retain provenance:

```text
provider
provider_request_id
provider_timestamp
ingested_at
data_version
```

---

# 10. Kite Connect Constraints

Kite's current documentation confirms that historical candles are exposed through the historical-candle endpoint and that the API supports daily and intraday intervals. The documentation also recommends storing the instrument dump locally rather than repeatedly retrieving it.

Current Kite documentation/forum information indicates:

- historical data is included in the base Kite Connect subscription;
- historical candle requests are rate limited to approximately 3 requests/second;
- historical data is fetched per instrument rather than as a bulk multi-instrument historical endpoint;
- current documented/requested maximum ranges vary by interval;
- daily data can be retrieved in multiple chunks;
- intraday historical availability is materially more limited than daily history.

**Kite limitations that shape this design (verify against current Kite documentation before relying on them):**

- The instrument dump lists *currently tradable* instruments. Delisted securities and historical series/segment states cannot be reconstructed from it.
- Kite is not a corporate-action feed. Store returned candles exactly as received. Zerodha states they are adjusted for splits, bonuses and other capital actions as of fetch time, so the adjustment engine applies only actions after each bar's fetch date (DATA_SPECIFICATION §21.1; audit 2026-09-30 P0-1). Verify with `vcp verify kite-adjustment`.
- Instrument tokens can change or be reused, so identity must go through `provider_instruments` with `valid_from`/`valid_to`.
- Access tokens expire daily and need a login/refresh workflow (DATA_SPECIFICATION §28A).

Kite is therefore the OHLCV provider only. Historical security master, delistings, corporate actions and surveillance history need separate sources (§14A).

The implementation must therefore treat these as **provider constraints**, not strategy assumptions.

A provider capability object should exist:

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    daily_history_max_request_days: int
    intraday_history_max_request_days: dict[str, int]
    historical_requests_per_second: float
    supports_bulk_historical: bool
    supports_websocket: bool
    supports_quotes: bool
    daily_history: bool
    intraday_history: bool
    corporate_actions: bool          # Kite: expected False
    adjusted_prices: bool
    instrument_master: bool
    delisted_history: bool           # Kite: expected False
```

Do not hard-code a historical limit into the strategy.

The ingestion scheduler must read provider capabilities and chunk requests accordingly.

Kite's official documentation states that its instrument dump is generated daily and recommends retrieving it once per day and storing it locally.

Kite's official historical-data documentation is the authoritative reference for endpoint semantics.

---

# 11. Local Data Architecture

Storage:

```text
data/
├── raw/
│   ├── kite/
│   └── dhan/
├── normalized/
│   ├── daily/
│   └── intraday/
├── corporate_actions/
├── fundamentals/
├── universe/
├── features/
├── labels/
└── reports/
```

Parquet is the durable analytical storage format.

DuckDB provides:

- SQL querying
- joins
- analytical scans
- feature generation
- backtest queries
- repository abstraction

---

# 12. Raw vs Normalized Data

Raw provider data must never be overwritten.

```text
RAW
 ↓
VALIDATION
 ↓
NORMALIZATION
 ↓
CORPORATE-ACTION PROCESSING
 ↓
CANONICAL DATASET
```

Canonical OHLCV should contain:

```text
instrument_id
symbol
exchange
timestamp
open_raw
high_raw
low_raw
close_raw
volume_raw

open_adjusted
high_adjusted
low_adjusted
close_adjusted

provider
source_timestamp
ingested_at
data_quality_status
```

---

# 13. Corporate Actions

Corporate-action-aware storage is mandatory.

Table:

```text
corporate_actions
-----------------
action_id
instrument_id
action_type
ex_date
record_date
ratio_numerator
ratio_denominator
cash_amount
source
created_at
```

Supported initially:

- split
- bonus
- dividend
- symbol change
- merger/demerger where data can be reliably represented

Adjusted prices must be derived rather than replacing raw prices.

An anomaly detector should flag large unexplained overnight movements.

Initial heuristic:

```text
abs(overnight_gap_pct) >= 30%
AND
no matching corporate action
→ UNEXPLAINED_GAP (see DATA_SPECIFICATION §18A)
```

This is a data-quality flag, not a trading signal.

Fallback convention:

```python
effective_close = coalesce(
    nullif(adjusted_close, 0),
    close
)
```

---

# 14. Universe Engine

The universe is:

> All NSE equities, subject to configurable exclusions and liquidity/price requirements.

Exclude by default:

- ETFs
- SME securities
- T2T / BE series where applicable
- non-equity instruments
- instruments failing minimum price
- instruments failing minimum liquidity

Flag but do not necessarily exclude:

- ASM
- GSM
- other regulatory surveillance classifications

Configuration:

```yaml
universe:
  exchange: NSE

  exclusions:
    etf: true
    sme: true
    t2t: true

  liquidity:
    basis: raw                     # raw close x raw volume, never adjusted
    min_avg_traded_value_20d: 10000000
    min_avg_traded_value_50d: 10000000

  price:
    min_price: 20

  surveillance:
    flag_asm: true
    flag_gsm: true
```

The actual thresholds must be validated through research and remain configurable.

---

# 14A. Historical Security Master, Delistings and Corporate-Action Sources

Survivorship-bias control (§15, §40) and correct adjusted prices (§13) both need data that a current-listing broker feed cannot supply. Each need is behind its own interface, separate from `MarketDataProvider`:

```python
class SecurityMasterProvider(Protocol):
    def get_security_history(self, start: date, end: date) -> list[SecurityRecord]: ...   # ISIN, listing/delisting dates, series
class CorporateActionProvider(Protocol):
    def get_actions(self, start: date, end: date) -> list[CorporateAction]: ...
class SurveillanceProvider(Protocol):
    def get_flags(self, start: date, end: date) -> list[SurveillanceRecord]: ...          # ASM/GSM/T2T/BE history
```

Source layering (details and verification checklist: DATA_SPECIFICATION §4A, §18A):

- **Corporate actions:** NSE feed (primary, daily pre-market job) + Upstox by ISIN (secondary). Disagreement raises `PROVIDER_CONFLICT` and blocks signals for that symbol. A raw-price gap detector (`UNEXPLAINED_GAP`) is the safety net for actions both feeds miss.
- **Security master, delistings, symbol history:** NSE files (candidate; coverage to be verified). BSE later as reconciliation only.
- **Historical raw OHLCV incl. delisted names:** NSE bhavcopy archive (candidate; two formats around 8 Jul 2024; verify coverage and terms of use). Kite for incremental updates and cross-checks.
- Stored candles stay immutable and adjusted prices are derived from stored factors. For Kite, factors are applied only for actions after each bar's fetch date, because Kite had already applied the earlier ones (DATA_SPECIFICATION §21.1).

**Survivorship status** is stamped on every scan and backtest:

| Status | Meaning |
|---|---|
| `POINT_IN_TIME_COMPLETE` | delisted names and historical universe membership included |
| `PARTIAL` | some delisted/merged securities known to be missing |
| `BIASED` | current listings only |

`PARTIAL` and `BIASED` results **must not** be used for threshold validation or performance claims, and reports must display the label. Until the sources above are secured, corporate-action gaps are the gating dependency for Phase 2. The 30% gap anomaly detector is a safety net, not a substitute.

# 15. Point-in-Time Universe

The system must maintain:

```text
universe_snapshot
-----------------
snapshot_date
instrument_id
symbol
eligible
exclusion_reason
avg_traded_value
price
asm_flag
gsm_flag
```

Backtests must use the universe that existed at the historical date.

Never use today's NSE universe to backtest historical years.

This is required to prevent survivorship bias.

---

# 16. Relative Strength Architecture

RS is calculated across the **full eligible liquid universe**, not only Trend Template passers.

Pipeline:

```text
All eligible liquid stocks
        ↓
RS calculation
        ↓
RS ranking
        ↓
Trend Template gate
        ↓
VCP detection
```

Default:

```yaml
trend_template:
  min_rs_rank: 70
```

Research configurations may use:

```yaml
min_rs_rank: 80
```

RS methodology must be versioned because changing the formula changes the strategy.
Authoritative formula: TREND_TEMPLATE_SPECIFICATION §3 (`rs-1.0.0`).

The system should support:

```text
RS raw return
RS normalized score
RS percentile/rank
RS trend
```

---

# 17. Trend Template Engine

Trend Template is a **mandatory gate**.

**Authoritative definition (conditions, RS formula, weekly Stage 1–4): TREND_TEMPLATE_SPECIFICATION.md.** Summary:

Default conditions:

1. Price > SMA150
2. Price > SMA200
3. SMA150 > SMA200
4. SMA200 rising over `sma200_slope_lookback_days` (default 21 trading days)
5. SMA50 > SMA150
6. SMA50 > SMA200
7. Price > SMA50
8. Price ≥ 52-week low + 25%
9. Price within 25% of 52-week high
10. RS rank ≥ configured threshold

The exact implementation must preserve the distinction between:

- condition
- measurement
- threshold
- pass/fail

Example:

```json
{
  "condition": "close_above_sma_200",
  "measurement": 1243.5,
  "threshold": 1181.2,
  "passed": true
}
```

A failed mandatory condition means:

```text
TrendTemplate = FAIL
VCP detection = not eligible for valid classification
```

The system may still retain the stock for research/watchlist analysis.

---

# 18. Weekly Context Engine

Weekly analysis establishes:

- Stage 2 context
- major base duration
- long-term trend
- weekly contraction structure
- major resistance
- historical highs/lows
- structural quality

Stage 1–4 classification and the `weekly_stage2_pass` definition are in TREND_TEMPLATE_SPECIFICATION §4.

Weekly data should be derived consistently from the canonical daily dataset or stored as a separately validated aggregate.

Avoid using independently sourced weekly candles unless their methodology is reconciled.

---

# 19. Daily VCP Engine

The daily engine measures:

- contraction legs
- swing highs
- swing lows
- contraction depth
- duration
- progressive tightening
- price range compression
- ATR contraction
- volume dry-up
- pivot formation
- distance to pivot
- base length
- support behavior

---

# 20. VCP Conceptual Model

The VCP detector should not search for a single visual shape.

It should detect a sequence:

```text
Impulse / prior advance
        ↓
Base formation
        ↓
Contraction 1
        ↓
Contraction 2
        ↓
Contraction 3
        ↓
Optional later contractions
        ↓
Volatility / volume compression
        ↓
Tight pivot
        ↓
Potential breakout
```

---

# 21. Contraction Object

```python
@dataclass(frozen=True)
class Contraction:
    index: int
    start_date: date
    end_date: date
    peak_price: float
    trough_price: float
    depth_pct: float
    duration_days: int
    atr_pct: float
    volume_ratio: float
```

The detector must preserve the measured values.

---

# 22. Progressive Contraction Requirement

For a classical VCP:

```text
D1 > D2 > D3 > D4
```

where:

```text
D = contraction depth %
```

But the detector must allow tolerance.

Example:

```yaml
vcp:
  progressive_tolerance_pct: 10
```

This means a later contraction may be slightly larger than the preceding one without immediately invalidating the entire structure.

The tolerance is **relative**: `D(n+1) <= D(n) x (1 + tolerance/100)`. With 10%, a 10.0% contraction may be followed by one of up to 11.0%. Depth is measured on adjusted highs and lows. Operational definitions: VCP_SPECIFICATION §18A.

All such tolerance decisions must be measurable and backtestable.

---

# 23. VCP Classification

The same measured pattern is classified under different configuration thresholds.

## A+ VCP

Default hypothesis:

```yaml
a_plus:
  min_contractions: 3
  max_contractions: 6
  max_final_contraction_pct: 8
  require_progressive_tightening: true
  require_volume_dryup: true
  require_tight_pivot: true
```

## VCP

```yaml
vcp:
  min_contractions: 2
  max_final_contraction_pct: 12
  require_progressive_tightening: true
```

## VCP-like / Watchlist

```yaml
vcp_like:
  min_contractions: 2
  max_final_contraction_pct: 15
```

These values are **initial hypotheses**, not validated truths.

Backtesting must determine whether stricter classifications produce superior forward outcomes.

**Confirmation state.** The detector uses confirmed swings, so the newest bars are structurally unconfirmed (VCP_SPECIFICATION §9A). Classification is computed from measured values and stored alongside a separate `confirmation_state` (`CONFIRMED` | `PROVISIONAL`). New-setup alerts and default ranking use `CONFIRMED` only.

---

# 24. Pivot Engine

Pivot detection should identify the most actionable resistance level associated with the current base.

Pivot features:

```text
pivot_price
pivot_date
pivot_distance_pct
base_high
recent_high
tightness
breakout_volume_threshold
```

The engine must distinguish:

- structural pivot
- provisional pivot
- confirmed pivot
- breakout trigger

---

# 25. Volume Engine

Volume analysis should measure:

- average volume
- volume ratio
- up-volume
- down-volume
- dry-up ratio
- volume contraction
- breakout volume expansion
- accumulation/distribution proxy

Example:

```text
volume_ratio = current_volume / average_volume
```

VCP quality should reward:

```text
contraction → lower volume
breakout → expanding volume
```

---

# 26. Volatility Engine

Volatility features:

```text
ATR14
ATR_pct
ATR contraction
range_pct
rolling_std
true_range_pct
```

The VCP engine should measure both:

```text
price contraction
+
volatility contraction
```

rather than relying only on peak-to-trough depth.

---

# 27. Scoring Architecture

Scores:

```text
Trend Score
VCP Score
Volume Score
RS Score
Fundamental Score
        ↓
Final Setup Score
```

Default weights:

```yaml
scoring:
  weights:
    trend: 25
    vcp: 35
    volume: 15
    rs: 15
    fundamentals: 10

  fundamentals_max_weight: 15
```

Weights must sum to 100. Authoritative scoring definitions: SCORING_SPECIFICATION.md.

Validation:

```python
assert sum(weights.values()) == 100
assert weights["fundamentals"] <= fundamentals_max_weight
```

---

# 28. Scoring Rules

Each sub-score should be independently explainable.

Example:

```text
VCP Score = 83

Contraction sequence           22/25
Tightening                     16/20
Final contraction              17/20
Volatility compression         12/15
Pivot tightness                 8/10
Base structure                  8/10
------------------------------------
                               83/100
```

Volume dry-up is scored **once**, in the Volume Score, not inside the VCP Score. Full definitions: SCORING_SPECIFICATION.md.

Do not implement unexplained black-box scoring.

---

# 29. Fundamental Support Engine

Fundamentals are evaluated only after technical qualification.

Primary metrics:

- YoY EPS growth
- QoQ EPS growth
- sales growth
- EPS acceleration
- margin expansion
- ROE
- simple debt check
- earnings-data availability

Benchmark hypothesis:

```text
EPS growth >= 25%
```

This is a research benchmark, not a permanent hard-coded law.

---

# 30. Fundamental Hard Filters

Default hard filters:

1. Negative trailing EPS
2. Insufficient earnings-data availability

Other fundamental metrics are score modifiers.

Liquidity is not a fundamental filter. It belongs to the universe stage.

Configuration:

```yaml
fundamentals:
  hard_filters:
    negative_trailing_eps: true
    minimum_data_availability: true

  benchmarks:
    eps_yoy_growth_pct: 25
    eps_qoq_growth_pct: 25
```

---

# 31. Fundamental Data Architecture

Fundamental data must be modeled independently from the market-data provider.

```python
class FundamentalProvider(Protocol):
    def get_snapshot(
        self,
        instrument: Instrument,
        as_of: date,
    ) -> FundamentalSnapshot:
        ...
```

Every fundamental observation must have:

```text
period_end
publication_date
available_at
source
retrieved_at
```

**publication_date / available_at is critical.**

Backtests must never use a quarterly result before it was publicly available.

---

# 32. Fundamental Data Quality

Fundamental fields should have statuses:

```text
AVAILABLE
MISSING
STALE
CONFLICTING
ESTIMATED
RESTATED
INVALID
```

Missing fundamentals must not silently become zero.

Correct:

```text
EPS growth = NULL
data_quality = MISSING
```

Incorrect:

```text
EPS growth = 0
```

---

# 33. Data Freshness

Every scan has a freshness policy.

Example:

```yaml
data_quality:
  max_market_staleness_trading_days: 1
  max_fundamental_staleness_days: 120
```

If required market data is stale:

```text
No signal emission
```

but:

```text
Pipeline continues
Data-quality warning emitted
```

This satisfies the requirement that the pipeline should not hard-stop while still preventing stale-data signals.

---

# 34. Scan Pipeline

Canonical pipeline:

```text
1. Load provider capabilities
2. Refresh instrument master
3. Update local raw market data
4. Validate raw data
5. Normalize corporate actions
6. Build canonical OHLCV
7. Build point-in-time universe
8. Apply liquidity filters
9. Calculate RS across full eligible universe
10. Apply Trend Template gate
11. Build weekly context
12. Run daily VCP detection
13. Calculate volume/volatility features
14. Apply fundamental hard filters
15. Calculate fundamental support
16. Calculate component scores
17. Calculate Final Setup Score
18. Classify A+ / VCP / VCP-like
19. Generate reports
20. Update monitoring state
21. Evaluate breakout alerts
22. Persist scan run
```

---

# 35. Signal State Machine

Classification and lifecycle status are separate axes (details: VCP_SPECIFICATION §5, §28).

```text
classification:  NONE -> VCP_LIKE -> VCP -> A_PLUS_VCP     (from measurements; may move up or down daily)
status:          FORMING -> PIVOT_READY -> BREAKOUT
                 terminal/side states: FAILED, INVALIDATED
                 data states: INSUFFICIENT_DATA, DATA_NOT_READY, STALE_DATA
confirmation:    CONFIRMED | PROVISIONAL
```

`VCP_LIKE` is a watchlist/research classification. Only `VCP` and `A_PLUS_VCP` are production classifications. The system must retain state history and never rewrite earlier observations.

---

# 36. Breakout Monitoring

V1 breakout monitoring is separate from VCP detection.

Default breakout concept:

```text
price > pivot
AND
volume expansion
```

Example configurable parameters:

```yaml
breakout:
  pivot_break_buffer_pct: 0.1
  min_volume_ratio: 1.5
  confirmation_window_days: 1
```

Breakout alerts must include:

- symbol
- pivot
- breakout price
- volume
- average volume
- volume ratio
- setup classification
- setup score
- timestamp
- data quality status

---

# 37. Intraday Architecture

Intraday data is not required for VCP detection.

It is added later for:

```text
breakout alert confirmation
```

Architecture:

```text
WebSocket
    ↓
Tick Buffer
    ↓
Candle Builder
    ↓
Live Breakout Monitor
    ↓
Alert Engine
```

The historical scanner remains independent.

---

# 38. Backtesting Architecture

Backtesting is a first-class module.

It must support:

- historical universe
- point-in-time fundamentals
- historical corporate actions
- historical Trend Template
- historical VCP classification
- historical setup score
- forward outcome labeling

Example:

```text
As-of 2020-08-10
       ↓
What did the system know?
       ↓
VCP detected?
       ↓
Pivot?
       ↓
Score?
       ↓
Forward 5/10/20/40/60 day outcomes
```

---

# 39. Forward Labels

Initial outcome labels:

```text
forward_return_5d
forward_return_10d
forward_return_20d
forward_return_40d
forward_return_60d

max_favorable_excursion
max_adverse_excursion

breakout_within_N_days
failed_breakout
```

Future labels may include:

```text
+20%
+30%
+50%
```

but these should be defined as research labels, not assumptions.

---

# 40. Backtest Bias Controls

Mandatory protections:

## Look-ahead bias

Never use future candles when calculating the setup.

## Survivorship bias

Use point-in-time universe snapshots.

## Fundamental look-ahead

Use publication/availability timestamp, not merely fiscal period.

## Corporate-action leakage

Only apply actions according to their effective historical availability.

## Parameter leakage

Do not optimize thresholds on the same period used for final evaluation.

---

# 41. Walk-Forward Validation

Recommended structure:

```text
Train / calibrate
        ↓
Validation
        ↓
Out-of-sample test
        ↓
Forward walk
```

Example:

```text
2008–2016  development
2017–2019  validation
2020–2022  test
2023–2025  forward test
2026+      live paper validation
```

Exact windows should remain configurable.

The goal is not to maximize historical CAGR.

The first objective is:

> Determine whether the detector identifies structurally meaningful setups with reproducible forward behavior.

---

# 42. ML Confirmer Architecture

Future ML must plug into the deterministic pipeline:

```text
Deterministic VCP
       ↓
Feature vector
       ↓
ML Confirmer
       ↓
Confirmation score
       ↓
Final ranking
```

ML cannot bypass:

```text
Universe gate
Trend Template gate
Data-quality gate
VCP validity gate
```

Potential future models:

- gradient boosting
- random forest
- XGBoost/LightGBM
- calibrated classifier
- sequence model

The first ML model should preferably operate on structured features rather than raw chart images.

---

# 43. LLM Architecture

LLM is an explanation layer.

Inputs:

```text
Trend measurements
VCP measurements
Volume measurements
RS
Fundamentals
Score
Classification
Data-quality flags
```

Output:

```text
Human-readable setup report
```

LLM must not be allowed to mutate:

- scores
- measurements
- gates
- classifications
- database records

LLM output should be stored separately:

```text
llm_report
llm_provider
model
prompt_version
generated_at
input_hash
```

---

# 44. Reproducibility

Every scan run receives:

```text
scan_id
run_timestamp
strategy_version
code_version
config_hash
data_snapshot_id
provider_versions
```

Example:

```text
scan_id = 2026-09-28T10:30:00+05:30
config_hash = sha256(...)
code_version = git_commit
data_snapshot_id = ...
```

A historical scan must be reproducible. Canonical tables are append-only/bitemporal and each `data_snapshot_id` carries a content-hash manifest (DATA_SPECIFICATION §68A, DATABASE_SCHEMA §41).

---

# 45. Configuration

Target:

```text
config/
├── strategy.yaml
├── universe.yaml
├── data.yaml
├── scoring.yaml
├── monitoring.yaml
└── logging.yaml
```

Secrets:

```text
.env
```

Never put:

- API keys
- access tokens
- client secrets

in YAML committed to Git.

---

# 46. Example Strategy Configuration

Abbreviated. The `vcp:` / `classification:` shape is owned by VCP_SPECIFICATION §60; the earlier flat `vcp.require_volume_dryup` / `require_tight_pivot` keys shown here conflicted with it and were removed (audit 2026-09-30 Fix 7).

```yaml
trend_template:
  required: true
  min_rs_rank: 70
  stricter_rs_rank: 80

vcp:                         # full block: VCP_SPECIFICATION section 60 (authoritative)
  contractions: {min: 2, max: 6}
  progressive_tolerance_pct: 10
  # swing / volatility / volume / pivot / confirmation: see VCP_SPECIFICATION section 60

classification:              # per-tier require_* flags: VCP_SPECIFICATION section 60
  a_plus:
    min_contractions: 3
    max_final_contraction_pct: 8

  vcp:
    min_contractions: 2
    max_final_contraction_pct: 12

  vcp_like:
    min_contractions: 2
    max_final_contraction_pct: 15

scoring:
  weights:
    trend: 25
    vcp: 35
    volume: 15
    rs: 15
    fundamentals: 10

  fundamentals_max_weight: 15
```

---

# 47. Configuration Validation

Configuration loading must perform:

```text
schema validation
range validation
cross-field validation
strategy safety validation
```

Examples:

```text
weights sum == 100
fundamental weight <= max allowed
VCP max contraction >= VCP min contraction
A+ thresholds stricter than VCP thresholds
RS threshold between 0 and 100
```

Invalid configuration must prevent a scan from starting.

---

# 48. Repository Interfaces

Persistence should be abstracted.

Example:

```python
class MarketDataRepository(Protocol):
    def load_daily(...)
    def save_daily(...)
    def latest_timestamp(...)
```

```python
class UniverseRepository(Protocol):
    def save_snapshot(...)
    def load_snapshot(...)
```

```python
class ScanRepository(Protocol):
    def save_scan(...)
    def save_results(...)
    def load_scan(...)
```

DuckDB is the initial implementation.

---

# 49. Suggested Repository Layout

```text
vcp_scanner/
├── pyproject.toml
├── README.md
├── PROJECT_DESIGN.md
├── CHANGELOG.md
├── .env.example
│
├── config/
│   ├── strategy.yaml
│   ├── universe.yaml
│   ├── data.yaml
│   └── scoring.yaml
│
├── src/
│   └── vcp_scanner/
│       ├── domain/
│       ├── config/
│       ├── data/
│       │   ├── providers/
│       │   │   ├── base.py
│       │   │   ├── kite.py
│       │   │   └── dhan.py
│       │   ├── repositories/
│       │   ├── ingestion/
│       │   ├── adjustment/
│       │   ├── reconciliation/
│       │   ├── quality/
│       │   ├── features/      (SQL indicator builders)
│       │   ├── storage/
│       │   └── universe/      (universe rules, DB-free)
│       ├── features/          (RS, Trend Template, weekly Stage)
│       ├── patterns/
│       │   └── vcp/
│       ├── fundamentals/
│       ├── scoring/
│       ├── monitoring/
│       ├── alerts/
│       ├── reporting/
│       ├── backtest/
│       ├── ml/
│       ├── api/
│       └── infrastructure/
│
├── scripts/
│   ├── bootstrap.py
│   ├── update_data.py
│   ├── run_scan.py
│   ├── run_backtest.py
│   └── monitor.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   ├── backtest/
│   └── fixtures/
│
├── data/
├── reports/
└── notebooks/
```

---

# 50. Testing Strategy

Testing must be treated as part of the strategy implementation.

## Unit tests

Test:

- indicators
- Trend Template
- RS
- contractions
- pivots
- volume
- VCP classification
- scoring

## Integration tests

Test:

```text
provider → repository → feature engine → scanner
```

## Regression tests

Maintain known VCP examples and non-VCP examples.

Every change to the detector must run the regression suite.

## Data-quality tests

Detect:

- duplicate candles
- missing dates
- impossible OHLC
- negative volume
- zero prices
- unexplained splits
- timestamp anomalies

---

# 51. Golden Dataset

Create a manually reviewed dataset:

```text
golden_vcp/
├── confirmed_vcp/
├── failed_vcp/
├── non_vcp/
├── trend_fail/
└── ambiguous/
```

Each example should contain:

```text
symbol
as_of_date
classification
human_review_notes
```

This becomes the benchmark for detector changes.

Labeling protocol (see VCP_SPECIFICATION §57): labels are assigned from charts **blind to detector output**, ideally by two reviewers. Target ≥ 100 examples per class (built up over time, not required on day one). A 30% hold-out is never used for tuning. `ambiguous` examples are excluded from precision/recall.

---

# 52. Explainability Contract

Every signal must answer:

### Why did this stock pass?

Example:

```text
Trend Template: PASS
RS Rank: 87
Weekly Stage 2: PASS

VCP:
  contractions: 3
  depths: 18.4%, 11.2%, 6.8%
  progressive tightening: PASS
  volume dry-up: PASS
  final contraction: 6.8%
  pivot distance: 1.4%

Fundamentals:
  EPS YoY: +31%
  Sales YoY: +18%
  EPS acceleration: PASS
  ROE: 19%
  Debt check: PASS

Classification:
  A+ VCP

Final Setup Score:
  88/100
```

---

# 53. Monitoring Model

Monitoring should be event-driven.

Events:

```text
VCP_FORMING
VCP_QUALIFIED
PIVOT_READY
PIVOT_CHANGED
BREAKOUT
BREAKOUT_FAILED
SETUP_INVALIDATED
DATA_STALE
```

Monitoring state must persist between scans.

---

# 54. Alert Engine

Initial channels:

```text
console
email
Telegram/webhook (future)
```

Alert payload should contain structured information, not only prose.

Example:

```json
{
  "event": "BREAKOUT",
  "symbol": "XYZ",
  "pivot": 1250.0,
  "price": 1261.5,
  "volume_ratio": 2.1,
  "classification": "A_PLUS_VCP",
  "setup_score": 91
}
```

---

# 55. API Layer

The API is an application interface, not the strategy engine.

Potential endpoints:

```text
GET /health
GET /universe
GET /scan/latest
GET /scan/{scan_id}
GET /stocks/{symbol}
GET /stocks/{symbol}/vcp
GET /stocks/{symbol}/history
GET /watchlist
GET /alerts
POST /scan
```

The API must call application services.

It must not contain VCP calculations.

---

# 56. Dashboard

Dashboard is a consumer of the API/repositories.

Primary views:

## Screener

Columns:

```text
Symbol
Classification
Final Score
Trend Score
VCP Score
Volume Score
RS
Fundamental Score
Pivot
Pivot Distance
Data Quality
```

## Stock detail

```text
Weekly chart
Daily chart
Contractions
Pivot
Volume
Trend Template
RS
Fundamentals
Score breakdown
Historical setup events
```

## Watchlist

```text
Forming VCP
Qualified VCP
Pivot Ready
Breakout
Failed
```

---

# 57. Data Quality Framework

Every signal receives:

```text
data_quality_score
data_quality_flags
```

Example flags:

```text
STALE_PRICE_DATA
MISSING_CANDLES
CORPORATE_ACTION_UNRESOLVED
FUNDAMENTAL_DATA_MISSING
FUNDAMENTAL_DATA_STALE
SYMBOL_MAPPING_UNCERTAIN
PROVIDER_CONFLICT
```

Critical data-quality failure:

```text
NO_SIGNAL
```

Non-critical issue:

```text
SIGNAL_WITH_WARNING
```

---

# 58. Provider Reconciliation

When multiple providers become available:

```text
Kite
Dhan
  ↓
same instrument/date
  ↓
OHLCV comparison
```

Flag:

```text
PRICE_CONFLICT
VOLUME_CONFLICT
TIMESTAMP_CONFLICT
```

The system must not silently overwrite one provider with another.

---

# 59. Database Conceptual Tables

Conceptual only. **Authoritative table names and columns: DATABASE_SCHEMA.md.**

Core:

```text
instruments
instrument_aliases
instrument_symbol_history
security_master_history
surveillance_flags_history
universe_snapshots
universe_memberships

raw_ohlcv
daily_prices
daily_prices_adjusted
corporate_actions
corporate_action_adjustments
trading_calendar

fundamental_snapshots
fundamental_metrics

technical_features_daily
technical_features_weekly
relative_strength_snapshots
trend_template_results
trend_template_conditions
weekly_context

vcp_patterns
contractions
pivots
volume_features

score_components
setup_scores
vcp_status_history

scan_runs
scan_results

monitoring_states
breakout_events
alerts

data_quality_events
provider_fetch_log
config_versions
```

---

# 60. Versioning

The following must be versioned:

```text
strategy_version
vcp_algorithm_version
trend_algorithm_version
rs_algorithm_version
fundamental_algorithm_version
scoring_version
configuration_hash
data_schema_version
```

Changing a scoring weight must create a new scoring version.

Changing VCP detection must create a new algorithm version.

---

# 61. Performance Targets

Initial targets should be measured rather than assumed.

Desired architecture target:

```text
500–3000 liquid NSE equities
10+ years daily data
incremental daily scan
```

Performance objectives:

```text
Local daily scan: minutes, not hours
Incremental data update: bounded and resumable
Feature generation: vectorized where possible
Backtest: batch-oriented DuckDB/Parquet
```

Do not sacrifice correctness for an arbitrary benchmark.

---

# 62. Failure Handling

Every external operation must support:

```text
retry
backoff
timeout
partial failure
resume
idempotency
logging
```

If 1000 instruments are requested and 37 fail:

```text
963 successful
37 failed
```

The pipeline should continue.

But:

```text
required universe coverage below configured threshold
→ NO SIGNAL EMISSION
```

---

# 63. Idempotency

Running:

```bash
python scripts/update_data.py
```

twice must not duplicate candles.

Use unique keys such as:

```text
instrument_id
timestamp
timeframe
```

for canonical candles.

---

# 64. Caching

Cache:

- instrument master
- provider metadata
- fundamental data
- derived weekly candles
- indicators
- feature vectors

Cache keys should include:

```text
instrument
date range
timeframe
algorithm version
configuration hash
```

---

# 65. AI-Agent Development Rules

Because the system will be built with AI agents, the repository must contain an explicit agent-development contract.

AI agents must:

1. Read `PROJECT_DESIGN.md` before modifying architecture.
2. Read relevant existing modules before coding.
3. Never replace an interface with direct provider calls.
4. Never silently change strategy thresholds.
5. Never remove tests to make a feature pass.
6. Never change scoring weights without explicit configuration changes.
7. Never add an external dependency without justification.
8. Never mix raw and normalized data.
9. Never use future data in features.
10. Never modify historical results without a migration/version.
11. Run tests before declaring completion.
12. Report files changed.
13. Report tests run.
14. Report known limitations.
15. Preserve backward compatibility where practical.

---

# 66. AI-Agent Task Protocol

Every implementation task should follow:

```text
READ
  ↓
UNDERSTAND
  ↓
PLAN
  ↓
IMPLEMENT
  ↓
TEST
  ↓
AUDIT
  ↓
REPORT
```

An AI agent should not begin by blindly editing files.

Recommended prompt structure:

```text
Read PROJECT_DESIGN.md first.

Inspect the existing implementation relevant to this task.

Do not modify files yet.

Explain:
1. current architecture
2. relevant code paths
3. proposed implementation
4. files that will change
5. tests required
6. risks

Wait for approval before implementation.
```

---

# 67. Development Phases

## Phase 0 — Architecture

Deliver:

- repository skeleton
- configuration
- domain objects
- interfaces
- test framework
- logging
- versioning

Acceptance:

```text
Project installs
Tests execute
Configuration validates
No strategy code depends on broker SDK
```

---

## Phase 1 — Market Data

Deliver:

- Kite provider
- instrument master
- historical downloader
- raw storage
- DuckDB
- Parquet
- resumable ingestion
- Kite login/token-refresh workflow (DATA_SPECIFICATION §28A)

Acceptance:

```text
Historical NSE equity data can be downloaded and reproduced.
```

---

## Phase 2 — Normalization

Deliver:

- OHLCV validation
- corporate-action layer: NSE primary + Upstox secondary, reconciliation, `PROVIDER_CONFLICT` / `UNEXPLAINED_GAP` handling (DATA_SPECIFICATION §18A)
- golden corporate-action fixtures (5–10 real splits/bonuses with known adjusted prices)
- adjusted series
- anomaly detection
- canonical dataset

Acceptance:

```text
Raw data remains immutable.
Canonical data is reproducible.
```

---

## Phase 3 — Universe

Deliver:

- NSE universe
- exclusions
- liquidity
- price filters
- ASM/GSM flags
- historical security master and delisting source (§14A) with survivorship status stamping
- point-in-time snapshots

Acceptance:

```text
Historical universe can be reconstructed for any supported date.
```

---

## Phase 4 — Indicators

Deliver:

- SMA
- EMA
- ATR
- volatility
- volume
- relative strength

Acceptance:

```text
All indicators have deterministic unit tests.
```

---

## Phase 5 — Trend Template

Deliver:

- Trend Template engine
- RS gate
- explainability

Acceptance:

```text
Every condition exposes measurement + threshold + result.
```

---

## Phase 6 — VCP Engine

Deliver:

- swing detection
- contraction detection
- progressive tightening
- volume dry-up
- volatility contraction
- pivot detection
- classification

Acceptance:

```text
Golden dataset regression tests pass.
```

---

## Phase 6B — Minimal Event-Study Harness

Deliver:

- as-of detector runs over history (no look-ahead)
- forward labels (§39) for A+/VCP/VCP-like observations vs. a non-VCP Trend-Template-passing baseline
- survivorship-status stamped output

Acceptance:

```text
Detector output can be compared against a baseline on forward outcomes before scoring, fundamentals or alerts are built.
```

Threshold decisions are made here rather than after nine more phases are built on unvalidated hypotheses.

---

## Phase 7 — Scoring

Deliver:

- sub-scores
- configurable weights
- fundamental cap
- final ranking

Acceptance:

```text
Scores are reproducible and explainable.
```

---

## Phase 8 — Fundamentals

Deliver:

- provider abstraction
- point-in-time snapshots
- EPS
- sales
- acceleration
- margins
- ROE
- debt check
- data availability

Acceptance:

```text
Fundamentals cannot bypass technical gates.
```

---

## Phase 9 — Backtesting

Deliver:

- event engine
- forward labels
- walk-forward framework
- bias checks

Acceptance:

```text
Historical scans use only information available at the time.
```

---

## Phase 10 — Reporting/API

Deliver:

- stock report
- scan report
- API
- dashboard integration

---

## Phase 11 — Monitoring

Deliver:

- state machine
- VCP monitoring
- pivot monitoring
- invalidation

---

## Phase 12 — Breakout Alerts

Deliver:

- breakout detection
- volume confirmation
- alert channels
- interim option before Phase 14: poll `get_quotes` for the small PIVOT_READY set during market hours. This gives intraday breakout awareness without a WebSocket stack

---

## Phase 13 — ML Confirmer

Only after sufficient labeled data exists.

Deliver:

- feature dataset
- training pipeline
- validation
- calibration
- ML confirmer interface

---

## Phase 14 — Intraday

Deliver:

- WebSocket provider
- candle builder
- live breakout monitoring

---

## Phase 15 — Future Trade Management

Separate module:

```text
position sizing
risk
stop
exit
portfolio
broker execution
```

This module must not modify the VCP detector.

---

# 68. Definition of Done

A feature is not complete merely because code executes.

A feature is complete when:

```text
Implementation
+
Unit tests
+
Integration tests
+
Documentation
+
Configuration
+
Logging
+
Error handling
+
Data-quality handling
+
Regression tests
```

are complete.

---

# 69. Research Governance

Strategy changes must be treated as experiments.

Example:

```text
Experiment:
A+ final contraction <= 8%
vs
A+ final contraction <= 10%
```

Record:

```text
hypothesis
configuration
data period
sample size
outcomes
metrics
conclusion
```

Do not silently promote a research parameter into production.

---

# 70. Metrics for Detector Evaluation

The VCP detector should be evaluated independently from trading returns.

Primary metrics:

```text
precision of qualified setups
recall on golden dataset
false-positive rate
false-negative rate
breakout rate
forward return distribution
maximum favorable excursion
maximum adverse excursion
setup decay rate
```

Ranking metrics:

```text
top-decile forward return
rank correlation
information coefficient
hit rate
calibration
```

Trading metrics such as CAGR, Sharpe, and drawdown belong to later strategy/backtest analysis.

---

# 71. Important Research Distinction

The system has three different questions:

### Detection

> Does the price/volume structure satisfy the VCP definition?

### Ranking

> Among valid setups, which structures are stronger according to our measured features?

### Outcome prediction

> What is the probability/distribution of future outcomes?

These must not be conflated.

The first two are deterministic in V1.

The third requires validated statistical/ML modeling.

---

# 72. Fundamental Data Strategy

Fundamental data is intentionally not part of the primary detection engine.

Architecture:

```text
Technical Universe
      ↓
Trend Template
      ↓
VCP
      ↓
Technical Ranking
      ↓
Fundamental Support
```

This solves a major design problem: the system does not need perfect real-time fundamentals to detect VCPs.

Fundamental data can be incomplete without breaking the technical scanner, provided critical configured hard filters are handled explicitly.

---

# 73. Why DuckDB + Parquet

DuckDB is appropriate for this project because the dominant workloads are analytical:

- historical scans
- feature generation
- ranking
- backtesting
- event studies
- large sequential reads

Parquet provides:

- columnar storage
- compression
- portability
- efficient analytical reads
- easy archival

PostgreSQL should be introduced only when operational multi-writer state actually requires it.

---

# 74. Future PostgreSQL Boundary

Potential PostgreSQL scope:

```text
alerts
monitoring state
trade journal
portfolio
user settings
audit events
```

Market history should remain optimized for analytical storage unless requirements change.

---

# 75. Security

Secrets must be supplied through:

```text
environment variables
secret manager
```

Never:

```text
Git
YAML
Python source
logs
reports
```

API tokens must never appear in exception traces or generated reports.

---

# 76. Observability

Structured logging fields:

```text
timestamp
level
component
symbol
instrument_id
scan_id
provider
operation
duration_ms
status
error_code
```

Metrics:

```text
provider_success_rate
provider_latency
data_freshness
universe_count
trend_pass_count
vcp_count
a_plus_count
breakout_count
data_quality_failures
```

---

# 77. Operational Commands

Target CLI:

```bash
vcp bootstrap
vcp update-data
vcp build-universe
vcp calculate-features
vcp scan
vcp report
vcp monitor
vcp backtest
vcp validate-data
```

All commands should support:

```text
--as-of
--config
--symbols
--start
--end
--dry-run
```

---

# 78. Example End-to-End Run

```text
$ vcp scan --as-of 2026-09-28
```

System:

```text
Load configuration
        ↓
Resolve config hash
        ↓
Load local market data
        ↓
Repair missing data if permitted
        ↓
Validate freshness
        ↓
Build universe
        ↓
Calculate RS
        ↓
Trend Template gate
        ↓
Weekly context
        ↓
Daily VCP detection
        ↓
Fundamental support
        ↓
Scoring
        ↓
Classification
        ↓
Persist
        ↓
Generate report
        ↓
Update monitor
        ↓
Evaluate breakout
```

---

# 79. Example Result

```text
SYMBOL: ABC

Trend Template: PASS
RS Rank: 88

Weekly:
  Stage 2: PASS
  Base duration: 17 weeks

Daily:
  Contractions: 3
  Depths: 18.2% → 10.1% → 6.4%
  Progressive tightening: PASS
  Volume dry-up: PASS
  ATR contraction: PASS
  Pivot distance: 1.2%

Fundamentals:
  EPS YoY: +34%
  EPS QoQ: +29%
  Sales YoY: +21%
  EPS acceleration: PASS
  ROE: 18%
  Debt check: PASS

Scores:
  Trend: 94
  VCP: 91
  Volume: 86
  RS: 88
  Fundamentals: 82

Final Setup Score: 89

Classification:
  A+ VCP

Status:
  PIVOT_READY
```

---

# 80. Final Architectural Rule

The most important rule in the entire system is:

```text
DATA
  ↓
MEASUREMENTS
  ↓
DETERMINISTIC RULES
  ↓
TECHNICAL VALIDATION
  ↓
CLASSIFICATION
  ↓
SCORING
  ↓
RANKING
  ↓
MONITORING
  ↓
ALERT
```

Not:

```text
DATA
  ↓
AI
  ↓
"Looks like a VCP"
```

AI can assist the system, but it must not replace the measurable definition of the strategy.

---

# 81. Initial Strategy Hypotheses

The following are starting hypotheses and **must not be treated as validated edge**:

```yaml
trend_template:
  required: true
  min_rs_rank: 70

classification:
  a_plus:
    min_contractions: 3
    max_final_contraction_pct: 8

  vcp:
    min_contractions: 2
    max_final_contraction_pct: 12

  vcp_like:
    min_contractions: 2
    max_final_contraction_pct: 15

scoring:
  trend: 25
  vcp: 35
  volume: 15
  rs: 15
  fundamentals: 10
```

These values must be evaluated using out-of-sample research.

---

# 82. Initial Acceptance Criteria for the Entire System

The project is considered ready for serious paper use only when:

- historical data ingestion is reproducible;
- raw data is immutable;
- corporate actions are handled;
- point-in-time universe exists;
- Trend Template is deterministic and tested;
- RS is calculated across the eligible universe;
- VCP classification is deterministic;
- every VCP decision is explainable;
- fundamental data is point-in-time;
- fundamentals cannot bypass technical gates;
- score weights are configurable and validated;
- configuration hashes are stored;
- backtests are protected against look-ahead;
- survivorship bias is addressed;
- golden VCP regression tests exist;
- provider failure is handled;
- stale data cannot generate a signal;
- scan results can be reproduced;
- monitoring state persists;
- breakout alerts are tested;
- no order execution is enabled by default.

---

# 83. Recommended First Implementation Principle

Do **not** begin by implementing the dashboard.

Do **not** begin by implementing the LLM.

Do **not** begin by scraping every fundamental metric.

Do **not** begin with ML.

Build in this order:

```text
1. Data correctness
2. Point-in-time universe
3. Trend Template
4. VCP detector
5. Golden dataset
6. Scoring
7. Backtesting
8. Fundamentals
9. Monitoring
10. Alerts
11. Dashboard
12. ML
13. Intraday
14. Trade management
```

The hardest and most valuable asset of the project is the **validated VCP detection engine plus its historical labeled dataset**.

Everything else should be designed around protecting that asset.

---

# 83A. Deferred Scope

Not needed for V1 detection. Keep the interfaces, do not build the implementations yet:

- Dhan adapter and multi-provider reconciliation (§58)
- ML confirmer, LLM reports
- PostgreSQL, WebSocket intraday, trade management

Design work on these resumes only after Phase 6B shows the detector has measurable value.

---

# 84. External Data-Provider Reference

For Kite Connect implementation, use the official Kite Connect documentation as the primary provider contract. Historical candle semantics are documented in the historical-data API reference, and the instrument master is documented separately.

The current Kite documentation also confirms that the API provides live market streaming through WebSockets, which is appropriate for a future intraday breakout-monitoring layer rather than historical VCP detection.

Kite's current published/forum information indicates that historical data is included in the base Kite Connect subscription rather than requiring the former separate historical-data add-on. This should still be verified against the account/subscription actually used before production ingestion is configured.

---

# 85. Architecture Decision Record — Initial Decisions

| Decision | Choice | Reason |
|---|---|---|
| Primary signal | VCP + Trend Template | Technical structure is primary |
| Trend gate | Mandatory | Prevent VCP-like bases in weak trends |
| RS | Full eligible universe | Avoid biased RS calculation |
| VCP engine | Deterministic V1 | Explainability and validation |
| ML | Future confirmer | Avoid premature black-box detection |
| Fundamentals | Secondary | Support, not override |
| Fundamental weight | 10% default | Limited influence |
| Market provider | Kite first | Existing primary source |
| Future provider | Dhan | Provider redundancy |
| Local truth | DuckDB + Parquet | Analytical workload |
| Architecture | Modular monolith | Production structure without microservice overhead |
| Configuration | YAML + `.env` | Reproducibility + security |
| History | Maximum available | Research across regimes |
| Universe | All NSE equity | Broad opportunity set |
| Bias control | Point-in-time | Required for valid backtests |
| Corporate actions | Explicit layer | Historical correctness |
| V1 workflow | Scan → Rank → Report → Monitor → Alert | Human-in-the-loop |
| Execution | Future | Reduce operational risk |
| LLM | Explanation | Preserve deterministic strategy logic |

---

# 86. Status

**Architecture baseline approved by requirements; revised after design review (REVIEW_FIXES.md). Open decisions are listed there.**

Next implementation artifact should be:

```text
PROJECT_DESIGN.md
        ↓
DATABASE_SCHEMA.md
        ↓
DATA_SPECIFICATION.md
        ↓
VCP_SPECIFICATION.md
        ↓
TREND_TEMPLATE_SPECIFICATION.md
        ↓
SCORING_SPECIFICATION.md
        ↓
AI_AGENT_RULES.md / AGENTS.md
        ↓
Phase 0 implementation
```

No production strategy code should be written until the data contracts and VCP measurement definitions have been finalized.