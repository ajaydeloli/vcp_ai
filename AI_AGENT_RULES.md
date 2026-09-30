# AI_AGENT_RULES.md

**Project:** Institutional-Grade Minervini VCP Scanner  
**Purpose:** Governing rules for AI coding agents working on the project  
**Status:** Architecture / Development Contract  
**Version:** 1.1

---

# 1. Purpose

This document defines the mandatory operating rules for any AI agent that reads, modifies, tests, reviews, refactors, or extends this project.

The project is intended to be:

- production-grade;
- research reproducible;
- point-in-time correct;
- provider-neutral;
- configurable;
- auditable;
- maintainable by humans and AI agents;
- suitable for long-term personal use.

AI agents must treat this document as a development contract, not as optional guidance.

**Short form:** `AGENTS.md` is the concise entry point loaded by agents. This file is the detailed reference. On any conflict, the specifications win.

---

# 2. Core Principle

An AI agent must optimize for:

```text
Correctness
>
Reproducibility
>
Data integrity
>
Testability
>
Maintainability
>
Performance
>
Convenience
```

A shortcut that makes development faster but compromises correctness, reproducibility, or data integrity is not acceptable.

---

# 3. Project Context

The system detects and ranks stocks exhibiting Mark Minervini-style VCP setups.

The primary strategy path is:

```text
Market Data
    ↓
Corporate-Action Normalization
    ↓
Point-in-Time Universe
    ↓
Trend Template Gate
    ↓
Weekly Context
    ↓
Daily VCP Detection
    ↓
Volume / RS
    ↓
Fundamental Support
    ↓
Sub-Scores
    ↓
Final Setup Score
    ↓
Ranking
    ↓
Detailed Report
    ↓
Monitoring
    ↓
Breakout Alert
```

The deterministic engine is authoritative.

Future ML is a confirmation layer.

LLMs are explanation/reporting assistants.

---

# 4. Source of Truth Documents

Before making architectural changes, agents must inspect the relevant specification.

Primary documents:

```text
PROJECT_DESIGN.md
DATABASE_SCHEMA.md
DATA_SPECIFICATION.md
VCP_SPECIFICATION.md
TREND_TEMPLATE_SPECIFICATION.md
SCORING_SPECIFICATION.md
```

Future specifications may include:

```text
FUNDAMENTALS_SPECIFICATION.md
BACKTEST_SPECIFICATION.md
FRONTEND_SPECIFICATION.md
ALERT_SPECIFICATION.md
ML_SPECIFICATION.md
```

If implementation conflicts with a specification:

1. identify the conflict;
2. do not silently change architecture;
3. determine whether the specification or implementation is outdated;
4. propose the smallest justified change;
5. update documentation if the architecture is intentionally changed.

---

# 5. Never Guess Existing Project Behavior

Before modifying an existing component, inspect:

```text
implementation
tests
configuration
callers
data contracts
documentation
```

Never infer behavior from filenames alone.

For example, do not assume:

```text
fetch_universe()
```

actually returns the complete NSE universe.

Verify the implementation and tests.

---

# 6. Read Before Writing

An agent must inspect enough surrounding code to understand:

```text
who calls this function?
what does it return?
what assumptions do callers make?
what configuration controls it?
what tests cover it?
what database tables depend on it?
```

Do not immediately rewrite a file because the requested behavior appears simple.

---

# 7. Minimal Change Principle

Prefer:

```text
small, isolated, testable change
```

over:

```text
large refactor
```

unless the requested task explicitly requires architectural restructuring.

Do not rewrite working modules merely because another implementation looks cleaner.

---

# 8. Architecture Boundaries

The project is a modular monolith.

Expected conceptual modules:

```text
data/
universe/
indicators/
patterns/
scoring/
research/
reporting/
alerts/
api/
frontend/
```

These are *conceptual* modules. The concrete package layout is PROJECT_DESIGN §6 (audit 2026-09-30 Fix 6): universe rules in `data/universe/`, indicator SQL in `data/features/`, RS / Trend Template / weekly Stage in `features/`. Do not create `universe/`, `indicators/` or `trend/` packages; `tests/unit/test_architecture_rules.py` fails if they reappear.

Modules must have clear responsibilities.

---

# 9. Dependency Direction

Preferred dependency direction:

```text
Infrastructure
    ↓
Data
    ↓
Features
    ↓
Strategy
    ↓
Scoring
    ↓
Research / Reporting
    ↓
API / UI
```

Lower-level modules must not depend on higher-level presentation modules.

For example:

```text
VCP detector
```

must not import:

```text
Streamlit
Next.js
dashboard code
```

---

# 10. Broker SDK Isolation

Strategy code must never import:

```text
Kite SDK
Dhan SDK
```

directly.

Correct:

```text
VCP Engine
    ↓
MarketDataProvider
    ↓
KiteProvider
```

Incorrect:

```text
VCP Engine
    ↓
KiteConnect(...)
```

Provider-specific logic belongs inside adapters.

---

# 11. Provider-Neutral Interfaces

Use interfaces/protocols for external providers.

Examples:

```python
MarketDataProvider
FundamentalDataProvider
UniverseProvider
```

The strategy should depend on the interface, not the provider.

This is mandatory because Kite is the initial provider and Dhan is planned later.

---

# 12. Local Data Is the Source of Truth

Normal analysis must use:

```text
DuckDB + Parquet
```

Provider APIs are ingestion sources.

Do not design the scanner so that every scan requires downloading market data again.

---

# 13. Raw Data Must Be Preserved

Never silently overwrite raw provider observations.

Raw data should remain auditable.

If a provider corrects a historical candle:

```text
old observation
new observation
```

must remain traceable.

Canonical data can change; raw lineage must remain available.

---

# 14. Corporate Actions Are First-Class Data

Do not "fix" a split by simply editing OHLC values.

Use:

```text
Raw OHLCV
+
Corporate Actions
↓
Adjustment Engine
↓
Adjusted OHLCV
```

Any change to corporate-action handling requires tests.

---

# 15. No Look-Ahead Bias

This is one of the highest-priority rules.

An agent must never introduce future information into a historical observation.

For a scan at:

```text
T
```

the system may use only information available by:

```text
T
```

This applies to:

- prices;
- volume;
- RS;
- universe membership;
- fundamentals;
- sector classification;
- corporate actions;
- market regime;
- ML features.

---

# 16. Fundamental Availability

Fundamental data must distinguish:

```text
period_end
```

from:

```text
available_at
```

A quarterly result for:

```text
Q1 ending June 30
```

cannot be used in a June 30 backtest if it was published later.

Agents must preserve point-in-time availability.

---

# 17. Historical Universe Integrity

Never use today's universe for historical backtests unless the research experiment explicitly requests survivorship-biased analysis.

The system must support:

```text
universe as of T
```

including:

- listings;
- delistings;
- exclusions;
- liquidity;
- surveillance status.

---

# 18. No Survivorship Bias

Do not silently drop:

```text
delisted companies
failed companies
merged companies
historical securities
```

from historical datasets.

If a security existed during the research period, its historical membership must be represented where appropriate.

---

# 19. No Silent Data Substitution

Do not silently replace:

```text
missing data
```

with:

```text
zero
```

Examples:

```text
missing EPS ≠ EPS = 0
missing volume ≠ volume = 0
missing RS ≠ RS = 0
```

Use explicit nulls and data-quality states.

---

# 20. No-Signal vs No-Data

These states must remain separate:

```text
NO_VCP
DATA_NOT_READY
INSUFFICIENT_DATA
STALE_DATA
PROVIDER_ERROR
INVALID_DATA
```

Never convert a data failure into:

```text
VCP = false
```

---

# 21. VCP Detector Authority

The deterministic VCP detector decides whether a VCP exists.

An LLM must not override:

```text
VCP detection
Trend Template gate
data-quality gates
```

An ML confirmer may be introduced later, but it must operate through an explicit interface.

---

# 22. LLM Role

LLMs may be used for:

```text
explanations
reports
research assistance
documentation
code review
test generation
```

LLMs must not silently modify:

```text
OHLCV
VCP measurements
Trend Template results
RS calculations
corporate actions
scores
```

---

# 23. ML Role

Future ML is a confirmation layer.

Architecture:

```text
Deterministic Detector
        ↓
Validated Features
        ↓
ML Confirmer
        ↓
Adjusted / Additional Score
```

The ML model must not override mandatory technical gates unless the specification explicitly changes.

---

# 24. Configuration First

Strategy parameters must be configurable.

Examples:

```text
Trend Template thresholds
RS threshold
VCP classification thresholds
score weights
liquidity thresholds
data freshness
fundamental weight
provider selection
```

Do not bury strategy constants inside implementation code.

---

# 25. Configuration Safety

Not every parameter should be freely configurable.

Critical safety limits may require code-level caps.

Example:

```yaml
fundamentals_weight: 100
```

must not be allowed to bypass the architectural requirement that fundamentals remain secondary.

Use:

```text
configurable range
+
code-enforced maximum
```

for safety-critical strategy constraints.

---

# 26. Configuration Hash

Every scan should record a hash of the resolved configuration.

Conceptually:

```text
raw config
    ↓
resolved config
    ↓
canonical serialization
    ↓
SHA-256
    ↓
config_hash
```

A result without its configuration identity is not fully reproducible.

---

# 27. Algorithm Versioning

Changes to important algorithms must have explicit versions.

Examples:

```text
vcp_algorithm_version
trend_template_version
scoring_version
adjustment_version
feature_version
```

Do not silently change historical calculations and call them the same version.

---

# 28. Database Rules

Database schema changes must be:

```text
intentional
versioned
tested
documented
```

Never manually modify production database structure without a migration mechanism.

---

# 29. Repository Pattern

Application logic should use repositories/services rather than scattering SQL throughout strategy modules.

Example:

```python
market_data_repository.get_daily(...)
```

rather than:

```python
duckdb.connect(...)
```

inside VCP code.

---

# 30. SQL Location

SQL should be:

```text
centralized
reviewable
parameterized
```

Avoid dynamically concatenating untrusted values into SQL.

---

# 31. Data Access Efficiency

Do not repeatedly load entire datasets when a bounded query is sufficient.

Prefer:

```text
symbol
date range
required columns
```

over:

```text
SELECT *
```

for large analytical operations.

---

# 32. Feature Calculation

Features should be derived from canonical data.

Examples:

```text
SMA
ATR
52-week high
52-week low
volume averages
relative strength
```

Feature functions should be:

```text
deterministic
testable
side-effect free
```

where practical.

---

# 33. VCP Measurements Must Be Preserved

Do not store only:

```text
VCP = true
```

Store the underlying measurements.

Examples:

```text
contraction depths
contraction durations
volume dry-up
pivot price
pivot distance
base depth
tightness
volatility contraction
```

This is essential for:

- debugging;
- explainability;
- research;
- backtesting;
- future ML labels.

---

# 34. VCP Classification

The detector supports tiers such as:

```text
A+ VCP
VCP
VCP-like / Watchlist
```

Classification must be derived from measured values.

Do not implement three unrelated detectors when the specification defines tiered classification from common measurements.

---

# 35. Trend Template

Trend Template is a mandatory technical gate.

Default RS threshold:

```text
70
```

with stricter values such as:

```text
80
```

configurable.

Do not let fundamental scores rescue a failed Trend Template gate.

---

# 36. Relative Strength

RS must be computed across the configured full liquid universe before candidate filtering.

Incorrect:

```text
Trend filter
   ↓
RS among survivors
```

Correct:

```text
Full eligible universe
   ↓
RS calculation
   ↓
Trend Template / candidate filtering
```

This rule protects the meaning of relative ranking.

---

# 37. Fundamentals

Fundamentals are secondary supportive evidence.

Default target weight:

```text
10%
```

Maximum configured architecture weight:

```text
15%
```

Fundamentals must not lift a failed technical setup into a valid VCP.

---

# 38. Fundamental Metrics

The initial fundamental layer should remain focused:

```text
EPS YoY growth
EPS QoQ growth
Sales growth
EPS acceleration
margin expansion
ROE
debt check
```

Do not build an enormous fundamental model before validating the technical detector.

---

# 39. Scoring

Sub-scores:

```text
Trend
VCP
Volume
RS
Fundamental
```

then:

```text
Final Setup Score
```

The score is:

```text
ranking score
```

not:

```text
probability
```

Do not describe a score as "80% likely to succeed" without calibration.

---

# 40. Score Weight Validation

Configured weights are hypotheses.

Example:

```yaml
trend: 25
vcp: 35
volume: 15
rs: 15
fundamentals: 10
```

must not be presented as empirically optimal until validated through historical research.

Agents must not claim that a weighting scheme "works" without evidence.

---

# 41. Avoid Overfitting

When modifying thresholds, agents must consider:

```text
in-sample performance
out-of-sample performance
walk-forward performance
multiple market regimes
```

Do not optimize parameters solely against one historical period.

---

# 42. Backtest Integrity

A backtest must use only information available at each historical timestamp.

Do not:

```text
calculate today's indicator
then apply it to 2018
```

or:

```text
use revised fundamental data
```

without explicitly labeling the experiment as revised-history research.

---

# 43. Tests Are Part of the Feature

A change is incomplete if its behavior is not tested where practical.

For meaningful changes, add or update:

```text
unit tests
integration tests
regression tests
```

depending on scope.

---

# 44. Never Disable Tests to Make a Build Pass

Do not:

```text
skip failing test
delete failing test
weaken assertion
mark expected failure
```

merely to achieve green CI.

If behavior intentionally changes:

```text
update test
document why
```

---

# 45. Existing Failures

If the repository already contains failing tests:

1. document them;
2. determine whether they are related to the current change;
3. do not hide them;
4. avoid claiming the project is fully passing.

---

# 46. Test Data

Tests should use deterministic fixtures.

Do not make unit tests depend on live:

```text
Kite
Dhan
NSE
internet
```

unless explicitly categorized as integration tests.

---

# 47. Live Provider Tests

Provider integration tests should be separated from deterministic unit tests.

Example:

```text
tests/unit/
tests/integration/
tests/e2e/
```

Live tests may require:

```text
credentials
network
provider availability
```

and must not be required for ordinary local unit-test execution.

---

# 48. Mocking Providers

When testing strategy logic:

```python
FakeMarketDataProvider
```

or equivalent test doubles should be used.

The VCP detector should be testable without Kite credentials.

---

# 49. Test Edge Cases

VCP-related tests should include:

```text
no contractions
one contraction
two contractions
progressive contractions
non-progressive contractions
deep final contraction
tight final contraction
volume dry-up
volume expansion
invalid pivot
gap event
missing data
stale data
insufficient lookback
```

---

# 50. Data Edge Cases

Also test:

```text
split
bonus
symbol change
duplicate candle
missing candle
zero volume
bad OHLC
provider correction
provider disagreement
delisted security
new listing
```

---

# 51. Reproducibility Tests

Given:

```text
same data snapshot
same configuration
same algorithm version
```

the output must be deterministic.

Run-to-run variation is a defect unless explicitly documented.

---

# 52. Randomness

If ML or probabilistic components introduce randomness:

```text
seed
model version
training dataset version
hyperparameters
```

must be recorded.

Deterministic components should not use uncontrolled randomness.

---

# 53. Logging

Logs must explain important pipeline decisions.

Example:

```text
RELIANCE
Trend Template: PASS
RS: 84
VCP: A+
Data Quality: VERIFIED
Fundamental: SUPPORTIVE
Final Score: 87.4
```

For rejected candidates:

```text
HDFCBANK
Rejected:
Trend Template condition #6 failed
```

Avoid useless logs such as:

```text
processing...
done...
```

without diagnostic value.

---

# 54. Error Handling

Do not catch all exceptions and continue silently.

Bad:

```python
try:
    ...
except Exception:
    pass
```

Good:

```text
capture
classify
log
recover if safe
surface if unsafe
```

---

# 55. Fail Safe for Signals

If critical market data is invalid:

```text
do not emit a trading signal
```

The system should fail toward:

```text
NO SIGNAL
```

rather than:

```text
GUESS
```

---

# 56. API Rate Limits

Respect provider limits.

Agents must not implement aggressive uncontrolled concurrency.

Use:

```text
rate limiter
retry
exponential backoff
provider-specific concurrency
```

where appropriate.

---

# 57. Credentials

Never commit:

```text
API keys
access tokens
secrets
cookies
private credentials
```

Never put secrets into:

```text
logs
tests
fixtures
screenshots
documentation
```

Use environment variables or secret management.

---

# 58. Git Rules

Before modifying:

```text
git status
git branch
git log --oneline -n ...
```

when relevant.

Do not blindly reset or force-push.

---

# 59. Protect User Work

An AI agent must never run destructive commands without explicit need and appropriate safeguards.

Avoid casually using:

```bash
rm -rf
git reset --hard
git clean -fd
git push --force
```

If such operations are genuinely required, inspect the state first and explain the risk.

---

# 60. Commit Hygiene

Commits should be logically grouped.

Prefer:

```text
feat: add VCP contraction measurement
test: add VCP contraction fixtures
docs: update VCP specification
```

over one giant mixed commit.

---

# 61. No Fake Completion

An agent must never claim:

```text
implemented
tested
production-ready
verified
```

unless it actually performed the relevant work.

If something was not tested:

```text
Not tested: ...
```

must be stated.

---

# 62. No Fake Data

Never create fabricated market data to make a feature appear functional.

Synthetic data is acceptable only when explicitly marked:

```text
synthetic
fixture
mock
```

and never used as real production market data.

---

# 63. External Information

When implementation depends on current external provider behavior:

```text
search official provider documentation
```

before making assumptions.

This is particularly important for:

```text
Kite historical data
Dhan historical data
API limits
instrument endpoints
authentication
corporate-action availability
```

---

# 64. Documentation Updates

When an implementation changes architecture or externally visible behavior, update the relevant `.md` specification.

Documentation drift is considered a defect.

---

# 65. No Spec Duplication

Do not copy the entire same rule into multiple documents.

Prefer:

```text
one authoritative specification
```

and reference it elsewhere.

For example:

```text
VCP thresholds → VCP_SPECIFICATION.md
database structure → DATABASE_SCHEMA.md
data behavior → DATA_SPECIFICATION.md
```

---

# 66. UI Independence

Frontend/UI changes must not change strategy calculations.

The dashboard consumes:

```text
structured results
```

rather than implementing its own:

```text
VCP detection
Trend Template
scoring
```

---

# 67. Frontend Data Integrity

The UI must clearly distinguish:

```text
score
classification
data quality
data freshness
```

Do not visually imply that:

```text
Final Setup Score = probability
```

unless probability calibration has actually been implemented.

---

# 68. Alert Integrity

Breakout alerts should be based on deterministic conditions.

Example:

```text
pivot break
+
volume expansion
+
data quality valid
```

Do not let an LLM decide whether a breakout occurred.

---

# 69. Human-in-the-Loop

V1 is a:

```text
semi-automated assistant
```

The system:

```text
scans
ranks
explains
monitors
alerts
```

The user decides whether to place an order.

Broker execution is outside the initial core strategy.

---

# 70. No Automatic Trading by Accident

An agent must never add live order execution as a side effect.

Order execution requires:

```text
explicit architecture
explicit specification
explicit safety controls
explicit user authorization
```

---

# 71. Research vs Production

The codebase must distinguish:

```text
production scan
```

from:

```text
research experiment
```

A research experiment may intentionally use:

```text
revised data
alternative thresholds
experimental features
```

but it must be labeled accordingly.

---

# 72. Experimental Features

New ideas should initially live behind:

```text
feature flag
experimental module
research branch
```

Do not silently insert experimental logic into the production scoring path.

---

# 73. AI Agent Workflow

For a substantial task, agents should follow:

```text
1. Understand request
2. Inspect specifications
3. Inspect repository
4. Identify affected modules
5. Inspect tests
6. Form implementation plan
7. Implement smallest coherent change
8. Run targeted tests
9. Run broader tests
10. Inspect diff
11. Update documentation
12. Report exactly what changed
13. Report tests and known limitations
```

---

# 74. Before Coding Checklist

```text
[ ] What specification governs this task?
[ ] What existing code implements related behavior?
[ ] What callers depend on it?
[ ] What database schema is involved?
[ ] What configuration controls it?
[ ] What tests exist?
[ ] Could this introduce look-ahead bias?
[ ] Could this introduce survivorship bias?
[ ] Could this break provider abstraction?
[ ] Could this alter raw data?
```

---

# 75. During Coding Checklist

```text
[ ] Keep provider logic isolated
[ ] Keep strategy deterministic
[ ] Preserve data lineage
[ ] Avoid hidden constants
[ ] Handle nulls explicitly
[ ] Preserve existing interfaces where possible
[ ] Add tests for changed behavior
[ ] Avoid unnecessary refactoring
```

---

# 76. After Coding Checklist

```text
[ ] Run targeted tests
[ ] Run related integration tests
[ ] Run full suite where practical
[ ] Check lint/type errors
[ ] Inspect generated data
[ ] Inspect git diff
[ ] Verify documentation
[ ] Verify no secrets were added
[ ] Report limitations
```

---

# 77. Code Review Rules for AI Agents

When reviewing code, inspect for:

### Correctness

- wrong formulas;
- off-by-one errors;
- incorrect dates;
- look-ahead;
- null handling.

### Architecture

- provider leakage;
- circular dependencies;
- UI logic in strategy;
- database logic in feature calculations.

### Data

- missing observations;
- duplicate records;
- corporate actions;
- stale data;
- survivorship bias.

### Performance

- repeated full scans;
- unnecessary API requests;
- N+1 queries;
- excessive Python loops where vectorization is appropriate.

---

# 78. Performance Rule

Do not prematurely optimize.

First establish:

```text
correct
tested
measurable
```

Then optimize measured bottlenecks.

Do not replace a clear implementation with complicated optimization without evidence.

---

# 79. Parallelism

Parallel processing may be used for independent symbols, but must respect:

```text
provider rate limits
memory
DuckDB concurrency
disk I/O
```

Do not assume:

```text
more workers = faster
```

---

# 80. Caching

Cache deterministic expensive calculations when appropriate.

Cache keys should include all meaningful inputs.

Example:

```text
symbol
date range
feature version
config hash
data snapshot
```

Never return stale cached results merely because a cache entry exists.

---

# 81. Data Snapshot Awareness

When an agent adds a derived dataset, determine whether it needs:

```text
data_snapshot_id
```

If it affects historical reproducibility, the answer is usually yes.

---

# 82. Schema Migration Rule

When changing schema:

```text
1. update schema specification
2. create migration
3. update repositories
4. update tests
5. verify backward/upgrade behavior
```

Do not modify tables ad hoc.

---

# 83. API Contract Changes

When changing a public/internal API:

```text
identify callers
update interface
update implementation
update tests
update documentation
```

Avoid silent breaking changes.

---

# 84. CLI Changes

CLI behavior is an interface.

If adding or changing:

```text
argument
default
output
exit code
```

update:

```text
help text
tests
documentation
```

---

# 85. Error Messages

Error messages should identify:

```text
what failed
which instrument
which date/range
which provider
what the system expected
```

when relevant.

Bad:

```text
Error
```

Better:

```text
Kite historical request returned no candles for RELIANCE
for 2026-09-25 → 2026-09-28.
```

---

# 86. Observability

Production runs should make it possible to answer:

```text
What did the system scan?
What data did it use?
Which provider supplied it?
What configuration was active?
Why did a stock pass?
Why did it fail?
Was the data complete?
```

---

# 87. Explainability

Every candidate should eventually be explainable from structured measurements.

Example:

```text
VCP:
- contractions: 3
- depths: 18.2%, 10.1%, 6.4%
- final contraction: 6.4%
- volume dry-up: 42%
- pivot: ₹...
```

The explanation should be generated from these measurements.

The LLM must not invent missing measurements.

---

# 88. Agent Must Prefer Evidence

When uncertain:

```text
inspect
measure
test
verify
```

rather than:

```text
guess
```

If evidence is unavailable, explicitly state:

```text
unknown
not verified
requires implementation
```

---

# 89. Agent Must Not Hide Ambiguity

If a specification has two possible interpretations:

```text
identify the ambiguity
choose only if existing architecture resolves it
otherwise ask or document the assumption
```

Do not silently select a materially different interpretation.

---

# 90. Decision Records

Important architectural decisions should be recorded.

Examples:

```text
ADR-001 provider abstraction
ADR-002 DuckDB + Parquet
ADR-003 VCP deterministic detector
ADR-004 fundamentals secondary
```

Use ADRs when a decision has long-term architectural consequences.

---

# 91. Strategy Rule Changes

Any change to:

```text
Trend Template
VCP detection
classification
score weighting
RS
fundamental gates
breakout criteria
```

must be treated as a strategy change, not merely a code refactor.

Require:

```text
spec update
tests
version
research/backtest consideration
```

---

# 92. Threshold Changes

Changing:

```text
25% EPS growth
70 RS
8% final contraction
12% final contraction
```

is not a harmless constant change.

It changes strategy behavior.

Record:

```text
old value
new value
reason
config/version
```

---

# 93. Backtest Before Claiming Improvement

An agent must not claim:

```text
better
more accurate
higher quality
institutional grade
```

based solely on code inspection.

Such claims require measurable evidence.

---

# 94. Avoid Data Snooping

When experimenting with thresholds, avoid repeatedly optimizing against the same test period.

Maintain:

```text
development period
validation period
out-of-sample period
```

where practical.

---

# 95. Labeling for Future ML

If generating labels for ML, define:

```text
observation timestamp
entry definition
forward horizon
outcome definition
```

before training.

Never let future outcomes leak into feature generation.

---

# 96. AI-Generated Code Review

AI-generated code is not trusted merely because it compiles.

Agents must inspect:

```text
edge cases
error paths
data types
performance
security
tests
```

Compilation is not validation.

---

# 97. Dependency Management

Before adding a package, evaluate:

```text
Is it necessary?
Is there already an equivalent dependency?
Is it maintained?
Does it increase deployment complexity?
Does it create licensing concerns?
```

Avoid dependency bloat.

---

# 98. Security

Never introduce:

```text
eval
exec
shell injection
unsafe deserialization
hard-coded secrets
unvalidated SQL
```

without a compelling, reviewed reason.

Prefer safe parsers and parameterized operations.

---

# 99. File and Path Safety

Use controlled paths for:

```text
data
logs
exports
cache
reports
```

Do not allow user-controlled paths to escape intended directories.

---

# 100. Time and Date Safety

Never use:

```text
datetime.now()
```

inside historical calculations when the calculation should use:

```text
as_of_date
```

Historical functions should receive explicit dates.

---

# 101. Current-Date Dependence

Production scans may use current market date.

Research functions should prefer:

```python
as_of_date
```

over implicit current time.

This is critical for reproducibility.

---

# 102. Market Calendar

Do not assume:

```text
every weekday = trading day
```

Use an exchange-aware calendar or validated trading-date dataset.

---

# 103. Time-Series Alignment

When joining datasets:

```text
price
volume
fundamentals
RS
market regime
```

ensure alignment is based on information availability, not simply matching calendar dates.

---

# 104. Joining Fundamentals

Never do:

```sql
JOIN fundamentals ON period_end <= scan_date
```

without considering publication availability.

The correct conceptual condition is:

```text
available_at <= scan_date
```

and then select the latest valid observation.

---

# 105. Joining Corporate Actions

Corporate actions must use the appropriate effective/ex dates according to the adjustment methodology.

Do not apply future corporate actions to earlier observations.

---

# 106. Agent Handling of External APIs

When implementing a provider:

```text
1. Read official documentation
2. Confirm endpoint behavior
3. Confirm limits
4. Confirm response fields
5. Confirm authentication
6. Build adapter
7. Add integration tests
```

Do not invent endpoint names or response schemas.

---

# 107. Provider Documentation Drift

External APIs can change.

Keep provider-specific assumptions isolated so they can be updated without touching:

```text
VCP
Trend Template
Scoring
Database contracts
Frontend
```

---

# 108. Offline Capability

The strategy and research engine should work using the local dataset without live provider access.

This enables:

```text
backtesting
debugging
development
historical research
```

without requiring API availability.

---

# 109. Production Mode

Production scan should enforce stricter rules than exploratory research.

Production:

```text
strict data-quality gates
strict freshness
known configuration
known algorithm version
auditable snapshot
```

Research:

```text
may tolerate experimental conditions
```

but must be explicitly labeled.

---

# 110. Agent Output Format

When completing a development task, an AI agent should report:

```text
1. What changed
2. Files changed
3. Why it changed
4. Tests executed
5. Test results
6. Documentation updated
7. Known limitations
8. Follow-up recommendations
```

Do not provide vague statements such as:

```text
Done, everything works.
```

---

# 111. Example Good Completion Report

```text
Implemented VCP contraction measurement.

Changed:
- patterns/vcp.py
- patterns/models.py
- tests/test_vcp.py
- VCP_SPECIFICATION.md

Behavior:
- detects swing highs/lows;
- calculates contraction depth;
- validates progressive contraction;
- stores measurements.

Tests:
- 31 VCP tests passed;
- full suite not executed.

Limitation:
- corporate-action-adjusted fixtures are not yet covered.
```

---

# 112. Example Bad Completion Report

```text
Done.
VCP is now institutional grade.
Everything is tested.
```

This is unacceptable unless the claims have actually been demonstrated.

---

# 113. When to Ask the User

Ask for clarification when:

```text
the requested behavior conflicts with architecture
the specification is materially ambiguous
a destructive operation is required
a strategy decision is needed
a provider behavior is unknown and cannot be verified
```

Do not ask unnecessary questions when the existing specification clearly determines the answer.

---

# 114. Agent Autonomy

Agents may independently decide:

```text
file organization
function decomposition
test structure
internal naming
implementation details
```

when those choices do not alter externally observable strategy behavior or architecture.

---

# 115. Agent Non-Autonomy

Agents must not independently decide to change:

```text
strategy definition
VCP classification criteria
Trend Template gates
score semantics
fundamental weight cap
point-in-time rules
provider source of truth
database architecture
live trading behavior
```

without the relevant design decision being explicit.

---

# 116. Priority of Rules

When instructions conflict, use:

```text
1. Explicit user requirement
2. Project specification
3. Database/data contracts
4. Existing stable architecture
5. Tests
6. Implementation convenience
```

If a conflict cannot be resolved safely, surface it.

---

# 117. Definition of Production-Grade

The phrase "production-grade" should mean:

```text
tested
observable
reproducible
recoverable
auditable
data-quality controlled
versioned
maintainable
```

It does NOT simply mean:

```text
large codebase
many features
complex architecture
microservices
AI-powered
```

---

# 118. Avoid Premature Microservices

The project is a modular monolith.

Do not split modules into services merely because:

```text
microservices sound institutional
```

Introduce a service only when there is a concrete reason such as:

```text
independent scaling
independent deployment
isolation
resource-heavy workloads
operational boundary
```

---

# 119. Containerization

Containerization may be introduced once the architecture is stable.

Do not containerize everything prematurely if it makes development harder without providing a current benefit.

---

# 120. Future Service Boundaries

Potential future services:

```text
heavy historical ingestion
ML training
large backtests
alert worker
```

But these remain future architectural options unless required.

---

# 121. Frontend Rule

The Bloomberg-style dashboard is a presentation layer.

It should display:

```text
market state
watchlist
VCP candidates
scores
charts
measurements
fundamentals
alerts
data quality
```

It must not become a second strategy engine.

---

# 122. Charting Rule

Charts should visualize canonical/derived measurements.

Do not calculate a different VCP definition in JavaScript/TypeScript merely for visualization.

The frontend should receive structured annotations such as:

```text
pivot
contraction boundaries
pivot price
base high
base low
breakout state
```

---

# 123. Alert Rule

Alerts must reference:

```text
scan/run ID
instrument
data snapshot
trigger timestamp
trigger measurements
```

This makes an alert auditable after the fact.

---

# 124. Research Export

Exports should preserve:

```text
as_of_date
data_snapshot_id
config_hash
algorithm_version
```

CSV alone is insufficient for full reproducibility.

---

# 125. Agent Must Preserve Auditability

When adding a new result, ask:

```text
Can we later explain where this number came from?
```

If not, add lineage.

---

# 126. Agent Must Preserve Raw Measurements

If a score depends on:

```text
A
B
C
```

store:

```text
A
B
C
```

not only:

```text
score
```

This allows future recalculation and debugging.

---

# 127. Strategy Changes Must Be Rebuildable

Changing a VCP threshold should allow:

```text
recompute classifications
```

without requiring:

```text
redownload market data
```

unless the underlying data itself changed.

---

# 128. Derived Data Rebuildability

Derived datasets should be rebuildable from lower-level canonical data.

Example:

```text
VCP results
```

must be rebuildable from:

```text
canonical OHLCV
+
feature algorithm
+
VCP configuration
```

---

# 129. No Hidden State

Avoid strategy behavior depending on:

```text
global mutable variables
local machine state
previous run side effects
undocumented cache
```

unless explicitly designed and persisted.

---

# 130. Deterministic Scan

For the same:

```text
data snapshot
config
algorithm versions
```

a scan should produce the same result.

This is a core acceptance criterion.

---

# 131. Agent Self-Check Before Finalizing

Before claiming completion, ask internally:

```text
Did I change strategy behavior?
Did I preserve point-in-time correctness?
Did I preserve provider abstraction?
Did I add tests?
Did I update the specification?
Did I introduce hidden assumptions?
Did I verify external API behavior?
Did I accidentally use future data?
Did I create a data-quality blind spot?
```

---

# 132. Final Golden Rules

Every AI agent working on this project must follow these rules:

```text
1. Inspect before changing.
2. Never guess data behavior.
3. Never fabricate market data.
4. Never introduce look-ahead bias.
5. Never introduce survivorship bias silently.
6. Preserve raw provider data.
7. Keep providers behind interfaces.
8. Keep strategy deterministic.
9. Keep fundamentals secondary.
10. Preserve underlying measurements.
11. Separate no-signal from no-data.
12. Make important parameters configurable.
13. Enforce safety caps in code.
14. Version strategy changes.
15. Hash resolved configuration.
16. Use point-in-time snapshots.
17. Test meaningful changes.
18. Never disable tests to hide failures.
19. Never claim verification without performing it.
20. Never silently change architecture.
21. Never allow UI logic to become strategy logic.
22. Never allow LLM output to become authoritative market data.
23. Never add live trading as an accidental side effect.
24. Prefer simple architecture with strong boundaries.
25. Optimize only after measuring.
26. Preserve auditability.
27. Preserve reproducibility.
28. Fail safely when critical data is unreliable.
29. Update documentation when architecture changes.
30. When uncertain, use evidence rather than assumptions.
```

---

# 133. Agent Contract

By operating on this repository, an AI coding agent should behave as:

```text
an implementation assistant
+
a code reviewer
+
a test engineer
+
a data-integrity guardian
```

It should not behave as:

```text
an autonomous strategy designer
```

unless the user explicitly requests strategy research or design.

The final authority for strategy decisions remains the project owner.

---

# 134. End State

The desired development environment is:

```text
                    ┌────────────────────┐
                    │      HUMAN          │
                    │ strategy decisions  │
                    └─────────┬──────────┘
                              │
                              ▼
                    ┌────────────────────┐
                    │ SPECIFICATIONS     │
                    │ architecture/rules │
                    └─────────┬──────────┘
                              │
                              ▼
              ┌──────────────────────────────┐
              │       AI CODING AGENTS       │
              │                              │
              │ implement                    │
              │ test                         │
              │ review                       │
              │ document                     │
              │ diagnose                     │
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │     DETERMINISTIC ENGINE     │
              │                              │
              │ data → trend → VCP → score  │
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │ REPORT / DASHBOARD / ALERTS  │
              └──────────────────────────────┘
```

The goal is not to build an AI that independently invents or changes the trading strategy.

The goal is to use AI agents to build and maintain a **deterministic, auditable, institutionally structured research and screening system** whose strategy behavior remains under explicit human and specification control.