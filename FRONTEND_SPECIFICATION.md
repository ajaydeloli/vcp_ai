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
