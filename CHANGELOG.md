# Changelog

All notable changes to the Institutional-Grade NSE VCP Scanner will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Fundamentals: gentler on NSE after the website rate-limited the first backfill (403 after ~7,600 requests): one request every 2 seconds, no website cookie request for archive downloads, a stop after 20 failures in a row, and `vcp fundamentals backfill --resume` from the saved target list.

- Stock chart: the Pivot and Stop titles no longer sit inside the plot over the latest candles and the breakout mark; a legend at the top left shows them with their prices. The breakout arrow is orange and larger (the blue one was hard to see on the dark background), and the chart leaves six empty bars after the last one so recent candles clear the price axis.

- FUNDAMENTALS_SPECIFICATION §15 (draft): shareholding pattern (promoter, FII/FPI, DII, mutual funds, public, promoter pledge) from NSE's quarterly Regulation 31 filings as step F8, display and research only. Docs only; the feed is probed after the results backfill.

- Fundamentals: `vcp fundamentals update` no longer skips days or new stocks. It resumes listing from the last date listed completely (new table `fundamental_fetch_runs`), so days without a daily run are caught up, and it fetches the whole filing history of stocks entering the scope for the first time (up to `new_stock_limit` per run; table `fundamental_stock_history`). Stocks that leave the universe stay in scope, so a return leaves no gap.

- Fundamentals F4: `vcp fundamentals backfill`, `update`, `status`. Lists all result filings by date range (the integrated feed pages: `size`/`page`/`totalCount`), keeps universe stocks since `history_from` (consolidated preferred), downloads with the database closed, then records, parses and stores one view per stock and change date in `fundamental_metrics` / `fundamental_data_quality` (now keyed by stock and as-of date; the empty F1 tables are replaced on migration). Config `data.fundamentals` in data.yaml. `vcp run daily` runs `fundamentals update` after the paper ledger (off with `daily_update: false`).

- Fundamentals F3: `fundamentals/metrics.py` computes, as of the close of any date, EPS and sales growth (YoY, QoQ), EPS acceleration, margin expansion, ROE, debt/equity, TTM EPS, availability score, staleness and hard-gate flags (shown only) from the stored snapshots: point in time (broadcast by 15:30 IST, highest revision then), consolidated preferred, earlier EPS put on the current share basis across splits and bonus issues. `vcp fundamentals show SYMBOL [--date]` prints it. The pipeline test now checks that stored fundamentals leave the score hash unchanged.

- Fundamentals F2: `vcp fundamentals parse` reads the stored NSE filings (both feeds, banks included) into `fundamental_snapshots` and the new `fundamental_facts` (quarter, year-to-date, annual and balance-sheet values; EPS as filed). Basis, period type and revisions are set from the file; a quarter missing from a filing is derived from year-to-date values and marked ESTIMATED; failed sanity checks are INVALID; unreadable files are PARSE_ERROR and not retried. Idempotent. Scan, score, backtest and paper code are unchanged.

- Fundamentals F1: `vcp fundamentals fetch [--symbol X] [--from D --to D]` lists NSE result filings (old results feed and integrated-filing feed), stores each XBRL file unchanged under `data/raw/fundamentals/` with its SHA-256, and records it in the new `fundamental_filings` manifest. Resumable: a stored, unchanged filing is not fetched again; an unreachable source is a warning. Also adds the empty tables `fundamental_snapshots`, `fundamental_metrics`, `fundamental_data_quality`, `fundamental_research_scores`. Nothing the scan, score, backtest or paper ledger reads is changed. Probe findings in FUNDAMENTALS_SPECIFICATION §3.

### Added
- FUNDAMENTALS_SPECIFICATION.md (draft): fundamentals from NSE filings, point-in-time, display and research only until the 2027-04-01 review; steps F1-F7. Decision F2 in AUDIT_FIX_LOG. Docs only; nothing built yet.
- Reports page fits the window: a slim Generate bar, then the report list and the report, each scrolling on its own.
- Reports page: Generate a report for a past day or week (`POST /api/v1/reports/generate`, the API's only write: an HTML file under `reports/`). The weekly summary now follows the week's last trading day, with a catch-up for a missed week (FRONTEND_SPECIFICATION 67.27, STRATEGY_SPECIFICATION 21.10).
- Reports: the VQI base date and what VQI is are a note under Market regime.
- Reports call the index VQI, as the dashboard does.
- Daily and weekly reports now show the index (VQI) on the same 250-day window as the Market Overview page, with its base date, so both show the same level.
- Backtest page (`GET /api/v1/backtests`, stored runs from the research database, read-only) and Reports page (`GET /api/v1/reports`, the daily and weekly HTML reports); drafts for review (FRONTEND_SPECIFICATION 67.26, 67.27). `vcp api serve` gains `--research-db` and `--reports-dir`.
- README status section rewritten for the monitoring phase.
- Daily and weekly HTML reports (`vcp report daily|weekly`; written by the daily run after the serving copy, weekly on Fridays), read-only, built from the same queries as the dashboard (STRATEGY_SPECIFICATION 21.10). Monitoring step M4.
- Screener check boxes Universe only and EQ only: unchecked, stocks outside the scan universe are listed with prices only and the reason they are outside (FRONTEND_SPECIFICATION 67.24).
- Recent IPOs page and `GET /api/v1/ipos`: main-board stocks with fewer than 253 bars, display only, outside the universe, scans, scores, strategies and paper ledger (FRONTEND_SPECIFICATION 67.23). The duplicate VCP Scanner page is removed.
- Dashboard pages Strategies, Paper Trading and System Status (first draft, read-only, FRONTEND_SPECIFICATION 67.22).

### Changed (Stock Analysis layout)
- Stock Analysis: each card has its own colour, as on Market Overview (spec 67.21g).
- Details panel: Stage (weekly stage) added after Pivot.
- Details panel: one font size for all figures, vertical lines between open, high, low and volume, and the setup section moved below Score / RS rank / Pivot.
- Stock Analysis: chart three quarters, details one quarter of the card.
- Stock Analysis: the VCP pattern, Trend Template, Fundamentals and Score breakdown cards are equal in width and height.
- Stock chart toolbar: "Range :" and "Chart :" labels on the two left groups.
- Stock chart toolbar: range group, then chart type icons (candlesticks, OHLC bars, line), with the SMA check boxes on the right; groups separated by vertical lines (spec 67.21d).
- Stock chart: check boxes to show or hide SMA 20, SMA 50 and SMA 200, and a Candles / OHLC switch (spec 67.21c).
- Stock Analysis: a vertical line between the chart and the details panel.
- Stock Analysis: the symbol and strategy buttons are the first section of the right-hand panel; the left column is the chart alone (spec 67.21b).
- Stock Analysis: symbol and strategy buttons in the left column, taller chart, right details separated by lines instead of boxes (spec 67.21a).
- The Dashboard page is renamed Market Overview (address unchanged).
- Stock Analysis: symbol first, then the chart on the left and a details panel on the right (close, live with day open/high/low/volume, setup, score, RS rank, pivot) (spec 67.21).

### Changed (Screener filter bar)
- Screener and watch list: a # column numbering the rows.
- Screener: removed the note under the results table.
- Screener: 15 stocks per page instead of 25.
- Screener: the "X of Y stocks match" count sits at the far right of the Results title line.
- Screener: filters in a set order with Reset filters at the end of the single row; the card is collapsed by default and a More button shows the weekly stage row.
- Screener: all filters in one row on wide screens; weekly stage and Reset filters on a second row.
- Screener: removed the VCP setup and Setup class filters; Strategy and Min setup grade cover them.
- Screener: equal column widths; titles and values centred except Symbol (left).
- Screener: removed the always-empty Company and Trend columns; fixed column widths and alignment so the gaps are even (spec 67.20a).
- Screener: removed the header card; the filter bar is the first thing on the page.
- Screener: removed the preset buttons and the left filter panel; all filters are one bar above the results table, which now uses the full width (spec 67.20). New "On strategies" filter replaces the "On several strategies" preset.
- Fixed "Reset filters", which left the Trend Template, near-high, RS, conditions, grade, status and class filters set.

### Added (D4: live prices, display only)
- `src/vcp_scanner/live/`: in-memory live quote cache with an Upstox feed (default) and a Kite feed (one `provider` setting in `config/live.yaml`), refreshed only during NSE hours, with clear states for a missing token, rate limit, error and stale data. No automatic provider switch; no writes to any database or file; the daily run does not import it.
- API: `GET /api/v1/live/quotes`, `/live/indices`, `/live/status` (cache only, stamped live or closed).
- Dashboard: NIFTY 50 and SENSEX tiles with live value, change and intraday line; a Live column beside the stored Close on the screener and watch list; a live line on the stock page; a feed banner and status-bar chip. Stored values and rankings are unchanged.
- Provider additions (additive): `UpstoxProvider.get_full_quotes/get_intraday_candles`, `KiteProvider.get_live_quotes`, `ProviderRateLimited`.
- Tests: fake-provider unit tests, isolation test, contract samples; spec 67.19.

### Changed (Dashboard redesign)
- Breadth and Market stage charts are taller to fill their cards.
- Index price action and Leadership charts are taller (262 px) so they fill their cards.
- Index figures are titled VQI vs 50-day MA and VQI vs 200-day MA.
- Index price action card shows the index against its 50-day average as well as its 200-day average (index_days.pct_from_50).
- Removed the Details link from the Feedback loop card (the sentence stays as the tooltip of each reading).
- Removed the explanatory footnotes under the Market stage chart and under the market cards (the spec keeps the explanation).
- All eight cards are 23 rem tall (charts 200 px), the height of the Breadth card, which does not scroll; "Advancers minus decliners" is now "Net advancers".
- All eight dashboard cards are one height; Paper trading and Recent activity have their own colours; Recent activity lists the stocks that joined or left the universe with the reason (spec 67.18g).
- Market stage card: chart of the share of scanned stocks in Stage 1-4 over 52 weeks (rebuilt with the scan's own stage rule, read-only) with today's share and count of each stage on the right (spec 67.18f).
- Market stage and Feedback loop cards swapped (Market stage now sits beside Breadth).
- Breadth card laid out like the Index and Leadership cards: the two charts on the left; regime, % above 50-day, % above 200-day and advancers minus decliners as figures on the right (readings list and Details removed).
- Leadership chart shows whole numbers on the axis; the cross on the market charts snaps to the nearest point of a line.
- Index and Leadership figures: the small note (last 25 sessions, today, points) now sits beside the number; the Failed breakouts note was removed.
- Leadership card laid out like Index price action: chart on the left; new 52-week highs, new lows, failed breakouts and leaders vs index as figures on the right (readings list and Details removed).
- Index price action and Leadership charts made taller (270 px) so they fill their cards.
- Index price action card: chart on the left, figures on the right (index vs 200-day average, accumulation days, distribution days, last 25 sessions); no score or Details. `/api/v1/market/health` adds `index_days`.

- Our index is renamed "VCP Quality Index (VQI)" (was VCP Universe Index); charts label it VQI. Same index, still not NIFTY.

- Paper trading and Recent activity side by side; today's date at the far right of the top bar (not in the bottom bar); search is an icon that opens the search box.

- Market health header removed; each card has its own border colour; the gauge tile tooltip names what pulls the score down and holds it up.

- Market health: no outer card; two columns (Index price action and Leadership, Breadth and Feedback loop, Market stage and Setups today); Setups today laid out like Market stage.

- Top navigation bar with a logo side bar; tile row with the Market health score gauge; Market health as one coloured card with five inner cards (Market stage moved in); setup counts in their own card; data status and date in the bottom bar. NIFTY 50 and SENSEX tiles wait for an index feed. `/api/v1/market/health` readings gain `short`.

### Added (Dashboard: Market health verdict)
- Market health verdict: a 0 to 100 score and a label (Confirmed uptrend, Uptrend under pressure, Correction, Downtrend) from straight-line scores of the nine readings; `/api/v1/market/health` adds `verdict` and a `score` per reading. Display only.

### Changed (Dashboard: Market health charts)
- Market overview card merged into the Breadth card of Market health (regime, breadth, days on, rule).

- Top VCP setups card removed from the dashboard. Market health is four separate cards (Index price action, Leadership, Breadth, Feedback loop) with charts; `/api/v1/market/health` adds `points` and `trades`. Display only.

### Added (Dashboard: Market health)
- Market health card on the dashboard and `GET /api/v1/market/health`: index vs its 50/150/200-day averages, distribution days, new 52-week highs vs lows, failed breakouts, leaders vs the index, breadth above the 50- and 200-day averages, advance/decline line, and the last 10 paper trades; green/amber/red/grey. Display only; feeds no rule.

### Fixed (Dashboard: market charts)
- The VCP Universe Index and breadth charts show the latest value on the price axis again. Frontend only.

### Changed (Dashboard: index name, breadth chart)
- Our equal-weight index is named "VCP Universe Index". The breadth chart no longer prints the 40 % label over the latest value.

### Changed (Dashboard: page roles)
- Dashboard is now a market overview (market stage, our index, breadth, top VCP setups, activity, paper); its watch list is the Screener (strategy selector, A+ VCP / VCP / VCP like / Forming / Pivot ready / Breakouts / On several strategies presets, setup class filter, pivot column); chart and setup details are on the Stock Analysis page; the Watchlist page holds several user-made lists (kept in the browser). `GET /api/v1/screener` gains `classification`, `min_strategies` and `pivot`.

### Added (Dashboard: Watchlist, first design)
- `/watchlist` page and a ★ button on the Screener and Stock Analysis pages. The list is kept in the browser only (no write to any database); `GET /api/v1/screener` takes `symbols`.

### Changed (Dashboard: Screener links)
- A symbol in the Screener, and a search from the Screener page, opens the Stock Analysis page (`/stocks/SYMBOL`) instead of the dashboard. Frontend only.

### Changed (Dashboard: Stock Analysis default)
- `/stocks` opens the best-ranked VCP setup instead of an empty page. Frontend only.

### Added (Dashboard: Stock Analysis, first design)
- `/stocks/SYMBOL` page: chart, setup and Trend Template cards, setups across strategies, history and paper trades of the stock; `GET /api/v1/stocks/{symbol}/history`. Read-only.

### Fixed (Dashboard D2.4)
- The universe index tile and the "Data of" tile in the top bar have the same fixed height (76 px), padding and type sizes. The data dates appear once (navigation box); the bottom bar keeps the warnings and the disclaimer. Frontend only.

### Added (Dashboard: Screener)
- `/screener` page and `GET /api/v1/screener`: every scanned stock with its Trend Template result and VCP setup; filters, seven built-in presets, sortable columns, 25 rows a page; a symbol opens on the dashboard (`/dashboard?symbol=X`). Read-only.

### Changed (Dashboard: serving copy on)
- The scheduled daily run (`scripts/daily_run.sh`) now refreshes the dashboard's serving copy as its last step (`--serving-copy`), so the page shows each evening's scan without a manual refresh.

### Changed (Dashboard D2.3)
- The lists above the watch list (Top setups, A+ VCP, VCP, Forming, Breakout watch, the other strategies, On several lists) are chosen from one dropdown.

### Changed (Dashboard D2.2: layout after the second mockup)
- Watch list (ten rows a page, page numbers) and the chart in separate cards side by side; setup overview in four cards (VCP pattern, Trend Template, Fundamentals marked not available, score ring); six KPI tiles with a breadth donut; our own universe index in the top bar; data status in the navigation. Frontend only.

### Fixed (Dashboard D2.1)
- The setups and chart cards, and the recent activity, market overview and paper trading cards, now have equal heights. `scripts/dashboard.sh` starts the API with the project's own `vcp` (it did not start before).

### Added (Dashboard D2: the web page)
- `frontend/` (Next.js, TypeScript, Tailwind, Lightweight Charts): the `/dashboard` page of the read-only dashboard: KPI cards, ranked setups per strategy, candlestick chart with pivot and stop, setup details, recent activity, market overview, paper panel and status bar. `scripts/dashboard.sh` starts the API and the page (`http://localhost:3000/dashboard`).
- API: `GET /api/v1/search` (symbol or company name) for the page's search box.

### Added (Dashboard D1: serving copy and read-only API)
- `vcp api serve` starts a read-only local API (127.0.0.1:8000, GET only) for the dashboard: status, summary, market breadth and regime, ranked setups per strategy, stocks (bars with averages, setups with marks and score parts, Trend Template conditions), recent activity and the paper panel with the review criteria. It reads only a serving copy of the database and picks up a replaced copy without a restart.
- `vcp run daily --serving-copy` refreshes that copy as the run's last step (atomic; off by default until enabled). Adds `fastapi` and `uvicorn` to the dependencies.

### Changed (Dashboard D0: spec signed off)
- FRONTEND_SPECIFICATION §67 (Dashboard v1: read-only local API and web page for the monitoring phase) is signed off: FastAPI + Next.js, a serving copy of the database refreshed by the daily run, the dashboard page only, dark theme. Docs only.

### Added (Monitoring phase M3: paper ledger)
- `vcp paper update` (run by the daily run) records each evening what the frozen rules decide for every paper strategy (watch list, entries, exits, skipped entries) in a new append-only table `paper_events`; `vcp paper status` shows open paper positions and results so far. Database schema version 3 (the table is created on first open).

### Changed (Monitoring phase M2: the daily run scans every paper strategy)
- The evening run now also finds and scores Flat base, Three Weeks Tight, Cup and handle and Double bottom setups for each new session, and updates their forward labels. All five strategies are marked `stage: paper` and are frozen until the paper review.

### Changed (Multi-Strategy phase step 7d: backtest defaults)
- Backtests now default to the rules chosen on the development period for every strategy: enter on a real cross of the pivot (at most 5 % above it), only while at least 40 % of the universe is above its 50-day average, and exit at the setup low or −8 % (60-session time exit). Set in `config/backtest.yaml` (`defaults`); the old behaviour is `--entry breakout --regime none --rule hold_s7`.

### Added (Multi-Strategy phase step 7b: entry, market regime and trailing exits for backtests)
- `vcp backtest run|walk-forward --entry cross_5`: enter only on a real cross of the pivot and at most 5 % above it.
- `--regime breadth50|ew50`: no new entries when the market (our own universe) is weak; open trades keep running.
- Exit rules `trail_e20` and `trail_s50`: exit on a close below the 20-day EMA or the 50-day average, with a −7 % first stop.
- Defaults are unchanged; existing backtests give the same results.

### Changed (Multi-Strategy phase step 5b: Cup and handle 1.1.0)
- A cup's right lip may now be at most 3 % above its left lip. Before, most "cups" were stocks that had already rallied past their old high and then paused.

### Added (Multi-Strategy phase step 5b: Cup and handle and Double bottom)
- Two new research-only strategies, `cup_handle` and `double_bottom`, with their own detectors, grades, pivots and stops, score parts and config files. The daily run does not run them.
- A double bottom stops being a setup 10 sessions after its breakout, so an old breakout is not ranked or traded as a fresh one.
- They work with `vcp compute setups|scores|labels --strategy ID`, backtests and the chart-review sheet like the step-4 strategies.
- Chart-review sheets: long bases get a longer chart, and the pattern's points (cup: left lip, bottom, right lip, handle low; double bottom: left high, two lows, middle peak) are marked.

### Changed (Multi-Strategy phase step 4c: after the chart review)
- Three Weeks Tight 1.1.0: a pattern deeper than 15 % (one that includes the sharp move into it) is no longer a full Three Weeks Tight; it stays a watch-list grade.
- Two more exit rules to compare in backtests: `hold_low8` and `hold_low5` (stop at the setup's low or 8 % / 5 %, whichever is tighter). The default rule is still `hold_s7`.

### Added (Multi-Strategy phase step 4: Flat base and Three Weeks Tight)
- Two new strategies, `flat_base` and `three_weeks_tight`, with their own detectors, grades, pivots and stops, score parts and config files (`config/strategies/`). They are research-only for now: the daily run does not run them.
- `vcp compute setups --strategy flat_base|three_weeks_tight --as-of DATE`, and `--strategy` on scores, labels, backtests and the score views for them.
- `vcp research review-sheet --strategy ID`: a chart-review sheet of stored setups with the detector's base start, pivot, stop and breakout drawn on each chart.

### Added (Multi-Strategy phase step 3: shared base measurements)
- Shared measurements for the coming base patterns: weekly bars as of a date, weekly close range and week-to-week close change, prior advance, base high/low/depth/length, and a volume dry-up ratio. They are computed on the fly; nothing is stored and no VCP result changes.

### Changed (Multi-Strategy phase step 2: strategy framework, VCP only)
- Every stored score, label and backtest now names its strategy (`strategy_id`). Existing results all belong to VCP and their values are unchanged: a rebuild of 30 Sep, 1 Oct and 10 research dates with the old and the new code gave identical patterns, scores, labels and backtests.
- New `config/strategies/vcp.yaml` registers VCP as strategy #1. Its thresholds stay in `strategy.yaml` and `scoring.yaml`.
- `--strategy` (default `vcp`) on `compute scores`, `compute labels`, `backtest run`, `backtest walk-forward`, `scores list` and `scores explain`. New `vcp compute setups --strategy vcp` (same as `compute vcp`) and `vcp config hash --strategy ID`. Validation looks are counted per strategy.
- The setup score is split into shared parts (trend, RS, volume) and a per-strategy pattern part, ready for the next strategies.
- Database: the first open of a database with the new code adds the strategy column to four tables (rebuilt in place, rows kept) and to `scan_runs`, and creates the empty tables and views for the next strategies. Schema version 2.

### Added (Multi-Strategy phase step 1: spec)
- `STRATEGY_SPECIFICATION.md`: how several base patterns (VCP first, then Flat base and Three Weeks Tight) will share the same data, gates, scores, labels and backtests. Each pattern match is its own setup, each strategy has its own ranked list, and VCP results stay exactly as they are.
- `DATABASE_SCHEMA.md` §35A: the planned tables, columns and views that add a strategy to every stored result. Nothing is built yet; that is step 2.

### Fixed (Fix C11: phantom bonus adjustments)
- NSE bonuses of preference shares ("Bonus Ncrps 4:1" and similar) were read as equity bonuses, so all earlier prices were divided by up to 47. SIYSIL then passed the Trend Template (and ranked #1) while its real price was below its 50-day average; TVSMOTOR and TVSHLTD were also affected. These records are now stored as unmodelled actions and never rescale prices.
- Safety net: a split or bonus that would move prices by 30 % or more is applied only when the raw prices around the ex-date show that jump. Otherwise the factor is withheld and the stock is blocked with a `ratio_unconfirmed` data-quality event until someone checks it.

### Added (Phase 9 steps 3–5: backtests)
- `vcp backtest run` replays stored scans day by day. It buys a breakout on volume and exits by the chosen rule, after costs. It reports every trade on its own and a 10-position portfolio (return, CAGR, drawdown, Sharpe), and stores every event.
- `vcp backtest walk-forward` runs the periods in `config/backtest.yaml`. The validation period is shown only on request and each look is counted; live paper trading from Oct 2026 is the real test.
- `vcp backtest lookahead-check` proves a stored scan used no later data by rebuilding it without that data. `vcp backtest bias-report` shows survivorship gaps and later split or bonus adjustments.

### Added (Phase 9 step 2: forward labels)
- `vcp compute labels` records what happened after each scored setup: the return after 5, 10, 20, 40 and 60 sessions, the best and worst move, whether it broke out within 20 sessions, and whether that breakout failed.
- Labels fill in as new days arrive. The daily run updates them once after scoring.

### Changed (Phase 9 step 1: faster scans)
- Scans are about 5× faster (33 s instead of 146 s for one historical date). The results are identical.
- The database client no longer retries a missing optional library thousands of times, and the main tables are written in one batch instead of row by row.

### Decided (owner, 2026-10-03)
- Fundamentals (Phase 8) are skipped for now. Setup scores keep using only the technical components, and their weights are rescaled automatically. Notes on NSE's filing data are kept in the audit log for later.

### Added (Phase 7 step 5: does the score predict outcomes?)
- `vcp research score-outcomes` checks whether higher scores did better, using score quintiles within each scan date and the information coefficient.
- **First result (2022–2025):** the score orders setups consistently but does not predict 60-day results (IC about 0). Only the VCP-shape part shows a small signal. Nothing was tuned.

### Added (Phase 7 step 4: reading scores)
- `vcp scores list` shows the ranked setups of a date: score, percentile, state, class, status and the four component scores.
- `vcp scores explain SYMBOL` shows how a stock's score was built: each component's share, and each sub-score's raw value, its 0–100 value, its points and the bounds used.

### Added (Phase 7 step 3: scores stored and in the daily run)
- `vcp compute scores --as-of DATE` scores every Trend Template passer and stores each score with all its sub-scores. VCP-like-or-better setups that are forming, pivot-ready or broken out get a ranking percentile.
- The daily run now computes scores after VCP detection.

### Added (Phase 7 step 2: final score and ranking)
- The final setup score is a weighted average of the available component scores. While fundamentals are missing (until Phase 8), their weight is shared out among the others and the score is flagged `FUNDAMENTALS_UNAVAILABLE`.
- Each setup gets a ranking percentile within its scan, computed separately for confirmed and provisional patterns.

### Added (Phase 7 step 1: component scores)
- Trend, VCP-shape, volume and RS scores from 0 to 100, each built from sub-scores with weights and bounds from `config/scoring.yaml`. A missing input is left out, never counted as zero.
- A misspelt sub-score name in `scoring.yaml` is now refused when the config loads.

### Changed (owner decision, 2026-10-03; VCP algorithm vcp-1.1.0)
- The equal-high merge is now on: a shallow dip followed by a deeper drop from the same price level counts as one contraction. The config hash changes, so new scans get new ids.

### Added (research setting, 2026-10-03)
- `vcp.swing.merge_equal_highs` (off by default) treats a shallow dip and a deeper drop from the same price level as one contraction. On 2022–2025 data it changes the class of 4 % of charts and slightly improves VCP breakout results. `vcp research outcomes --scan-config-hash` reuses older scans when only research settings have changed.

### Changed (owner decision, 2026-10-03)
- The research tools now use "let winners run" as the default exit for breakout trades: a −7 % stop, no target, and a sale after 60 days. It did best in every group on 2022–2025.

### Added (Phase 6 validation, 2026-10-03)
- **Mark-check sheet:** `vcp research review-sheet` draws the detector's marks on each chart: base start, contraction peaks and lows with depths, the pivot, and a one-line verdict. You answer only whether the marks sit in sensible places.
- **Outcome study:** `vcp research outcomes` measures what happened after each scan date, by detector class. It reports the win rate (+10 % before −7 %), excess over the same day's passers, and a breakout-trade result. The development and validation periods are reported separately, and config variants are compared on development only.
- **First result:** on month-end scans from 2024-10 to 2026-09, the VCP class did no better than looser setups. No thresholds were changed.

### Added (outcome study: exit rules, 2026-10-03)
- The outcome study now compares four exit rules for the breakout trade, fixed in advance:
  - +10 % target, −7 % stop (the current rule);
  - +20 % target, −7 % stop;
  - +20 % target, stop below the last contraction's low (never more than 8 %);
  - no target, −7 % stop, sell after 60 days.
- Validation is shown for one chosen rule only (`--validate-rule`). The CSV export has one column per rule.
- On month-end scans from 2022 to 2025, a VCP did no better than other Trend Template passers under any rule. Letting winners run was the best exit in every group.

### Changed (owner, 2026-10-03)
- Phase 6 is now accepted on the mark check and the outcome study, not a labelled golden set. The blind labelling sheet remains optional.

### Added (Phase 6 step 8: golden dataset)
- **Blind labelling sheet:** `vcp research labelling-sheet` writes a sheet of chart windows (Trend Template passers) to label in a browser. It shows no detector output, and by default no symbol or date. `vcp research import-labels` turns the labels into test fixtures.
- **Accuracy and regression:** `vcp research golden` reports the detector's accuracy on the labelled examples, with 30 % held out from tuning. A regression test fails when a detector change alters any labelled example's answer.

### Added (Phase 6 step 7: VCP results stored and in the daily run)
- `vcp compute vcp --as-of DATE` stores each stock's base, contractions, pivots, class, status and breakouts. It records every change of class or status, and each breakout as a separate event that is never rewritten. Each scan also gets an immutable run record.
- The daily run now runs VCP detection after each Trend Template scan. The summary line shows its step.
- `vcp verify scan` refuses VCP runs with a clear message: it rebuilds Trend Template runs only.

### Changed (owner decision 2026-10-02)
- A VCP no longer needs a tight right side (last 10 days within 5 %); only A+ does. Before, no stock could be a VCP while its last contraction was deeper than 5 % and recent, although VCP allows up to 12 %. On 2026-10-01 this gives 37 VCPs among the 203 Trend Template passers (was 0).

### Added (Phase 6 step 6: classification and status)
- **Classification.** Each stock's base is classified A+ VCP, VCP, VCP-like or none, with the rules it missed for each class.
- **Status.** The status is one of:
  - forming;
  - pivot-ready: within 3 % of the pivot;
  - breakout: closed above the pivot on 1.5× volume;
  - failed: back below the pivot after a breakout;
  - invalidated: trend failure, the base low broken, or volatility doubling;
  - a data status when the data isn't usable.
- **Production classes.** VCP and A+ need the Trend Template and weekly Stage 2; otherwise a base is at most VCP-like, for research.

### Added (Phase 6 step 5: pivots)
- Each base lists its possible pivots (base high, last swing high, top of the last 10 days, repeated resistance), each with its distance from the close and how often price touched and was turned back there. One is chosen as the main pivot by a fixed rule: the top of a tight right side if there is one, otherwise the last swing high.

### Fixed (Phase 6: sharp swings are not noise)
- A sharp move of 4 % or more in one or two bars is no longer merged away as noise when finding contractions. Before, nearly all merges (921 of 966 on 2026-10-01) removed such moves, e.g. ABDL's 9 % two-day rally, which hid a real contraction. `vcp.swing.short_swing_max_depth_pct: 100` restores the old rule.

### Added (Phase 6 step 4: VCP measurements)
- Every base now gets its measurements:
  - how much each contraction shrinks compared with the one before;
  - volatility per contraction;
  - volume dry-up through the sequence;
  - how tight the last 10 days are;
  - supporting up/down-volume counts.
- Volatility contraction is judged on each contraction's own daily ranges by default, not on the 14-day ATR, which lags and hid volatile final contractions (e.g. APOLLO). `vcp.volatility.measure: atr` restores the old rule; both numbers are always kept.

### Added (Phase 6 step 3: bases and contractions)
- Each stock's base (from its highest swing high in the last 130 bars, after a rise of at least 20 %) is split into contractions T1, T2, ... Small wiggles under 2 % or under 3 bars, whether dips or bounces, are merged so they don't count as extra contractions. A contraction still in progress is marked provisional until 5 bars pass without a lower low.

### Added (Phase 6 step 2: swing detector)
- Swing highs and lows (5 bars each side by default) with the date each one became known. A historical run only sees swings that were confirmed by its date, so backtests cannot peek ahead; the newest unconfirmed turns are reported separately as pending.

### Added (Phase 6 step 1: VCP configuration and domain model)
- New strategy settings for the VCP detector, with the owner's 2026-10-01 defaults: `vcp.prior_advance` (≥ 20 % rise within 120 bars before the base), `vcp.base.max_duration_days` (130 bars) and `vcp.invalidation` (spec §25). Configuration loading refuses a base plus prior advance longer than the data-quality block lifetime (253 bars).
- The VCP result objects now carry every measurement the database schema stores (swings with confirmation dates, contractions, pivot candidates, base and pass/fail measurements, gate verdicts) and refuse contradictory states, such as a production VCP outside the Trend Template or two provisional contractions.
- Adding settings changes the strategy config hash, so Trend Template scans made after this change get new scan ids; results are unchanged.

### Added (capital reductions; owner decision 2026-10-02)
- Capital reductions are their own corporate-action type, `CAPITAL_REDUCTION`. They never adjust prices and always show a warning for manual review; a reviewed entry in `config/manual_corporate_actions.yaml` (kind, share counts, cash per cancelled share) marks the warning as reviewed. Reviewed entries for MAXIND (2022 tender at Rs 85) and EASTSILK (2024 resolution plan), and UEL's 2024 demerger as reviewed with no price adjustment.

### Changed (RS rank 1–99, `rs-1.1.0`; owner decision 2026-10-02)
- RS ranks now run 1–99: the strongest stock is 99, the weakest 1. The old formula counted each stock half against itself, so the top rank was 98. Ranks move up slightly, so a few stocks near the Trend Template's RS minimum (70) can change from FAIL to PASS. `strategy.rs.version: rs-1.0.0` keeps the old formula for re-running old scans.

### Fixed (audit P1-2a: data-quality history)
- Every change of a data-quality event (opened, resolved, reopened, blocking or not) is now kept with its date in `data_quality_event_history`. Point-in-time runs (`vcp verify scan`, backtests) see the block exactly as it was then; before, reopening an event erased the period in which it had been resolved.

### Fixed (audit P1-2b: missing candles on suspensions)
- The Kite completeness check no longer reports a stock as missing bars on days NSE's bhavcopy shows it did not trade (suspension, untraded day). On the last year of data the old rule flagged 626 stocks.

### Changed (audit P3-1: unexplained gaps; owner decision 2026-10-02)
- Every unexplained price drop of 30 % or more now blocks the stock until a corporate action explains it or it is marked genuine. Before, only drops "close to" a small-integer ratio blocked, but that test matched 81 of 83 real cases, so in practice almost nothing changes (2 more blocks today). The nearest ratio is still shown as a hint when it is within 3 % of one.

### Changed (audit P3-1: hygiene)
- `vcp auth kite --api-secret` is no longer accepted (the command line is kept in shell history); put `KITE_API_SECRET` in `.env` or the environment.
- The browser User-Agent sent to NSE is one setting, `data.nse_user_agent` in `config/data.yaml` (default: a current Chrome string), instead of five hard-coded copies.
- `scratch/` is no longer tracked by git.

### Changed (audit P2-6: one config hash)
- Universe snapshot config hashes use the same canonical serialisation as scan config hashes, so universe snapshot ids change once. Results are unchanged.

### Added (backups before the daily run)
- `vcp run daily` now checks that the database opens and copies it to `data/backups/` before any step, keeping the newest 3 copies. A damaged database or a failed backup stops the run with the newest backup and the command to restore it. Flags `--backup-dir`, `--backup-keep`, `--no-backup`.

### Fixed (Upstox amount 0 on rights issues)
- Upstox reports `amount: 0` on splits, bonuses and rights issues; it is now read as "not reported". Before, a rights issue's NSE issue price disagreed with that 0, the rights issue became a provider conflict and lost its price-adjustment factor (seen on 9 stocks, e.g. NATCOPHARM, in the first live Upstox run).

### Fixed (Upstox rate limits; owner decision 2026-10-02)
- A run now asks Upstox about at most 800 instruments, one request every 1.9 s (about 25 minutes, under Upstox's 1,000 requests per 30 minutes): first every instrument with an NSE split, bonus, rights issue or demerger in the window, then the least recently checked, so each instrument is checked about every three runs (new table `secondary_ca_checks`). Before, every run asked all ~2,600 instruments unpaced, and the 2026-10-01 22:00 run failed when Upstox rate-limited 30 of them.
- A rate limit (HTTP 429 twice in a row) stops the Upstox requests for that run with a warning instead of failing it; a few other failures (at most 5 %) are tolerated with a warning. Instruments not asked stay NSE-only for that run. New settings `data.corporate_actions.secondary_min_request_interval_seconds`, `secondary_max_requests_per_run`, `secondary_max_failure_share`.

### Changed (audit P2-2: staleness in NSE sessions; owner decision 2026-10-01)
- Staleness is now counted in missed NSE sessions (settled bhavcopy days after the stock's last bar, up to the as-of date), so weekends and holidays never count. New `data.quality.staleness`: the universe excludes a stock after more than 5 missed sessions (was 30 calendar days), RS stops ranking it after more than 1 (was 4 calendar days); signals still need a bar on the as-of session. `universe.max_staleness_days` and `strategy.rs.max_staleness_days` are removed.
- Universe snapshot ids and scan config hashes change once with this release.

### Changed (audit P2-1: feature definitions, `features-1.2.0`)
- `volume_ratio_20` and `volume_ratio_50` now compare today's volume with the average of the 20 (50) bars **before** today; the old average included today, which diluted the spike being measured. They need 20 (50) prior bars. Feature rows are written as `features-1.2.0`; the Trend Template reads that version (its inputs are unchanged).
- `DATA_SPECIFICATION.md` §39A documents every daily feature: SMAs, ATR(14) as a simple mean of true range (not Wilder), 20/50/252-bar highs and lows, volume averages, returns and volatility. EMA columns are not computed.

### Documented (audit P1-10 second part: small corporate actions)
- `DATA_SPECIFICATION.md` §18A now records that the gap safety net cannot catch a missed bonus or split that moves the price by less than 30 % (for example 1:4), and why the threshold stays at 30 %: on the full NSE history a lower one would flag about 850 earnings, news and circuit drops. A second corporate-action source (Upstox, or a BSE feed) is the remedy.

### Fixed (same-day ratio action and demerger/rights; superseded readings)
- A rights issue or demerger on the same ex-date as a split or bonus is now derived from the prior close on the post-split/bonus scale. Before, the split/bonus was counted twice: AHLEAST's 2022-10-06 demerger + 1:2 bonus got factor 0.352 instead of 0.528, leaving a +50 % jump in its adjusted prices. The gap detector uses the same order.
- An older, ratio-less SPLIT/BONUS/RIGHTS reading of a record that the parser now stores as `UNMODELLED` (same instrument and ex-date) no longer raises a second, blocking event (QUINT 2026-08-25, BRITANNIA 2021-05-25); the record is reported once, as the unmodelled warning.

### Fixed (delisting of a company that listed again under a later ISIN)
- `vcp ingest security-master` no longer skips a delisting record whose instrument is held by the same issuer's equity under a later ISIN (DHFL, delisted 2021-09-29, INE202B01012 → PIRAMALFIN, INE202B01038). Like a relisting under the same ISIN, the delisting is kept as an earlier period of that instrument. A different issuer reusing the symbol is still refused.

### Added (audit P1-10: unmodelled corporate actions become warnings)
- NSE records for price-affecting actions the scanner does not model (capital reduction, merger or amalgamation, scheme of arrangement, a bonus of debentures, rights in CCPS/warrants/NCDs) are no longer dropped: they are stored as `UNMODELLED` actions and raise a non-blocking `CORPORATE_ACTION_UNMODELLED` warning on their ex-date (`vcp quality list`). The warning clears when a manual override covers that date, or a person resolves it. Prices are still not adjusted for them; the gap detector still blocks a split-like jump.
- Rights in non-equity securities (QUINT 2026-08-25, "Rights - 7 CCPS And 7 Warrants:40") are no longer read as an equity rights issue without a ratio, which blocked the stock.

### Changed (demerger factor edge rules; owner decision 2026-10-01)
- A demerger whose ex-date open is at or up to 1 % above the prior close now gets factor 1.0 instead of a blocking `factor_unknown` (DALMIASUG 2025-10-31). The plausibility floor is 0.02, not 0.05, so KESORAMIND's 2025-03-10 demerger (factor 0.04997) is applied.
- `config/manual_corporate_actions.yaml` accepts `DEMERGER` entries with `price_factor`, which wins over the ex-date open (for demergers with no usable ex-date bar).

### Added (audit P1-8d: verify a scan on demand)
- `vcp verify scan RUN_ID` rebuilds a recorded scan run on a copy of the database: it freezes the data at the run's cutoff, rebuilds the universe as known then (same deterministic id), RS and the Trend Template over that universe, and compares the results hash (exit 0 match, 2 mismatch with the differing instruments, 1 error). The copy is deleted unless `--keep`. A code commit different from the run's is reported. Without an id it lists recent runs.

### Added (audit P1-8c: scan-run records; owner decision 2026-10-01)
- Tables `scan_runs` and `scan_run_results`. Every `vcp compute trend-template` (so every evening scan of `vcp run daily`) inserts one immutable row: as-of date, data cutoff, data and universe snapshot ids, the scan's config hash and per-section hashes, the git commit and whether tracked files had uncommitted changes, algorithm versions, survivorship label, counts, a content hash of the verdicts, start and end times. The verdicts themselves are copied to `scan_run_results`, so a rerun cannot erase what an earlier run reported.
- The command prints the scan run id, the code commit and the results hash.

### Changed (audit P1-8b: deterministic universe ids, explicit universe for scans)
- Universe snapshot ids are derived from the as-of date, the knowledge cutoff, the config hash and the method version (`uv_YYYYMMDD_<10 hex>`), not random. Rebuilding with the same inputs replaces the stored snapshot instead of adding another.
- `vcp compute trend-template --universe-snapshot-id` evaluates a named snapshot (default: the latest for the date, as before) and prints which one it used; the Trend Template reads RS ranked over that same universe snapshot.

### Changed (audit P1-8a: per-section config hashes; owner decision 2026-10-01)
- Trend Template scan ids now hash only the sections that can change a result: `strategy`, `universe` and the data-quality gate (`data.quality`). Changing the log level, monitoring settings or storage paths no longer starts a new scan id. Existing scan ids change once with this release.
- A gated universe snapshot's `config_hash` now includes the gate settings, which change eligibility.
- `vcp config validate` prints the scan config hash and each section's hash.

### Added (audit P1-2c: trading absences; owner decision 2026-10-01)
- New data-quality flag `TRADING_ABSENCE`: a stock that misses 20 or more NSE sessions (`data.quality.absence_min_missed_sessions`; sessions = settled bhavcopy days) is blocked from its return until it has a full block lifetime (253 bars) of new history, so no feature window mixes prices from both sides of the absence. Detected by `vcp quality scan` and `vcp ingest corporate-actions`.
- The price jump on such a return is no longer a blocking unexplained gap; it stays recorded as a warning (`after_trading_absence_sessions`).

### Changed (audit P1-2b: data-quality blocks end; owner decision 2026-10-01)
- A dated blocking event (unexplained gap, corporate action without a usable factor, provider conflict) now stops applying once the stock has `data.quality.block_lifetime_bars` (253, new in `config/data.yaml`) of its own bars from the event date to the as-of date; the bad bar has then left every lookback. Before, a block lasted forever (BRITANNIA, PATINTLOG, SIGMAADV, TFL, UEL and KESORAMIND were blocked by events 385 to 1,329 bars old). Earlier as-of dates still see the block. Config loading refuses a lifetime shorter than the longest lookback (`ScannerConfig.longest_lookback_bars`).

### Fixed (audit P1-2a: debenture bonus)
- NSE's "Scheme Of Arangement- Bonus - 1 Debenture For 1 Equity Share Held" (BRITANNIA 2021) is no longer read as a share bonus without a ratio, which blocked BRITANNIA; it is reported as an unhandled record. The raw row already stored stays (raw data is immutable); the block it causes expires under P1-2b.

### Added (audit 2.7d: manual corporate-action overrides; owner decision 2026-10-01)
- `config/manual_corporate_actions.yaml`: hand-entered, evidenced corporate actions. `vcp ingest corporate-actions` (and so `vcp run daily`) loads all of them on every run as source `MANUAL`; reconciliation marks their (type, ex-date) `MANUAL_OVERRIDE` with the manual values. An invalid file aborts the run.
- First entries, verified against NSE record-date notices and bhavcopy prices: DTIL bonus 1:2 (2021-08-05), GICL split Rs 10 → Rs 5 and bonus 1:1 (2025-10-15), JSLL split Rs 10 → Rs 2 (2025-06-12).

### Changed (audit 2.7c: gap safety net window; owner decision 2026-10-01)
- An action now explains a gap when its ex-date falls after the previous bar and up to the gap bar, not only on the gap bar's own date. Illiquid stocks often do not trade on the ex-date, and their correctly adjusted splits, bonuses and rights still blocked them (DOLPHIN, UEL, TIL …).
- The action must also account for the gap's size: the previous close times the combined factor (split/bonus ratio, rights TERP) must land within `gap_pct` of the open. A long absence is not explained by an unrelated action inside it, and an ex-date action whose ratio does not fit the jump is now flagged. Such events record `residual_gap_pct_after_actions`.

### Fixed (audit 2.7b: share consolidations)
- An NSE "Consolidation Of Equity Shares From Re 1 … To Rs 10 …" record is now a reverse split, SPLIT (1, 10), instead of an unhandled record (VERTOZ 2025-06-25). Capital reductions still carry no ratio and stay reported as unhandled.

### Fixed (audit 2.7a: SME corporate actions)
- NSE corporate actions are fetched from both `index=equities` and `index=sme`. SME bonuses and splits (e.g. KSOLVES' 2021 bonuses) were missing, which left 34 blocking unexplained gaps on 31 active SME stocks. A failure of either feed aborts the run. The SME feed's internal number is no longer stored as an ISIN.

### Added (daily run)
- `vcp run daily` runs every evening and catches up after skipped evenings. Steps:
  1. security master with the ASM/GSM/T2T lists;
  2. NSE bhavcopy from the day after the last settled file;
  3. corporate actions for the last 60 days;
  4. adjusted prices and features;
  5. universe, RS and Trend Template for every session not scanned yet.
  It appends one summary line per run to `<db folder>/logs/daily_runs.log` and warns when today's ASM/GSM lists were not collected.
- Table `surveillance_collections` records each day a surveillance list was collected in full, including empty lists; it is seeded once from existing flags. A universe snapshot is POINT_IN_TIME_COMPLETE only if ASM and GSM were collected on its as-of date ("ASM list not collected on …").

### Changed (audit P0-4: point-in-time universe)
- The universe takes each date's series and trade-to-trade status from the NSE bhavcopy (`daily_series`) instead of today's EQUITY_L. Delisted, merged and renamed names take part on dates when they traded.
- The survivorship label is derived from the data: price coverage of a 380-day window, plus ASM and GSM collection dates. The reasons are stored in `universe_snapshots.survivorship_detail` and printed by `vcp ingest universe`. The `survivorship_coverage_verified` config field is removed. `UNIVERSE_METHOD_VERSION` is now 2.0.
- The NSE GSM list (`/api/reportGSM`) is collected with ASM and T2T on every security-master run.

### Changed (owner decision 2026-10-01: Upstox credentials rejected)
- An Upstox HTTP 401/403 (`ProviderAuthError`, e.g. an expired token) no longer aborts `vcp ingest corporate-actions`. The run continues NSE-only, nothing is escalated because of Upstox's silence, and the CLI prints a warning to refresh `UPSTOX_ACCESS_TOKEN`. Other Upstox failures still abort the run.

### Added (audit step 2.5: Kite as provisional bar and cross-check)
- `vcp ingest market --today` stores today's Kite bar as `PROVISIONAL` (`IngestionWorker.ingest_instrument(provisional=True)`, `save_daily(data_status=...)`).
- `--allow-provisional` on `vcp ingest adjusted-prices` and `vcp ingest universe`.
- `vcp verify kite-crosscheck` (`data/quality/kite_crosscheck.py`) explains every step in the Kite/ours close ratio by corporate action.

### Changed (audit step 2.5)
- **Breaking:** `vcp ingest market` needs a mode: `--today` (provisional bar) or `--kite-history` (Kite history, comparison only). Daily history comes from `vcp ingest bhavcopy`.
- `load_daily`, `load_daily_as_of` and the universe candidates leave PROVISIONAL bars out unless `include_provisional=True`.
- `save_adjusted_daily(prune_missing=True)` (used by full rebuilds) deletes rows of the same instrument, version and snapshot that the build no longer produces.
- `vcp verify kite-adjustment` only samples splits and bonuses (rights and demergers now also explain gaps, but have no ratio factor to test).
- The NSE rights parser also reads `Rights Issue a:b@ Premium ...` and `Prm Rs ...` (live variants).

### Added (audit step 2.4: rights issues and demergers)
- NSE corporate actions:
  - `Demerger` records now parse as `DEMERGER` (they were dropped as unhandled);
  - rights records carry their ratio `(a, b)` and issue price (face value + premium) in `cash_amount`.
- `derived_factor` / `factor_unknown` / `ExDatePrices` (domain) and `adjustment.engine.ex_date_prices`:
  - RIGHTS factor = TERP / prior close; the volume factor is its inverse;
  - DEMERGER factor = ex-date open (NSE special pre-open) / prior close; volume factor 1.0;
  - factors are derived only from raw bhavcopy bars.
  - `CALCULATION_VERSION` 1.2.
- `CorporateActionIngestionWorker(market=...)` derives these factors. The CLI passes the market repository.
- An underivable factor on raw prices is a blocking `CORPORATE_ACTION_UNRESOLVED` event (cause `factor_unknown`), raised by both the corporate-action worker and the quality scan.
- `tests/fixtures/corporate_actions/golden_derived_actions.json`: BHARTIARTL rights 2021, RELIANCE demerger 2023, ITC demerger 2025 (real NSE records and bhavcopy prices).

### Changed (audit step 2.4)
- The gap detector treats an adjustable rights issue or demerger as explaining its ex-date gap.
- Reconciliation uses the most recently seen record of a source for its values (a parser upgrade re-reads the same NSE record with more detail) and the earliest one for the grace period.

### Added (audit step 2.3: bhavcopy ingestion)
- `vcp ingest bhavcopy --start D [--end D] [--refresh] [--cache-dir]` (`BhavcopyIngestionWorker`) ingests NSE bhavcopy files day by day. It records the manifest and resolves identity. It writes final bars with `DuckDBMarketDataRepository.save_final_daily`, which is set-based and supersedes other providers' bars for the same session. It stops at the first PENDING or ERROR day, and re-runs are idempotent.
- `domain.market.FINAL_PRICE_SOURCE = "NSE_BHAVCOPY"`.

### Changed (audit step 2.3)
- `save_daily` no longer lets another provider (Kite) supersede a bhavcopy bar.
- `save_adjusted_daily` writes set-based through Arrow instead of `executemany`. Adjusting 5 stocks × 1,424 bars now takes 0.7 s instead of about 40 s, with identical values (D3).
- `classify_missing`: an earlier weekend day is NO_SESSION at once, so a Monday run is not held up by Saturday/Sunday 404s. Today's 404 is always PENDING.

### Added (audit step 2.2: identity for bhavcopy history)
- `data/ingestion/bhavcopy_identity.py` maps each day's bhavcopy rows to permanent instruments. It matches by known ISIN first, then by same symbol and same issuer when the ISIN changes after a face-value split. Otherwise it creates a new, inactive instrument, disambiguated as `NSE_EQ|SYMBOL#ISIN` when NSE reused the symbol. Re-runs replay the stored mapping.
- Tables `instrument_identifier_history` (symbol/ISIN periods) and `daily_series` (per-day instrument and series), plus `DuckDBIdentityRepository`.
- `identity.same_issuer_equity`; `mint_instrument_id(..., disambiguator=)`.
- Quality scan: `SYMBOL_MAPPING_UNCERTAIN` warning for ISIN changes that no split explains.
- `DuckDBStore.insert_rows`: bulk insert through Arrow. Writing one day (~3,400 rows) went from 24 s with `executemany` to 0.1 s.

### Changed (audit step 2.2)
- The bhavcopy parser skips non-company-equity ISINs (ETFs `INF...` and partly-paid `IN9...`, which trade in series EQ/BZ). On 2026-09-29 that is 350 ETF rows.

### Added (audit step 2.1: NSE bhavcopy as the raw price source)
- `data/providers/nse_bhavcopy.py`:
  - `NseBhavcopyProvider` downloads NSE capital-market bhavcopy files in both layouts (legacy up to 2024-07-05, UDiFF from 2024-07-08). Each zip is cached unchanged with its sha256, and requests are rate-limited (1 s).
  - `parse_bhavcopy` normalises both layouts, keeps equity series EQ/BE/BZ/SM/ST, and returns invalid or duplicate rows as rejects with reasons.
  - `classify_missing` decides what a 404 means: NO_SESSION or PENDING.
  - `get_trading_holidays` reads NSE's current-year holiday list.
- `domain/bhavcopy.py`: bhavcopy row, file status and manifest types.
- Table `bhavcopy_files` (manifest, one row per date and file hash) and `DuckDBBhavcopyRepository`.
- DATA_SPECIFICATION §21.2.
- Nothing is wired into ingestion yet (step 2.3).

### Fixed (Upstox corporate actions, found on live data)
- Upstox serves only about the last 12 months of corporate actions per ISIN. Its silence about an older NSE split/bonus was treated as evidence, so with a token configured every historical split/bonus would have become `PROVIDER_CONFLICT` after the 3-day grace period (blocked, factor withdrawn). `UpstoxCorporateActionProvider.coverage_start` records the earliest ex-date Upstox returned per instrument; the CA worker only counts Upstox's silence from that date (no records = no evidence).
- Upstox split ratios are share ratios (`"1:5"` for KOTAKBANK's face value 5 -> 1); they are now converted to the engine/NSE convention `(old face value, new face value)`. Before, every split reported by both sources was a ratio conflict. Bonus ratios are unchanged.

### Fixed (found by the first real-data run, audit 2026-09-30 Fix 5b)
- Same-day split and bonus (BAJFINANCE 2025-06-16: 1:2 split + 4:1 bonus) lost a factor: `save_adjustment` closes any row with the same (instrument, effective_date), so the bonus closed the split and the stored factor was 0.5 instead of 0.1. `AdjustmentEngine.compute_factors` now combines all actions of one ex-date into one factor (`resolution_id` = ids joined by `+`). The corporate-action worker also rebuilds an instrument's factors whenever they differ from what the engine computes now, so existing databases are repaired on the next `vcp ingest corporate-actions`.
- `--instrument` accepted only full ids in `compute features`, `compute trend-template`, `quality scan`, `quality list`, `ingest adjusted-prices` and the post-ingest quality scan of `ingest corporate-actions`; a symbol such as `RELIANCE` silently selected nothing (the quality scan checked 0 instruments). All now accept an id or a symbol, any case.
- Placeholder credentials copied from `.env.example` (`your_..._here`) or blank values now count as unset (`cli_pipeline.env_secret`). The placeholder Upstox token made `vcp ingest corporate-actions` fail with HTTP 401 instead of running NSE-only.

### Added (audit Fix 5b)
- `scripts/capture_golden_ca.py` and `tests/fixtures/corporate_actions/golden_actions.json`: 7 real splits/bonuses (IRCTC, TATASTEEL, NESTLEIND x2, RELIANCE, BAJFINANCE split+bonus, HDFCBANK), NSE bhavcopy raw prices vs Kite adjusted prices. `tests/regression/test_golden_corporate_actions.py` (49 offline checks).

### Fixed (`vcp auth kite` ignored .env)
- `vcp auth kite` now loads `--env-file` (default `.env`) before reading `KITE_API_KEY` / `KITE_API_SECRET`, like every other command; `--api-key`/`--api-secret` and variables already in the environment still take precedence. Before, it read only the process environment and failed with "Kite API Key and Secret are required" although both were in `.env`.
- `KiteAuthenticator` creates `.env` owner-only (0600) and tightens an existing group/world-readable one before writing the access token (audit P3: `.env` permissions).

### Changed (audit 2026-09-30 P1-7, VCP configuration contract)
- `vcp:` and `classification:` now follow VCP_SPECIFICATION §60 verbatim: nested `contractions`, `swing`, `volatility`, `volume`, `pivot`, `confirmation` blocks and per-tier `max_contractions` / `require_*` flags (`VCPThresholdsConfig`, `ClassificationConfig`, new `VCP*Config` sub-models). The flat keys `min_contractions`, `max_contractions`, `require_volume_dryup`, `require_tight_pivot` under `vcp:` are gone (`extra="forbid"` rejects them). New validation: volume periods ordered, ratios in (0, 1], tiers inside `vcp.contractions`, stricter tiers never looser. A test parses the §60 YAML from the spec and requires it to validate. No detector code yet (Phase 6).
- Behavior change: the configuration hash changes, so new `trend-template` scan ids differ from earlier ones; earlier rows are kept.

### Added (audit 2026-09-30 P1-6, RS tests)
- `tests/unit/test_rs_ranking.py`: known-answer ranking, weights/windows, ties, NULL-history and zero-close exclusion, staleness boundary, 1–98 rank range and monotonicity, and "RS unchanged when a stock leaves the Trend Template stage but not the population". TREND_TEMPLATE_SPECIFICATION §3 documents that `rs-1.0.0` ranks run 1–98.

### Changed (audit 2026-09-30 P1-3 / P1-4, architecture boundary and layout)
- RS ranking is now a pure function (`features.relative_strength.compute_rs_rows`) behind the new `RelativeStrengthRepository` Protocol; `DuckDBRelativeStrengthRepository` does only data access. `RelativeStrengthEngine(repository, calculation_version=None, config=None, quality_gate=None)` replaces `RelativeStrengthEngine(store, ..., data_snapshot_id=...)` (the data snapshot now binds the repository).
- Universe rules are a pure function (`data.universe.builder.evaluate_eligibility`) over `domain.universe.UniverseCandidate`; point-in-time SQL moved to `DuckDBUniverseRepository.load_universe_candidates` / `count_known_delistings` (new `UniverseInputRepository` Protocol). `UniverseBuilder(repository, config, ...)` replaces `UniverseBuilder(store, config, ...)`.
- SQL feature builders moved: `features.daily_features` -> `data.features.daily_features`, `features.weekly_aggregation` -> `data.features.weekly_aggregation`.
- Removed the empty duplicate packages `indicators/`, `trend/`, `universe/`, `data/normalization/`. PROJECT_DESIGN §6/§49 and AGENTS.md rule 3 describe the real layout.
- `test_architecture_rules.py` now scans `domain/`, `features/`, `data/universe/` (must contain code) plus future strategy packages, and forbids storage/ingestion/concrete-adapter imports as well as SDKs. The old version scanned only empty packages.
- No behavior change: new `tests/regression/test_rs_universe_golden.py` was recorded from the SQL implementations before the refactor and passes unchanged after it (RS statuses, returns, ranks, ties, staleness, NaN, gate exclusion; every universe exclusion reason; Kite price undo; survivorship label).

### Fixed (audit 2026-09-30 P1-1, pipeline could not start from an empty database)
- `vcp ingest security-master` now seeds `instruments` from NSE's current listing (`SecurityMasterIngestionWorker._sync_instruments`): upsert with ISIN-first canonical ids, so a symbol rename updates the existing instrument; instruments missing from a non-empty listing become `is_active = FALSE` (history kept); delisted-list records never create instruments. New stats `instruments_upserted`, `instruments_deactivated`. Why: nothing populated `instruments`, so `vcp ingest market` / `corporate-actions` stopped with "no instruments found".
- New `tests/integration/test_pipeline_e2e.py`: all eight pipeline commands through the real CLI from an empty database with synthetic providers.

### Fixed (audit 2026-09-30 P0-1, Kite history adjusted twice)
- Kite historical candles are adjusted by Zerodha for splits/bonuses (and rights, spin-offs, extraordinary dividends) as of the fetch time. The engine treated them as raw and applied NSE factors on top, so any history downloaded after a split was adjusted twice while history downloaded before it was right.
- `KiteProvider.get_capabilities().adjusted_prices` is now `True`; `domain.market.PROVIDER_ADJUSTED_SOURCES = {"KITE"}` (a test keeps the two in step).
- Adjustment is fetch-time aware (`CALCULATION_VERSION` 1.0 -> 1.1): a provider-adjusted bar gets only the factors of actions whose ex-date is after both its trade date and its IST fetch date (`daily_prices.known_from`, now carried on loaded candles as `ingested_at`). `apply_factors` and `build_adjusted_rows` share one factor rule.
- Universe minimum price uses the price that actually traded: the latest Kite close is divided by the factors Kite had applied. Traded value is unchanged (split scaling cancels).
- New read-only command `vcp verify kite-adjustment` classifies Kite's history around known splits/bonuses as ADJUSTED / RAW / INCONCLUSIVE (exit 0 / 2 / 1).
- Behavior change: rebuild adjusted prices (`vcp ingest adjusted-prices`) to get version `adj-1.1-*`; older `adj-1.0-*` rows stay for reproducibility. Docs: DATA_SPECIFICATION §21.1, PROJECT_DESIGN §10/§14A, DATABASE_SCHEMA §14, AGENTS.md rule 2, README.

### Fixed (audit 2026-09-30 P1-9, non-finite and non-positive values)
- `validate_ohlc` rejects NaN/inf prices or volume and any price <= 0. Rejected bars stay in `raw_ohlcv` for audit but never reach `daily_prices`. Why: NaN compared False with every ordering rule, so NaN bars looked valid.
- Trend Template: a non-finite input is stored as NULL and the result is `INSUFFICIENT_DATA`, not `FAIL`; the `close` extreme basis ignores a window containing a non-finite close. Weekly Stage returns `INSUFFICIENT_DATA` for a non-finite weekly close. RS stores non-finite returns as NULL, so the instrument is `INSUFFICIENT_DATA` and leaves the ranking population instead of sorting NaN above every value.

### Fixed (audit 2026-09-30 P0-2, reconciliation policy)
- Cash amounts are compared only when both sources report one (tolerance half a paisa). NSE's feed carries no parsed amount, so every dividend reported by both NSE and Upstox used to become `PROVIDER_CONFLICT`.
- Only SPLIT/BONUS conflicts block signals. DIVIDEND/RIGHTS conflicts are still recorded (`CORPORATE_ACTION_UNRESOLVED`, severity WARNING) but never gate the universe, RS or Trend Template.
- An NSE-only action past `secondary_grace_days` becomes `PROVIDER_CONFLICT` only if it is a split/bonus and the secondary source was actually queried for that instrument over a window containing the ex-date (`UpstoxCorporateActionProvider.queried_instrument_ids`, `ReconciliationEngine.reconcile(secondary_window=...)`). Without an Upstox token, or for an instrument without an ISIN, it stays `SINGLE_SOURCE` and keeps its factor, as the README always said. `conflict_fields = secondary_source_missing` marks the escalated case.
- Behavior change: existing conflicts are re-evaluated on the next `vcp ingest corporate-actions`; dividend-driven and unqueried NSE-only blocks clear, and withheld split factors come back. Why: the old rules systematically removed dividend payers from the universe and RS population.

### Fixed (audit 2026-09-30 P0-3, gap safety net and unknown split ratios)
- `GapDetector` treats a gap as explained only by an *applied* split/bonus: status CONFIRMED / SINGLE_SOURCE / MANUAL_OVERRIDE and a present, finite, positive ratio (`domain.corporate_actions.explains_price_gap`). Before, any resolution on the ex-date (dividend, rights, PROVIDER_CONFLICT, or a split whose ratio NSE text could not be parsed) silenced the detector while the adjustment engine applied factor 1.0, so an unadjusted split passed through as a normal-looking crash.
- `corporate_action_events` also emits a `CORPORATE_ACTION_UNRESOLVED` event (`cause = ratio_unknown`, distinct event id) for every adjustable split/bonus without a usable ratio. It always blocks from the ex-date, cannot be hand-resolved, and clears when a later resolution carries a ratio. Producers (CA worker, `vcp quality scan`) pick it up automatically.
- Behavior change: instruments with an unparsed split/bonus ratio, or with a split-like gap on a dividend/conflict ex-date, now report `DATA_QUALITY_BLOCKED` and drop out of the universe and RS population. Test fixtures that used ratio-less CONFIRMED splits as "explanations" now carry a ratio.

### Fixed (audit P0-2, data-quality gate)
- Data-quality events are now persisted and enforced. New `data_quality_events` table and `DuckDBDataQualityRepository` (idempotent `sync_events`, human `resolve`, point-in-time `blocked_instruments`). Producers: the completeness check (`MISSING_CANDLES`, blocks from the first missing session), corporate-action reconciliation (`CORPORATE_ACTION_UNRESOLVED` per `PROVIDER_CONFLICT`, blocks from the ex-date, honours `conflict_blocks_signals`) and the gap detector (`UNEXPLAINED_GAP`, blocks only when split-like, per DATA_SPECIFICATION 18A). Why: conflicts, gaps and missing bars were modeled but never stored or consulted, so a symbol with unresolved adjustment data produced normal-looking signals. The gap detector was never invoked at all; it now runs after `vcp ingest corporate-actions` and via `vcp quality scan`.
- Consumers: `UniverseBuilder(quality_gate=...)` marks blocked instruments ineligible (`Data quality blocked: ...`); `RelativeStrengthEngine(quality_gate=...)` leaves them out of the ranking population; `TrendTemplateEngine(quality_gate=...)` returns the new status `DATA_QUALITY_BLOCKED` (checked before any data is read, all ten conditions stay NULL) and `trend_template_results.blocked_by` stores the flags. Behavior change: symbols with an open blocking event no longer reach PASS/FAIL, and RS `population_size` excludes them.
- CLI: `vcp quality scan | list [--all] | resolve EVENT_ID --by NAME --note TEXT`. Point-in-time: a run under `--data-snapshot-id` only sees events detected by that snapshot's `known_at`. Event ids are now deterministic (were random UUIDs). `DataQualityEvent` gained `trade_date`, `blocks_signal`, `dataset`, `status` and resolution fields (all defaulted).
- Safety rule: `vcp quality resolve` refuses `CORPORATE_ACTION_UNRESOLVED` events; a conflict clears only when its resolution is superseded (DATABASE_SCHEMA 17A). No command creates a `MANUAL_OVERRIDE` yet, so a standing conflict blocks until the feeds agree.

### Added (audit P1-1, provider instrument mapping)
- `provider_instruments` table and `DuckDBProviderInstrumentRepository` (validity-dated provider id to permanent `instrument_id`); `KiteProvider.get_provider_instruments()`; `data/ingestion/provider_mapping.py` syncs the dump through the shared `InstrumentResolver` (unresolved rows skipped, empty dump raises `ProviderError`).
- `vcp ingest market` syncs mappings before storing bars; Kite and Upstox candles carry `provider_instrument_id` into `raw_ohlcv` (internal-id fallback only for providers without a native id).
- Not done: Upstox mapping rows, backfill of older raw bars, live-Kite verification.

### Added (audit P0-3, survivorship: delisted securities)
- `NSEDelistedProvider` (`data/providers/nse_delisted.py`) reads NSE's official "List of Companies Delisted from NSE" workbook (link discovered from the NSE delisting page; `--delisted-file PATH` for a manual copy) and returns `SecurityRecord`s with `delisting_date`, `valid_to` and the delisting type. Standard-library XML parsing; no new dependency. Checked against the published file: 457 rows (2002-2026), 455 after collapsing two duplicated ISINs.
- `SecurityMasterIngestionWorker(delisting_provider=...)` ingests them into `security_master_history` (with `delisting_reason`, previously always NULL) next to the live listing. A delisted record is skipped and counted (`delisted_skipped`) if it could be confused with a live security: same instrument id held by a live security with a different or unknown ISIN, or the same (instrument, period) as a live row. `SecurityRecord` gained an optional `delisting_reason`.
- CLI: `vcp ingest security-master` now includes the delisted list by default. `--delisted-file PATH` uses a local copy; `--no-delisted` skips it (survivorship stays `BIASED`). A failed or unparseable download exits 1 with a hint, never an empty success.
- Effect: `survivorship_status` moves `BIASED` -> `PARTIAL`. Not `POINT_IN_TIME_COMPLETE`: NSE's list omits merger/amalgamation delistings (HDFC Ltd, Mindtree, the 2019-20 PSU-bank mergers), is thin before 2016, and has no listing date or series. Delisted names also still need price history (bhavcopy, unverified) before they can enter a universe. See DATA_SPECIFICATION §4A.

### Fixed (audit P0-1, point-in-time reproducibility)
- New data-snapshot boundary. `data_snapshots` table (`domain/snapshot.py`, `DuckDBSnapshotRepository`): a deterministic id per `known_at`, created idempotently. `AdjustedPriceBuilder(snapshot=...)` reads raw bars and corporate-action adjustments *as known at* `known_at` and stores the rows under that snapshot; without a snapshot it builds `LIVE` (unfrozen) as before. Why: derived data read "latest/current" rows, so a later price correction or corporate action silently changed earlier results.
- Schema: `daily_prices_adjusted.computed_from_snapshot_id` is now `NOT NULL DEFAULT 'LIVE'` and part of the primary key; `daily_prices_adjusted_current` picks one version per (instrument, snapshot). `data_snapshot_id` added to `technical_features_daily`, `weekly_prices`, `relative_strength_snapshots`, `weekly_context`, `trend_template_conditions` (all in the key) and `trend_template_results` (column). `relative_strength_snapshots` key now also includes `universe_snapshot_id` (a re-run over another universe no longer overwrites). `store.migrate()` rebuilds legacy tables and keeps their rows under `LIVE`.
- `DailyFeatureEngine`, `WeeklyAggregationEngine`, `RelativeStrengthEngine`, `DuckDBFeatureRepository` and `DuckDBTrendRepository` take `data_snapshot_id` (default `LIVE`). `DuckDBFeatureRepository.load_adjusted_closes` now reads the same source as the engines (one version per instrument within the snapshot), resolving the previous selection conflict. `load_relative_strength(..., universe_snapshot_id=None)` defaults to the newest universe snapshot.
- CLI: `vcp ingest adjusted-prices --known-at ISO_DATETIME` freezes a snapshot and builds under it; `vcp compute features|rs|trend-template --data-snapshot-id ID` (default `LIVE`, unknown ids are rejected). Trend scan ids embed a non-LIVE snapshot id. Behavior change: frozen data is never fed to a LIVE run, so compute commands on a DB built only with `--known-at` need `--data-snapshot-id`.
- Not done (see audit P1-5): per-dataset cutoffs, `snapshot_manifest` content hashes, `scan_runs`.

### Changed
- Watchlist table now uses the Screener table theme: fixed column widths, sortable headers, 15 per page with the page bar (FRONTEND_SPECIFICATION 67.25).
- Universe defaults reconciled with `PROJECT_DESIGN.md` §14 (audit P1-4): `eligible_series` `[EQ, BE]` → `[EQ]`; `min_close_price` 10 → 20; `min_daily_turnover_inr` (20d average) 5,000,000 → 10,000,000; new `min_avg_traded_value_50d_inr` = 10,000,000 gate using a 50-day average. Universe `method_version` `1.0` → `1.1`. Why: config silently widened the research population versus the governing design. Snapshots built under `1.0` are not rewritten; rebuild to get the new population. SME series (SM/ST) are excluded by the EQ-only whitelist; ETFs are not identifiable from series alone and remain open.

### Fixed (audit P0-4, data completeness)
- Providers no longer turn failures into empty results: `NSECorporateActionProvider`, `NSESecurityMasterProvider`, `NSESurveillanceProvider` (ASM and T2T) and `UpstoxCorporateActionProvider` raise `ProviderError` on HTTP errors, network errors and unparseable/empty feeds; `KiteProvider` raises for an unmapped symbol. Why: an empty list read as "no corporate actions / no securities / no active flags", which left splits unadjusted and closed every open surveillance flag. Upstox 404 (no record for an ISIN) is still "no actions"; other per-ISIN failures are collected and raised together after the loop. `vcp ingest corporate-actions` and `vcp ingest security-master` print the error and exit 1.
- New daily-bar completeness check (`data/quality/completeness.py`): market sessions are observed from the stored cross-section (no holiday list), and an instrument missing a session between its first bar and the end of the window is INCOMPLETE. `IngestionWorker(completeness=...)` re-fetches interior holes once, records a `daily_ohlcv_gapfill` ingestion run, downgrades a still-incomplete run to PARTIAL and raises `MISSING_CANDLES` events (`worker.quality_events`). `vcp ingest market` runs a final verification pass after the loop and reports incomplete instruments and low-breadth dates. Config: `data.completeness.min_breadth` (0.5) and `min_active_instruments` (5). Events are not persisted yet (audit P0-2).
- `IngestionWorker` marks a run PARTIAL when a range longer than 7 days returns zero bars (backstop when completeness cannot be judged).

### Fixed (audit Phase 0, logging never wired)
- The `vcp` CLI now configures structured logging on every command (it never did; `configure_logging` was only called from tests, so all module loggers were silent below WARNING and unformatted). Precedence: CLI flags > `config/logging.yaml` > defaults. New global options (give before the command): `--log-level`, `--log-format {text,json}`, `--log-file PATH`. `logging.yaml` `log_file` was parsed but never used; it now also appends records to that file. Logs go to **stderr** so command output on stdout stays clean (`configure_logging` previously defaulted to stdout; nothing at runtime used it). A missing or invalid `logging.yaml`, or an unwritable log file, prints a warning and falls back to defaults instead of blocking the command. New `load_logging_config()` reads only `logging.yaml`, independent of the rest of the config. Why: ingestion, completeness and provider failures were logged but never visible.

### Removed
- `domain.market.CorporateActionRecord` (unused legacy duplicate of `domain.corporate_actions.CorporateAction`); the `PROJECT_DESIGN.md` provider sketch now names `CorporateAction`.

### Fixed
- `DailyFeatureEngine` (`features-1.0.0` → `features-1.1.0`): all windowed features (SMA 20/50/150/200, high/low 20/50/252, volume averages and ratios, ATR, rolling volatility) are now NULL until their window is full, per AGENTS.md rule 4 ("missing is not zero"). The first bar's `daily_return` and true range are NULL instead of 0 / `high - low`. Rows computed under `features-1.0.0` are not overwritten; recompute to get corrected values. Old vs new: partial-window values → NULL. Why: partial windows looked like real values to Phase 6/7 consumers.

### Documented
- `README.md` rewritten (audit L): setup, credentials the code actually reads, the pipeline order with per-step dependencies, logging flags, known limitations and research restrictions. `AI_AGENT_RULES.md` added. Known gap recorded in the README: no command populates the `instruments` table, so `vcp ingest market` / `corporate-actions` cannot run from an empty database.
- `TREND_TEMPLATE_SPECIFICATION.md` §4: prior lookback equals `sma_weeks`; missing as-of bar gives `INSUFFICIENT_DATA` (weekly stage) vs `DATA_NOT_READY` (Trend Template).
- `DATABASE_SCHEMA.md` §29 and §30: NULL-until-full rule for daily features; `weekly_context` table entry.

## [0.1.0] - 2026-09-28

### Added
- **Phase 0 — Architecture baseline delivery**:
  - Repository skeleton conforming to `PROJECT_DESIGN.md` §49.
  - Core domain models in `vcp_scanner.domain` (`market`, `trend`, `vcp`, `scoring`, `fundamentals`, `enums`, `errors`).
  - Strict StrEnum enumerations for classifications, lifecycle statuses, confirmation states, and error categories.
  - Provider protocols in `vcp_scanner.data.providers.base` (`MarketDataProvider`, `SecurityMasterProvider`, `CorporateActionProvider`, `SurveillanceProvider`, `FundamentalProvider`).
  - Repository protocols in `vcp_scanner.data.repositories.base` (`MarketDataRepository`, `UniverseRepository`, `ScanRepository`, `FundamentalRepository`).
  - Pattern and alert protocols in `vcp_scanner.patterns.base` (`PatternDetector`, `PatternConfirmer`) and `vcp_scanner.alerts.base` (`AlertChannel`).
  - Structured logging with JSON and text formatters in `vcp_scanner.infrastructure.logging`, supporting contextual field enrichment (`scan_id`, `run_id`, `symbol`, `component`, `duration_ms`).
  - Centralized version manifest in `vcp_scanner.versioning` covering package, strategy, algorithm, and data schema versions.
  - Configuration models and strict Pydantic validation in `vcp_scanner.config.models` implementing rules from `TREND_TEMPLATE_SPECIFICATION.md` and `SCORING_SPECIFICATION.md`.
  - Canonical deterministic configuration SHA-256 hash calculator and YAML loader in `vcp_scanner.config.loader`.
  - Production-ready YAML configurations under `config/`: `strategy.yaml`, `scoring.yaml`, `universe.yaml`, `data.yaml`, `monitoring.yaml`, `logging.yaml`.
  - Environment variables template in `.env.example`.
  - Command-line interface `vcp` in `vcp_scanner.cli` supporting `vcp version`, `vcp config validate`, and `vcp config hash`.
  - Complete Phase 0 test suite under `tests/unit/` covering domain models, config validation, protocol compliance, structured logging, CLI commands, and architectural boundary static analysis.
