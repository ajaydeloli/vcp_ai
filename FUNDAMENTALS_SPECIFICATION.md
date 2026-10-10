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

Verified by read-only probes on 2026-10-03 (AUDIT_FIX_LOG "Decision F1"); the points marked **verify** are checked again in step F1 before code depends on them.

| Feed | Covers | Gives |
|---|---|---|
| `api/corporates-financial-results?index=equities&symbol=X&period=Quarterly\|Annual` (also `from_date`/`to_date`, with or without a symbol) | filings up to Jan 2025, history to 2007 | per filing: period end (`toDate`), `broadCastDate`, consolidated flag, audited flag, revision fields, link to an XBRL file |
| `api/integrated-filing-results?index=equities&symbol=X` | filings from Feb 2025 (SEBI integrated filing) | `broadcast_Date` and a link to an inline-XBRL HTML file. **Verify:** date-range query, annual vs quarterly, revision fields |
| `api/results-comparision` | summary incl. debt/equity | **not used**: not point-in-time |

XBRL facts (old feed): RevenueFromOperations, ProfitLossForPeriod, ProfitOrLossAttributableToOwnersOfParent, Basic EPS, ProfitBeforeTax, FinanceCosts, DepreciationDepletionAndAmortisationExpense, DebtEquityRatio (some companies only). Contexts: `OneD` is the quarter, `FourD` the year to date.

**Verify in F1:** (a) whether a filing carries the prior-year and preceding-quarter comparatives; (b) the other-income tag; (c) the inline-XBRL fact names of the new feed; (d) the balance-sheet tags (equity, borrowings) of the half-yearly statement; (e) the taxonomy of banks, NBFCs and insurers; (f) the all-equities date-range query for the daily update.

Fetching: the existing NSE HTTP helper (`nse_http`), one request per second at most, back-off on 403/429, every raw file cached unchanged under `data/raw/fundamentals/` with its SHA-256, resumable (a filing already stored is not fetched again). Credentials are not needed. A source that cannot be reached is a warning, never a failed daily run.

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
3. **EPS** is the basic EPS (continuing and discontinued operations) as filed. For a year-on-year comparison the prior-year EPS is taken from the same filing's comparative column if it has one (**verify**); otherwise from the stored filing of that quarter, multiplied by our corporate-action adjustment factor between the two period ends (splits and bonus issues), so a split does not look like an earnings fall.
4. **Operating profit** = profit before tax + finance costs + depreciation and amortisation − other income; **operating margin** = operating profit / revenue from operations. Tags confirmed in F1.
5. **Financial companies** (banks, NBFCs, insurers) use other statements. Version 1: revenue = total income, EPS as filed, operating margin NULL with status `NOT_APPLICABLE`. Revisited after F1 shows how many stocks this covers.
6. **Units** are normalised to rupees; a filing in lakhs or crores is converted using its own scale tag. A filing that fails a sanity check (revenue negative, EPS outside ±10,000, period end after broadcast) is stored as `INVALID` and not used.

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
| `roe` | trailing four quarters' profit attributable to owners / average of the equity at the last two balance-sheet dates, % | equity not filed or fewer than four quarters |
| `debt_to_equity` | the filed `DebtEquityRatio`, or borrowings / equity from the balance sheet | neither exists |

Also stored: trailing-twelve-month EPS (for the negative-EPS hard gate) and the number of quarters available.

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

- `vcp fundamentals backfill [--from DATE] [--symbol X]`: lists filings per stock and fetches the missing ones. It works in small batches and, per batch, takes the daily-run lock, writes, and releases it, so the evening run is never blocked by an hours-long backfill (DuckDB has one writer). Stops cleanly on Ctrl-C and resumes.
- `vcp fundamentals update`: the incremental step. Asks the all-equities date-range query for filings since the last stored broadcast date (**verify**), fetches those of universe stocks, parses, stores, recomputes metrics.
- `vcp fundamentals status`: counts by status, newest broadcast, stocks without data, parse errors.
- `vcp run daily`: a guarded step after the paper ledger and before the serving copy. A failure prints a warning and never fails the run; the daily summary line gains "fundamentals: N new filings".
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
| F2 | Parser (XBRL and inline XBRL) to quarterly values; basis, revisions, units, derived quarters | known-answer tests on saved sample filings, including one bank and one with a split |
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
