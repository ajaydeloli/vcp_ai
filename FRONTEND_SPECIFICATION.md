# FRONTEND_SPECIFICATION.md

**Project:** Minervini AI  
**Purpose:** Frontend architecture and UI specification  
**Status:** Design Specification  
**Version:** 1.0  
**UX Direction:** Modern institutional equity-research terminal  
**Theme:** Dark-first, Bloomberg-inspired, modernized

---

# 1. Purpose

This document defines the frontend architecture, page structure, information hierarchy, visual language, component system, interaction model, and frontend/backend data contracts for Minervini AI.

The frontend is a presentation and research interface. It is **not a second strategy engine**.

The deterministic backend remains authoritative for:

- Trend Template evaluation
- VCP detection and classification
- Relative Strength
- volume analysis
- fundamental scoring
- final setup scoring
- breakout detection
- data-quality decisions

The frontend visualizes those results and provides navigation, filtering, research, monitoring, and reporting workflows.

---

# 2. Design Goals

The frontend must be:

1. Fast
2. Information-dense
3. Modern
4. Auditable
5. Responsive
6. Keyboard-friendly
7. Config-aware
8. Data-quality-aware
9. Suitable for daily trading research
10. Suitable for historical research
11. Easy for AI agents to maintain
12. Independent of strategy implementation details

The UI should feel like a professional trading/research terminal rather than a consumer finance application.

---

# 3. Primary User Workflow

```text
Open Dashboard
      ↓
Understand Market State
      ↓
Open Screener
      ↓
Filter / Rank Candidates
      ↓
Select Stock
      ↓
Review Chart
      ↓
Review VCP
      ↓
Review Trend Template
      ↓
Review Fundamentals
      ↓
Review Score
      ↓
Add to Watchlist
      ↓
Monitor
      ↓
Receive Breakout Alert
      ↓
Human Trading Decision
```

The system does not place trades in V1.

---

# 4. Core Architecture

```text
Browser
   │
   ▼
Frontend Application
   ├── Routing
   ├── UI Components
   ├── State Management
   ├── Query / Cache Layer
   ├── Charting
   └── Formatting
   │
   ▼
Backend API
   ├── Screener API
   ├── Stock Analysis API
   ├── Market API
   ├── Watchlist API
   ├── Alert API
   ├── Research API
   └── Report API
   │
   ▼
Deterministic Strategy Engine
   │
   ▼
DuckDB / Parquet / Repository Layer
```

The frontend must never directly access DuckDB, Parquet, Kite, or Dhan.

---

# 5. Recommended Technology Direction

Preferred stack:

```text
Next.js
TypeScript
React
Tailwind CSS
shadcn/ui or equivalent component primitives
TanStack Query
TradingView-compatible / Lightweight Charts
Zod or equivalent runtime API validation
```

Exact libraries may change; the architectural principles must remain.

---

# 6. Frontend Module Structure

```text
frontend/
├── app/
│   ├── dashboard/
│   ├── screener/
│   ├── watchlist/
│   ├── market/
│   ├── stock/
│   ├── vcp/
│   ├── trend-template/
│   ├── fundamentals/
│   ├── alerts/
│   ├── backtest/
│   ├── research/
│   ├── reports/
│   └── settings/
│
├── components/
│   ├── layout/
│   ├── navigation/
│   ├── cards/
│   ├── tables/
│   ├── charts/
│   ├── vcp/
│   ├── trend-template/
│   ├── fundamentals/
│   ├── scores/
│   ├── alerts/
│   └── data-quality/
│
├── lib/
│   ├── api/
│   ├── formatting/
│   ├── validation/
│   └── constants/
│
├── hooks/
├── types/
└── styles/
```

---

# 7. Global Layout

Desktop:

```text
┌─────────────────────────────────────────────────────────────┐
│ Top Bar                                                     │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│ Sidebar      │ Main Content                                 │
│              │                                              │
│ Navigation   │ Page                                         │
│              │                                              │
│ Data Status  │                                              │
│              │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

Recommended sidebar width:

```text
220–240px
```

Collapsed:

```text
64–72px
```

---

# 8. Top Bar

Contains:

```text
Minervini AI
Global Search
Market Indicators
Market Status
Current Date/Time
User Menu
```

Example:

```text
MINERVINI AI
[ Search symbol or company... ]

NIFTY 50   25,698 +0.68%
NIFTY 500  23,412 +0.52%
SENSEX     84,731 +0.61%

Market Open
```

Global search supports:

- NSE symbol
- company name
- ISIN
- watchlist search
- recent stocks

Keyboard shortcut:

```text
/
```

---

# 9. Sidebar Navigation

```text
Dashboard
Screener
Watchlist
Market Overview
Stock Analysis
VCP Scanner
Trend Template
Fundamentals
Alerts
Backtest
Research & Notes
Reports
Settings
```

Visually distinguish:

- active page
- unread alerts
- watchlist count
- data/system warnings

---

# 10. Global Data Status

The frontend must display data freshness.

```text
DATA STATUS

● Market Data       Live
● Fundamentals      1h ago
● Universe          1h ago
● Last Scan         03:30 PM
```

Possible states:

```text
LIVE
FRESH
STALE
DEGRADED
ERROR
UNKNOWN
```

Stale data must not look identical to live data.

---

# 11. Dashboard

Route:

```text
/dashboard
```

Purpose: high-level market and setup overview.

KPI cards:

```text
A+ VCP Setups
VCP Setups
Forming Bases
Breakout Watch
Today's Scan
Market Breadth
```

Each card may contain:

- current value
- change
- timestamp
- small sparkline
- click-through

Top setup tabs:

```text
Top Setups
A+ VCP
VCP
Forming
Breakout Watch
Custom Screen
```

Table:

```text
Rank
Symbol
Company
VCP
Score
RS
Pivot
Price
Status
Change
```

Market breadth:

```text
Stage 1
Stage 2
Stage 3
Stage 4
New Highs
New Lows
Advancers
Decliners
Volume Expansion
Volume Contraction
```

---

# 12. Screener

Route:

```text
/screener
```

Primary candidate-discovery workspace.

Filters:

```text
Universe
Sector
Industry
Market Cap
Price
Average Traded Value
VCP Classification
Setup Score
RS Rating
Trend Template
Contraction Count
Final Contraction
Pivot Distance
Volume Dry-up
Fundamental Support
Data Quality
```

Table columns:

```text
Rank
Symbol
Company
Price
Change
VCP
Score
Trend
RS
Volume
Fundamental
Pivot
Distance to Pivot
Status
Data Quality
```

Interactions:

- sorting
- filtering
- column visibility
- row selection
- multi-select
- export
- pinning
- keyboard navigation
- pagination or virtual scrolling

Filters should be URL-shareable where practical.

---

# 13. Stock Analysis

Route:

```text
/stock/[symbol]
```

Core research page.

Header:

```text
RELIANCE
Reliance Industries Ltd

Energy · Large Cap · NIFTY 50

₹1,236.40
+₹22.35 (+1.84%)

[A+ VCP]

Score 91
RS 88
Pivot ₹1,250

☆ Watchlist
🔔 Alert
```

Tabs:

```text
Chart
VCP Analysis
Trend Template
Fundamentals
Score Breakdown
Events
Notes
```

---

# 14. Chart

Primary chart:

```text
Candlesticks
+
SMA 50
+
SMA 150
+
SMA 200
+
Volume
+
VCP annotations
+
Pivot
+
Breakout markers
+
Corporate-action markers
```

Time controls:

```text
1D
1W
1M
3M
6M
1Y
3Y
5Y
MAX
```

VCP annotations are supplied by the backend:

```text
T1
T2
T3
...
Pivot
Base High
Base Low
Breakout
Failed Breakout
Volume Expansion
```

The frontend must not independently calculate VCP measurements.

---

# 15. VCP Analysis

Display:

```text
Classification
Contraction Count
Contraction Depth
Contraction Duration
Progression
Volume Dry-up
Base Depth
Base Duration
Pivot Price
Current Price
Distance to Pivot
Tightness
```

Example:

```text
VCP PATTERN                    A+

T1        18.2%     42d      Valid
T2        10.1%     36d      Valid
T3         6.4%     28d      Valid

Base Depth              18.8%
Base Duration           106d
Final Contraction        6.4%
Volume Dry-up             42%
Pivot                 ₹1,250
Distance                 1.2%
```

Classification:

```text
A+ VCP
VCP
VCP-like
Not Valid
```

Include a tooltip explaining that classification comes from the deterministic VCP engine.

---

# 16. Trend Template

Route:

```text
/trend-template
```

Individual stock view:

```text
Trend Template: 8/8 PASS
```

Checklist:

```text
✓ Price > SMA 150
✓ Price > SMA 200
✓ SMA 150 > SMA 200
✓ SMA 200 rising
✓ SMA 50 > SMA 150
✓ Price > SMA 50
✓ 52-week low condition
✓ RS Rating ≥ threshold
```

Show actual values beside each condition.

---

# 17. Fundamentals

Route:

```text
/fundamentals
```

Display:

```text
EPS YoY Growth
EPS QoQ Growth
Sales Growth
EPS Acceleration
Margin Expansion
ROE
ROCE
Debt / Equity
Data Availability
Reporting Date
Available At
```

The UI must distinguish:

```text
Period End
Publication / Availability Date
```

Missing data is:

```text
N/A
```

not zero.

---

# 18. Fundamental Data Quality

Example:

```text
Fundamental Data
● Current
```

or:

```text
Fundamental Data
⚠ Stale
Last updated: ...
```

Fundamentals remain secondary supportive data.

---

# 19. Score Breakdown

Display:

```text
FINAL SETUP SCORE
91 / 100
```

Breakdown:

```text
Trend Template       94   30%
VCP Pattern           91   30%
Volume Analysis       86   15%
Relative Strength     88   15%
Fundamentals          82   10%
```

Audit drawer:

```text
Configuration Version
Configuration Hash
Scoring Version
Algorithm Version
Data Snapshot
```

The UI must label this:

```text
Setup Score
```

not:

```text
Probability
Success Probability
Win Probability
```

unless probability calibration has explicitly been implemented.

---

# 20. Watchlist

Route:

```text
/watchlist
```

Support:

- multiple watchlists
- add/remove symbol
- priority
- notes
- tags
- current score
- VCP state
- pivot distance
- breakout status
- last update

Example:

```text
My VCP Watchlist

RELIANCE      A+     91     1.2% to pivot
HDFCBANK      A+     89     0.6% to pivot
ICICIBANK     A+     87     2.2% to pivot
```

---

# 21. Breakout Watch

Route:

```text
/alerts/breakout
```

Statuses:

```text
Forming
Pivot Ready
Near Pivot
Breakout
Breakout Confirmed
Failed Breakout
```

Breakout confirmation is backend-defined, for example:

```text
pivot crossed
+
volume expansion
+
valid data quality
```

---

# 22. Alerts

Route:

```text
/alerts
```

Types:

```text
Pivot Approaching
Breakout
Volume Expansion
New A+ VCP
VCP Classification Change
Data Quality Warning
Provider Failure
```

Each alert contains:

```text
timestamp
symbol
trigger
values
scan/run ID
data snapshot
```

---

# 23. Market Overview

Route:

```text
/market
```

Sections:

```text
Index Overview
Market Breadth
Stage Distribution
Sector Strength
New Highs/Lows
Volume Conditions
VCP Environment
Breakout Environment
```

Index cards:

```text
NIFTY 50
NIFTY 500
SENSEX
```

Future support:

```text
sector rotation
market regime
relative-strength heatmap
```

---

# 24. VCP Scanner

Route:

```text
/vcp
```

Sections:

```text
A+ VCP
VCP
VCP-like
Invalid / Rejected
```

Diagnostic filters:

```text
contraction count
depth progression
final contraction
volume dry-up
pivot distance
base duration
base depth
```

---

# 25. Backtest

Route:

```text
/backtest
```

Configuration:

```text
Universe
Start Date
End Date
VCP Version
Scoring Version
Configuration
Entry Definition
Exit Definition
Forward Horizon
```

Results may include:

```text
Observations
Average Forward Return
Median Forward Return
Hit Rate
Expectancy
Maximum Drawdown
Profit Factor
```

Only display metrics supported by the implemented methodology.

---

# 26. Research

Route:

```text
/research
```

Support:

```text
experiments
parameter comparisons
threshold studies
VCP classification studies
score-weight studies
out-of-sample analysis
```

Every experiment identifies:

```text
dataset
date range
config
algorithm version
```

---

# 27. Reports

Route:

```text
/reports
```

Reports:

```text
Daily Scan Report
Weekly Market Report
VCP Candidate Report
Breakout Report
Research Report
Data Quality Report
```

Exports where implemented:

```text
PDF
CSV
JSON
```

Each report identifies:

```text
date
report type
scan ID
data snapshot
config hash
generation timestamp
```

---

# 28. Settings

Route:

```text
/settings
```

Sections:

```text
Strategy
Scoring
VCP
Trend Template
Data Providers
Data Quality
Universe
Alerts
Display
System
```

Sensitive credentials must never be rendered back into the UI.

---

# 29. Configuration UI

Configuration controls show:

```text
Current Value
Default Value
Allowed Range
Description
Impact
```

Example:

```text
RS Minimum
Current: 70
Allowed: 50–95
Default: 70

Controls the minimum relative-strength threshold
for the Trend Template gate.
```

Safety caps are enforced by backend validation.

---

# 30. Reusable Data-Quality Components

Reusable:

```text
<DataQualityBadge />
```

States:

```text
Verified
Fresh
Stale
Incomplete
Degraded
Invalid
Unavailable
```

Each state has:

- icon
- tooltip
- timestamp where relevant
- explanation where relevant

Do not communicate status using color alone.

---

# 31. Loading / Error / Empty States

Use skeleton loaders and progressive loading rather than blank pages.

Error example:

```text
Market data unavailable

The latest local snapshot is 3 trading days old.
Signals are disabled until fresh data is available.

[View Data Status]
```

Empty example:

```text
No A+ VCP setups found

Try:
- expanding the universe
- lowering the minimum RS threshold
- viewing VCP-like formations
```

Do not imply "no setups" when the actual state is "no data."

---

# 32. Responsive Design

Desktop is the primary target.

Support:

```text
Desktop
Laptop
Tablet
Mobile
```

Mobile behavior:

- collapse sidebar
- stack multi-column layouts
- preserve key metrics
- horizontally scroll large tables
- collapse secondary panels

The full institutional dashboard is optimized for desktop.

---

# 33. Keyboard Navigation

Suggested shortcuts:

```text
/       Global search
g d     Dashboard
g s     Screener
g w     Watchlist
g m     Market
g a     Alerts
Esc     Close modal
```

Exact shortcuts may change, but keyboard navigation should be intentional.

---

# 34. Visual Design System

Direction:

```text
Background: near-black / dark navy
Surface: dark blue-gray
Primary: electric blue
Positive: green
Warning: amber
Critical: red
VCP: violet / blue
Text: high-contrast neutral
Muted: gray-blue
```

Use subtle borders and restrained animation.

Avoid excessive gradients and glassmorphism.

---

# 35. Typography

Preferred:

```text
Inter
Geist
IBM Plex Sans
```

Use tabular-number styling for:

```text
Price
Score
RS
Pivot
Percentages
```

Numbers should align cleanly in tables.

---

# 36. Reusable Component System

Core components:

```text
AppShell
Sidebar
TopBar
SearchCommand
MarketTicker
KpiCard
DataQualityBadge
ScoreBadge
VcpBadge
TrendStatus
PriceChange
StockHeader
StockTable
ScreenerFilters
ChartPanel
VcpAnalysisCard
TrendTemplateCard
FundamentalCard
ScoreBreakdown
AlertCard
WatchlistButton
```

Components should remain presentation-focused.

Bad:

```text
VcpCard calculates contraction depth
```

Good:

```text
VcpCard receives contraction measurements
```

---

# 37. API Data Contract

Frontend APIs return structured objects.

Example:

```json
{
  "symbol": "RELIANCE",
  "price": 1236.40,
  "change_pct": 1.84,
  "vcp": {
    "classification": "A_PLUS",
    "contractions": [
      {
        "label": "T1",
        "depth_pct": 18.2,
        "duration_days": 42,
        "valid": true
      }
    ],
    "pivot_price": 1250.0,
    "distance_to_pivot_pct": 1.2
  },
  "trend_template": {
    "passed": true,
    "conditions_passed": 8,
    "conditions_total": 8
  },
  "scores": {
    "trend": 94,
    "vcp": 91,
    "volume": 86,
    "rs": 88,
    "fundamentals": 82,
    "final": 91
  }
}
```

The backend API specification remains authoritative for exact schemas.

---

# 38. API Validation and Caching

Validate API responses at runtime where practical.

Invalid responses should produce controlled error states.

Use query caching for:

```text
market overview
stock metadata
screener results
fundamentals
historical chart data
watchlists
```

Cache keys must include relevant filters, dates, and configuration where applicable.

---

# 39. Real-Time Updates

Polling or real-time updates may be used for:

```text
prices
breakout alerts
market status
```

VCP calculations should not be recomputed on every UI repaint.

---

# 40. Chart Data Contract

Chart endpoint should provide:

```text
timestamp
open
high
low
close
volume
sma_50
sma_150
sma_200
```

Optional annotations:

```text
pivot
contraction
breakout
corporate_action
```

Chart data must identify:

```text
exchange
timezone
candle timeframe
adjustment mode
data snapshot
```

The UI must not silently mix adjusted and raw prices.

---

# 41. Corporate Actions in UI

Stock analysis should expose:

```text
Split
Bonus
Dividend
Symbol Change
```

Chart annotations may mark these events.

Users can open event details.

---

# 42. Security Identity

Internally prefer:

```text
security_id / ISIN
```

as stable identity.

Symbol is a display/search identifier and may change.

Historical pages must resolve previous symbols.

---

# 43. AI Explanation Layer

Future LLM-generated explanations may appear under:

```text
AI Analysis
```

and must be clearly labeled.

The explanation must be generated from structured backend facts.

Example:

> RELIANCE qualifies as A+ VCP because three measured contractions progressively tightened from 18.2% to 10.1% to 6.4%, with qualifying volume contraction and price within 1.2% of the pivot.

The LLM must not invent measurements.

---

# 44. AI Restrictions

The AI explanation layer must not:

- modify scores
- modify VCP classification
- override gates
- invent financial data
- claim certainty
- claim probability without calibration
- place trades

---

# 45. Accessibility

Required:

- keyboard navigation
- visible focus states
- sufficient contrast
- semantic labels
- screen-reader-friendly controls
- accessible table headers
- non-color-only status indicators

---

# 46. Performance Targets

Initial targets:

```text
Dashboard first meaningful render: <2s locally
Screener interaction: <300ms excluding network
Stock navigation: <1s cached
Chart interaction: smooth 60fps where hardware permits
```

These are targets to measure, not assumptions.

---

# 47. Large Dataset Handling

Do not load the entire NSE universe into the browser unnecessarily.

Use:

```text
server-side filtering
server-side sorting
pagination
virtualization
```

for large datasets.

---

# 48. Frontend Security

Never expose:

```text
Kite API secret
Dhan secret
database credentials
server credentials
```

Browser code receives only authorized API data.

---

# 49. Authentication

Initial deployment may be single-user/private.

Future support may include:

```text
local authentication
reverse-proxy authentication
OIDC
```

Authentication changes must not require strategy changes.

---

# 50. Frontend Testing

Test categories:

```text
unit tests
component tests
API contract tests
end-to-end tests
visual regression tests
```

Priority:

```text
screener
stock page
VCP display
score display
filters
watchlist
alerts
```

Tests must verify that frontend displays backend values correctly.

The frontend must not independently recompute the final score.

---

# 51. End-to-End Workflow

At minimum, once features exist:

```text
Open dashboard
→ open screener
→ filter A+ VCP
→ select stock
→ view chart
→ inspect VCP
→ inspect Trend Template
→ inspect fundamentals
→ inspect score
→ add watchlist
→ create alert
```

---

# 52. Frontend/Backend Ownership

Backend owns:

```text
truth
calculations
classification
scores
data quality
timestamps
versions
```

Frontend owns:

```text
presentation
navigation
interaction
filter state
visualization
layout
```

---

# 53. Mandatory No-Duplication Rules

Never implement in the frontend:

```text
VCP detector
Trend Template engine
scoring engine
RS calculation
corporate-action adjustment
```

if the authoritative backend implementation already exists.

Never call:

```text
Kite
Dhan
NSE
```

directly from the frontend for strategy data.

---

# 54. Dashboard Information Hierarchy

Prioritize:

```text
1. Market state
2. Candidate discovery
3. Setup quality
4. VCP structure
5. Trend Template
6. Breakout proximity
7. Fundamentals
8. Research/audit details
```

The VCP structure must not be buried beneath secondary information.

---

# 55. Design Principle: Dense but Calm

The interface should be information-dense without becoming noisy.

Use:

```text
clear hierarchy
small number of accent colors
consistent spacing
compact tables
high-quality charts
```

Avoid:

```text
giant cards
excessive animation
large decorative graphics
unnecessary gradients
```

---

# 56. Design Principle: Evidence Before Decoration

Every visual element should answer a research question.

Useful:

```text
VCP chart annotation
pivot distance
score decomposition
data freshness
```

Unnecessary:

```text
decorative 3D chart
```

---

# 57. Design Principle: Auditability

Users should eventually be able to answer:

```text
Why is this stock ranked here?
Why is this an A+ VCP?
Which Trend Template conditions passed?
Where did the fundamental number come from?
Which data snapshot was used?
Which configuration produced this score?
```

Important numbers must be traceable to backend data.

---

# 58. Design Principle: No False Precision

Examples:

```text
Fundamental data stale → show stale
Provider unavailable → show unavailable
Incomplete VCP → show forming/incomplete
```

Do not display a polished score when critical inputs are missing.

---

# 59. Design Principle: Signal Safety

If the backend marks:

```text
DATA_NOT_READY
STALE_DATA
INVALID_DATA
```

the frontend must visibly communicate that the result is not signal-ready.

---

# 60. Future Extensions

Architecture should leave room for:

```text
ML confirmation
LLM research assistant
portfolio monitor
trade journal
broker integration
paper trading
intraday breakout alerts
sector analytics
market regime models
```

These remain modular additions.

---

# 61. Explicitly Out of Scope for V1

```text
live order execution
full portfolio management
social features
multi-user collaboration
complex options analytics
crypto trading
AI autonomous trading
```

---

# 62. Frontend Acceptance Criteria

```text
[ ] Dashboard exists
[ ] Screener exists
[ ] Stock analysis exists
[ ] VCP visualization exists
[ ] Trend Template visualization exists
[ ] Fundamental visualization exists
[ ] Score breakdown exists
[ ] Watchlist exists
[ ] Breakout alerts exist
[ ] Market overview exists
[ ] Data-quality status exists
[ ] Configuration/audit metadata is accessible
[ ] Backend remains the sole strategy authority
[ ] No provider SDK is imported by frontend
[ ] API contracts are validated
[ ] Core workflows have tests
```

---

# 63. Recommended Build Order

```text
Phase F0  Frontend foundation
          Routing, theme, layout, navigation

Phase F1  API client
          Types, validation, errors, loading states

Phase F2  Dashboard

Phase F3  Screener

Phase F4  Stock Analysis

Phase F5  Chart + VCP visualization

Phase F6  Trend Template + Fundamentals

Phase F7  Score Breakdown

Phase F8  Watchlist

Phase F9  Alerts / Breakout Watch

Phase F10 Market Overview

Phase F11 Research / Backtest

Phase F12 Reports

Phase F13 Performance / accessibility / visual polish
```

---

# 64. Final Architecture

```text
                         ┌───────────────────────┐
                         │        USER           │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │    MINERVINI AI UI    │
                         │                       │
                         │ Dashboard             │
                         │ Screener              │
                         │ Stock Analysis        │
                         │ VCP                   │
                         │ Trend Template        │
                         │ Fundamentals          │
                         │ Alerts                │
                         │ Research              │
                         └───────────┬───────────┘
                                     │
                                   API
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │      BACKEND API      │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │ DETERMINISTIC ENGINE  │
                         │                       │
                         │ Data                  │
                         │ Universe              │
                         │ Trend Template        │
                         │ VCP                   │
                         │ RS                    │
                         │ Volume                │
                         │ Fundamentals          │
                         │ Scoring               │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │ DuckDB + Parquet      │
                         │ + Provider Adapters   │
                         └───────────────────────┘
```

The central rule is:

> **The frontend explains and visualizes the strategy; it does not implement the strategy.**

---

# 65. Final UX Direction

Minervini AI should feel like a:

**modern institutional equity-research terminal**

combining:

- Bloomberg-style information density
- modern dark UI
- TradingView-style charts
- compact professional tables
- strong VCP visualization
- transparent score decomposition
- visible data quality
- point-in-time auditability
- fast keyboard-driven workflows

The design should support the actual research process rather than simply make the application look sophisticated.

---

# 66. End-State Workflow

```text
             MARKET OVERVIEW
                    │
                    ▼
                SCREENER
                    │
                    ▼
             TOP VCP SETUPS
                    │
                    ▼
             STOCK ANALYSIS
                    │
        ┌───────────┼────────────┐
        ▼           ▼            ▼
       VCP       TREND       FUNDAMENTALS
        │         TEMPLATE         │
        └───────────┬──────────────┘
                    ▼
              SCORE BREAKDOWN
                    │
                    ▼
                WATCHLIST
                    │
                    ▼
             BREAKOUT WATCH
                    │
                    ▼
                  ALERT
                    │
                    ▼
             HUMAN DECISION
```

The system is therefore a **decision-support and research terminal**, not an autonomous trading interface.

---

# 67. Dashboard v1 for the monitoring phase (signed off 2026-10-06)

The owner asked (2026-10-06, signed off the same day: W1–W4 all as recommended, §67.8) for a web dashboard that calls an API, in the style of his mockup (dark theme: KPI cards, a candidates / breakouts table, a candlestick chart with the base and pivot, setup details, recent activity, market overview). It replaces the static HTML reports of STRATEGY_SPECIFICATION §21.4 (step M4) and is the first, narrow slice of PROJECT_DESIGN Phase 10. The rest of this document stays the long-term target; this section is what is built now.

## 67.1 Principles

- **Read-only.** Neither the API nor the page writes anything; the frozen strategies and the paper ledger are untouched (STRATEGY_SPECIFICATION §21.1).
- **Honest data only.** Every number comes from our database; nothing the database does not have is shown or imitated. Lists are labelled a watch list, not trade instructions; the page footer says "Research tool, not financial advice".
- **Local.** The API listens on `127.0.0.1:8000`, the page on `localhost:3000`, on this PC only (Windows reaches WSL's localhost). No login, no remote access, no alerts in v1.
- The frontend never reads DuckDB directly (§4).

## 67.2 Database access: a serving copy

DuckDB does not let another process read while the daily run writes. The daily run's last step copies the database (after its checkpoint) to `data/serving/vcp_serving.duckdb` (replaced atomically: write to a temporary file, then rename). The API opens only that copy, read-only, and re-opens it when the file changes. During the evening run the dashboard shows the previous evening's data, with its time stamped in the status bar. The copy is a cache: never backed up, never committed.

## 67.3 API v1 (`/api/v1`, GET only, JSON, FastAPI)

| Endpoint | Returns |
|---|---|
| `/status` | latest prices date, latest scan date per strategy, serving copy time, last daily-run summary line, paper ledger through, newest backup's age; the warnings of STRATEGY_SPECIFICATION §21.5 |
| `/summary?date=` | the KPI cards: universe size, Trend Template passers, grade 2+ setups per strategy, breakouts that day, mean score of the top 10 per strategy |
| `/market?days=250` | per day: breadth (share above the 50-day average), the equal-weight universe index and its 50-day average, regime on/off |
| `/strategies` | the five strategies: version, config hash, stage, ranked tiers |
| `/setups?strategy=&date=&min_grade=&status=` | the ranked list: symbol, company, classification, grade, status, score and its parts, RS rank, pivot, distance to pivot, stop, base start / end / length, breakout date |
| `/setups/overlap?date=` | stocks with an eligible setup in more than one strategy |
| `/stocks/{symbol}/bars?days=260` | adjusted OHLCV with the 20/50/200-day averages |
| `/stocks/{symbol}/setups?date=` | each strategy's setup for the stock on that date with its marks (VCP contractions; flat base / 3WT / cup / double-bottom points from `details_json`), Trend Template conditions, weekly stage |
| `/activity?days=7` | breakouts, paper entries and exits, scans and daily runs, newest first |
| `/search?q=&limit=` | symbol search for the top bar: symbol prefix or company-name match, exact symbol first (added in D2) |
| `/paper` | per strategy: closed trades, win rate, average, profit factor, open positions (entry, stop, last, open %), skipped, divergences; the review criteria of §21.6 with progress |

Pydantic response models; every response carries `as_of` and `data_time`. Missing values are `null`, never 0 (AGENTS.md rule 4).

## 67.4 Page v1 (`/dashboard`)

Layout after the owner's mockup (dark theme, left navigation, top bar with symbol search and the data date):

- **KPI cards:** universe scanned, grade 2+ setups (all strategies; per strategy on hover), breakouts today, market regime (on/off with breadth %), paper positions open.
- **Setups table** with one tab per strategy plus "Breakouts" and "On several lists": symbol, score, grade badge, status, RS rank, pivot, distance to pivot, stop, base length, breakout date; sortable; a row opens the stock in the chart panel.
- **Chart panel** (TradingView Lightweight Charts): candles, volume, 20/50/200-day averages, the selected strategy's marks (base start, pivot line, stop line, points, breakout), range buttons.
- **Setup details** beside the chart: the strategy's measurements and score parts, Trend Template pass/fail, weekly stage.
- **Recent activity** (from `/activity`) and **Market overview** (breadth, our universe index vs its 50-day average, regime days on in the last 20 sessions; labelled "our NSE universe", not NIFTY).
- **Paper panel:** per strategy results so far against the review criteria, open positions.
- **Status bar:** data date, serving copy time, warnings.

Left-navigation items other than Dashboard (Screener, Watchlist, Backtests, Reports) are shown disabled with "later" until built.

## 67.5 Stack and running it

- API: FastAPI + uvicorn (added to `pyproject.toml`), package `vcp_scanner.api`; `vcp api serve`.
- Web: `frontend/` (Next.js, TypeScript, Tailwind, TanStack Query, Lightweight Charts, Zod for response checks), built with the Node 18 already installed.
- `scripts/dashboard.sh` starts both; the owner opens `http://localhost:3000`. Starting it at login is the owner's choice (not automatic in v1).

## 67.6 Steps

| Step | Content |
|---|---|
| D0 | This section (docs only) |
| D1 | Serving copy in the daily run; API v1 with tests (fixture database); `vcp api serve` (built 2026-10-06, §67.9) |
| D2 | `frontend/`: the dashboard page against the API; component and API-contract tests; `scripts/dashboard.sh`; screenshot check (built 2026-10-07, §67.10) |
| D3+ | Later, each specified first: stock page, screener filters, watchlist (needs writes: own decision), backtest and paper history pages, phone access (needs login and network setup) |

## 67.7 Not in v1

NIFTY / BANKNIFTY quotes (no index data); sector and market-cap filters (no such data in `instruments`); quotes of named traders; interactive screening that runs scans from the page; any write; alerts; remote access.

## 67.8 Decisions (owner, 2026-10-06: all as recommended)

| # | Question | Options | Decided |
|---|---|---|---|
| W1 | Stack | (a) FastAPI + Next.js as in §5 · (b) FastAPI serving one plain HTML/JS page | (a): FastAPI + Next.js (TypeScript, Tailwind, TanStack Query, Lightweight Charts, Zod) |
| W2 | Database access | (a) serving copy refreshed by the daily run · (b) the API reads the main database directly and shows "busy" during the run | (a): `data/serving/vcp_serving.duckdb`, refreshed atomically as the daily run's last step; the API opens only that copy, read-only |
| W3 | v1 scope | (a) the dashboard page of §67.4 only · (b) also a full stock page and screener now | (a): one page done well first |
| W4 | Theme | (a) dark, after the mockup · (b) light · (c) both with a switch | (a), the switch later |

## 67.9 As built: API v1 (step D1, 2026-10-06)

- **Serving copy** (`vcp_scanner.serving`): `vcp run daily --serving-copy` adds a last step that checkpoints the main database, copies it to `<db folder>/serving/vcp_serving.duckdb` under a `.partial` name, checks that the copy opens, then renames it over the old one. A failed copy fails the run (named in the summary line) and leaves the previous copy in place. **Opt-in, off by default** until the owner agrees; the 2.2 GB copy takes about 9 s.
- **API** (`vcp_scanner.api`, FastAPI): the endpoints of §67.3 under `/api/v1`, GET only (other verbs: 405), CORS for `http://localhost:3000` and `http://127.0.0.1:3000` only. `vcp api serve [--host 127.0.0.1] [--port 8000] [--data-dir data] [--serving-db PATH]`. Logs and backups are read from the main database's folder (`--data-dir`).
- **The copy is opened read-only and re-opened when the file is replaced**, once no request still reads the old one (DuckDB keeps one instance per path in a process, so a plain re-connect would keep showing the old file). A missing copy gives a 503 naming the command that writes it.
- **Strategies and hashes** are read from `config/` at start-up: the five paper strategies with their config hashes (equal to the frozen ones of STRATEGY_SPECIFICATION §21.1; a test pins them) and tiers. `/setups` shows the ranked list (`eligible=true`, the default; `eligible=false` adds the other scored rows), best score first, with optional `min_grade`, `status`, `limit`; `date` defaults to the strategy's latest scan.
- **`/market`** uses the repository's breadth query and the frozen regime of `config/backtest.yaml` (`breadth50`), so it agrees with the paper ledger; the equal-weight index is rebased to 100 at the first day shown and labelled "our NSE universe ... not NIFTY".
- **`/stocks/{symbol}/bars`**: adjusted bars with 20/50/200-day averages computed from the closes (null until that many bars exist; `technical_features_daily.ema_*` are not used). **`/stocks/{symbol}/setups`**: per strategy the stored setup, VCP contractions or the detector's points (`details_json`, drawn on the bar's high or low as in the chart review), score parts, and the Trend Template conditions and weekly stage.
- **`/status` warnings** (STRATEGY_SPECIFICATION §21.5): no new prices for 2 or more sessions (weekdays not recorded as non-sessions; today counts after 20:00 IST), a strategy without a scan for the newest scan date, a paper ledger behind that date, no backup or the newest older than 7 days.
- **`/paper`**: `paper/status.summarize` over the ledger rows, with the §21.6 criteria. Closed trades, profit factor and average trade are judged only from 30 closed trades; **portfolio drawdown and return against the equal-weight index are `null` in v1** (not computed yet; they belong to the weekly summary).
- **Missing is null**: no company name is stored for most instruments (`instruments.company_name`), so `company` is often `null`; a strategy with no closed trade has `null` win rate and average, not 0.

## 67.10 As built: dashboard page (D2, 2026-10-07)

- **`frontend/`**: Next 15 (App Router), React 19, TypeScript strict, Tailwind (dark theme), TanStack Query (5-minute refetch, no refetch on window focus), Lightweight Charts v4, Zod. `/` redirects to `/dashboard`. The browser calls `/api/v1/*` on port 3000; Next forwards it to `VCP_API_URL` (default `http://127.0.0.1:8000`).
- **Every response is checked by a Zod schema**; a response that does not match shows an error box, not a wrong number. Missing values show "—", never 0.
- **Panels** as §67.4: KPI cards, setups table (tab per strategy, Breakouts, On several lists; sortable, missing values last), chart with 20/50/200-day averages and marks (pivot and stop lines, base and breakout markers; marks on days without bars are dropped), setup details with the unmet rules per tier, Trend Template, recent activity, market overview, paper panel, status bar. Left navigation: only Dashboard is active.
- **Honest labels**: "our NSE universe" (not NIFTY), lists are a watch list, "No real money", "Research tool, not financial advice" in the status bar.
- **API addition**: `GET /api/v1/search` (§67.3) for the top-bar search.
- **Tests**: 62 frontend tests (Zod contract against committed real API samples in `frontend/tests/fixtures/api/`, kept equal to the API by `tests/api/test_contract_samples.py`; format and chart-series functions; components; the page with a fetch mock and a chart-library mock). A real-data check with the browser pane on a copy of the main database.
- **Run**: `scripts/dashboard.sh` starts `vcp api serve` and the web server; open `http://localhost:3000/dashboard`.
- **Not changed**: any strategy, rule, scan, score, label or the ledger (read-only).

## 67.11 As built: layout after the second mockup (D2.2, 2026-10-07)

The owner's second mockup (nine panels in a fixed grid) replaced the first layout. Built as shown, with these honest differences:

- **Top row:** search; **our own equal-weight universe index** (value, day change, 60-day line; not NIFTY); the data date. NIFTY 50, NIFTY 500 and SENSEX tiles come with live data (step D4), because no index data is stored.
- **KPI tiles (6):** A+ VCP setups, VCP setups, Forming bases, Breakout watch (all counted from the ranked VCP list: classification A_PLUS_VCP, VCP; status FORMING; status PIVOT_READY or BREAKOUT), Today's scan (symbols, scanned, Trend Template passers), Market breadth (donut of our universe above / below its 50-day average, with the regime). The mockup's "new today" deltas, sparklines and Stage 1 to 4 breadth are not shown: they are not computed.
- **Watch list card** (left) with a **dropdown** of lists (D2.3): Top setups, A+ VCP, VCP, VCP like, Forming, Breakout watch (cuts of the VCP ranking), one entry per other strategy and On several lists; columns #, Symbol, Company, Setup, Score, RS, Pivot, Price, Status, Change; **ten rows a page with page numbers**. Sector and index filters are not shown (no such data); the star and Add to Watchlist buttons are not shown (a watchlist needs writes).
- **Chart card** (right, separate): symbol, company, price and change, setup badge and status, Score / RS rank / Pivot, strategy chips, candles with averages, marks, pivot and stop.
- **Setup overview row (4 cards):** VCP pattern (contractions with drop %, days peak to trough, tighter or wider than the one before; base depth and duration, pivot, current price, distance to pivot, stop and the rules not met), Trend Template (checklist), Fundamentals (**not available**: every value shown as a dash), Score breakdown (a ring with one arc per score component filled by its points out of its maximum, the final score in the middle).
- **Below:** Recent activity, Market overview, Paper trading, as before. Navigation items other than Dashboard stay disabled ("later"); the Data status box shows prices, last scan and the data copy time.
- No API change; all numbers come from the endpoints of §67.3.

## 67.12 Serving copy switched on (2026-10-07)

Owner go (2026-10-07): `scripts/daily_run.sh` now passes `--serving-copy`, so every scheduled run (19:15 and 22:00 IST) and every manual run of the script ends by refreshing `data/serving/vcp_serving.duckdb`. The copy is atomic: the previous copy stays in place if the refresh fails, and a failed refresh marks the run `FAILED: dashboard serving copy` in `data/logs/daily_runs.log` (the data steps are unaffected). Cost: about 9 seconds and 2.3 GB (4.6 GB for a moment during the swap). `vcp run daily` called by hand without the flag still writes no copy.


## 67.13 As built: Screener page (2026-10-07)

Owner go: "go with screener"; own design, built-in presets only (no CSV export). Route `/screener`, API `GET /api/v1/screener`.

**API.** One row per stock of the newest Trend Template scan (1,275 on 2026-10-06): weekly stage, Trend Template pass, conditions passed of 10, near-52-week-high, RS rank, trend score, close and day change, and the stock's VCP setup (class, grade, status, score, distance to pivot, eligible) where it has one. Filters (all optional, combined with AND): `q`, repeated `stage`, `tt_pass`, `near_high`, `min_rs`, `min_conditions`, `has_setup` (ranked setups only), `min_grade`, `status`; `sort` (9 columns) with `direction`; `page` and `page_size` (default 25, max 100); `strategy` (default `vcp`) and `date`. Values the database lacks are `null` and sort last. The response also gives `scanned`, `total` and the stage counts of the whole scan. GET only; the serving copy is opened read-only.

**Page.** Preset buttons (All scanned stocks, Trend Template passers, Stage 2 RS 80+, Near 52-week high, Pivot-ready setups, Breakouts, Top VCP setups) set a fixed combination of filters; nothing is saved. Filter panel on the left, sortable results table with page numbers on the right; a symbol (and the top-bar search) opens `/stocks/X`, the Stock Analysis page (§67.14). Not available: sector, market cap, fundamentals, index membership (no data).

**Rule kept.** Screening only reads the scan; it changes no strategy, rule, scan, score, label or ledger.

## 67.14 As built: Stock Analysis page, first design (2026-10-07)

Owner request: "first design the page, then I will review ... then update as I propose". Route `/stocks/SYMBOL` (`/stocks` asks for a search). Reached from the new navigation entry, the top-bar search, and a "Full analysis" link in the dashboard chart card.

**Layout.** Chart card with the stock's header (price, change, grade, status, score, RS rank, pivot) and the strategy buttons; the four overview cards of the dashboard (VCP pattern, Trend Template with the 10 conditions, Fundamentals marked not available, score ring); then two cards: Setups across strategies (one line per strategy: grade, status, score, pivot, stop; a click shows that strategy on the chart) and History and paper trades (breakouts and paper-ledger events of the stock over 180 days).

**API.** One addition, `GET /api/v1/stocks/{symbol}/history?days=180` (1-400): the activity feed cut to the symbol, scan events left out. No other new query.

**Not on the page:** sector, market cap, news, live prices (no data yet); anything that writes. First design: layout is open to the owner's review.

**Default stock (2026-10-07).** `/stocks` without a symbol opens the best-ranked VCP setup (the first row of the VCP list, as on the dashboard); a search shows the page for any other stock.

## 67.15 As built: Watchlist page, first design (2026-10-07)

Owner request: design the watch list page. Route `/watchlist`.

**Decision taken for the first design (open to review):** "my watch list" is kept in the browser (localStorage), not on the server. Nothing is written to the main or serving database and no API call writes, so the read-only rule of §67 holds; the cost is that the list belongs to one browser and is lost if its site data is cleared. A server-side list would need a write path and an owner decision.

**Page.** A ★ button (Screener rows, the Stock Analysis header) adds or removes a stock; the page also has an add box (search by symbol or company). The table shows each listed stock with its latest scan result in the Screener's columns (stage, Trend Template, RS, price, change, VCP setup, score, status, distance to pivot). A listed symbol missing from the latest scan is named under the table.

**API.** The Screener endpoint takes `symbols` (comma-separated, case-insensitive) to return only those stocks.

## 67.16 Page roles changed: market-overview dashboard (2026-10-07)

Owner request, after the multi-page mockup sheet: the dashboard shows the overall market; its watch list moves into the Screener; the stock chart and setup information live on the Stock Analysis page; the Watchlist page holds lists the owner makes. This supersedes the dashboard layout of §67.10 and §67.11 (the cards themselves are reused).

**Dashboard.** KPI tiles (A+ VCP, VCP, forming bases, breakout watch, today's scan, market breadth), Market stage (the scanned stocks by weekly stage, from the Trend Template scan), Market overview (our equal-weight universe index, breadth, regime), Top VCP setups (five best, linking to Stock Analysis), Recent activity, Paper trading. The search box opens Stock Analysis.

**Screener (absorbs the old watch list).** Strategy selector; presets Top setups, A+ VCP, VCP, VCP like, Forming, Pivot ready, Breakouts, On several strategies, plus the Trend Template presets; new filter Setup class; new Pivot column. API: `classification`, `min_strategies` (stocks ranked by that many strategies or more) and `pivot` in each row.

**Stock Analysis.** Chart, pattern, Trend Template, fundamentals (not available), score, strategies, history (§67.14), unchanged.

**Watchlist.** Several named lists made by the owner (tabs, + New watchlist, rename, delete with a confirmation step). A ★ on the Screener and Stock Analysis pages opens a small menu to put the stock in any list or in a new one. Lists stay in this browser only (§67.15); the first design's single list is carried over as "My watch list".

**From the mockup sheet, not built (no data or a later phase):** NIFTY/SENSEX/BANK NIFTY tiles and the index chart switch (live data, D4), sector strength, Alerts, Fundamentals, Backtest, Research, Reports, Data Quality and Settings pages.

**Index name and regime label (2026-10-07).** Our equal-weight index of the scanned stocks is shown as "VCP Universe Index" (still not NIFTY). On the breadth chart the 40 % threshold is a dashed line without its own label, because the label covered the latest value; the caption above the chart names the rule and the threshold.

## 67.17 As built: Market health card (2026-10-07)

Owner request: read the market the way Minervini does (price action first, then leadership, breadth and our own results) from what the dashboard already holds. `GET /api/v1/market/health`; a card under the KPI tiles. **Display only: nothing here feeds the regime rule, a scan, a score or a strategy.** No summary verdict (Healthy / Mixed / Weak) was added; that needs rules the owner approves.

**Basis.** Our scanned universe (the eligible members of the latest scan on each day), the same population as the VCP Universe Index and the breadth chart. Not NIFTY: index volume, NIFTY averages and sector groups need data we do not hold (D4 and later).

| Group | Reading | Green | Amber | Red |
|---|---|---|---|---|
| Index price action | Index vs its 50/150/200-day averages, 200-day line rising | above all three and 200-day rising | other | below the 200-day |
| | Distribution days in the last 25 sessions (index -0.2 % or worse on higher total volume than the day before) | 0-3 | 4-5 | 6+ |
| Leadership | New 52-week highs vs lows today | highs at least twice the lows | in between | lows above highs |
| | Failed breakouts: of the last 60 days' breakouts with 5 sessions behind them, the share that closed below their pivot within 5 sessions | up to 25 % | up to 50 % | above 50 % |
| | Leaders (Trend Template pass, RS 80+) vs the index over 20 sessions | ahead | behind by under 2 points | behind by 2+ |
| Breadth | Share above the 50-day average | 50 % + | 40-50 % | under 40 % |
| | Share above the 200-day average | 50 % + | 35-50 % | under 35 % |
| | Advance/decline line vs its 50-day average | above | | below |
| Feedback loop | Winners among the last 10 closed paper trades (all strategies) | 6+ | 4-5 | 0-3 |

Grey means too little data to read (fewer than 5 judged breakouts, fewer than 10 closed paper trades, not enough index history). The thresholds are display conventions, not strategy parameters, and may be changed here without a strategy-version bump.

### 67.17a Market health: four cards with charts (2026-10-07)

Owner request: the Top VCP setups card leaves the dashboard (the Screener holds that list), and Market health shows four separate cards, each with a chart. `GET /api/v1/market/health` now also returns `points` (last 250 sessions: index rescaled to 100, its 50/150/200-day averages, new 52-week highs and lows, % above the 50- and 200-day averages, advance/decline line and its 50-day average) and `trades` (return of the last 10 closed paper trades). Cards: Index price action (index with its three averages), Leadership (new 52-week highs vs lows), Breadth (% above 50/200-day; A/D line with its average), Feedback loop (bars of the last paper trades). A line with too little history for its average is not drawn. The readings and thresholds of 67.17 are unchanged; display only.

### 67.17b Market health charts trimmed (2026-10-07)

The Market overview card no longer repeats the index and breadth charts (they are in Market health); it keeps the regime, breadth and days-on figures. The index chart shows only the 50- and 200-day averages (the 150-day line was dropped from the chart and the API `points`; the status reading is unchanged). The breadth chart carries the regime's 40% dashed line. Chart lines carry no names; the legend below each chart names them and the axis shows each value in the line's colour.

### 67.17c Market overview merged into Market health (2026-10-07)

The Market overview card is gone. Its Regime, Breadth and "On, last 20" figures and the regime rule sit at the top of the Breadth card in Market health, above the breadth chart whose dashed line is the 40% switch. The dashboard rows are now Market stage + Paper trading, then Recent activity. No data or rule changed.

### 67.17d Feedback loop filled; stage order (2026-10-07)

The Feedback loop card also shows, from the paper ledger already served: closed trades toward the review per strategy (of 30), and the open paper positions with their gain or loss since entry. Market stage lists Stage 1, 2, 3, 4, then Transition. Display only.

### 67.17e Market health verdict (2026-10-08)

A banner at the top of the Market health card: a label and a score from 0 to 100. Display only: it feeds no rule, scan, score, strategy or the regime, and it is not a trading signal. Computed in the API (`health.py`, `_verdict`), returned as `verdict` with a `score` on each reading.

**Reading scores (0 to 100, straight lines, held flat at both ends; grey readings carry no score and are left out).**

| Reading | Score |
|---|---|
| Index vs its averages | 35 above the 200-day, 15 the 200-day rising, 20 above the 150-day, 20 above the 50-day, 10 the 50-day above the 150-day |
| Distribution days | 100 at 3 or fewer, 0 at 8 or more (20 points a day between) |
| New 52-week highs vs lows | share of highs among highs+lows: 0 at 30%, 100 at 70% |
| Failed breakouts | failure rate: 100 at 10% or less, 0 at 60% or more |
| Leaders vs the index | gap in points: 0 at -5, 100 at +5 |
| Above 50-day / above 200-day | share of the universe: 0 at 20%, 100 at 60% |
| Advance/decline line | distance from its 50-day average, against the line's range over 50 sessions (50 on the average) |
| Our paper trades | winners among the last 10 closed trades times 10 |

**Verdict.** Score = average of the scored readings, rounded. 70 or more Confirmed uptrend; 45 to 69 Uptrend under pressure; 25 to 44 Correction; under 25 Downtrend. Override: the index below its 200-day average is Downtrend whatever the score. The banner also lists the readings pulling the score down and holding it up. The colour dots and thresholds of 67.17 are unchanged. The regime figures are not counted (they repeat the 50-day breadth reading).

**Check on real data (2 week steps, 2026).** Downtrend from January to early April, Confirmed uptrend from mid-April to early September, Under pressure in late July and mid-September, Correction early October. The leaders and paper readings use the latest scan, so a back-test of them is not point-in-time.

## 67.18 As built: dashboard redesign (2026-10-08)

Owner request: the dashboard looked cluttered; new layout from the owner's mock-up. Display only; no rule, scan, score, strategy or ledger changes.

- **Top navigation, no permanent side panel.** A top bar on every page: the logo, then Dashboard, Screener, Stock Analysis and Watchlist (active page underlined), and the stock search. Clicking the logo opens a side bar with all pages; the pages still to come (VCP Scanner, Trend Template, Fundamentals, Alerts, Backtest, Research & Notes, Reports, Settings) are listed there, disabled. Escape or a click outside closes it.
- **Tile row** under the bar, each tile coloured: VCP Universe Index (with the day change and a 60-day line), Today's scan, NIFTY 50, SENSEX, and the Market health score as a half-circle gauge (0 to 100, in the colour of the verdict, with the label). NIFTY 50 and SENSEX say "Needs an index feed": no index data is stored, so no number is shown. The date tile, the setup-count tiles and the breadth donut are gone from the top.
- **Bottom bar** holds the data status (prices to, last scan, data copy), the warnings, and today's date (India time). The data status block left the side bar.
- **Market health** is one card with a coloured border holding five inner cards: Index price action and Leadership (charts), then Breadth (with the regime), Market stage and Feedback loop. Each reading is one line: dot, name, the figure (`short` from the API), its 0 to 100 score; the full sentence is the tooltip and a "Details" toggle. The verdict is one line under the title (score, label, what pulls it down and holds it up); the gauge is in the tile row.
- **Setups today**: a separate card with the four counts (A+ VCP setups, VCP setups, Forming bases, Breakout watch), then Paper trading and Recent activity.
- API: each reading in `/market/health` gains `short`.

### 67.18a Market health without the outer card (2026-10-08)

The coloured outer card is gone; the cards stand on their own in two columns: Index price action and Leadership; Breadth and Feedback loop; Market stage and Setups today. Setups today is laid out like Market stage: a headline ("Most setups are ...") and one bar per list (A+ VCP setups, VCP setups, Forming bases, Breakout watch) with its count, bars scaled to the largest list. The title line (date, score, what pulls it down and holds it up) and the note stay above and below the cards. Paper trading is full width under them.

### 67.18b Market health: no header, a border colour per card (2026-10-08)

The "Market health" heading and its line (date, score, what pulls it down) are gone. The score and label are in the gauge tile, and its tooltip lists what pulls the score down and holds it up. Each of the six cards has its own border colour with a faint tint: Index price action blue, Leadership green, Breadth violet, Feedback loop orange, Market stage cyan, Setups today pink. The note under the cards says the data is from our scanned stocks, not NIFTY.

### 67.18c Top bar and bottom row (2026-10-08)

Paper trading and Recent activity sit side by side in two columns. Today's date moved from the bottom bar to the far right of the top bar. The search box is now a magnifier icon in the top bar; clicking it opens the search box (Escape or choosing a stock closes it).

### 67.18d Index renamed (2026-10-08)

Our equal-weight index of the scanned stocks is now called **VCP Quality Index (VQI)** (was "VCP Universe Index"). Charts and legends label it "VQI"; the tile and the API label say "VCP Quality Index, VQI". It is still the equal-weight index of the stocks we scan, not NIFTY; the name does not mean a quality filter is applied.

### 67.18e Index price action card (2026-10-08)

The chart (VQI with its 50- and 200-day averages) is on the left; on the right, in the style of the owner's price-and-volume mock-up: Index vs 200-day average (x %, green above, red below), Accumulation days and Distribution days, each with "(last 25 sessions)". No score and no Details toggle on this card. An accumulation day is a session where the index rose 0.2 % or more on higher total volume than the day before (the mirror of a distribution day); volume is that of our scanned stocks, not NIFTY. API: `/market/health` gains `index_days` (`pct_from_200`, `accumulation`, `distribution`, `window`). The distribution reading and its score in the verdict are unchanged; accumulation days do not enter the verdict.

### 67.18f Market stage card: four stages over time

The Market stage card is laid out like Index price action, Leadership and Breadth: a chart on the left and a column of figures on the right. The chart draws, for the last 52 weeks, the share of the scanned stocks in Stage 1, 2, 3 and 4 (four lines). The figures give today's share and count for each stage. Transition (between stages) is not drawn; its count is stated under the chart.

The history is not stored. `GET /api/v1/market/health` rebuilds it (`stages`, one point a week) with the scan's own rule, `classify_weekly_stage` (stage-1.0.0, default StageConfig), from the stored daily adjusted closes: a week's close is the last close of that week, the current week uses the latest close. The stocks are those of the latest scan, so the chart shows how today's scan list moved through the stages, not who was in the scan then. The latest week equals the screener's stage counts exactly. Display only; read-only; nothing feeds a scan, score, label or ledger. The card moved beside Breadth; Feedback loop now sits above Setups today.

### 67.18g Equal cards, colours for Paper trading and Recent activity, universe changes in the feed

All eight dashboard cards (the six market cards, Paper trading, Recent activity) have the same height on wide screens (23 rem, the height of the Breadth card); what does not fit scrolls inside the card. Paper trading (orange) and Recent activity (lime) take their own border colours like the other six.

Recent activity now says which stocks joined or left the scan universe and why. `GET /api/v1/activity` adds three event kinds, read from the stored universe snapshots (the last snapshot of each day, a stock is in when its membership is eligible): `UNIVERSE` (one line a day, e.g. "Universe 1,275 to 1,271 stocks: 4 joined, 8 left"), `UNIVERSE_REMOVED` (the stock and its exclusion reason now, in plain words: traded value below the minimum, price below the minimum, ASM/GSM surveillance list, data problem, wrong series, short history, no recent trades, or no longer in the NSE list) and `UNIVERSE_ADDED` (the stock and the reason it was out before, or newly in the NSE list). Display only; nothing here changes a scan or the universe rules.
