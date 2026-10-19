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
| F2.5 | **Shareholding** (promoter, FII/FPI, DII, public, promoter pledge) is added as step F8, display and research only, after F5 (§15). | Owner, 2026-10-10 |
| F2.4 | **History:** from 2023-10-01 first (enough for year-on-year growth on every quarter shown); the research needs 2021-01-01 and is added later (§12). | Default taken; the owner did not choose. Change on request |

This replaces Decision F1 (fundamentals skipped, 2026-10-03). Its probe findings are the basis of §3.

---

# 1. Purpose

Show, for each stock, how its earnings and sales are growing, and find out with evidence whether this adds anything to the technical setups. Fundamentals stay secondary: a setup is valid or not on its technical structure only (PROJECT_DESIGN §29, VCP_SPECIFICATION §36).

Not in scope: valuation ratios, analyst estimates, news, any provider other than NSE, intraday data. Shareholding is added later as step F8 (§15).

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
- (e) **Undefined contexts (backfill 2026-10-11).** 108 bank filings of 2022-23 (old feed, `BANKING_*` files) put every fact in `OneD`/`FourD` but never define those contexts. The parser then takes the period from the listing: `OneD` is the quarter ending on the listed period end, `FourD` runs from 1 April of that fiscal year. Only used when the file itself has no definition (checked: AUBANK Mar-23 EPS 6.37).
- (f) **Half-year columns instead of quarters.** Some integrated-feed filings of Sep and Mar carry only the half year in `OneD` (`ReportingQuarter` "Half yearly" Apr-Sep, or "Yearly" with `OneD` Oct-Mar and `FourD` the year) and no quarter column: 207 filings of 184 universe stocks in the backfill (e.g. CGPOWER, LTTS, ZEEL), not SME stocks. The quarter is derived and marked `ESTIMATED`: Q2 = H1 - Q1, Q4 = year - 9M (or H2 - Q3 when there is no year column); a first quarter counts as its own year to date. 189 recovered; 16 stay `PARSE_ERROR` (companies that report only half-yearly, or whose earlier quarters are on a different statement basis) and 2 are 15-month periods.
- (g) **Profit attributable to owners does not always add up (F5 review, 2026-10-11).** Owners + minority interests must equal the period's profit. About a fifth of consolidated filings leave owners at 0, and a few file a wrong figure (CGPOWER Sep-25 half year: owners Rs 44.62 cr against profit 551.31 and minority 0.33, which made a derived Sep-25 quarter negative and ROE 9.5 % instead of about 16 %). When profit and the minority share are filed and owners is missing, 0 or off by more than 1 % of profit + Rs 1 crore, owners is taken as profit − minority. A large minority share that adds up (GRASIM, GMRAIRPORT) is real and kept. `vcp fundamentals parse --rebuild` re-reads every cached filing after such a parser change.
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
- **Rate limit (2026-10-10).** After about 7,600 requests in 2.5 hours at one a second, www.nseindia.com answered 403 to us while the archive host (nsearchives, which serves the XBRL files and the bhavcopy) kept working. So: `request_interval_seconds` is 2.0; downloads from the archive never touch the website (no session-cookie request); a download run stops after 20 failures in a row instead of retrying for hours; the backfill saves its target list (`data/raw/fundamentals/backfill_targets.json`) and `vcp fundamentals backfill --resume` continues from it without listing again.
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

Built in F5 (2026-10-11); as built: FRONTEND_SPECIFICATION §67.30. ROCE (FRONTEND §17) is not shown: the filings carry no capital-employed figure.

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

**F6 as built (2026-10-17 to 19).** `history_from` moved to 2022-02-01 (filings from the October 2020 quarters). `vcp fundamentals backfill --research-db data/golden_src.duckdb` adds every stock with a labelled setup in the research database since `history_from`, delisted ones included (440 stocks outside the current universe, e.g. IDFC, Ujjivan), so the study has no survivorship gap. 17,282 new filings recorded, 5 parse errors, 113 fetch errors (files NSE no longer has); 25 invalid (negative revenue) and 81 estimated quarters. NSE's site refused us after about 9 hours at one request every 2 s (2026-10-18 19:40 IST); the run was stopped and resumed the next morning, as §10. A long backfill should be split across days.

**F7 as built (2026-10-19).** `vcp fundamentals.research` (the shadow score, version `shadow-1-<hash>`, stored in `fundamental_research_scores` with its parts) and `vcp research fundamentals` (the study; `--period validation` refuses without `--unblind`). Rows: each strategy's labelled setups of its frozen config only (the research database also holds labels of earlier configs). Besides the quintiles and the IC, the report gives two regime-controlled spreads: top fifth minus bottom fifth within each scan date, and for the hard gate, pass minus fail within each date (the plain pass/fail table mixes periods).

**F7 result, development period (2022-02-01 to 2024-06-30; 45,161 setups, 84 % scored, 1.4 % with no results filed).**

| Strategy | Setups | IC 5d / 10d / 20d / 60d (t) | Top − bottom fifth, 60d | Gate pass − fail, 60d (within date) |
|---|---|---|---|---|
| VCP | 26,425 | +0.031 (3.7) / +0.040 (4.5) / +0.034 (3.5) / +0.013 (1.4) | +0.68 pp (t 0.9) | −0.13 pp (t −0.3) |
| Flat base | 8,748 | −0.003 / +0.018 / +0.013 / −0.014 | −0.03 pp (t 0.0) | +1.16 pp (t 1.7) |
| Three weeks tight | 7,440 | +0.009 / +0.003 / −0.014 / −0.023 (−1.6) | −1.89 pp (t −1.6) | −1.06 pp (t −1.2) |
| Cup and handle | 1,032 | +0.085 / +0.071 / +0.121 (2.5) / +0.095 (1.7) | +1.41 pp (t 0.3, 32 dates) | +1.74 pp (t 0.5) |
| Double bottom | 1,516 | +0.013 / +0.006 / −0.059 / +0.004 | −3.80 pp (t −1.4) | −5.13 pp (t −1.5) |
| All | 45,161 | +0.023 (2.8) / +0.029 (3.2) / +0.022 (2.2) / +0.000 | −0.11 pp (t −0.1) | −0.05 pp (t −0.1) |

* The quintiles are U-shaped, not ordered: the lowest and the highest fifth do best and the middle worst (all strategies, 60 sessions: Q1 +11.9 %, Q3 +9.1 %, Q5 +11.3 %).
* VCP alone shows a small positive rank correlation over the first 5 to 20 sessions (IC 0.03 to 0.04, also with one setup per stock and month), fading by 60 sessions, the horizon of the trades; in money it is under one point between the top and bottom fifth. Cup and handle is larger but rests on 32 usable dates.
* Parts (all strategies, 60 sessions): EPS growth QoQ is the steadiest (IC +0.024, t 3.3); margin change is negative (−0.023, t −3.0); debt to equity has the wrong sign for §7 (+0.021: more debt, slightly better); ROE nothing.
* The hard gate's 2-point advantage in the plain table disappears within dates: setups failing it did as well as those passing in the same weeks.
* Shareholding (exploratory, 66 % coverage): FII and promoter changes nothing; DII change +0.032 IC at 20 sessions (t 3.4), fading by 60; "pledge rising" slightly positive at 60 (+0.028, t 2.7), against intuition, most likely noise (many keys and horizons were tried).

**Recommendation for the April 2027 review: keep fundamentals display only; do not add them to the score.** The §7 score does not order outcomes at the trade horizon, in any strategy with enough dates, and a weight of up to 15 would rank simultaneous signals by a quantity that does not separate them. By §12 the result is not worth opening the validation period for, so it stays unopened. Not tested and not to be tuned here: the bounds, a different weighting (EPS QoQ alone), or using fundamentals as a filter; any of those would be a new hypothesis, written down before it is tested on the validation period once. The decision is the owner's.

---

# 13. Steps

| Step | Delivers | Acceptance |
|---|---|---|
| F1 | Probe of the **verify** points (read-only, a few requests); provider interface and NSE adapter; raw cache and manifest; tables | the probe results are written into §3; one stock fetched end to end; refetch does nothing |
| F2 | Parser (XBRL and inline XBRL) to quarterly values; basis, revisions, units, derived quarters | known-answer tests on saved sample filings, including one bank and one with a split (the split is kept as filed here; the adjustment is F3) |
| F3 | Metrics, point-in-time selection, data quality, hard-gate flags (pure functions) | tests of §6–8; a recompute of a past date equals the original; the hashes of §2 unchanged |
| F4 | Backfill of 2023-10 onward for the universe; `update` and `status`; guarded daily step | counts and parse-error rate reported; a daily run with the step on a copy of the database |
| F5 | API and the Fundamentals page; the Stock Analysis card; Screener columns | contract tests; FRONTEND_SPECIFICATION updated |
| F6 | Backfill from 2021-01-01 in the background (done 2026-10-19: from the October 2020 quarters, research stocks included) | as F4 |
| F7 | Shadow score and the research study (done 2026-10-19; result and recommendation in §12) | the report of §12 |
| F8 | Shareholding pattern (§15): probe, provider, parser, table, backfill, display | probe findings written into §15; known-answer tests on saved filings; categories sum to 100 % per filing; a backfill with counts and parse-error rate |

Each step is a branch with the full checks, a CHANGELOG entry and a spec update, as before. The owner reviews after F1 (what the data really looks like), F5 (the pages), F7 (the result) and F8 (shareholding).

---

# 14. Risks

- **NSE access:** the site rate-limits and changes endpoints. Mitigation: one request every 2 seconds, raw cache, resumable, a warning rather than a failure. Every request also has a wall-clock limit (`total_timeout`, 180 s): on 2026-10-11 one archive download stalled for 20 minutes without the per-read timeout firing; such an attempt is now abandoned and retried.
- **Parsing variety:** companies tag the same item differently; banks differ. Mitigation: known-answer tests on real samples, an `INVALID` status instead of a guess, a report of the parse-error rate.
- **Gaps:** ROE and debt will be missing for many stocks (equity is filed half-yearly). They show "N/A" and the score drops them (SCORING §7), so coverage is reported, not hidden.
- **History:** the old feed ends in Jan 2025 and the new one starts in Feb 2025; the join must not drop or double a quarter. Test: no gap or duplicate period per stock across the boundary.
- **Look-ahead:** the only protection is §7. It is tested, not assumed.

---

# 15. Shareholding pattern (step F8)

Added 2026-10-10 at the owner's request. Same rules as the rest of this spec: NSE only, point in time, display and research only (§2 freeze rule: nothing in the scan, score, backtest or paper ledger reads it).

**Source.** The quarterly shareholding pattern every listed company files under SEBI LODR Regulation 31, within 21 days of each quarter end, published by NSE per company with an XBRL file. It is a separate filing from the financial results (the result XBRL files of §3 carry no shareholding). **Verify in F8:** the NSE listing endpoint and its paging, the XBRL tag names and categories, how revisions appear, and how far back the files go.

**Probe findings (F8, 2026-10-13; about ten requests at one a second).**

- **Listing:** `https://www.nseindia.com/api/corporate-share-holdings-master?index=equities` with either `symbol=X` (every filing of one stock: RELIANCE back to the quarter of September 2021, 22 rows) or `from_date` / `to_date` (`DD-MM-YYYY`). It is not paged: 1-31 July 2026 returned 2,339 filings of 2,291 companies in one answer. Each row has `recordId`, `symbol`, `name`, `date` (the date the pattern is "as on"), `broadcastDate`, `submissionDate`, `pr_and_prgrp` / `public_val` (promoter and public %), `revisedData` / `revisedStatus` ("Revised"; 147 of the July filings), `newColumn` (the new layout) and `xbrl`, a file on the archive host `nsearchives.nseindia.com`, as the results files.
- **Not only quarter ends:** a company also files after events such as an allotment (RELIANCE: 8 Dec 2021, 29 Oct 2024). These are kept and marked; the quarterly series uses quarter-end dates only.
- **Three XBRL layouts** (tag set `in-bse-shp`), told apart by the lines present (`vcp_scanner/shareholding/parser.py`):
  - *NEW*, from the June 2025 quarter: contexts `<Category>_ContextI`, percentages as fractions, institutions split into domestic and foreign, pledge `EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares` (pledges only).
  - *SPLIT*, about 2022 to March 2025: contexts `<Category>I`, percentages in percent, the same split (FPI contexts spelt `...Catergory...`), pledge `PledgedOrEncumberedSharesHeldAsPercentageOfTotalNumberOfShares` (pledged or otherwise encumbered).
  - *OLD*, to about 2022: one `Institutions` group; domestic institutions are that group less foreign portfolio investors and foreign venture capital.
- **Definitions used:** FII = foreign portfolio investors (FPI categories I and II); foreign direct investment (a parent's strategic stake, e.g. JFE Steel's 15 % of JSW Steel) is a separate item, never FII. Pledge = % of the promoter group's own shares (JSW Steel June 2026: 125,650,440 of 1,083,203,750 = 11.6 %).
- **Totals:** filers differ on shares held by employee trusts (neither promoter nor public): RELIANCE leaves them out of the base (promoter + public = 100), JSW Steel counts them (promoter + public = 99.81, the trust 0.19). The parser accepts both and records the remainder as `other_pct`. On the four saved filings (one per layout plus a pledge) every category adds up to the public total exactly.

**Backfill findings (2026-10-14).** 22,645 filings of 1,378 stocks since June 2022. Three lessons, all now handled:

- **Companies without a promoter group** (most banks, ITC, L&T, many new listings such as Swiggy or Paytm) file no promoter line at all: promoter is 0 %, not unreadable (232 of the first run's 241 parse errors, 42 stocks).
- **Some total lines are wrong** (BSE 2022: 11.68 %) while the parts add up (0 + 73.85 + 26.14). Units and validity are judged from the parts (promoter + public + neither), not from the total line.
- **Some XBRL files contradict NSE's own listing** (AYMSYNTEX June 2023: promoter 0 % in the file, 73.95 % listed; IMAGICAA, ORISSAMINE, PVRINOX similar). The file adds up, so the parser cannot tell; the pipeline compares the parsed promoter % with the listing: more than 1 point apart, the filing is stored as `MISMATCH` and never shown; up to 1 point (rounding, a revision) it is kept and counted.
- **Some filings have no file at NSE** (78 of the backfill, 72 stocks): the listing gives no XBRL link (`.../xbrl/-`, 38) or the archive answers 404 (40). Stored as `NO_FILE`, never fetched again and not counted as download failures (they do not stop a run); the quarter is N/A.

Downloads from the archive give up after 45 s (20 s per wait) and start a fresh connection after a failure; 25 failures in a row stop the run cleanly (the rest is left for the next run). The first backfill stalled for 13 minutes on one connection under the results client's 180 s limit. `vcp shareholding reparse` re-reads every cached file after a parser change (no network).

**What is stored** (per stock and quarter end; `available_at` = broadcast time, as §7):

| Item | Meaning |
|---|---|
| `promoter_pct` | promoter and promoter group, % of shares |
| `promoter_pledged_pct` | promoter shares pledged or encumbered, % of promoter shares |
| `fii_pct` | foreign portfolio investors (FPI/FII), % of shares |
| `dii_pct` | domestic institutions: mutual funds, insurance companies, banks, financial institutions, pension and provident funds, alternative investment funds |
| `mf_pct` | mutual funds alone (part of DII) |
| `public_pct` | non-institutional holders (retail and others) |
| `shareholders` | number of shareholders, when filed |

Changes are computed like §6, between consecutive quarters of the same stock: `promoter_change`, `fii_change`, `dii_change` (percentage points), `pledge_change`. A missing filing is "N/A", never 0.

**Tables.** `shareholding_filings` (raw manifest, as `fundamental_filings`), `shareholding_snapshots` (one row per stock, quarter end and revision, with the items above and `available_at`). Raw files are cached unchanged under `data/raw/shareholding/`.

**Pipeline.** As §10: the backfill and the daily update list by date range, download with the database closed, then record and parse. The backfill runs only after the results backfill has finished, so the two never run at the same time against NSE. Scope and history as §9 (about 18,000 files for the universe since mid-2022, about 5 hours at one request a second).

**Display (with F5 or after it).** The stock's Fundamentals card shows the latest split and its change over one and four quarters, and flags a rising pledge. Screener columns: promoter %, FII change, DII change, pledge %.

*As built (2026-10-14; `vcp_scanner/api/shareholding.py`, FRONTEND_SPECIFICATION §67.40).* Point in time as §7: a filing counts at the close of a day when NSE broadcast it by 15:30 IST; quarter ends only (event filings are left out); of several revisions of one quarter the latest broadcast by then. Changes are percentage points against the calendar quarter one and four back; a quarter not filed (or set aside as `MISMATCH`) makes them N/A. The pledge change is N/A across the June 2025 layout switch (pledged-or-encumbered before, pledged only after); "rising" = up by 0.01 point or more over one quarter. The latest quarter is "stale" when more than 120 days old. API: `shareholding` and `shareholding_quarters` (last eight) on `/stocks/{symbol}/fundamentals`; Fundamentals table rows (`/fundamentals`) carry `shareholding_as_on`, `shareholding_stale`, `promoter_pct`, `promoter_change_1q`, `fii_pct`, `fii_change_1q`, `dii_pct`, `dii_change_1q`, `pledged_pct`, `pledge_rising` (the holdings and changes sortable), with filters `min_promoter_pct`, `max_pledged_pct` (a stock with no pledge filed passes), `fii_buying` (FII up over the quarter) and `pledge_rising`. *Changed 2026-10-15 (owner):* shareholding is on the Fundamentals page, not the Screener, which stays technical plus EPS and sales growth.

**Research.** The F7 study may test shareholding changes as an extra feature (for example a rise in institutional holding before a breakout). Any use in the score is a strategy change decided at the review, as §2.

