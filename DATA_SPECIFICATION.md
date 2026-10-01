# DATA_SPECIFICATION.md

**Project:** Institutional-Grade Minervini VCP Scanner  
**Version:** 1.1  
**Status:** Architecture baseline (revised after design review)  
**Primary historical providers:** Kite Connect, Upstox  
**Future provider:** Dhan API  
**Local source of truth:** DuckDB + Parquet  

---

# 1. Purpose

This document defines the data requirements and ingestion contracts for the Minervini VCP system.

It answers:

- What market data is required?
- What historical depth is required?
- Which provider supplies which data?
- How does Kite become the primary provider?
- How will Dhan be added later?
- How is local historical data treated as the source of truth?
- How are raw, normalized, adjusted, and curated datasets separated?
- How are missing and stale records handled?
- How are corporate actions handled?
- How is point-in-time correctness preserved?
- How is provider disagreement handled?
- How can the entire dataset be reproduced and audited?

This document defines **data behavior**, not VCP detection rules. VCP rules belong in `VCP_SPECIFICATION.md`.

---

# 2. Core Data Principle

The scanner must not depend on a live API to perform normal analysis.

The intended architecture is:

```text
                    ┌──────────────┐
                    │     Kite     │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │     Dhan     │
                    │ future       │
                    └──────┬───────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │   INGESTION     │
                  │ provider-neutral│
                  └────────┬────────┘
                           ▼
                  ┌─────────────────┐
                  │ RAW DATA        │
                  │ immutable       │
                  └────────┬────────┘
                           ▼
                  ┌─────────────────┐
                  │ NORMALIZATION   │
                  │ actions/identity│
                  └────────┬────────┘
                           ▼
                  ┌─────────────────┐
                  │ CURATED DATA    │
                  │ canonical       │
                  └────────┬────────┘
                           ▼
                  ┌─────────────────┐
                  │ FEATURES        │
                  └────────┬────────┘
                           ▼
                  │ Trend / VCP / RS │
```

The local data lake is the source of truth.

---

# 3. Data Provider Abstraction

The strategy engine must never import a broker SDK.

Required interface:

```python
class MarketDataProvider(Protocol):

    def get_instruments(self) -> list[InstrumentRecord]:
        ...

    def get_historical_daily(
        self,
        instrument: ProviderInstrument,
        start: date,
        end: date,
    ) -> list[OHLCVBar]:
        ...

    def get_historical_intraday(
        self,
        instrument: ProviderInstrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[OHLCVBar]:
        ...
```

Provider-specific implementations:

```text
KiteProvider
DhanProvider
```

The application sees only:

```text
MarketDataProvider
```

---

# 4. Provider Priority

V1:

```text
LOCAL
  ↓
KITE
```

Future:

```text
LOCAL
  ↓
KITE
  ↓
DHAN
```

The fallback chain means:

1. Query local canonical data first.
2. Determine whether requested data is complete.
3. Fetch only missing/invalid ranges from the configured provider.
4. Validate returned data.
5. Persist raw provider observations.
6. Normalize.
7. Update canonical local data.
8. Retry the analysis.

The system must avoid repeatedly downloading data already present locally.

---

# 4A. Reference-Data Sources and Survivorship Status

Kite supplies OHLCV. It does not supply what a survivorship-free, corporate-action-correct dataset needs. Reference data therefore has its own sources, layered so that no single feed is trusted alone:

| Need | Primary | Secondary / cross-check | Status |
|---|---|---|---|
| Corporate actions | **NSE corporate-actions feed** (daily pre-market job) | **Upstox corporate actions by ISIN** | layering defined in §18A; both feeds still to be verified |
| Gap safety net | own detector on raw prices (§18A) | | to build |
| Security master, delistings, listing/symbol history | NSE (securities master, delisted-companies list) | BSE (later) | current listing: `EQUITY_L.csv`. Delistings: NSE "List of Companies Delisted from NSE" **implemented and verified (see below)**, but **partial**. Listing dates, symbol history and merger delistings: not covered |
| Historical raw OHLCV incl. delisted names | NSE bhavcopy archive (two formats: pre/post 8 Jul 2024) | Kite (incremental + cross-check) | candidate, **verify coverage and terms of use** |
| ASM/GSM/series history | NSE archive files | | partial coverage expected |

**NSE delisted-companies list (verified 2026-09-30, `NSEDelistedProvider`).** A single `.xlsx` linked from `nseindia.com/static/list/list-of-companies-proposed-to-be-delisted`; the file name changes with every revision, so the link is discovered from that page (`--delisted-file` accepts a manual download). Sheet `delisted`: Symbol, ISIN, Company Name, Board (Main Board / SME / ITP), Delisted Date (Excel serial), Type of Delisting (Compulsory / Voluntary / Liquidation / ITP exit / other). 457 rows, 2002-04-15 to 2026-09-02; 5 rows have no ISIN. Coverage limits, found by checking known cases against the file:

- **Mergers and amalgamations are absent** (HDFC Ltd, Mindtree, Gruh Finance, the 2019-20 PSU-bank mergers, ICICI Securities and others are not in it), so it is not a complete delisting record.
- Pre-2016 history is thin (0 to 17 rows per year), so early years are probably incomplete as well.
- No listing date and no series: the records carry `listing_date = NULL`, `series = NULL`, `valid_from = ` the ingestion start date, `valid_to = delisting_date`. A delisted name therefore has no series until a per-day source (bhavcopy) provides it, and the universe builder keeps it ineligible rather than assuming `EQ`.
- The same ISIN can appear twice (platform moves, symbol reuse); the ingestion keeps the latest delisting per ISIN. A delisted symbol that a different company now trades under is skipped, not merged into the live instrument.

Ingesting this list moves `survivorship_status` from `BIASED` to `PARTIAL`. It does not make results `POINT_IN_TIME_COMPLETE`, and it does not by itself put any delisted name into a universe: that needs their price history (row above, bhavcopy, still unverified).

Interfaces and rationale: PROJECT_DESIGN §14A. Every scan and backtest is stamped with `survivorship_status` (`POINT_IN_TIME_COMPLETE` | `PARTIAL` | `BIASED`). Results that are not `POINT_IN_TIME_COMPLETE` must not be used to validate thresholds or claim performance.

**Identity.** `instrument_id` is the permanent internal key. ISIN is a mapped attribute with history (`isin_history`), not the identity, because splits and consolidations can issue a new ISIN. Upstox actions are looked up by ISIN and resolved to `instrument_id` through that history (verify ISIN behavior on known splits during the spike).

---

# 5. Local Source of Truth

The scanner should use:

```text
DuckDB + Parquet
```

for normal reads.

Provider APIs are:

```text
ingestion sources
```

not:

```text
analysis databases
```

This provides:

- deterministic scans;
- faster repeated research;
- lower API dependency;
- historical reproducibility;
- provider switching;
- offline analysis;
- easier backtesting.

---

# 6. Required Market Data

The minimum market dataset is:

### Daily OHLCV

```text
Date
Open
High
Low
Close
Volume
```

Optional where available:

```text
Open Interest
VWAP
Provider metadata
```

For the VCP system, daily OHLCV is the critical dataset.

---

# 7. Historical Depth

Target:

```text
10+ years
```

of daily history for the eligible NSE equity universe.

The ingestion architecture must not hard-code exactly 10 years.

Configuration:

```yaml
data:
  history:
    minimum_years: 10
    target_years: 15
    maximum_available: true
```

If a security has less history because it listed later, retain the available history and record its listing date.

Do not fabricate missing history.

---

# 8. Why Long Historical Data Is Required

The historical dataset must include materially different market regimes.

Research should cover, where the instrument existed:

```text
2008
2020
2022
2023–2026
```

The purpose is not to assume these regimes repeat.

The purpose is to test whether the detection logic remains robust under different volatility, liquidity, and market environments.

---

# 9. Daily Data as Canonical Resolution

V1 VCP detection is based on:

```text
Weekly → Daily
```

Daily data is therefore the canonical market-data resolution.

Weekly candles should be derived from daily data.

Do not independently download weekly data and allow it to become inconsistent with daily history unless there is a specific validated reason.

---

# 10. Weekly Aggregation

Weekly bars should be deterministically derived from canonical daily bars.

Required:

```text
weekly_open
weekly_high
weekly_low
weekly_close
weekly_volume
```

Use the exchange trading calendar.

Do not aggregate based on arbitrary seven-calendar-day windows.

The exact week-ending convention must be configurable and tested.

---

# 11. Intraday Data

Intraday data is **not required for V1 VCP detection**.

Future purpose:

```text
breakout monitoring
intraday alerts
execution assistance
```

Potential future resolutions:

```text
1 minute
5 minute
15 minute
```

Do not build the VCP detector around intraday data in V1.

---

# 12. Instrument Universe

The target universe is:

```text
NSE listed equity securities
```

with configurable exclusions.

Default exclusions:

```text
ETF
SME
T2T / BE series
non-equity instruments
```

The universe filter must also support:

```text
minimum price
minimum average traded value
minimum trading history
```

---

# 13. Point-in-Time Universe

The universe must be historical.

Never assume:

```text
today's NSE universe = historical universe
```

For every scan date:

```text
as_of_date
```

the system must be able to determine:

```text
Which securities were eligible on that date?
```

This is mandatory for valid backtests.

---

# 14. Universe Data Required

The instrument master should capture:

```text
instrument_id
ISIN
exchange
segment
symbol
trading symbol
company name
instrument type
series
listing date
delisting date
status
```

Provider mappings must be stored separately.

## 14.1 Point-in-time universe (audit 2026-10-01 P0-4)

Each snapshot is built from what was true on its as-of date (`UNIVERSE_METHOD_VERSION` 2.0):

- **Population.** Every instrument with a raw bar on or before the as-of date. Since step 2 this includes names that were delisted, merged or renamed later, because the NSE bhavcopy lists everything that traded.
- **Series.**
  - The series a stock traded in on its last session on or before the as-of date comes from `daily_series` (that day's bhavcopy; EQ is preferred when a stock had two rows).
  - Today's EQUITY_L series is used only for instruments without bhavcopy data.
  - The exchange is NSE for any bhavcopy-traded name.
- **Trade-to-trade (T2T):** the BE/BZ series of that day, with full history from 2021. Today's `sec_list` T2T list is the fallback.
- **ASM/GSM.** Collected from NSE's `reportASM` and `reportGSM` on each security-master run and stored with the collection date. There is no public history before the first collection, so the daily security-master run must not be skipped.
- **Collection days.** Each collection day is stored per list in `surveillance_collections`. A snapshot is complete for ASM/GSM only if both lists were collected on its as-of date; a skipped evening leaves that date `PARTIAL` ("ASM list not collected on …") permanently.
- **Daily run.** `vcp run daily` runs every trading evening, after NSE publishes the bhavcopy (about 19:00 IST). It catches up prices, corporate actions and scans for skipped evenings; the only loss from a skipped evening is that day's ASM/GSM list.
- **Survivorship label** (`derive_survivorship`). It is derived from the data, and the reasons are stored in `universe_snapshots.survivorship_detail`. The operator attestation `survivorship_coverage_verified` has been removed.
  - `POINT_IN_TIME_COMPLETE`: both conditions hold.
    - Every calendar day of the 380-day look-back window has a settled bhavcopy entry (OK or NO_SESSION).
    - The ASM and GSM lists were collected on or before the as-of date.
  - `BIASED`: no bhavcopy data and no delisting records (current listings only).
  - `PARTIAL`: anything else, e.g. "ASM history starts 2026-10-01" (owner decision 2026-10-01: collect from now; earlier snapshots stay PARTIAL, so they cannot validate thresholds).

---

# 15. Liquidity Data

The universe stage should calculate:

```text
average traded value
average volume
median traded value
```

at configurable windows:

```text
20 trading days
50 trading days
```

Example:

```yaml
universe:
  liquidity:
    min_avg_traded_value_20d: ...
    min_avg_traded_value_50d: ...
```

Do not permanently hard-code the threshold.

Traded value is `raw close x raw volume`. Never use adjusted prices or adjusted volume for liquidity or minimum-price tests.

---

# 16. Price Filter

Minimum price must be configurable.

Example:

```yaml
universe:
  minimum_price: ...
```

The exact production value should be validated through research.

The database should retain the observed price and the filter result.

---

# 17. ASM / GSM / Surveillance Flags

The system should capture surveillance status when reliable data is available.

Examples:

```text
ASM
GSM
T2T
BE
```

ASM/GSM stocks should normally be:

```text
flagged
```

rather than silently removed.

Whether they are excluded from a specific scan should be configurable.

---

# 18. Corporate Actions

Corporate actions are a first-class dataset.

Required categories:

```text
split
bonus
dividend
rights
merger
demerger
symbol change
name change
```

Store:

```text
announcement date
record date
ex date
effective date
ratio
cash amount
source
```

The exact fields depend on the action type.

---

# 18A. Corporate-Action Source Layering, Reconciliation and Gap Safety Net

## Layers

```text
NSE corporate actions (primary, daily pre-market job)      ─┐
Upstox corporate actions by ISIN (secondary)                ├─> reconcile -> corporate_action_resolution
Gap detector on raw prices (safety net)                    ─┘         │
                                                                      v
                                          adjustment factors (per action: factor, source, version)
                                                                      v
                                          daily_prices_adjusted (recomputed for that symbol only)
```

Raw candles are **never** flushed, re-fetched or overwritten. On an ex-date the system adds a resolved action and an adjustment-factor row, then recomputes that symbol's adjusted series. Past scans stay reproducible through `known_at` (§68A).

## Reconciliation rules

Compare per (instrument, action type): `ex_date`, action type, ratio (numerator/denominator), cash amount.

| Situation | Status | Effect |
|---|---|---|
| Both sources agree | `CONFIRMED` | factor applied |
| Only NSE has it, within `secondary_grace_days` of first sighting | `SINGLE_SOURCE` | factor applied, warning flag |
| Only NSE has it past the grace period, and Upstox was **not** queried for that instrument over a window containing the ex-date (no token, no ISIN), or the action is a dividend/rights | `SINGLE_SOURCE` | factor applied (split/bonus), warning flag |
| Sources disagree on ex-date, type, ratio, or cash amount (only when both report one); or only Upstox has it; or an NSE-only split/bonus past the grace period that Upstox was queried about and does not report | `PROVIDER_CONFLICT` | split/bonus: **signals blocked for that symbol** until resolved; dividend/rights: warning only |
| Human decision recorded | `MANUAL_OVERRIDE` | factor applied from the override, audited (§77) |

Upstox coverage (verified live 2026-09-30): Upstox returns only about the last 12 months of events per ISIN, so "asked" means asked **and within Upstox's coverage**: from the earliest ex-date Upstox returned for that ISIN to the end of the window; an ISIN with no Upstox records gives no evidence. Upstox writes a split as the share ratio `old:new` ("1:5" for face value 5 -> 1); it is converted to this spec's `(old face value, new face value)` convention on ingestion.

Policy (audit P0-2, 2026-09-30): only price-scaling actions (split, bonus) can block, because only they change adjusted prices; the secondary source's silence counts as evidence only when it was actually asked. Blocking means the symbol emits `NO_SIGNAL` with `CORPORATE_ACTION_UNRESOLVED` (critical data-quality failure, §55). The pipeline continues for all other symbols.

Point-in-time application: an action adjusts prices only when `ex_date <= as_of_date` **and** `known_from <= known_at`. Never apply a future action to earlier observations.

## Gap safety net (`UNEXPLAINED_GAP`)

Catches actions that both feeds missed. It compares the day's **raw open** with the stored **raw previous close**:

```text
abs(raw_open(t) / raw_close(t-1) - 1) >= gap_pct
AND no *applied* split/bonus with ex_date = t (for that instrument)
-> data_quality_event UNEXPLAINED_GAP, severity HIGH
```

"Applied" (audit P0-3) means a SPLIT or BONUS resolution whose status feeds adjustment factors (`CONFIRMED`, `SINGLE_SOURCE`, `MANUAL_OVERRIDE`) **and** whose ratio is present, finite and positive. A dividend, a rights issue, a `PROVIDER_CONFLICT`, or a split whose ratio could not be read does not rescale prices, so it never explains a gap.

- **Unknown ratio.** An adjustable split/bonus with no usable ratio gets factor 1.0 from the adjustment engine, so its price jump stays in the adjusted series. It raises a `CORPORATE_ACTION_UNRESOLVED` event (`context.cause = ratio_unknown`) that **always blocks** from the ex-date, independent of `conflict_blocks_signals`. Like a conflict, it cannot be closed with `vcp quality resolve`. It clears when a later resolution for the same action carries a usable ratio.

- Always raises the event and a `SIGNAL_WITH_WARNING` flag. Genuine large gaps exist (earnings, circuit moves), so a gap alone is not proof of bad data (§80).
- If the price ratio is also close to a small-integer split/bonus ratio (`split_like_*` config), it is treated as a **suspected missed action**: the symbol is signal-blocked until an action is added or a human marks the gap genuine.
- Never auto-"fix" prices from a gap.
- Implemented (audit P0-2): `vcp ingest corporate-actions` runs the detector after reconciliation for every priced instrument, and `vcp quality scan` re-runs it on demand. Events are persisted in `data_quality_events` and gate the universe, RS and Trend Template (DATABASE_SCHEMA 19.1). A human closes a gap with `vcp quality resolve`; a `PROVIDER_CONFLICT` cannot be closed by hand.

## Configuration

```yaml
corporate_actions:
  primary_source: nse
  secondary_source: upstox
  secondary_grace_days: 3          # proposal: tolerates secondary-feed lag
  conflict_blocks_signals: true
  unexplained_gap:
    gap_pct: 30
    split_like_max_integer: 10     # proposal: ratio near p/q with p,q <= 10
    split_like_tolerance_pct: 3
```

## Stored per action

`adjustment_factor` (price and volume), `source`, `source_record_id`, `calculation_version`, `reconciliation_status`. Provider-adjusted candles are never the truth for actions *after* their fetch time; for Kite, the actions it had already applied at fetch time are not applied again (§21.1). Upstox candles are treated as raw until verified.

## Verify before relying on any source (Phase 2 spike, record results here)

**Upstox corporate actions**
- [ ] returns ratio, ex-date and action type in usable form
- [ ] history depth (backtests need years of past actions)
- [ ] covers mergers/demergers, or only splits and bonuses
- [ ] delisted symbols still return data
- [ ] lookup by ISIN works across an ISIN change

**Kite / Upstox historical candles:** empirically test on known splits whether returned candles are split-adjusted. The Upstox question is currently unanswered. Do not assume either way.

**NSE feed:** fields, history depth, download limits, terms of use.

## Golden corporate-action fixtures

Use 5–10 real historical splits and bonuses (include at least one ISIN-changing split, one bonus, one dividend) whose correct adjusted prices are independently known. They live in `tests/fixtures/corporate_actions/` and gate every change to the adjustment engine.

---

# 19. Raw vs Adjusted Data

Never overwrite raw OHLCV.

Store:

```text
RAW
```

and derive:

```text
ADJUSTED
```

separately.

Conceptually:

```text
Raw OHLCV
    +
Corporate Actions
    ↓
Adjustment Engine
    ↓
Adjusted OHLCV
```

---

# 20. Adjustment Requirement

Adjusted prices must be deterministic.

Given:

```text
raw data
+
corporate-action dataset
+
adjustment algorithm version
```

the same adjusted series must be reproducible.

Store:

```text
adjustment_version
```

with the resulting dataset.

---

# 21. Provider-Adjusted Data

If a provider supplies adjusted data:

```text
provider_adjusted
```

it must be treated as an independent observation.

Do not automatically assume provider adjustment is correct.

Compare:

```text
internal adjustment
vs
provider adjustment
```

and create a reconciliation record.

## 21.1 Kite: adjusted as of fetch time (audit 2026-09-30 P0-1)

Zerodha states that Kite Connect historical prices are adjusted for bonuses, splits, rights issues, spin-offs and extraordinary dividends. The adjustment applies to the bars *as they are served at fetch time*: a bar fetched after an ex-date is already rescaled for that action; a bar fetched before it is not. Treating Kite bars as raw and applying local factors to all of them adjusted pre-split history twice, depending on when it was downloaded.

Rules (owner decision, "fetch-time aware"):

- Kite bars are still stored exactly as received (`raw_ohlcv`, `daily_prices.*_raw` = "as received"). `daily_prices.known_from` is the fetch time of each version.
- Providers listed in `domain.market.PROVIDER_ADJUSTED_SOURCES` (currently `KITE`; must match `ProviderCapabilities.adjusted_prices`) are treated as having applied every action with ex-date on or before the bar's fetch date in IST. The adjustment engine applies a stored factor to a bar only if its ex-date is after **both** the trade date and that fetch date (`adjustment.engine`, `CALCULATION_VERSION` 1.1). Raw sources keep the old rule (ex-date after the trade date).
- The universe's minimum-price rule divides the latest bar's close by the factors Kite had applied (ex-date in `(trade_date, fetch_date]`) to recover the price that actually traded. Traded value (close × volume) is unaffected by split/bonus scaling.
- Local factors from the NSE/Upstox layer remain the source for actions after the fetch, and for the gap safety net.
- Unverified: whether Kite has already rescaled a bar fetched on the ex-date itself before the session. The engine assumes yes. Run `vcp verify kite-adjustment` (read-only) against known splits/bonuses; exit code 2 means Kite returned unadjusted history and this policy must be revisited before any signal is trusted.
- Known limit: Kite also adjusts for rights, spin-offs and extraordinary dividends, which the local layer does not model; those remain in Kite-sourced history and cannot be undone for the price filter.
- **Verified on live data (audit Fix 5b, 2026-09-30).** `vcp verify kite-adjustment` on TATASTEEL, IRCTC and RELIANCE: all ADJUSTED. Golden fixture (`tests/fixtures/corporate_actions/golden_actions.json`, 7 real actions) shows NSE bhavcopy raw close × our factor = Kite close within 0.01 % for 6 actions. The exception, TATASTEEL, shows that Kite also rescales history for large *ordinary* dividends (₹3.60 in 2024 and 2025, ~2.2 % of price each; Nestlé's ~1 % dividends are not adjusted), so Kite's adjustment set is wider than "extraordinary" dividends. Consequence (open, owner decision pending): bars fetched before such a dividend and bars fetched after it differ by that factor, and the local engine cannot reconcile them, leaving a ~2 % step in incrementally ingested history.


## 21.2 NSE bhavcopy: the raw price source of truth (audit step 2, D1)

Owner decision (2026-09-30): raw daily bars come from NSE's capital-market bhavcopy. Adjusted prices are produced only by the local engine from reconciled corporate actions. Kite provides today's provisional bar before the bhavcopy is published, and serves as a cross-check. This removes the dependence on Kite's fetch-time adjustment set (§21.1), which includes dividends, demergers and rights.

Source (`data/providers/nse_bhavcopy.py`):

- **Layouts.**
  - Legacy `content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip` for sessions up to 2024-07-05.
  - UDiFF `content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` from 2024-07-08.
  - Both are parsed to one row type: symbol, series, ISIN, OHLC, last, previous close, volume, turnover and trades.
- **Series kept:** EQ, BE, BZ, SM, ST (`domain.bhavcopy.EQUITY_SERIES`). Other series are counted and skipped.
- **Validation.** Every kept row passes `validate_ohlc`. Failing, unparseable or duplicate (symbol, series) rows are returned as rejects with a reason and are never dropped silently. A file whose layout or session date is wrong is refused as a whole.
- **Raw zips** are cached unchanged under the cache directory (`YYYY/<file name>`). The manifest `bhavcopy_files` records one row per (date, sha256), with status OK / NO_SESSION / PENDING / ERROR. A re-published file with different bytes becomes a new row.
- **Missing files.**
  - A holiday, a weekend and a not-yet-published file all answer HTTP 404.
  - A 404 means NO_SESSION if the date is on NSE's holiday list (current year only) or is at least 3 days old; otherwise it means PENDING (`classify_missing`).
  - Sessions also happen on weekends (Muhurat Sunday 2023-11-12, special Saturday 2024-01-20), so the ingest probes every calendar day.
- **Rate limit:** at least 1 s between requests. A file takes about 1.2–1.5 s and is 70–210 KB.
- **`PREVCLOSE` is not adjusted** on corporate-action ex-dates (verified on 7 splits/bonuses and the RELIANCE demerger). It is kept for reference and must not be used as an adjustment factor.
- **ISINs change 0–1 trading day after a face-value split.** Identity therefore follows symbol continuity, with an ISIN history (step 2.2).
- **ETFs and other fund units** trade in series EQ with `INF...` ISINs, and partly-paid shares carry `IN9...`. Only company equity (`INE...`, `EQUITY_ISIN_PREFIX`) is kept; the rest is counted under `"<series>:<prefix>"`.

Identity (`data/ingestion/bhavcopy_identity.py`, step 2.2). Days are resolved in date order and each row is matched as follows:

1. **ISIN already seen** (in any identifier period, or `instruments.isin`): the row goes to that instrument.
2. **Unseen ISIN, same symbol and same issuer** (`same_issuer_equity`: ISIN characters 1–9 equal, i.e. the same issuer's equity shares) as an instrument now trading under that symbol, or a seeded instrument with that symbol and no history yet: the row goes to the same instrument, recorded as an `ISIN_CHANGE`.
3. **Otherwise:** a new instrument `NSE_EQ|SYMBOL`. If that ID is held by a different issuer (NSE reused the symbol), the new ID is `NSE_EQ|SYMBOL#ISIN`.

Storage and follow-up:

- New instruments are inserted **inactive**. The security master activates them if EQUITY_L lists them. Delisted names and SME stocks (which are not in EQUITY_L) stay inactive.
- `instrument_identifier_history` holds one row per span of sessions under one (symbol, ISIN). `valid_to` is exclusive, and `change_reason` is `FIRST_SEEN` / `ISIN_CHANGE` / `SYMBOL_CHANGE`.
- `daily_series` maps every kept row of every day to its instrument and series. Re-running a day replays that mapping. A re-run with rows the first run did not have, or a day earlier than the last one resolved, is refused.
- The quality scan raises `SYMBOL_MAPPING_UNCERTAIN` (a WARNING that never blocks) for an ISIN change with no split whose ex-date falls between 7 days before and 1 day after it.

Ingestion (`vcp ingest bhavcopy --start D [--end D] [--refresh]`, step 2.3):

- **Order.** Calendar days are processed in date order and never beyond today (IST). A day the manifest has as OK (with a cached file) or NO_SESSION is skipped unless `--refresh` is given.
- **What a 404 means** (`classify_missing`):
  - today's 404 is PENDING;
  - an earlier weekend day is NO_SESSION;
  - a listed holiday, or a weekday at least 3 days old, is NO_SESSION;
  - a recent weekday is PENDING.
- **PENDING or ERROR stops the run** (exit 0 for PENDING, 1 for ERROR). Identity must advance in date order, so a later day is never written before an earlier one.
- **Precedence.** `NSE_BHAVCOPY` (`domain.market.FINAL_PRICE_SOURCE`) is final for its session.
  - It closes (`known_to`) any other current bar for that (instrument, date), whatever the provider.
  - An identical bhavcopy bar (same source hash) is left as is.
  - `save_daily` never lets another provider's bar supersede a bhavcopy bar.
- **One bar per instrument and day.** When a stock has rows in two series, the most regular series wins (EQ > BE > BZ > SM > ST); `selection_reason` records `series=..`.
- **Raw provenance** is the cached zip plus its sha256 in `bhavcopy_files`. Bhavcopy rows are not copied into `raw_ohlcv`.
- **Crash safety.** The manifest row is written last, so an interrupted day is redone; identity is replayed from `daily_series`.

Rights issues and demergers (step 2.4; owner decisions: automatic when NSE's feed lists the action, TERP for rights). With raw prices their ex-date gaps are visible, so they get factors (`domain.corporate_actions.derived_factor`, `CALCULATION_VERSION` 1.2):

- **RIGHTS** `a:b` (NSE subject `Rights a:b @ Premium Rs X/-`):
  - Issue price `S` = face value (`faceVal`) + premium, stored as `cash_amount`.
  - With prior close `P`: `TERP = (b·P + a·S)/(a+b)`, price factor `TERP/P`, volume factor `P/TERP`.
  - `S ≥ P` gives factor 1.0 (no bonus element).
  - Check: BHARTIARTL 2021-09-27 (1:14 at ₹535, P 739.40) gives 0.98157; Kite's history implies 0.98156.
- **DEMERGER** (NSE subject `Demerger`):
  - Price factor = ex-date open / prior close. On the ex-date NSE runs a special pre-open session that discovers the parent's price without the demerged business. The volume factor is 1.0 because the share count does not change.
  - RELIANCE 2023-07-20: 2580 / 2841.85 = 0.90786. The 261.85 difference is JIOFIN's listing base price.
  - ITC 2025-01-06: 455.60 / 481.60 = 0.94601.
  - Kite differs for RELIANCE (about 0.9532, from RIL's 4.68 % cost-of-acquisition split). We follow NSE's price discovery, the basis NSE used for its own index and F&O adjustments.
- **When factors are derived.** Only from raw prices: both the prior bar and the ex-date bar must be `NSE_BHAVCOPY`. Provider-adjusted bars (Kite) already contain the action, so no factor is derived from them and no event is raised.
- **Underivable on raw prices** (no issue price, as in `Rights 613:399`; no ex-date trade; open not below prior close; factor under 0.05): a blocking `CORPORATE_ACTION_UNRESOLVED` event (cause `factor_unknown`) from the ex-date.
- **Gap detector.** An adjustable rights issue or demerger explains its ex-date gap, because either its factor or the `factor_unknown` block covers it.
- **Pipeline order:** `ingest bhavcopy` → `ingest corporate-actions` (derives the factors from the stored bars) → `ingest adjusted-prices`.
- **Other price-affecting actions** (consolidation, capital reduction, amalgamation, scheme of arrangement) are still reported as unhandled NSE records and left to the gap detector.

Kite's role (step 2.5):

- **Today's PROVISIONAL bar.** `vcp ingest market --today` stores Kite's bar for today (IST) with `daily_prices.data_status = 'PROVISIONAL'`. It is always re-fetched, so a later fetch in the session replaces it, and no completeness check runs. The bhavcopy bar supersedes it in the evening, and it can never supersede a bhavcopy bar.
- **Reads leave PROVISIONAL bars out unless explicitly allowed.** This covers `load_daily`, `load_daily_as_of`, the universe candidates, the adjusted-price builder, the quality scan and the corporate-action worker.
  - `--allow-provisional` on `vcp ingest adjusted-prices` and `vcp ingest universe` opts in.
  - A later build without the flag removes the provisional adjusted row again (`save_adjusted_daily(prune_missing=True)`).
- **Kite history** (`vcp ingest market --kite-history`) is for comparison only. `vcp ingest market` with no mode exits with an error that points to `vcp ingest bhavcopy`.
- **Cross-check.** `vcp verify kite-crosscheck --instrument X [--start] [--end] [--tolerance 0.002]` is read-only. It compares our LIVE adjusted closes with Kite's history and explains every step in the Kite/ours ratio:
  - INFO `KITE_DIVIDEND` / `DEMERGER_METHOD`: known methodology differences;
  - WARNING `FACTOR_MISMATCH` (split, bonus or rights applied with different factors), `UNEXPLAINED`, `BAR_MISMATCH` (one bad bar) or `LEVEL_MISMATCH` (latest bars differ).
  - Exit codes: 0 no warnings, 2 warnings, 1 error.

Still to come in step 2 (see `AUDIT_FIX_LOG.md`):

- migration (2.6).

---

# 22. Split/Gaps Anomaly Detection

The system must detect suspicious price gaps.

Example:

```text
previous close = 1,000
current close = 500
```

could represent a split.

Flag large overnight changes approximately:

```text
>= +30%
<= -30%
```

when no corresponding corporate action exists.

This is a diagnostic event.

Do not automatically "fix" the price without evidence.

---

# 23. OHLC Validation

Every incoming bar must satisfy:

```text
low <= open <= high
low <= close <= high
low <= high
volume >= 0
open, high, low, close > 0
every price (and volume, when present) is a finite number
```

The last two rules were added by audit P1-9 (2026-09-30): NaN compares False with everything, so a NaN bar used to pass all ordering tests. Downstream engines apply the same rule: a non-finite measurement is missing data (NULL, `INSUFFICIENT_DATA`), never a `FAIL`, a Stage, or an RS rank.

Invalid records should not enter canonical data as valid records.

They should be:

```text
rejected
```

or:

```text
flagged as suspect
```

with an audit record.

---

# 24. Duplicate Detection

Canonical daily data must contain at most one trusted record per:

```text
instrument_id
trade_date
```

Provider-level raw data may contain duplicate observations because ingestion runs can overlap.

The normalization layer resolves duplicates.

---

# 25. Missing Trading Days

Missing data must be evaluated against the exchange trading calendar.

Do not flag weekends as missing market data.

Example:

```text
Friday
Monday
```

is normal.

Example:

```text
Monday
Wednesday
```

may indicate a missing Tuesday bar if Tuesday was a trading day.

---

# 26. Stale Data

A dataset may exist locally but still be too old to produce a current signal.

The system must calculate:

```text
last_available_trade_date
```

and compare it with the required market cutoff.

Configuration:

```yaml
data_quality:
  max_market_staleness_trading_days: 1
```

If the market data exceeds the allowed staleness:

```text
no production signal
```

The system should report the reason.

---

# 27. Partial Provider Response

Providers may return incomplete ranges.

Never assume:

```text
HTTP success = complete dataset
```

Validate:

```text
requested start
requested end
returned first date
returned last date
expected trading days
actual trading days
```

A partial response must be recorded as:

```text
PARTIAL
```

unless completeness is independently established.

---

# 28. Provider Request Limits

The provider adapter must isolate:

- maximum candle range;
- request rate limits;
- pagination;
- authentication;
- retries;
- transient errors.

Do not spread Kite-specific limits through the application.

Configuration should contain provider settings:

```yaml
providers:
  kite:
    enabled: true
    request_chunk_days: ...
    retry_count: 3
    retry_backoff_seconds: ...
```

The actual limits must be verified against the current provider documentation before production deployment.

---

# 28A. Provider Authentication Lifecycle

Kite access tokens are short-lived (expected to expire daily; verify in current documentation) and need an interactive login. Therefore:

- A `vcp auth kite` helper performs the login/token exchange and writes the token to `.env`/secret store only, never to the DB, logs or Git.
- Ingestion checks token validity before starting. An `AUTHENTICATION_ERROR` halts provider calls and marks the run `FAILED` with that reason. It must **not** be recorded as missing data or `NO_VCP`.
- The scan reports `DATA_NOT_READY` when the required update could not run because of authentication.
- Daily workflow: authenticate, then update data, then scan.

---

# 29. Incremental Ingestion

Daily ingestion should be incremental.

For each instrument:

```text
last local valid date
        ↓
next required date
        ↓
provider request
        ↓
validate
        ↓
persist
```

Do not redownload 10 years every day.

---

# 30. Historical Backfill

Historical backfill should support:

```text
--start
--end
--symbols
--universe
--force
--provider
```

Example conceptual command:

```bash
python -m app.ingest.market_data \
    --universe nse_equity \
    --start 2010-01-01 \
    --end 2026-09-28
```

Exact CLI names belong to the implementation specification.

---

# 31. Chunked Backfill

Historical requests should be chunked according to provider constraints.

Architecture:

```text
2010–2012
2012–2014
2014–2016
...
2024–2026
```

The actual chunk size is provider-specific.

The ingestion engine must dynamically handle provider request limits.

---

# 32. Retry Policy

Retry transient failures:

```text
network timeout
HTTP 429
temporary provider error
connection reset
```

Do not endlessly retry permanent errors:

```text
invalid instrument
invalid date range
authentication failure
unsupported interval
```

Retry behavior must be configurable.

---

# 33. Idempotent Ingestion

Running ingestion twice with the same source data must not corrupt the dataset.

Example:

```text
ingest 2026-09-01 → 2026-09-28
ingest 2026-09-01 → 2026-09-28
```

must produce the same canonical dataset.

This is mandatory.

---

# 34. Ingestion Run Tracking

Every ingestion operation must create:

```text
ingestion_run_id
```

Store:

```text
provider
dataset
requested range
actual range
records requested
records received
records written
records rejected
status
started_at
completed_at
config hash
code version
```

---

# 35. Source Hashing

Where practical, calculate a deterministic source hash.

For a normalized record:

```text
provider
instrument
timestamp
OHLCV
```

can contribute to:

```text
source_hash
```

This helps detect provider corrections.

---

# 36. Provider Corrections

If a provider changes a historical candle:

```text
old observation
new observation
```

do not silently overwrite raw history.

Record the new observation and allow canonical selection to change.

The audit trail should answer:

```text
What did the provider originally return?
What does it return now?
Which version is canonical?
Why?
```

---

# 37. Canonical Selection

When multiple providers exist:

```text
Kite
Dhan
```

the canonical layer should use an explicit policy.

Example:

```yaml
data:
  canonical_provider_priority:
    - kite
    - dhan
```

But provider priority must not hide conflicts.

Store:

```text
selected_provider
selection_reason
```

and create a conflict record when values differ materially.

---

# 38. Provider Conflict Threshold

Material differences should be configurable.

Example:

```yaml
data_quality:
  provider_price_difference_pct: 0.5
```

This is a diagnostic threshold, not a trading threshold.

Volume differences may require a separate tolerance.

---

# 39. Volume Handling

Volume is critical for VCP analysis.

The system must distinguish:

```text
raw volume
adjusted volume
provider volume
```

Do not blindly adjust volume using price adjustment factors without validating the corporate-action methodology.

The adjustment engine must define how split/bonus events affect historical volume.

---

# 40. Volume Quality

Track:

```text
volume_missing
volume_zero
volume_suspect
volume_adjusted
```

VCP volume analysis should not treat missing volume as genuine volume dry-up.

---

# 41. Fundamental Data

Fundamentals are secondary supportive data.

They are NOT the primary VCP detector input.

The primary signal path is:

```text
Market Data
    ↓
Trend Template
    ↓
VCP
    ↓
Volume / RS
    ↓
Setup Score
```

Fundamentals contribute later:

```text
Fundamental Score
```

with a capped configurable weight.

---

# 42. Fundamental Point-in-Time Requirement

For each fundamental observation store:

```text
period_end
filing_date
available_at
provider
```

The backtest can use a fundamental observation only when:

```text
available_at <= scan_as_of_date
```

This is mandatory.

---

# 43. Fundamental Metrics

Minimum desired metrics:

```text
EPS
EPS YoY growth
EPS QoQ growth
Sales
Sales YoY growth
EPS acceleration
Margin
Margin expansion
ROE
Debt
```

Fundamental ingestion is provider-neutral.

The initial fundamental provider is intentionally not hard-coded in this data specification.

---

# 44. Fundamental Data Availability

Do not convert missing fundamentals into zero.

Example:

```text
EPS unavailable
```

must mean:

```text
EPS unavailable
```

not:

```text
EPS growth = 0%
```

Use explicit availability flags.

---

# 45. Relative Strength Data

RS is derived data.

Inputs should include:

```text
canonical adjusted price
full eligible universe
as_of_date
```

The calculation must be performed across the full configured universe.

Do not calculate RS only after Trend Template filtering.

---

# 46. Market Regime Data

Market regime is useful for research but must not leak future information.

Potential derived fields:

```text
index_trend
index_distance_from_ma
market_breadth
volatility_regime
```

For example:

```text
NIFTY trend as of T
```

must use only data available by T.

---

# 47. Index Data

The system should ingest relevant benchmark/index history separately.

Potential benchmarks:

```text
NIFTY 50
NIFTY 500
sector indices
```

The exact benchmark set should be configurable.

Index data supports:

- RS;
- market regime;
- benchmark-relative research;
- backtesting.

---

# 48. Sector / Industry Classification

If reliable classification is available, store:

```text
sector
industry
industry_group
classification_source
valid_from
valid_to
```

Classification must be point-in-time where historical research uses it.

Do not assume today's sector classification was always true.

---

# 49. Data Quality Score

Each dataset can expose a quality status.

Suggested levels:

```text
0 = invalid
1 = severely incomplete
2 = suspect
3 = usable
4 = verified
```

This is a data-quality indicator, not a trading score.

---

# 50. VCP Eligibility Data Contract

Before the VCP detector runs, it should receive a validated dataset satisfying:

```text
daily OHLCV available
weekly aggregation possible
sufficient lookback
no critical data-quality failure
corporate-action state resolved
```

Example:

```python
VCPDataContext(
    instrument_id=...,
    as_of_date=...,
    daily_prices=...,
    weekly_prices=...,
    quality_status=...,
    data_snapshot_id=...,
)
```

The detector should not fetch data directly.

---

# 51. Minimum Lookback

The VCP detector requires sufficient historical context.

The exact minimum is defined by the VCP algorithm.

The data layer should support:

```text
requested analysis window
+
required warm-up period
```

For example:

```text
analysis window = 1 year
warm-up = 200+ trading days
```

The exact value must be determined by indicator requirements.

---

# 52. Warm-Up Data

Indicators such as:

```text
SMA 200
ATR
rolling volume
52-week high/low
```

require historical warm-up.

Do not calculate:

```text
SMA 200
```

using only the visible scan window if earlier data is available.

The feature service should automatically request sufficient warm-up data.

---

# 53. Data Availability Contract

Every analysis request should return:

```text
data_start
data_end
requested_start
requested_end
missing_ranges
quality_status
```

This allows the scanner to distinguish:

```text
no VCP
```

from:

```text
insufficient data
```

---

# 54. No-Signal vs No-Data

These must never be conflated.

```text
NO_VCP
```

means:

```text
data was adequate and no VCP was detected
```

Whereas:

```text
INSUFFICIENT_DATA
```

means:

```text
the detector could not make a valid determination
```

Similarly:

```text
STALE_DATA
INVALID_DATA
PROVIDER_ERROR
```

must have distinct statuses.

---

# 55. Data Quality Gates

Before a production signal is emitted:

```text
Market data fresh?
        ↓
Canonical data valid?
        ↓
Corporate actions resolved?
        ↓
Required lookback available?
        ↓
Universe valid?
        ↓
Technical calculations valid?
```

If a critical condition fails:

```text
NO SIGNAL
```

rather than:

```text
best-effort signal
```

---

# 56. Cache Strategy

Local cache should operate at multiple levels:

```text
Raw provider data
Canonical market data
Derived features
```

Provider responses should not be repeatedly downloaded if the same valid data is already available.

Cache invalidation should be based on:

```text
date range
provider
instrument
dataset version
```

---

# 57. Data Freshness

For live/current scans, track:

```text
latest trading date
latest ingestion time
provider
```

Example:

```text
Market data through:
2026-09-28

Ingested:
2026-09-28 18:15 IST
```

The scanner should expose this metadata in reports and the frontend.

---

# 58. Daily Ingestion Workflow

Recommended workflow:

```text
1. Load instrument master
2. Determine today's trading session
3. Determine local data cutoff
4. Identify missing ranges
5. Request missing data from Kite
6. Validate provider response
7. Persist raw data
8. Pull NSE (pre-market) and Upstox corporate actions, reconcile (§18A), recompute adjusted series for affected symbols, run the gap detector
9. Build/update canonical prices
10. Run quality checks
11. Update weekly data
12. Update features
13. Mark data snapshot
```

VCP scanning should happen only after the required data update succeeds.

---

# 59. Historical Bootstrap Workflow

```text
1. Acquire instrument universe
2. Resolve provider instrument IDs
3. Create point-in-time security master
4. Backfill corporate actions
5. Backfill daily OHLCV
6. Validate all ranges
7. Build canonical adjusted series
8. Run anomaly detection
9. Build weekly data
10. Build technical features
11. Validate data completeness
12. Create initial data snapshot
```

---

# 60. Provider Switching

The system must support:

```yaml
providers:
  active_market_data: kite
```

Changing to:

```yaml
providers:
  active_market_data: dhan
```

must not require changes to:

```text
VCP engine
Trend Template
scoring
frontend
backtest engine
```

Only the provider/ingestion layer should change.

---

# 61. Provider Capability Matrix

Providers should expose capabilities.

Example:

```python
ProviderCapabilities(
    daily_history=True,
    intraday_history=True,
    corporate_actions=False,
    adjusted_prices=False,
    instrument_master=True,
    delisted_history=False,
    quotes=True,
    websocket=True,
)
```

The application can then determine whether a provider can satisfy a request.

---

# 62. No Provider-Specific Assumptions in Strategy

Bad:

```python
if provider == "kite":
    ...
```

inside VCP or scoring logic.

Good:

```python
prices = market_data.get_daily(...)
```

Provider differences belong inside adapters.

---

# 63. Data Contracts

The normalized data contract should define:

### OHLCV

```text
instrument_id
trade_date
open
high
low
close
volume
```

### Instrument

```text
instrument_id
symbol
exchange
isin
validity
```

### Corporate Action

```text
instrument_id
action_type
effective_date
parameters
```

### Fundamental

```text
instrument_id
period_end
available_at
metric
value
```

---

# 64. Null Semantics

Null must retain meaning.

Examples:

```text
NULL EPS
```

means:

```text
EPS unavailable
```

not:

```text
EPS = 0
```

Similarly:

```text
NULL volume
```

must not become:

```text
volume = 0
```

The only deliberate fallback allowed for adjusted close is:

```sql
COALESCE(NULLIF(adj_close, 0), close)
```

where applicable.

This fallback must be logged/traceable.

---

# 65. Precision

Store prices with sufficient decimal precision.

Recommended logical type:

```text
DECIMAL
```

or a validated floating representation where DuckDB analytical performance requires it.

Do not round prices before calculations.

Display rounding belongs to reporting/UI.

---

# 66. Time Zones

NSE market timestamps should be normalized consistently.

Canonical market session timezone:

```text
Asia/Kolkata
```

For daily bars, the canonical key is:

```text
trade_date
```

rather than a timezone-sensitive timestamp.

Provider timestamps must be normalized before persistence.

---

# 67. Data Lineage

Every derived dataset should be traceable to:

```text
source provider
source dataset
ingestion run
data snapshot
calculation version
configuration hash
```

For a VCP result:

```text
VCP
 ↓
feature snapshot
 ↓
daily_prices
 ↓
ingestion_run
 ↓
provider
```

---

# 68. Data Snapshot

A scan should reference one:

```text
data_snapshot_id
```

representing the data state used for the scan.

It should contain:

```text
market data cutoff
fundamental cutoff
universe cutoff
provider versions
dataset versions
```

This makes historical scans reproducible.

---

# 68A. Bitemporal Storage and Snapshot Manifests

Cutoff dates alone do not make a snapshot reproducible, because canonical data can change after the fact (provider corrections, late corporate actions, verification updates).

- Canonical tables (`daily_prices`, `corporate_actions`, `corporate_action_adjustments`, universe and security-master history) are **append-only with `known_from` / `known_to`** (DATABASE_SCHEMA §14).
- Adjusted prices live in a separate derived table keyed by `adjustment_version` and are computed using only actions known at the snapshot's `known_at`.
- Each `data_snapshot_id` stores per-dataset `known_at` cutoffs plus a `snapshot_manifest` (row counts and content hashes).
- A verification job recomputes manifest hashes. A mismatch is a `HIGH` severity data-quality event.

---

# 69. Reproducible Historical Scan

A historical scan should conceptually run as:

```python
scan(
    as_of_date="2024-06-30",
    data_snapshot_id="snapshot-123",
    config_hash="abc...",
)
```

The system must not silently substitute:

```text
today's corrected data
```

unless explicitly running a revised-data research experiment.

---

# 70. Original vs Revised Data

There are two legitimate research modes:

### Historical-as-known

Use only information that would have been available at the time.

### Revised-history

Use the latest corrected historical dataset.

Both can be useful.

They must be explicitly identified.

Example:

```text
research_mode:
    point_in_time
```

or:

```text
research_mode:
    revised_history
```

Production strategy validation should prioritize point-in-time data.

---

# 71. Data Versioning

Version:

```text
schema
provider adapter
raw ingestion
normalization
corporate-action adjustment
feature calculation
strategy algorithm
```

Example:

```text
schema_version = 1
data_pipeline_version = 1.0.0
adjustment_version = 1.0.0
feature_version = 1.0.0
```

---

# 72. Research Reproducibility

Every research result must identify:

```text
data_snapshot_id
config_hash
algorithm_version
universe_snapshot_id
```

Without these, a result should not be considered reproducible.

---

# 73. Storage Layout

Recommended:

```text
data/
├── raw/
│   ├── market/
│   │   └── provider=kite/
│   ├── instruments/
│   └── fundamentals/
│
├── normalized/
│   ├── market/
│   ├── corporate_actions/
│   └── instruments/
│
├── curated/
│   ├── daily_prices/
│   ├── weekly_prices/
│   ├── universe/
│   └── benchmarks/
│
├── features/
│   ├── daily/
│   ├── weekly/
│   ├── trend/
│   └── vcp/
│
└── research/
    ├── observations/
    └── outcomes/
```

---

# 74. Parquet Partitioning

Default partition strategy:

```text
dataset
year
```

For large datasets:

```text
dataset
year
month
```

Avoid:

```text
one file per symbol per day
```

because it creates excessive small files.

---

# 75. Data Compression

Use Parquet compression appropriate for analytical workloads.

Default:

```text
ZSTD
```

where supported.

Compression settings should be implementation-configurable.

---

# 76. Data Repair

A repair job must be able to target:

```text
instrument
date range
provider
dataset
```

Example:

```bash
repair market-data \
  --symbol RELIANCE \
  --start 2025-01-01 \
  --end 2025-02-01
```

The repair process must preserve raw lineage.

---

# 77. Manual Overrides

Manual corrections should not overwrite provider data.

If an exceptional correction is necessary, create:

```text
manual_data_overrides
```

with:

```text
instrument_id
trade_date
field
original_value
override_value
reason
created_by
created_at
approved
```

Manual overrides should be rare and auditable.

---

# 78. Data Validation Levels

Validation should operate at four levels:

```text
Level 1 — Schema
Level 2 — Record
Level 3 — Time series
Level 4 — Cross-source
```

### Schema

Types and required fields.

### Record

OHLC relationships.

### Time series

Missing/duplicate/outlier detection.

### Cross-source

Kite vs Dhan or provider-adjusted vs internally adjusted.

---

# 79. Time-Series Anomaly Checks

Potential checks:

```text
extreme return
extreme volume
flat price streak
missing candles
duplicate candles
zero volume
high/low discontinuity
```

These should generate data-quality events rather than automatically deleting data.

---

# 80. Outlier Policy

An extreme price movement is not automatically bad data.

Example:

```text
+25% move
```

could be a genuine event.

Therefore:

```text
anomaly ≠ invalid
```

The system should flag it and investigate corporate actions/provider consistency.

---

# 81. Data Completeness Report

The ingestion system should generate a report containing:

```text
total instruments
instruments attempted
instruments successful
instruments partial
instruments failed

date coverage
missing ranges
quality events
provider conflicts
corporate-action anomalies
```

---

# 82. Bootstrap Acceptance Criteria

A historical bootstrap is complete only when:

- [ ] instrument master populated;
- [ ] provider mappings resolved;
- [ ] requested historical range processed;
- [ ] raw data persisted;
- [ ] canonical prices built;
- [ ] corporate actions reconciled;
- [ ] missing ranges reported;
- [ ] invalid records reported;
- [ ] weekly data generated;
- [ ] feature warm-up requirements satisfied;
- [ ] data snapshot created.

"API returned successfully" is not sufficient.

---

# 83. Daily Update Acceptance Criteria

A daily update is complete only when:

- [ ] expected trading session identified;
- [ ] all eligible instruments checked;
- [ ] missing data fetched;
- [ ] data validated;
- [ ] canonical dataset updated;
- [ ] corporate actions checked;
- [ ] data-quality checks completed;
- [ ] freshness confirmed;
- [ ] data snapshot created.

Only then should production scanning begin.

---

# 84. VCP Data Readiness

The VCP engine may run only if:

```text
daily data = valid
weekly data = derivable
lookback = sufficient
corporate actions = resolved
volume = usable
data freshness = acceptable
```

Otherwise:

```text
status = DATA_NOT_READY
```

not:

```text
VCP = false
```

---

# 85. Data Flow Into VCP

```text
Provider
   ↓
Raw OHLCV
   ↓
Canonical OHLCV
   ↓
Corporate-action normalization
   ↓
Adjusted OHLCV
   ↓
Daily features
   ↓
Weekly aggregation/context
   ↓
VCPDataContext
   ↓
VCP Detector
```

The VCP detector receives prepared data.

It does not know whether the data came from Kite or Dhan.

---

# 86. Data Flow Into Trend Template

```text
Canonical adjusted daily data
          ↓
SMA / 52-week features
          ↓
RS universe calculation
          ↓
Trend Template evaluation
          ↓
TrendTemplateResult
```

---

# 87. Data Flow Into Fundamental Scoring

```text
Fundamental provider
       ↓
Raw fundamentals
       ↓
Point-in-time normalization
       ↓
Fundamental snapshot
       ↓
Fundamental metrics
       ↓
Fundamental score
```

Fundamentals cannot rescue a failed technical gate.

---

# 88. Data Flow Into Final Score

```text
Trend Result
VCP Result
Volume Features
RS Result
Fundamental Result
        ↓
Setup Scoring Engine
        ↓
Final Setup Score
```

The database supplies measurements.

The scoring engine applies configurable weights.

---

# 89. Future AI/ML Data Contract

The future ML model must consume frozen point-in-time feature snapshots.

Training dataset:

```text
features as of T
        +
outcome after T
```

Never:

```text
future-derived feature
```

inside the feature vector.

The ML system should receive:

```text
FeatureSnapshot
```

and produce:

```text
MLConfirmation
```

without changing the deterministic VCP detector.

---

# 90. LLM Data Contract

LLMs are not authoritative market-data processors.

The LLM layer may consume structured results:

```text
VCPResult
TrendTemplateResult
FundamentalSummary
DataQualitySummary
```

to generate:

```text
human-readable explanation
```

It should not silently modify:

```text
OHLCV
VCP measurements
Trend Template flags
scores
```

---

# 91. Frontend Data Contract

The dashboard should query structured application/repository APIs.

It should not read raw Parquet files directly.

Example:

```text
Frontend
   ↓
Reporting API
   ↓
Repositories
   ↓
DuckDB
```

This preserves separation between UI and data storage.

---

# 92. Reporting Data

Reports should expose:

```text
as_of_date
data_freshness
data_quality
provider
data_snapshot_id
```

for transparency.

A user should be able to distinguish:

```text
No VCP detected
```

from:

```text
Data incomplete
```

---

# 93. Configuration

All important data behavior must be configurable.

Example:

```yaml
data:
  history:
    minimum_years: 10
    target_years: 15

  canonical_provider_priority:
    - kite
    - dhan

  freshness:
    max_staleness_trading_days: 1

  anomaly:
    extreme_gap_pct: 30

  provider:
    request_chunk_days: ...
    retry_count: 3
```

Provider-specific constraints should be isolated under provider configuration.

---

# 94. Secrets

Never store API credentials in:

```text
YAML
DuckDB
Parquet
Git
```

Use:

```text
.env
environment variables
secret manager
```

Example:

```text
KITE_API_KEY
KITE_API_SECRET
KITE_ACCESS_TOKEN
DHAN_CLIENT_ID
DHAN_ACCESS_TOKEN
```

---

# 95. Logging

Data ingestion logs must contain:

```text
run_id
provider
instrument
requested range
returned range
record count
retry count
validation result
error
```

Logs should make it possible to diagnose:

```text
Why did RELIANCE have no VCP today?
```

without guessing.

---

# 96. Error Categories

Standardize errors:

```text
AUTHENTICATION_ERROR
RATE_LIMIT_ERROR
NETWORK_ERROR
PROVIDER_ERROR
INVALID_INSTRUMENT
INVALID_RANGE
PARTIAL_RESPONSE
DATA_VALIDATION_ERROR
CORPORATE_ACTION_ERROR
STORAGE_ERROR
```

These should be distinguishable in ingestion reports.

---

# 97. Testing

Required data tests:

### Provider adapter

- authentication;
- pagination;
- date ranges;
- retry;
- rate limiting;
- empty response;
- partial response.

### Normalization

- duplicates;
- invalid OHLC;
- timestamps;
- symbol mapping.

### Corporate actions

- splits;
- bonuses;
- symbol changes;
- adjustment factors.

### Point-in-time

- future data exclusion;
- filing availability;
- historical universe.

### Reproducibility

Same:

```text
input snapshot + config + algorithm
```

must produce the same derived dataset.

---

# 98. Performance Targets

Initial engineering targets:

```text
Historical bootstrap:
< 15 minutes for a 500-symbol test universe
```

This is a target, not a guarantee.

The actual performance depends on:

- provider limits;
- network;
- API plan;
- storage;
- number of symbols;
- historical depth.

Daily update should be optimized for incremental ingestion rather than full refresh.

---

# 99. Scalability

The architecture should comfortably support:

```text
500 symbols
1,000 symbols
2,000+ symbols
```

without redesigning the data model.

Scaling should initially come from:

```text
batch ingestion
columnar storage
parallel provider requests within limits
incremental updates
Parquet partitioning
DuckDB analytical execution
```

not microservices.

---

# 100. Production Data Checklist

Before calling the data layer production-ready:

- [ ] Kite adapter implemented behind provider interface
- [ ] Dhan interface reserved for future implementation
- [ ] instrument security master implemented
- [ ] provider instrument mapping implemented
- [ ] 10+ years historical backfill tested
- [ ] incremental daily ingestion implemented
- [ ] raw data immutable
- [ ] canonical data generated
- [ ] adjusted data reproducible
- [ ] corporate-action pipeline implemented
- [ ] anomaly detection implemented
- [ ] data-quality events implemented
- [ ] stale-data gate implemented
- [ ] point-in-time universe implemented
- [ ] point-in-time fundamentals implemented
- [ ] RS universe calculation implemented
- [ ] data snapshots implemented
- [ ] configuration hashing implemented
- [ ] ingestion runs audited
- [ ] provider conflicts handled
- [ ] backups tested
- [ ] historical reproducibility tested
- [ ] NSE and Upstox corporate-action feeds verified (fields, history depth, mergers/demergers, delisted symbols)
- [ ] corporate-action reconciliation with PROVIDER_CONFLICT signal blocking implemented
- [ ] UNEXPLAINED_GAP detector implemented and tested
- [ ] Kite/Upstox candle adjustment behavior verified empirically on known splits
- [ ] golden corporate-action fixtures pass
- [ ] historical security master / delisting source selected and verified
- [ ] Kite authentication workflow implemented and tested
- [ ] survivorship status stamped on scans and backtests
- [ ] bitemporal canonical tables and snapshot manifests verified

---

# 101. Final Data Architecture

```text
                         ┌───────────────────────┐
                         │    Provider APIs      │
                         │                       │
                         │  Kite      Dhan       │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │   Provider Adapters   │
                         │ MarketDataProvider    │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │      INGESTION        │
                         │ retries / pagination  │
                         │ validation / lineage  │
                         └───────────┬───────────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │     RAW PARQUET       │
                         │ immutable observations│
                         └───────────┬───────────┘
                                     │
                                     ▼
                    ┌────────────────────────────────┐
                    │         NORMALIZATION           │
                    │                                 │
                    │ instruments                     │
                    │ symbol history                  │
                    │ corporate actions              │
                    │ timestamp normalization         │
                    └───────────────┬────────────────┘
                                    │
                                    ▼
                    ┌────────────────────────────────┐
                    │       CURATED DATA              │
                    │                                 │
                    │ canonical daily OHLCV           │
                    │ adjusted prices                 │
                    │ weekly prices                   │
                    │ universe snapshots              │
                    └───────────────┬────────────────┘
                                    │
                                    ▼
                    ┌────────────────────────────────┐
                    │        DERIVED DATA             │
                    │                                 │
                    │ indicators                     │
                    │ RS                             │
                    │ Trend Template                 │
                    │ volume features                 │
                    │ fundamental metrics             │
                    └───────────────┬────────────────┘
                                    │
                                    ▼
                    ┌────────────────────────────────┐
                    │         VCP ENGINE              │
                    │                                 │
                    │ weekly context                 │
                    │ daily contractions             │
                    │ volatility                     │
                    │ volume                         │
                    │ pivot                          │
                    └───────────────┬────────────────┘
                                    │
                                    ▼
                    ┌────────────────────────────────┐
                    │        SCORING ENGINE           │
                    └───────────────┬────────────────┘
                                    │
                                    ▼
                    ┌────────────────────────────────┐
                    │      REPORT / DASHBOARD         │
                    │       / ALERTS / RESEARCH       │
                    └────────────────────────────────┘
```

---

# 102. Most Important Rules

The data layer must enforce these principles:

1. **Local data is the source of truth.**
2. **Provider APIs are ingestion sources, not strategy dependencies.**
3. **Raw data is never silently overwritten.**
4. **Adjusted data is derived, not substituted for raw data.**
5. **Corporate actions are first-class data.**
6. **Every important dataset is point-in-time aware.**
7. **Missing data is not the same as zero.**
8. **No-data is not the same as no-signal.**
9. **Provider success does not imply data completeness.**
10. **Historical universe membership must be preserved.**
11. **Fundamentals are timestamped by public availability, not merely fiscal period.**
12. **All derived data is versioned and rebuildable.**
13. **Every production signal must be traceable to its data snapshot.**
14. **The VCP engine must never fetch data directly from a broker.**
15. **LLMs do not modify authoritative market data.**
16. **Future ML models consume frozen point-in-time features.**
17. **Data-quality failures must prevent unreliable production signals.**
18. **Provider switching must not require changes to strategy logic.**
19. **All important data thresholds are configurable.**
20. **The complete historical research environment must be reproducible.**

---

# 103. Definition of Done

`DATA_SPECIFICATION.md` is considered implemented when:

- [ ] `MarketDataProvider` interface exists.
- [ ] `KiteProvider` implements the interface.
- [ ] Dhan adapter can be added without strategy changes.
- [ ] instrument master is provider-neutral.
- [ ] provider mappings exist.
- [ ] historical daily OHLCV ingestion works.
- [ ] incremental ingestion works.
- [ ] raw data is immutable.
- [ ] canonical daily data is generated.
- [ ] weekly data is reproducible from daily data.
- [ ] corporate actions are stored and applied.
- [ ] adjusted prices can be rebuilt.
- [ ] provider reconciliation exists.
- [ ] data-quality events exist.
- [ ] staleness is enforced.
- [ ] universe snapshots are point-in-time.
- [ ] RS can be calculated across the full universe.
- [ ] fundamental observations use `available_at`.
- [ ] data snapshots are generated.
- [ ] configuration is hashed.
- [ ] ingestion runs are auditable.
- [ ] historical scans can be reproduced.
- [ ] VCP receives a validated `VCPDataContext`.
- [ ] no strategy module imports a broker SDK.
- [ ] backups and recovery have been tested.

---

# 104. Relationship to Other Specifications

This document is one component of the larger system.

```text
PROJECT_DESIGN.md
        │
        ├── DATABASE_SCHEMA.md
        │       ↓
        ├── DATA_SPECIFICATION.md
        │       ↓
        ├── VCP_SPECIFICATION.md
        │       ↓
        ├── TREND_TEMPLATE_SPECIFICATION.md
        │       ↓
        ├── SCORING_SPECIFICATION.md
        │       ↓
        ├── FUNDAMENTALS_SPECIFICATION.md
        │       ↓
        ├── BACKTEST_SPECIFICATION.md
        │       ↓
        └── FRONTEND_SPECIFICATION.md
```

The dependency direction is intentional:

```text
Data
 ↓
Features
 ↓
Strategy
 ↓
Scoring
 ↓
Research
 ↓
Presentation
```

Higher layers must not dictate the structure of lower-level raw data.