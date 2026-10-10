# FUNDAMENTALS_SPECIFICATION.md

**Project:** Institutional-Grade NSE VCP Scanner
**Version:** 1.0 (draft for owner sign-off, 2026-10-10)
**Owns:** where fundamental data comes from, how it is fetched, stored, made point-in-time and turned into metrics, how it is shown, and the research that decides whether it ever joins the score.
**Does not own:** the score weights and bounds (SCORING_SPECIFICATION §7, `config/scoring.yaml`), the table layout in general terms (DATABASE_SCHEMA §36–39), the page frame (FRONTEND_SPECIFICATION §17–18).

Owner decisions this spec follows:

| # | Decision | Status |
|---|---|---|
| F2.1 | Source: **NSE filings** (the exchange's financial-results feeds and their XBRL files). Screener.in is not used: it shows today's restated figures with no publication date, which breaks the point-in-time rule. | Owner, 2026-10-10 |
| F2.2 | **Freeze rule:** fundamentals are display and research only until the review on or after 2027-04-01. They never touch `setup_scores`, a gate, a rank or a paper trade (§2). | Recommended, accepted with "go ahead", 2026-10-10 |
| F2.3 | **Scope:** the scan universe first (about 1,271 stocks); a setting can widen it to every main-board (EQ) stock later. | Default taken; the owner did not choose. Change on request |
| F2.4 | **History:** from 2023-10-01 first (enough for year-on-year growth on every quarter shown); the research needs 2021-01-01 and is added later (§12). | Default taken; the owner did not choose. Change on request |

This replaces Decision F1 (fundamentals skipped, 2026-10-03). Its probe findings are the basis of §3.

---

# 1. Purpose

Show, for each stock, how its earnings and sales are growing, and find out with evidence whether this adds anything to the technical setups. Fundamentals stay secondary: a setup is valid or not on its technical structure only (PROJECT_DESIGN §29, VCP_SPECIFICATION §36).

Not in scope: valuation ratios, shareholding, analyst estimates, news, any provider other than NSE, intraday data.

---

# 2. Freeze rule

The five strategies and the paper ledger are frozen (STRATEGY_SPECIFICATION §21). The backtest and the paper ledger rank simultaneous signals by setup score, so a changed score would change which paper trades are taken. Therefore, until the review:

1. `fundamental_score` stays NULL in `setup_scores`, as today; weights, bounds and the scoring config hash do not change.
2. No gate, rank, label, backtest or paper-ledger code reads a fundamental value.
3. Fundamental tables are separate; the shadow score of §12 is stored in its own table.
4. Tests: the scan, score and paper-ledger result hashes of a fixed fixture are equal with fundamentals present and absent; no module of `patterns/`, `scoring/`, `paper/` or `backtest/` imports `fundamentals`.

Adding fundamentals to the score is a strategy change (new score version, spec update, old/new/why) and an owner decision at the review.

---

# 3. Source: NSE filings

Verified by read-only probes on 2026-10-03 (AUDIT_FIX_LOG "Decision F1"); the open points were checked again in step F1 (findings below).

| Feed | Covers | Gives |
|---|---|---|
| `api/corporates-financial-results?index=equities&symbol=X&period=Quarterly\|Annual` (also `from_date`/`to_date`, with or without a symbol) | filings up to Jan 2025, history to 2007 | per filing: period end (`toDate`), `broadCastDate`, consolidated flag, audited flag, revision fields, link to an XBRL file |
| `api/integrated-filing-results?index=equities&symbol=X` | filings from Feb 2025 (SEBI integrated filing) | `broadcast_Date` and a link to an inline-XBRL HTML file. **Verify:** date-range query, annual vs quarterly, revision fields |
| `api/results-comparision` | summary incl. debt/equity | **not used**: not point-in-time |

XBRL facts (old feed): RevenueFromOperations, ProfitLossForPeriod, ProfitOrLossAttributableToOwnersOfParent, Basic EPS, ProfitBeforeTax, FinanceCosts, DepreciationDepletionAndAmortisationExpense, DebtEquityRatio (some companies only). Contexts: `OneD` is the quarter, `FourD` the year to date.

**F1 findings (probed 2026-10-03 and 2026-10-10, RELIANCE, HDFCBANK and date-range queries):**

- (a) **No comparatives.** A filing's XBRL carries only the current quarter (`OneD`) and year to date (`FourD`), not the prior-year or preceding-quarter column. Prior-year EPS therefore always comes from the stored filing of that quarter (§5.3 second branch).
- (b) Other income is `OtherIncome`. Also confirmed: `RevenueFromOperations`, `FinanceCosts`, `ProfitBeforeTax`, `DepreciationDepletionAndAmortisationExpense`, `ProfitLossForPeriod`, `BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations`, `DebtEquityRatio`.
- (c) **One parser is enough.** The integrated feed also links an XBRL file (`xbrl`, namespace `in-capmkt`, same tag names and `OneD`/`FourD` contexts). The inline-XBRL HTML (`ixbrl`) is not needed and not fetched.
- (d) Balance-sheet tags (`Equity`, `EquityAttributableToOwnersOfParent`, `BorrowingsCurrent`, `BorrowingsNoncurrent`) appear only in half-yearly (Sep, Mar) and annual filings; the Jun and Dec quarterly filings have no balance sheet. ROE and debt/equity therefore update twice a year.
- (e) **Banks** (feed flag `bank = B`, `Non-Ind-AS` format) use another tag set (`InterestEarned`, `Income`, `BasicEarningsPerShareAfterExtraordinaryItems`); the Ind-AS names are absent. Handled as §5.5 (version 1: total income, EPS as filed).
- (f) **Date-range queries.** The old feed answers an all-equities range (647 rows for 1–10 Nov 2024). The integrated feed returns 20 rows by default but pages: `type=Integrated Filing- Financials` keeps only results, `size` up to 1000, `page` 1-based, `totalCount` in the answer (F4 probe, 2026-10-10: 2,976 result filings for 1–14 Aug 2026). So both the backfill and the daily update list all stocks by date range, month by month; a window that fails or returns fewer rows than `totalCount` is reported and listed again by the next run.
- Old-feed filings before about 2013 have no XBRL file (the link ends in `/xbrl/-`); they are not listed.
- A quarterly and an annual filing share the March period end, so the revision number counts per (period end, type, basis). The integrated feed does not say quarterly or annual: its period type is set by the parser (F2) and its revisions renumbered then.
- Revision fields: old feed `reInd` (`N`, `I`, `-`) and `oldNewFlag`; integrated feed `type_Sub` (`Original`), `revised_Date`, `revision_Remark`. Their meaning is not documented; they are stored verbatim (`revision_flags`) and revisions are numbered by broadcast order, which is what point-in-time selection needs.

Fetching: the existing NSE HTTP helper (`nse_http`), one request per second at most, back-off on 403/429, every raw file cached unchanged under `data/raw/fundamentals/` with its SHA-256, resumable (a filing already stored, with an unchanged file, is not fetched again; `vcp fundamentals fetch`). Credentials are not needed. A source that cannot be reached is a warning, never a failed daily run.

---

# 4. Data model

DATABASE_SCHEMA §36–39 stands (`fundamental_snapshots`, `fundamental_metrics`, `fundamental_data_quality`, hard-gate flags). Added by this spec:

- `fundamental_filings` (raw manifest): `filing_id`, `instrument_id`, `period_end`, `period_type`, `statement_basis`, `broadcast_at` (IST, timezone-aware), `source_feed`, `url`, `revision_number`, `sha256`, `cache_path`, `fetched_at`, `status` (OK | FETCH_ERROR | PARSE_ERROR | NOT_APPLICABLE). One row per filing seen, whether or not it parsed.
- `fundamental_research_scores` (§12): the shadow score, kept apart from `setup_scores`.

Keys follow §36: `(instrument_id, period_end, period_type, statement_basis, revision_number)`. Nothing is overwritten; a restatement is a new revision.

---

# 5. Parsing rules

1. **Quarter values.** Use the `OneD` (quarter) context. If it is missing, derive the quarter from year-to-date values: Q2 = H1 − Q1, Q3 = 9M − H1, Q4 = annual − 9M. A derived value is marked `ESTIMATED`.
2. **Basis.** Keep CONSOLIDATED and STANDALONE as separate snapshots. Policy `prefer_consolidated` (DATABASE_SCHEMA §36); growth only between periods of the same basis; a switch gives NULL growth with status `BASIS_CHANGE`.
3. **EPS** is the basic EPS (continuing and discontinued operations) as filed. For a year-on-year comparison the prior-year EPS is taken from the same filing's comparative column if it has one (it has none: F1 finding a); otherwise from the stored filing of that quarter, multiplied by the price factors of the splits and bonus issues whose ex-date falls after that filing's broadcast date and on or before the later filing's, so a split does not look like an earnings fall. Broadcast dates, not period ends, because Ind-AS 33 restates EPS for a bonus or split that happens before the results are approved (RELIANCE: the 1:1 bonus went ex on 2024-10-28, after the Sep 2024 quarter ended; that quarter, filed on 14 Oct, shows pre-bonus EPS, the Dec 2024 quarter post-bonus). The factors come from our corporate-action tables as currently known (`known_to IS NULL`): a split or bonus is announced weeks before its ex-date, so using today's knowledge of past actions does not look ahead.
4. **Operating profit** = profit before tax + finance costs + depreciation and amortisation − other income; **operating margin** = operating profit / revenue from operations. Tags confirmed in F1.
5. **Financial companies** (banks, NBFCs, insurers) use other statements. Version 1: revenue = total income, EPS as filed, operating margin NULL with status `NOT_APPLICABLE`. Revisited after F1 shows how many stocks this covers.
6. **Units.** Facts in NSE's XBRL are already plain rupees (the "Crores" tag only says how the filer displayed them) and per-share amounts in rupees (verified F2, RELIANCE and HDFCBANK). No scaling is applied; a currency other than INR is `INVALID`. A filing that fails a sanity check (negative revenue, EPS outside ±10,000, period end after the broadcast date, period end different from the listing) is stored as `INVALID` and not used.
7. **Annual filings.** The old feed's annual filing repeats the last quarter in `OneD` and carries the year in `FourD`, plus the balance sheet (verified on RELIANCE 2024-03-31). It becomes an `ANNUAL` snapshot with the year as scope `ANNUAL`; the Q4 quarter comes from the Q4 quarterly filing. A filing of the integrated feed is a `QUARTER` snapshot; if it is a March filing its `FourD` (the year) is kept as scope `YTD`.
8. **Where the numbers live.** `fundamental_facts` (one row per snapshot, scope and item: revenue, other_income, finance_costs, depreciation, pbt, net_profit, profit_owners, eps; equity, borrowings, debt_equity_ratio; is_financial). Growth rates, margins and the split adjustment of prior-year EPS (rule 3) are computed from these in F3, not stored by F2: F2 keeps EPS exactly as filed (RELIANCE Q3 FY24 reads 25.52, Q3 FY25 reads 13.70 after the 1:1 bonus).

---

# 6. Metrics

Defined as in SCORING_SPECIFICATION §7; computed per stock, per `as_of_date`, from the latest filed quarter available then.

| Metric | Definition | NULL when |
|---|---|---|
| `eps_yoy` | (EPS − EPS a year earlier) / \|EPS a year earlier\| × 100 | prior EPS is missing or ≤ 0 (`NEGATIVE_BASE`; a turnaround is flagged, not given a percentage) |
| `eps_qoq` | same against the preceding quarter | preceding EPS missing or ≤ 0 |
| `sales_yoy` | revenue from operations, year on year, % | prior revenue missing or ≤ 0 |
| `eps_acceleration` | `eps_yoy` of this quarter − `eps_yoy` of the preceding quarter (percentage points) | either is NULL |
| `margin_expansion` | operating margin − operating margin a year earlier (percentage points) | either margin is NULL |
| `roe` | trailing four quarters' profit attributable to owners (net profit when not filed) / average of the equity attributable to owners at the last two balance-sheet dates (one date when only one exists), % | equity not filed or fewer than four consecutive quarters |
| `debt_to_equity` | borrowings (current + non-current) / equity from the latest balance sheet; the filed `DebtEquityRatio` only when no balance sheet exists (F3: some filings carry a filed ratio of 0 next to real borrowings, RELIANCE 2024-03-31) | neither exists |

Also computed: trailing-twelve-month EPS on the latest share basis (for the negative-EPS hard gate), the operating margin of the latest quarter and the number of quarters available.

Implementation (F3): `fundamentals/metrics.py`, `compute_view(snapshots, as_of, share_actions)`, a pure function; `vcp fundamentals show SYMBOL [--date]` prints it. Hard-gate reasons: `NO_DATA`, `INSUFFICIENT_DATA` (availability below `fundamentals_min_availability`), `NEGATIVE_TTM_EPS`. Writing the results to `fundamental_metrics` / `fundamental_data_quality` for every stock and date is part of F4.

---

# 7. Point in time

- `available_at` = the filing's broadcast timestamp. A value is usable for scan date `D` only if `available_at` ≤ 15:30 IST on `D` (the close). A filing broadcast in the evening of `D` is first used for `D+1`, because the scan of `D` models a decision made at the close of `D`.
- For each period take the highest revision with `available_at` ≤ the cutoff. A restatement never changes what an earlier scan saw.
- Metrics are computed by a pure function of the stored filings and `as_of_date`; the same inputs give the same output. A test recomputes a past date after later filings are added and expects the same result.
- Backtests and the research (§12) use only this function, never "the latest value".

---

# 8. Data quality, staleness and hard gates

- Statuses (PROJECT_DESIGN §32): `AVAILABLE`, `MISSING`, `STALE`, `CONFLICTING`, `ESTIMATED`, `RESTATED`, `INVALID`, plus `BASIS_CHANGE`, `NEGATIVE_BASE`, `NOT_APPLICABLE`.
- Availability score per stock = share of the seven score metrics that are not NULL (`fundamentals_min_availability` 0.5, scoring.yaml).
- **Stale** when the newest usable quarter's period end is more than 120 days before `as_of_date` (`max_fundamental_staleness_days`); shown as a warning, not hidden.
- **Hard-gate flags** (negative trailing EPS, insufficient data) are stored as `fundamental_hard_gate_pass` / `fundamental_gate_reason`. Under §2 they are shown only; no list is filtered by them yet.
- A missing value is NULL and shown as "N/A", never 0.

---

# 9. Scope and history

```yaml
fundamentals:
  scope: universe          # universe | eq  (every main-board stock)
  history_from: 2023-10-01
  request_interval_seconds: 1.0
```

A stock that enters the universe later is fetched on the next update; a stock with no filings (new listing) shows "N/A", with the reason "No results filed yet". Recent IPOs are covered by `scope: eq`, if chosen.

---

# 10. Pipeline

Implemented in F4 (`fundamentals/pipeline.py`, `cli_fundamentals.py`):

- `vcp fundamentals backfill [--limit N]`: lists every result filing broadcast since 15 months before `history_from` (all stocks, month by month), keeps those of stocks eligible in any universe snapshot since `history_from` (§9) with a period end in range, and downloads the missing ones; standalone filings only where no consolidated one exists for the period (policy `prefer_consolidated`). The network part runs with the database closed (DuckDB has one writer): the database is opened read-only at the start (which stocks) and read-write at the end for a few minutes (manifest, parse, views). A file already in the raw cache is not downloaded again, so an interrupted backfill resumes; if the database is busy at the end, the downloads are kept and the next run records them.
- `vcp fundamentals update [--days N]`: the same for recent filings, with no gaps:
  - **No skipped days.** Each run records how far the date-range listing is complete (`fundamental_fetch_runs.complete_through`: the end of the last window before the first failed one). The next update lists from 3 days before that date (late listings), so a week or a month without a daily run is caught up on the next one. The first update without such a record lists the last `update_days` (7).
  - **Stocks new to the scope.** The scope is every stock eligible in any universe snapshot since `history_from`, so a stock that leaves the universe and returns keeps being updated meanwhile and has no gap. A stock that enters the scope for the first time has its whole history listed by symbol on the next update (at most `new_stock_limit`, 100, per run; the rest on the following runs) and is then marked in `fundamental_stock_history`. A stock with filings already counts as listed; the backfill marks every stock when its listing is complete.
- `vcp fundamentals status`: filings by status and error rate, snapshots, stocks with data, stored views, newest broadcast and last fetch.
- `vcp fundamentals show SYMBOL [--date]`: the view of one stock as of a date.
- `vcp run daily`: runs `fundamentals update` after the paper ledger and before the serving copy when `data.fundamentals.daily_update` is true. NSE being unreachable is a warning inside the step (exit 0); only a real error fails the step.
- **Stored views.** `fundamental_metrics` and `fundamental_data_quality` hold one row per stock and date on which its view changed (the first date whose close sees a new filing), keyed `(instrument_id, as_of_date)`. The view of any date D is the row with the greatest `as_of_date` ≤ D, with STALE when D is after `stale_after`. A filing that arrives late (an earlier date than rows already stored) recomputes the later rows. Rows are computed by the pure function of §6–8, so they equal a recompute.
- The serving copy carries the fundamental tables, so the dashboard reads them like everything else.

---

# 11. Display (read only)

- `GET /api/v1/stocks/{symbol}/fundamentals`: the quarters shown (period end, **available at**, basis, revenue, EPS, margin), the metrics of §6, availability, data status and freshness. `GET /api/v1/fundamentals`: one row per universe stock with the metrics, for sorting and filtering.
- **Fundamentals page** (`/fundamentals`): table in the Screener's theme, sortable and filterable by EPS growth, sales growth, availability; columns as FRONTEND_SPECIFICATION §17, with period end and publication date as separate columns.
- **Stock Analysis**: the Fundamentals card (today dashes) shows the latest quarter's numbers, the sparkline of EPS by quarter, and "Data: current / stale".
- **Screener**: optional EPS and sales growth columns and a minimum-EPS-growth filter; display and filter only (§2).
- Every page says that fundamentals are supporting information and do not change the setup score or the paper ledger.

---

# 12. Research (decides the future of fundamentals in the score)

- Shadow score: SCORING_SPECIFICATION §7 applied to the point-in-time metrics, stored in `fundamental_research_scores(instrument_id, as_of_date, score, availability, metrics_hash)`, never in `setup_scores`.
- Needs history from 2021-01-01 (the development period starts 2022-02, and year-on-year growth needs the year before). This is the larger download (about 26,000 files, about 8 hours at one request a second), run in the background in batches.
- Study, on the **development period only** (2022-02 to 2024-06): for the eligible setups of each strategy, quintiles of the shadow score against the forward returns already stored in `forward_labels` (5, 10, 20, 40, 60 sessions), breakout hit rate and the information coefficient, with the share of setups that have no score reported. Stricter than the earlier score study: publication dates only, the same stocks and dates, no tuning of bounds.
- The validation period (2024-07 onward) is looked at once, only if the development result is worth it, and the paper ledger from 2026-10 remains the real out-of-sample test.
- Output: a short report and a recommendation for the review: include with weight ≤ 15 (new score version), keep as display only, or drop. The decision is the owner's.

---

# 13. Steps

| Step | Delivers | Acceptance |
|---|---|---|
| F1 | Probe of the **verify** points (read-only, a few requests); provider interface and NSE adapter; raw cache and manifest; tables | the probe results are written into §3; one stock fetched end to end; refetch does nothing |
| F2 | Parser (XBRL and inline XBRL) to quarterly values; basis, revisions, units, derived quarters | known-answer tests on saved sample filings, including one bank and one with a split (the split is kept as filed here; the adjustment is F3) |
| F3 | Metrics, point-in-time selection, data quality, hard-gate flags (pure functions) | tests of §6–8; a recompute of a past date equals the original; the hashes of §2 unchanged |
| F4 | Backfill of 2023-10 onward for the universe; `update` and `status`; guarded daily step | counts and parse-error rate reported; a daily run with the step on a copy of the database |
| F5 | API and the Fundamentals page; the Stock Analysis card; Screener columns | contract tests; FRONTEND_SPECIFICATION updated |
| F6 | Backfill from 2021-01-01 in the background | as F4 |
| F7 | Shadow score and the research study | the report of §12 |

Each step is a branch with the full checks, a CHANGELOG entry and a spec update, as before. The owner reviews after F1 (what the data really looks like), F5 (the pages) and F7 (the result).

---

# 14. Risks

- **NSE access:** the site rate-limits and changes endpoints. Mitigation: one request a second, raw cache, resumable, a warning rather than a failure.
- **Parsing variety:** companies tag the same item differently; banks differ. Mitigation: known-answer tests on real samples, an `INVALID` status instead of a guess, a report of the parse-error rate.
- **Gaps:** ROE and debt will be missing for many stocks (equity is filed half-yearly). They show "N/A" and the score drops them (SCORING §7), so coverage is reported, not hidden.
- **History:** the old feed ends in Jan 2025 and the new one starts in Feb 2025; the join must not drop or double a quarter. Test: no gap or duplicate period per stock across the boundary.
- **Look-ahead:** the only protection is §7. It is tested, not assumed.
