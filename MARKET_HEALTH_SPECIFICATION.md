# Market Health Score 2 — Specification

Status: built 2026-10-12 (owner-approved plan of 2026-10-12). **Display, research and a shadow
traffic light only.** Nothing here feeds a scan, a score, a strategy, the paper ledger or the
regime rule; those stay frozen until the April 2027 review, where the owner decides whether the
score earns a role.

Code: `src/vcp_scanner/market_health/` (settings, features, scoring, build), `vcp health build`,
daily step "market health", table `market_health_daily` (DATABASE_SCHEMA §86), API field `score`
on `GET /market/health`, the Market health score card and the score tile (FRONTEND_SPECIFICATION
§67.32). Settings: `config/market_health.yaml` — every weight, ladder and threshold lives there.

## 1. Sources

| Use | Source |
|---|---|
| Index trend, distribution and accumulation days (with NSE's own volume), the downtrend cap, the divergence cap | NIFTY 500 (`index_daily`) |
| Confirmation | NIFTY 50, Nifty Smallcap 250 (`index_daily`) |
| Breadth, leadership, breakouts, VQI | our universe, rebuilt per day (§2) |
| Sector participation | 18 NSE sector indices (`SECTOR_INDEX`) |

## 2. One rule over all history

Every session since prices begin (1 Jan 2021) is computed the same way, from stored data only:

- **Universe** per day: EQ series that day, raw close ≥ ₹20, 20- and 50-day average traded value
  ≥ ₹1 crore, 253 sessions of history (UniverseConfig). ASM/GSM membership has no history before
  1 Oct 2026, so the rebuilt universe keeps those stocks (about 70 today). Checked on 30 Sep 2026,
  before the surveillance filter: rebuilt 1,346 eligible / 271 Trend Template / 208 leaders
  against the scan's 1,338 / 265 / 205.
- **RS rank** per day: rs-1.1.0 weights over 63/126/189/252 sessions, percentile among that day's
  eligible stocks, 1–99.
- **Trend Template** per day: the scan's ten conditions (TrendTemplateConfig), high/low basis.
- **Leaders**: Trend Template pass with RS 80+.
- **Breakouts** (pillar E): a Trend Template stock closing above its prior 50-day high (the pivot)
  on 1.4× its 50-day average volume, the pivot unchanged for 10 sessions before (a base). Our
  strategies' own breakouts exist only from 31 Aug 2026, so the pillar uses this one rule over all
  history; their own failure rate stays on the Setups and breakouts card. A breakout counts on the
  day its result is known (10 sessions later): no day uses a later close.

The first scorable day is the first with 300+ eligible stocks (early 2022). Index averages need
200 sessions of NIFTY 500; earlier days have that pillar missing (§5).

## 3. Pillars (weights 25 / 25 / 20 / 15 / 15)

Each reading scores 0–100 on a ladder (straight lines between the configured points, flat
outside); a pillar is the weighted mean of its readings that could be measured.

**A. Index trend (25)** — NIFTY 500 above its 50 / 150 / 200-day average (20 each); averages
stacked 50 > 150 > 200 (20, half for one of the two); the 200-day higher than 20 sessions ago
(10); NIFTY 50 above its 50-day (5); Smallcap 250 above its 50-day (5).

**B. Breadth (25)** — share above the 50-day (20), 150-day (15), 200-day (15) average, each 30% → 0
to 70% → 100; net advances over 20 sessions as a share of all moves (15); new 52-week highs /
(highs + lows), 10-session averages (15; none either way is neutral, 50); change in the share above
the 50-day over 10 sessions (10); VQI minus NIFTY 500 over 21 sessions — participation broadening
or narrowing (10).

**C. Leadership (20)** — share passing the Trend Template (25); leaders vs 20 sessions ago (20);
share at a 20-day high (10) and a 52-week high (10), 10-session averages; sector indices in an
uptrend — above their own 50-day with a positive 1-, 3- and 6-month return, averaged (20); the
leaders of 20 sessions ago followed forward vs VQI (15). The constant RS ≥ 80 share (≈19% by
construction) is not used.

**D. Accumulation / distribution (15)** — distribution days in 25 sessions (down 0.2%+ on higher
volume), NIFTY 500 60% and VQI 40% (30); NIFTY 500 accumulation days (20); traded value of
advancing / declining stocks over 10 sessions (20); clustering: NIFTY 500 distribution days in
the last 10 (20); accumulation share of the volume days (10).

**E. Breakout environment (15)** — of breakouts judged in the last 20 sessions: 5% above the
pivot 10 sessions later (30); no close below the pivot in 5 sessions (25); a close 3% below it
within 10 (20); median 5- and 10-session return (15); plus stocks set up near a pivot — Trend
Template, within 5% of the 50-day high, 20-day volatility below 50-day (10). Fewer than 20 judged:
the pillar is missing.

## 4. Labels and the shadow traffic light

Raw score = weighted mean of the pillars measured. Labels: Strong 80+, Constructive 65–79,
Mixed 50–64, Weak 35–49, Adverse below 35. Shadow light by the label after the caps: Strong /
Constructive → full size, Mixed → half, Weak / Adverse → no new buys. Logged in
`market_health_daily.exposure`; no trading effect.

## 5. Caps and missing data

The raw score is kept and shown; the **label** is no better than the worst cap that holds:

| Cap | Condition | At most |
|---|---|---|
| downtrend | NIFTY 500 below a falling 200-day and < 30% of stocks above their 50-day | Weak |
| distribution | 5+ NIFTY 500 distribution days in 10 sessions | Mixed |
| failures | > 60% of 10+ judged breakouts closed below the pivot within 5 sessions | Weak |
| tt_collapse | Trend Template passes down a third or more in 10 sessions | Mixed |
| divergence | NIFTY 500 within 3% of its 52-week high, A/D line more than a quarter of its 52-week range below its own high | Constructive |
| incomplete | a pillar could not be measured (its weight is shared by the others) | Mixed |

A breadth thrust (Zweig, on our universe: the 10-day advancers share from below 40% to above
61.5% within 10 sessions) is shown as an event, never averaged in.

## 6. Storage and runs

`vcp health build` computes every session (~6 s) and stores the days not yet stored for the
current settings version; a stored day keeps what it said that day. `--rebuild` replaces the
version's days. The settings version is `<version>+<hash of the settings>`, so a change of any
weight or threshold starts a new version and the old days stay comparable to themselves.

## 7. Calibration (2026-10-12)

Plan: tune on 7 Jan 2022 – 30 Jun 2024 (613 sessions) only, then look once at 1 Jul 2024 –
9 Oct 2026 (566 sessions). Measured against what follows each day: VQI's and NIFTY 500's return
over the next 20 sessions, and the share of breakouts of the next 20 sessions that reached 5%
above the pivot (the pillar E rule).

| Label after caps | In-sample days | Breakout success | Out-of-sample days | Breakout success | NIFTY 500 next 20 |
|---|---|---|---|---|---|
| Strong | 63 | 37% | 13 | 32% | −2.3% |
| Constructive | 264 | 43% | 174 | 38% | −0.4% |
| Mixed | 98 | 36% | 135 | 35% | −0.1% |
| Weak | 93 | 38% | 134 | 36% | −0.7% |
| Adverse | 95 | 41% | 110 | 27% | +1.9% |

Findings:
- In sample no pillar relates to what follows by more than a correlation of 0.21 (accumulation /
  distribution with breakout success); the others are between 0.01 and 0.08. Moving weights on that
  would fit noise, so **no setting was changed**: the plan's weights and thresholds stand.
- The score is **not a forecast of the index**: out of sample its correlation with NIFTY 500's next
  20 sessions is −0.23 (Adverse days were followed by rebounds, as at most lows).
- Out of sample it does sort the **breakout environment**: correlation 0.21 with breakout success,
  and Adverse days were followed by the weakest breakouts (27% against 35–38%). This is the use the
  score is for — how much to trust new breakouts — and it is modest.
- The owner decides at the April 2027 review whether the shadow light earns any role; until then
  it is logged only.
