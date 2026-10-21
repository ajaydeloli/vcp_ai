# VCP Scanner: User Guide

How to install, run and look after the scanner, from a fresh machine to the daily routine. Every `vcp` command is listed in [section 8](#8-command-reference).

- Design and rules live in the specifications (`README.md` has the map).
- This guide is the operator's view: what to type, when, and what to do when something looks wrong.

Contents:

1. [What it is](#1-what-it-is)
2. [Installation](#2-installation)
3. [Credentials and tokens](#3-credentials-and-tokens)
4. [The daily routine](#4-the-daily-routine)
5. [The dashboard](#5-the-dashboard)
6. [Live prices: Upstox or Kite](#6-live-prices-upstox-or-kite)
7. [Weekly and occasional tasks](#7-weekly-and-occasional-tasks)
8. [Command reference](#8-command-reference)
9. [Troubleshooting](#9-troubleshooting)
10. [What is frozen, what is display only](#10-what-is-frozen-what-is-display-only)
11. [Where things live](#11-where-things-live)

---

## 1. What it is

A research scanner for NSE stocks.

- **Every evening** it loads the day's prices from NSE and checks every stock of the universe against Minervini's Trend Template. It then looks for five setups: VCP, flat base, three weeks tight, cup with handle and double bottom.
- **It scores and ranks** the setups it finds.
- **A paper ledger** records what each strategy would have done.
- **A local dashboard** shows the results, a Market Health Score and live prices.

**It never places orders.** There is no trading path and there must not be one.

---

## 2. Installation

The project runs in **WSL Ubuntu** on Windows. The path used throughout is `~/projects/vcp_ai`. Run every command from that folder: the defaults (`config`, `data/vcp_scanner.duckdb`, `.env`) are relative paths.

### 2.1 Requirements

| What | Version | Check |
|---|---|---|
| Python | 3.11 or newer (developed on 3.12) | `python3 --version` |
| Node.js and npm | 18 or newer (for the dashboard) | `node --version` |
| Disk | about 10 GB free | database about 2.2 GB; serving copy 2.2 GB; 3 backups; raw NSE files |
| Network | NSE archive, Kite, Upstox | the daily run downloads from NSE |

### 2.2 Steps

```bash
cd ~/projects/vcp_ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # the vcp command and the test tools
cp .env.example .env             # then fill in the credentials (section 3)
vcp config validate              # checks config/*.yaml
vcp version                      # versions of every part, for the record

cd frontend
npm ci                           # dashboard packages
npm run build                    # build the web pages
cd ..
```

### 2.3 First data load

The evening pipeline builds everything from an empty database. On the first run it needs the first day of history:

```bash
vcp run daily --history-start 2021-01-01 --serving-copy
```

The first run downloads every NSE bhavcopy since that date and scans the latest session. It takes a long time, and it is safe to stop and start again: every step catches up from where it stopped.

Older sessions are not scanned by the daily run. To scan a past date, run the scan commands of [section 8.4](#84-vcp-compute-scans) for that date, oldest first.

These one-off loads run once after the first daily run. Each can be resumed:

```bash
vcp indices update --from 2022-01-01      # NSE index closes (Market Overview, sector rotation)
vcp health build                          # Market Health Score for every stored session
vcp fundamentals backfill                 # quarterly results from NSE filings (long; --resume continues)
vcp classify update                       # sectors from NSE's index lists + config/manual_sectors.yaml
```

### 2.4 Schedule the evening run

Two Windows Task Scheduler tasks run the pipeline on weekdays, **with no window** (since 2026-10-13). Both run the same command:

```text
conhost.exe --headless wsl.exe -d Ubuntu -u ubuntu -- bash -lc "~/projects/vcp_ai/scripts/daily_run.sh"
```

`conhost.exe --headless` starts the run without a console, so nothing pops up and nothing can be closed by accident. Task Scheduler's "Last Run Result" then always reads success: the real result is in `data/logs/daily_runs.log` and on the dashboard. The tasks' earlier settings are saved in `Documents\vcp_task_backup\` (import them in Task Scheduler to go back to a visible window).

| Task | When | Why |
|---|---|---|
| VCP daily run | Mon–Fri 19:15 IST | NSE publishes the bhavcopy at about 19:00 |
| VCP daily run late | Mon–Fri 22:00 IST | safety net when NSE publishes late |

Only one run can work at a time: the second exits with "another run is in progress". A run that finds nothing new finishes quickly.

The PC must be on (or wake) at those times. A skipped evening is caught up by the next run, except that day's ASM/GSM/T2T surveillance lists: NSE only publishes the current ones.

---

## 3. Credentials and tokens

Credentials live in `.env` in the project folder. It is git-ignored and never printed. A value left as `your_..._here` counts as not set.

| Variable | Used for | How long it lasts |
|---|---|---|
| `UPSTOX_ACCESS_TOKEN` | live prices (the feed in use); cross-check of corporate actions | **one day** (expires early the next morning) |
| `UPSTOX_API_KEY` | live prices with Upstox | until you change the app |
| `KITE_API_KEY`, `KITE_API_SECRET` | Kite login | until you change the app |
| `KITE_ACCESS_TOKEN` | live prices when testing Kite; Kite comparisons (`vcp verify ...`) | **one day**; written by `vcp auth kite` |

### Refresh the Upstox token (mornings you open the dashboard)

1. Generate a new access token in your Upstox developer app.
2. Replace the `UPSTOX_ACCESS_TOKEN=` line in `.env` and save.

There is no restart. The live feed re-reads `.env` every minute while it shows "needs a fresh token".

Without a token the evening run still works. Corporate actions then come from NSE only, and the run prints a reminder.

### Refresh the Kite token (only on days you test Kite)

```bash
vcp auth kite
```

1. It prints a login URL. Open it, log in to Kite and approve the app.
2. The browser lands on your redirect URL; copy the `request_token=...` value from its address bar.
3. Paste it when the command asks. The new `KITE_ACCESS_TOKEN` is written to `.env`.

The token expires at about 06:00 the next morning.

---

## 4. The daily routine

### 4.1 What runs by itself every weekday evening

`scripts/daily_run.sh` runs `vcp run daily --serving-copy`. In order:

| # | Step | Notes |
|---|---|---|
| 0 | Database check and backup | Stops everything if the database will not open or the backup fails. Keeps the newest 3 backups in `data/backups/`. |
| 1 | Security master and ASM/GSM/T2T lists | Listings, delistings, surveillance flags. |
| 2 | NSE bhavcopy | Every session since the last one stored. If today's file is not out yet, it waits for the 22:00 run. |
| 3 | Corporate actions | NSE, cross-checked with Upstox when its token is valid. |
| 4 | Adjusted prices, index closes, features | |
| 5 | Market Health Score | New sessions only. |
| 6 | Scans for every new session | Universe, RS, Trend Template, VCP, scores; then the four other strategies' setups and scores. |
| 7 | Forward labels and paper ledger | |
| 8 | Fundamentals | Filings of the last days. |
| 8a | Shareholding pattern | Filings of the last days (promoter, pledge, FII, DII). |
| 9 | Sector classification | Fridays only. |
| 10 | Serving copy and reports | Refreshes the dashboard's database copy; writes `reports/daily/<date>.html` (and `reports/weekly/<week>.html` on Fridays). |

A failed step does not stop the steps after it. The run is marked failed and the dashboard's status bar shows a warning: "Last daily run (...) failed: ...".

**While a run is going**, the status bar shows it: "Daily run in progress: corporate actions since 19:16 (started 19:15 IST)", updated every 15 seconds. A run that is stopped before it finishes (stopped by hand, killed, the PC shut down) leaves a warning instead: "The daily run started ... stopped during <step> and did not finish." The next run catches up; the warning goes when a new run starts.

### 4.1a Stopping a run

There is no window to close. To stop the run that is going, in a WSL terminal:

```bash
cd ~/projects/vcp_ai
grep '^pid=' data/logs/daily_run.state      # the run's pid
kill <pid>                                  # stops it cleanly; the status bar then says it stopped
```

Or end the task in Task Scheduler (right-click the task, End). Stopping is safe: DuckDB keeps every completed write and rolls back the unfinished one, and the next run catches up.

### 4.2 Your morning (or evening) check, two minutes

1. **Open the dashboard** (start it if needed, [section 5](#5-the-dashboard)): `http://localhost:3000/dashboard`.
2. **Read the status bar at the bottom.** It should say "No warnings", with "Prices to" the last trading day and "Last scan" the same day. A warning says what is wrong (failed step, stale data, stale manual sectors, etc.); [section 9](#9-troubleshooting) has the fixes.
3. **Refresh the Upstox token** if you want live prices today ([section 3](#3-credentials-and-tokens)).
4. Work from the Market Overview, the Screener and the stock pages.

### 4.3 Where the run's output goes

| File | What |
|---|---|
| `data/logs/daily_runs.log` | One line per run: prices up to, sessions scanned, surveillance lists collected, `all steps OK` or `FAILED: <steps>`. Read this first. |
| `data/logs/daily_run_<date>.out` | The full output of that day's runs, step by step. |
| `data/logs/daily_run.state` | The newest run: pid, start time, and how it ended. Read by the dashboard; written by `scripts/daily_run.sh`. |
| `reports/daily/<date>.html`, `reports/weekly/<ISO week>.html` | The daily and weekly HTML reports (also on the dashboard). |

### 4.4 Running it by hand

```bash
bash scripts/daily_run.sh                     # exactly what the scheduler runs (output goes to data/logs/)
vcp run daily --serving-copy                  # same, output on screen
```

Run it by hand after the PC was off at 19:15 and 22:00, or after fixing a failed step. It is safe to run twice: the second run finds nothing new.

---

## 5. The dashboard

### 5.1 Start, stop, restart

```bash
bash scripts/dashboard.sh                     # API on :8000, web pages on :3000; stop with Ctrl-C
```

Then open `http://localhost:3000/dashboard`. To keep it running after you close the terminal:

```bash
(setsid nohup bash scripts/dashboard.sh > logs/dashboard.log 2>&1 < /dev/null &)
```

To stop that background copy, kill its processes by PID:

1. `pgrep -a bash | grep dashboard` gives the script's PID; kill it.
2. `pgrep -a vcp` gives `vcp api serve`; kill it.
3. `pgrep -a npm` gives `npm run start`; kill it.
4. `pgrep next-server` gives the web server; kill it.

Avoid `pkill -f`: it can match and kill unrelated processes.

**After updating the code** (a `git pull` that touched `frontend/`), rebuild the pages before starting. `dashboard.sh` only builds when there is no build yet:

```bash
cd frontend && npm run build && cd ..
```

The API only listens on this PC (127.0.0.1). It only reads data: nothing on the dashboard changes the database.

### 5.2 Where its data comes from

The API reads the **serving copy** (`data/serving/vcp_serving.duckdb`), not the main database. The evening run refreshes the copy. That way the dashboard keeps working while the run writes, and a page can never change the data.

After working on the main database by hand (a rebuild, a manual scan), refresh the copy yourself:

```bash
.venv/bin/python -c "from vcp_scanner.serving import refresh_serving_copy; refresh_serving_copy('data/vcp_scanner.duckdb')"
```

### 5.3 The pages

| Page | What it shows |
|---|---|
| **Market Overview** | NIFTY 50 / SENSEX / NIFTY 500 tiles, the Market Health Score and its pillar cards, the cards below, setups today, recent activity. |
| **Screener** | Every stock of the latest scan with its Trend Template, setup and score; filters. The **Fundamentals** box adds EPS and sales growth. **Export CSV / Excel** (next to the match count) saves every matching stock, not just the page shown, with the visible columns as plain numbers; the file name carries the strategy and scan date. |
| **Fundamentals** | Every universe stock's latest quarterly results and, with the **Shareholding** switch above the table, its latest shareholding pattern: promoter, FII and DII % with their change over the quarter, pledge % (▲ when rising). Filters for both: EPS and sales growth, sector, min promoter %, max pledge %, "FII buying", "Pledge rising" (display only). |
| **Trend Template** | The whole scan through the ten Trend Template conditions: how many stocks pass each condition and how many fail only that one (where stocks drop out); stocks that started or stopped passing since the previous scan; stocks one condition short (the failing condition and how far off, filterable); stocks that moved into weekly Stage 2 this week; and the weekly stage by sector. The pass share over time is on the Dashboard's Leadership card. |
| **Stock Analysis** | One stock: chart, Trend Template conditions, setup, score, fundamentals, sector. The Fundamentals card ends with the latest shareholding (promoter, FII, DII, pledged; changes over one and four quarters); the **Shareholding pattern** card below the quarterly results has the last eight quarter ends. Next to the symbol, **TradingView ↗** opens the stock's chart on TradingView in a new tab, and **Copy symbol** copies the NSE symbol to paste into Dhan or Angel One search. |
| **Recent IPOs** | Recent listings and how they trade. |
| **Watchlist** | Your lists of stocks, kept in this browser only. **Import** reads a CSV or Excel (.xlsx) file: the symbol column is found by its heading (Symbol, Ticker, Stock, Scrip...) or is the first column; `NSE:RELIANCE`, `RELIANCE.NS` and TradingView's `M_M` are understood. A preview shows the stocks found and the symbols not recognised (left out); add them to the current list or to a new list named after the file. **Export CSV / Excel** saves the current list with its scan results; use it as a backup, or to move lists to another browser. Old `.xls` files must be saved as `.xlsx` or `.csv` first. |
| **Strategies** | The five frozen strategies, their tiers and configs. |
| **Paper Trading** | The paper ledger: open positions, closed trades, results per strategy. |
| **Backtest** | Stored backtest runs (read-only). |
| **Reports** | The daily and weekly HTML reports. |
| **System Status** | Data freshness, the last daily runs, data-quality events, recent activity. |

The Market Overview cards:

| Row | Left | Right |
|---|---|---|
| 1 | Market Health Score (full width) | |
| 2 | Index trend | Breadth |
| 3 | Accumulation / distribution | Participation |
| 4 | Leadership | Market stage |
| 5 | New highs today | Setups and breakouts |
| 6–7 | Sector rotation (two rows tall) | Sector strength, then Setups today |
| 8 | Recent activity | Feedback loop |

Cards with a chart never scroll. Data cards (New highs today, Sector strength, Setups today, Recent activity) scroll their rows.

### 5.4 The status bar

| Item | Meaning |
|---|---|
| **Daily run in progress: <step>** | Shown only while the evening run is going (refreshed every 15 s). |
| **No warnings** / a warning | Failed daily-run steps, a run stopped before it finished, stale data, stale manual sectors and similar; the text says what is wrong. |
| **Prices to** | The last session with NSE prices stored. |
| **Last scan** | The last session scanned. |
| **Data copy** | When the serving copy was made. |
| **Live feed** | The live-price state and provider ([section 6](#6-live-prices-upstox-or-kite)). |

---

## 6. Live prices: Upstox or Kite

Live prices are for display only. They never reach a scan, a score, the regime or the paper ledger, and nothing writes them anywhere.

- Prices are fetched only while a dashboard page is open and the market is open.
- After the close, one closing snapshot is taken.

### 6.1 Switching provider

Upstox is the feed in use; Kite is for testing. The switch is one line of `config/live.yaml`:

```yaml
provider: upstox            # or: kite
```

Edit and save. That's all:

- **No restart.** The running dashboard notices the change at the next page refresh and switches at once.
- **No commit, no test change.** When the file says `kite`, git shows `config/live.yaml` as modified; that is expected. Set it back to `upstox` and it is clean again.
- **Old prices are dropped.** On a switch the old provider's prices are not shown under the new name. The status bar shows "starting", then "Live (KITE)" or "Live (UPSTOX)" within about 15 seconds.
- **A broken edit is ignored.** On a typo or an unknown provider, the last good setting stays in force and the API log says why.
- **Each provider needs its own token for the day** ([section 3](#3-credentials-and-tokens)). Run `vcp auth kite` before testing Kite.

A failure never switches the provider by itself. It is shown in the status bar instead.

### 6.2 Live feed states

| Status bar | Meaning | What to do |
|---|---|---|
| Live (UPSTOX) / Live (KITE) | prices are fresh | nothing |
| stale | the last price is older than 2 minutes | usually a slow provider; it recovers by itself |
| closed | the market is closed; the closing snapshot is shown | nothing |
| starting | first fetch, or just switched | wait one poll (15 s) |
| needs a fresh token | the token expired or is missing | refresh it in `.env` (Upstox) or run `vcp auth kite`; picked up within a minute |
| rate limited | the provider asked to slow down | it waits and retries by itself |
| error | the provider did not answer | it backs off and retries; check the network |

The other settings in `config/live.yaml` (poll interval, when prices count as stale, and the index keys) rarely need changing.

---

## 7. Weekly and occasional tasks

### 7.1 Sectors (stocks without a sector)

Sectors come from NSE's index lists, read every Friday. Stocks in no NSE index have no sector until you enter one in `config/manual_sectors.yaml`:

```yaml
sectors:
  - {symbol: 20MICRONS, sector: Metals & Mining, industry: Industrial Minerals, as_of: 2026-10-12}
```

- **`sector`** must be one of NSE's 22 sectors, spelled exactly as NSE spells it. The list is at the top of the file.
- **`industry`** (optional) is NSE's "Basic Industry".
- **`as_of`** is the day you checked the stock's NSE quote page.
- **NSE always wins.** A stock NSE classifies is skipped, and when a manual stock later appears in an NSE list, NSE's sector replaces yours.
- **Nothing is overwritten.** A changed sector closes the old row and opens a new one, so past scans keep the sector they had.
- **Entries older than 12 months** show as a status-bar warning. Check the page again and update the date.

To apply the file now instead of waiting for Friday:

```bash
vcp classify update
```

Then refresh the serving copy ([section 5.2](#52-where-its-data-comes-from)), or wait for the evening run.

### 7.2 A corporate action NSE missed

When a split or bonus is missing from NSE's feeds, the price history shows an unexplained gap. The stock is then blocked by the data-quality gate. Enter the action in `config/manual_corporate_actions.yaml`:

- Give the ratio in the convention described at the top of the file.
- Give the evidence (a notice URL or source) and who approved it.

The next evening run applies it. To apply it now:

```bash
vcp ingest corporate-actions --instrument SYMBOL --start YYYY-MM-DD
vcp ingest adjusted-prices                         # rebuilds the adjusted prices (the evening run does this too)
vcp quality scan --instrument SYMBOL               # the gap is now explained: the block clears
```

Applied after that day's scan, the action leaves the scan's RS, Trend Template, stage and setups for the stock computed on the old prices (on 19 Oct 2026, CCAVENUE showed RS 99 after its 1-for-10 consolidation). System Status then warns "Prices of ... were re-adjusted ... after the DATE scan", and the stock's page shows the same note. Both clear with the next scan; past scans are not re-run.

### 7.3 Data-quality events

Unexplained price gaps, corporate-action conflicts and missing sessions block a stock's signals until they are resolved.

```bash
vcp quality list                                   # open events (add --all for resolved ones)
vcp quality scan                                   # run the gap check again (after new prices)
vcp quality resolve EVENT_ID --by "Your name" --note "Gap confirmed genuine: results day"
```

Some events cannot be closed by hand:

- **A split/bonus conflict** blocks until the feeds agree or the action is entered by hand (7.2).
- **A split/bonus whose ratio could not be read** blocks the same way.

### 7.4 Backups and restore

- Every evening run first copies the database to `data/backups/` and keeps the newest 3.
- A damaged database stops the run before anything is written. `data/logs/daily_runs.log` then shows `FAILED: database check/backup`.
- The run's output names the newest backup. To restore it:

```bash
mv data/vcp_scanner.duckdb data/vcp_scanner.duckdb.damaged
cp data/backups/vcp_scanner_<newest>.duckdb data/vcp_scanner.duckdb
vcp run daily --serving-copy
```

A run that stopped halfway (power cut, crash) needs no restore. DuckDB keeps every completed write, and the next run catches up.

For safety against a disk failure, copy `data/backups/` and `.env` to another drive now and then.

### 7.5 Market Health Score

Built for new sessions by the evening run. To rebuild, after a change of `config/market_health.yaml`, which gives a new settings version:

```bash
vcp health build --rebuild                         # replace the stored days of the current settings version
vcp health build --from 2024-01-01                 # or score from a date
```

Then refresh the serving copy.

### 7.6 Fundamentals

The evening run fetches the latest filings. To check what is stored, or to look at one stock:

```bash
vcp fundamentals status
vcp fundamentals show RELIANCE --date 2026-09-30
vcp fundamentals parse --retry-errors              # after a parser fix: parse the failed filings again
```

### 7.7 Reports

The evening run writes them. To write one again, or for an earlier day:

```bash
vcp report daily --date 2026-10-12
vcp report weekly --date 2026-10-09
```

### 7.8 Paper ledger

Updated by the evening run. To look at it in the terminal:

```bash
vcp paper status                                   # every paper strategy
vcp paper status --strategy flat_base
```

### 7.9 Checks worth running now and then

```bash
vcp verify scan                                    # list recent scan runs
vcp verify scan RUN_ID                             # rebuild one on a copy and compare: must match
vcp backtest lookahead-check --as-of 2026-06-30    # a stored scan rebuilt without later data must be IDENTICAL
vcp config validate
```

---

## 8. Command reference

`vcp --help`, `vcp <command> --help` and `vcp <command> <sub-command> --help` give the full options. Below are every command and sub-command, what each is for, and the options you will use.

**Options most commands share:**

| Option | Default | Meaning |
|---|---|---|
| `--db PATH` | `data/vcp_scanner.duckdb` | the main database |
| `--config-dir DIR` | `config` | the YAML configs |
| `--env-file PATH` | `.env` | credentials |
| `--as-of YYYY-MM-DD` | (required where shown) | the session to work on |
| `--instrument ID_OR_SYMBOL` | all | restrict to a stock (repeatable) |
| `--data-snapshot-id ID` | `LIVE` | read a frozen data snapshot (research); LIVE is the working data |
| `--strategy ID` | `vcp` | `vcp`, `flat_base`, `three_weeks_tight`, `cup_handle`, `double_bottom` |

**Global flags** go before the command: `--log-level DEBUG|INFO|WARNING|ERROR`, `--log-format text|json`, `--log-file PATH`. For example `vcp --log-level DEBUG run daily`.

### 8.1 `vcp version` and `vcp config`

| Command | What it does |
|---|---|
| `vcp version` | Version of every part (algorithms, configs, schema), for the record. |
| `vcp config validate` | Checks every `config/*.yaml`. Run it after editing one. |
| `vcp config hash [--strategy ID]` | The configuration hash stored with results (or one strategy's). |

### 8.2 `vcp auth`

| Command | What it does |
|---|---|
| `vcp auth kite [--api-key KEY]` | Interactive Kite login; writes `KITE_ACCESS_TOKEN` to `.env` ([section 3](#3-credentials-and-tokens)). |

### 8.3 `vcp ingest` (data in)

| Command | What it does |
|---|---|
| `vcp ingest security-master [--start D] [--delisted-file PATH \| --no-delisted]` | NSE listings, delisted companies, ASM/GSM/T2T flags. |
| `vcp ingest bhavcopy --start D [--end D] [--refresh]` | NSE daily bhavcopy: **the price source of truth**. Missing sessions are loaded in date order. |
| `vcp ingest corporate-actions [--start D] [--instrument S]` | Splits, bonuses, dividends, rights from NSE; cross-checked with Upstox when its token is valid; manual entries from `config/manual_corporate_actions.yaml`. |
| `vcp ingest adjusted-prices [--known-at ISO] [--instrument ID]` | Adjusted prices from raw prices and corporate actions. `--known-at` freezes a data snapshot (research). |
| `vcp ingest universe --as-of D [--known-at ISO]` | The eligible universe of a date (liquidity, series, surveillance). |
| `vcp ingest market --today` | Kite: today's provisional bar (before the bhavcopy). |
| `vcp ingest market --kite-history --start D --end D` | Kite's history, for comparison only. |

### 8.4 `vcp compute` (scans)

For one date, run these in this order. For several dates, go oldest first: VCP breakout tracking reads the previous date.

| Command | What it does |
|---|---|
| `vcp compute features [--instrument ID]` | Daily features (moving averages, ATR, volume, highs/lows) and weekly bars. |
| `vcp compute rs --as-of D` | Relative-strength ranks over that date's universe. |
| `vcp compute trend-template --as-of D` | Weekly stage and the ten Trend Template conditions. |
| `vcp compute vcp --as-of D` | VCP detection over that date's Trend Template scan. |
| `vcp compute setups --strategy ID --as-of D` | Another strategy's detector (`--strategy vcp` = `compute vcp`). |
| `vcp compute scores --as-of D [--strategy ID]` | Setup scores and ranking. |
| `vcp compute labels [--strategy ID]` | Forward labels (returns after 5–60 sessions, breakouts) of earlier observations. |

Example, one date by hand:

```bash
D=2026-10-12
vcp ingest universe --as-of $D
vcp compute rs --as-of $D
vcp compute trend-template --as-of $D
vcp compute vcp --as-of $D
vcp compute scores --as-of $D
for s in flat_base three_weeks_tight cup_handle double_bottom; do
  vcp compute setups --strategy $s --as-of $D && vcp compute scores --strategy $s --as-of $D
done
```

### 8.5 `vcp run`

| Command | What it does |
|---|---|
| `vcp run daily [--serving-copy] [--history-start D] [--backup-keep N] [--no-backup]` | The whole evening pipeline ([section 4](#4-the-daily-routine)). `--serving-copy` also refreshes the dashboard's copy and writes the reports. |

### 8.6 `vcp scores` (read the results in the terminal)

| Command | What it does |
|---|---|
| `vcp scores list [--as-of D] [--all] [--limit N] [--strategy ID]` | Ranked setups of a date (latest by default); `--all` adds unranked passers. |
| `vcp scores explain SYMBOL [--as-of D] [--strategy ID]` | How one stock's score was built, part by part. |

### 8.7 `vcp paper`

| Command | What it does |
|---|---|
| `vcp paper update [--strategy ID] [--through D]` | Append the latest sessions' paper decisions (the evening run does this). |
| `vcp paper status [--strategy ID]` | Open paper positions and results so far. |

### 8.8 `vcp report`

| Command | What it does |
|---|---|
| `vcp report daily [--date D]` | Write `reports/daily/<date>.html`. |
| `vcp report weekly [--date D]` | Write `reports/weekly/<ISO week>.html`. |

### 8.9 `vcp health` (Market Health Score)

| Command | What it does |
|---|---|
| `vcp health build [--rebuild] [--from D]` | Score and store the sessions not stored yet; `--rebuild` replaces the current settings version's days. |

### 8.10 `vcp indices`

| Command | What it does |
|---|---|
| `vcp indices update [--from D] [--interval SECONDS]` | NSE index closes (NIFTY 50/500, sector indices) of trading days not stored yet. |

### 8.11 `vcp classify`

| Command | What it does |
|---|---|
| `vcp classify update` | Sectors from NSE's index lists, plus `config/manual_sectors.yaml` (Fridays in the evening run). |

### 8.12 `vcp fundamentals`

| Command | What it does |
|---|---|
| `vcp fundamentals update [--days N]` | Fetch, parse and compute the filings of the last days (the evening run does this). |
| `vcp fundamentals backfill [--limit N] [--resume] [--research-db FILE]` | Everything since `history_from` in the config; resumable. `--research-db data/golden_src.duckdb` also fetches the stocks with past setups in the research database, delisted ones included (for the F7 study). Long: NSE refuses after many hours, so split it across days. |
| `vcp fundamentals fetch [--symbol S] [--from D] [--to D]` | Download result filings not stored yet. |
| `vcp fundamentals parse [--retry-errors] [--rebuild]` | Turn stored filings into quarterly values. |
| `vcp fundamentals status` | What is stored. |
| `vcp fundamentals show SYMBOL [--date D]` | One stock's metrics as of a date. |

### 8.12a `vcp shareholding` (display only)

| Command | What it does |
|---|---|
| `vcp shareholding update [--days N]` | List, download and parse the filings of the last days (the evening run does this). |
| `vcp shareholding backfill [--limit N] [--resume]` | Every filing since `history_from` in `config/shareholding.yaml`; about 7 hours at one file a second; `--resume` continues from the saved list without listing again. |
| `vcp shareholding reparse` | Read every downloaded file again (after a parser fix); no network. |
| `vcp shareholding status` | What is stored: filings by status, layouts, the last run. |
| `vcp shareholding show SYMBOL [--quarters N]` | One stock's promoter, pledge, FII, DII, MF and public % by quarter. |

**On the dashboard:** the stock page's Fundamentals card (latest split and changes, a "▲ rising" flag when the promoters pledged more than a quarter before), the Shareholding pattern card on Stock Analysis (eight quarters, chart and table) and the Fundamentals page's Shareholding view (every stock, sortable, with promoter, pledge, "FII buying" and "Pledge rising" filters). Only quarter-end filings broadcast by the day shown are used, the latest revision of each; a quarter not filed is a dash, never 0. Changes are percentage points. The pledge change is not shown across the June 2025 quarter, when NSE's layout switched from "pledged or encumbered" to pledged only.

Settings live in `config/shareholding.yaml` (its own file, so it can never change a strategy's config hash): the stocks in scope, how far back, the pauses between requests to NSE, and whether the evening run updates it.

### 8.13 `vcp quality` (data-quality gate)

| Command | What it does |
|---|---|
| `vcp quality list [--instrument ID] [--all]` | Data-quality events that block signals. |
| `vcp quality scan [--instrument S]` | Run the gap check and sync corporate-action conflicts into the gate. |
| `vcp quality resolve EVENT_ID --by NAME --note TEXT` | Close an event you have checked (kept as the audit trail). |

### 8.14 `vcp verify` (read-only checks)

| Command | What it does |
|---|---|
| `vcp verify scan [RUN_ID] [--keep]` | Without an id: list recent scan runs. With one: rebuild it on a copy of the database and compare its results hash. |
| `vcp verify kite-adjustment [--action SYM:DATE:SPLIT\|BONUS:NUM:DEN]` | Does Kite return split/bonus-adjusted history? (needs the Kite token) |
| `vcp verify kite-crosscheck --instrument S [--start D] [--end D]` | Compare our adjusted closes with Kite's; explain every difference. |

### 8.15 `vcp backtest` (research)

| Command | What it does |
|---|---|
| `vcp backtest run --from D --to D [--strategy ID] [--rule R] [--entry breakout\|cross_5] [--regime none\|breadth50\|ew50] [--baseline]` | Replay the eligible setups of a date range. |
| `vcp backtest walk-forward [--validation] [--test]` | Each period of `config/backtest.yaml`. |
| `vcp backtest bias-report --from D --to D` | Survivorship, corporate-action and look-ahead exposure. |
| `vcp backtest lookahead-check --as-of D [--variant prices\|corporate-actions]` | Rebuild a stored scan without later data; must be IDENTICAL. |

### 8.16 `vcp research` (golden dataset and studies)

| Command | What it does |
|---|---|
| `vcp research labelling-sheet --from D --to D --out DIR` | A blind labelling sheet of candidates (HTML). |
| `vcp research import-labels --candidates FILE --labels CSV` | Turn the sheet's CSV into golden fixtures. |
| `vcp research golden [--split development\|holdout\|all] [--record]` | Score the detector on the golden fixtures. |
| `vcp research review-sheet --from D --to D --out DIR [--strategy ID]` | A mark-check sheet with the detector's marks drawn. |
| `vcp research fundamentals [--period development\|validation --unblind] [--no-store]` | F7: the shadow fundamental score and shareholding against past setups' outcomes; writes `reports/research/f7_fundamentals_<period>.md` and stores the scores in `fundamental_research_scores`. The validation period needs `--unblind` (look once). |
| `vcp research outcomes --from D --to D --split D [--variant ...] [--csv FILE]` | Forward outcomes by detector class. |
| `vcp research score-outcomes --from D --to D --split D` | Do higher scores lead to better outcomes? |

### 8.16b `vcp alerts` (Telegram)

Alerts tell you, on your phone, what the dashboard would show. They never change a scan, a score, the paper ledger or a strategy, and they are not trading advice. Settings: `config/alerts.yaml`.

* **Evening summary:** at the end of every daily run: whether the run was OK (or which step failed, or an NSE holiday), market health, today's breakouts, and setups within 2 % of their pivot.
* **Intraday pivot alerts:** in market hours, from the live feed the dashboard polls: "approaching" (within 1 % below the pivot) and "crossed" (trading above it; with a warning beyond 5 %, the entry limit), at most once per stock per day. Watched: setups of the latest scan within 3 % of their pivot, the AI watchlist, and your own `symbols:` in `config/alerts.yaml`. A crossing is confirmed only at the close (volume). Each pivot alert shows: the strategy, grade, score, RS rank and sector; the price with the day's change and range; the buy zone (pivot to pivot +5 %); the setup's stop and the risk % from the current price; volume so far against the 50-day average, with the pace (volume per minute against a normal day; early in the session the pace runs high because volume is heavy at the open); the market-health line; the time (IST); and a TradingView chart link. The watcher starts with the dashboard (`scripts/dashboard.sh`), so it runs while the dashboard runs; its log is `logs/alerts_watch.log`.

**Setup (once, 5 minutes):**

1. In Telegram, open **@BotFather**, send `/newbot`, pick a name; it gives you a token like `123456:ABC...`.
2. Add it to `.env`: `TELEGRAM_BOT_TOKEN=123456:ABC...` (never share or commit `.env`).
3. Open your new bot in Telegram and send it any message (e.g. "hi").
4. Run `vcp alerts chat-id`; copy the line it prints (`TELEGRAM_CHAT_ID=...`) into `.env`.
5. Run `vcp alerts test`: a test message arrives. Restart the dashboard to start the intraday watcher.

| Command | What it does |
|---|---|
| `vcp alerts test` | Send a test message |
| `vcp alerts chat-id` | Show the chat ids that messaged your bot |
| `vcp alerts evening` | Send the evening summary now (the daily run sends it itself) |
| `vcp alerts watch [--once]` | The intraday watcher (the dashboard script starts it) |
| `vcp alerts log` | What was sent, and anything that could not be sent |

The dashboard page **Alerts** shows the same: whether Telegram is set up (yes or no; the token is never shown), the watcher's last check and the stocks it watches with their live distance to the pivot, the alerts sent (and why one could not be), and the settings.

### 8.16a `vcp agent` (the AI's second brain, AGENT_SPECIFICATION)

| Command | What it does |
|---|---|
| `vcp agent serve` | The MCP server; Claude Code starts it itself (registered in `.mcp.json`), you do not run it by hand. |
| `vcp agent notes [--kind decision] [--search TEXT]` | The notebook: decisions, findings, hypotheses, questions, newest first. |
| `vcp agent note KIND "TEXT" [--tags a,b]` | Add your own entry (marked as yours, `source owner`). |
| `vcp agent audit [--limit N]` | Every tool call the AI made, with its arguments. |
| `vcp agent seed` | Add the standing decisions to a new notebook (done once; safe to repeat). |

**Using it with Claude Code:** in an Ubuntu terminal, `cd ~/projects/vcp_ai` and run `claude`. The first time, Claude Code asks whether to use the project's MCP server `vcp-scanner`: approve it (`/mcp` shows it and its tools). Then ask in plain words, e.g. "give me today's brief", "why is DEEPINDS a grade-2 VCP?", "what did we decide about fundamentals?", "note that I want to look at failed breakouts in weak breadth". It can also read past scans: "which stock had the highest VCP score on 16 Oct?", "how has GRWRHITECH's score moved over the last month?". And it can investigate data and runs: "which stocks have open unexplained gaps?", "check TVSHLTD's corporate actions", "show SIYSIL's raw and adjusted prices around 21 Aug", "what warnings did tonight's run log?", "break down backtest <id> by exit". Since step A2 it can also run research, under rules the server enforces: it must first register a hypothesis (the idea, the measure and what result would count, before seeing any result; 3 per strategy until the April 2027 review), then run studies of forward returns or backtests on the development years (2022 to mid-2024), check once on the validation years, and conclude. Every run is logged in the notebook; `vcp agent hypotheses` lists them. Try "register a hypothesis that VCP breakouts fail more in weak breadth, then study it". With `what_if_score` it can also ask "would the ranking have been better with different scoring weights?" on stored scores, for example more weight on RS. Nothing it concludes changes a strategy. Since step A3 it can propose changes it cannot make itself: a corporate action with evidence, closing a data-quality warning, a sector, or symbols for its own AI watchlist (that one you pre-approved). See them with `vcp agent proposals` (or `--status PENDING`); decide in your own Ubuntu terminal with `vcp agent approve 7` or `vcp agent reject 7 "the ratio is wrong"`: you type the number again to confirm. The AI reads your reason. Approved file changes are committed to git locally (not pushed). The dashboard page **AI Agent** shows the same proposals (with the commands to copy), the AI watchlist, the research lab and the latest notebook decisions. Step A1 can only read and write the notebook: no tool changes a strategy, a config, the ledger or credentials, or calls NSE. Proposals and approvals come in later steps (AGENT_SPECIFICATION §8). The notebook and audit live in `data/agent/agent.duckdb`.

### 8.17 `vcp api`

| Command | What it does |
|---|---|
| `vcp api serve [--port 8000] [--host 127.0.0.1]` | The read-only dashboard API (`scripts/dashboard.sh` starts it). |

---

## 9. Troubleshooting

| You see | Likely cause | Fix |
|---|---|---|
| Status bar: "Last daily run (...) failed: <step>" | One step of the evening run failed. The others ran. | Read that day's `data/logs/daily_run_<date>.out`, find the step, fix the cause, then run `vcp run daily --serving-copy` again. |
| "The daily run started ... stopped during <step> and did not finish" | The run was stopped (by hand, a kill, a shutdown) before it finished | Nothing to repair: the next run catches up. To run it now: `bash scripts/daily_run.sh`. |
| "Prices to" is a day behind after 19:15 | NSE had not published the bhavcopy yet | The 22:00 run picks it up; or run `bash scripts/daily_run.sh` later. |
| `daily_runs.log`: `FAILED: database check/backup` | The database did not open, or the backup failed (disk full?) | Free disk space and run again. If the database is damaged, restore the newest backup ([section 7.4](#74-backups-and-restore)). |
| `daily_runs.log`: `surveillance lists collected: NONE` | NSE did not answer for the ASM/GSM/T2T lists | Run `vcp ingest security-master` again the same evening; a missed day cannot be fetched later. |
| Security master step failed: delisted file not found | NSE renamed or moved its delisted-companies file | Download it from NSE's page and run `vcp ingest security-master --delisted-file PATH`; report it so the link finder can be updated. |
| NSE requests get HTTP 403 | NSE's bot protection | Wait and try later at a gentle pace. Do not use VPNs, proxies or IP changes to get around it. |
| Live feed: "needs a fresh token" | Upstox (or Kite) token expired | [Section 3](#3-credentials-and-tokens). Picked up within a minute, no restart. |
| Live feed shows the wrong provider | `config/live.yaml` says the other one, or an edit had a typo | Check the `provider:` line; the API log (`logs/dashboard.log`) names a rejected edit. |
| Dashboard page does not load | The dashboard is not running | `bash scripts/dashboard.sh`. If port 3000 or 8000 is taken, an old copy is still running: stop it ([section 5.1](#51-start-stop-restart)). |
| Dashboard shows old data after a manual change | It reads the serving copy | Refresh the serving copy ([section 5.2](#52-where-its-data-comes-from)). |
| Dashboard looks unchanged after a code update | The pages were not rebuilt | `cd frontend && npm run build`, then restart the dashboard. |
| A stock is missing from the scans | Not in the universe (liquidity, series, surveillance) or blocked by data quality | `vcp quality list --instrument SYMBOL`; the stock page and the Screener say why. |
| A stock has no sector | Not in any NSE index list | Add it to `config/manual_sectors.yaml` ([section 7.1](#71-sectors-stocks-without-a-sector)). |
| Status bar: manual sector entries older than 12 months | `as_of` dates are old | Check NSE's quote page again and update `as_of`. |
| "unknown keys" or "not valid YAML" from a command | A config edit has a typo | `vcp config validate` names the file and the key. |

---

## 10. What is frozen, what is display only

**Frozen until the review on or after 2027-04-01** (STRATEGY_SPECIFICATION §21):

- **The five strategies:** `vcp-1.1.0`, `flat_base-1.0.0`, `three_weeks_tight-1.1.0`, `cup_handle-1.1.0` and `double_bottom-1.0.0`, with their scan configs.
- **The trade rules:** entry `cross_5`, regime `breadth50`, exit `hold_low8`, a 20-session watch, 10 positions, 15 bps a side.
- **Scoring and labels:** `scoring-1.0.0` and `labels-1.0.0`.
- **The paper ledger:** rule set `paper-v1`, started 2026-10-01. It is append-only and never edited.

A change to any of these is a new version and restarts that strategy's paper clock. Data fixes (corporate actions, late bhavcopies) continue as normal. The review gates per strategy: at least 30 closed trades, a profit factor of 1.3 or better, and a drawdown no worse than −20 %.

**Display and research only** (they never change a scan, a score, the regime or the paper ledger):

- **Market Health Score:** its pillars, label and shadow traffic light.
- **Fundamentals:** quarterly results from NSE filings.
- **Sectors:** NSE and manual; also Sector strength and Sector rotation.
- **Live prices:** Upstox or Kite.
- **Reports and the dashboard.**

**Rules that always apply:**

- The project never places orders.
- NSE data is fetched gently: no VPNs, proxies or IP changes to get around a block, no faked browser, and no use of your own browser to scrape NSE.
- screener.in is not scraped (its terms forbid it).
- Results built on `LIVE` data are not valid for tuning thresholds. Only point-in-time snapshots are (README, "Known limitations").

---

## 11. Where things live

| Path | What |
|---|---|
| `data/vcp_scanner.duckdb` | The main database (about 2.2 GB). Only the commands write it. |
| `data/serving/vcp_serving.duckdb` | The dashboard's read-only copy, refreshed by the evening run. |
| `data/backups/` | The newest 3 pre-run backups. |
| `data/raw/` | Downloaded NSE files (bhavcopies and others), kept so a rebuild needs no download. |
| `data/logs/daily_runs.log`, `data/logs/daily_run_<date>.out` | Evening-run summary and full output. |
| `logs/` | Logs of hand-run jobs and the dashboard (`logs/dashboard.log`). |
| `reports/daily/`, `reports/weekly/` | HTML reports. |
| `config/` | All settings. The ones you edit: `live.yaml` (provider), `manual_sectors.yaml`, `manual_corporate_actions.yaml`. The others are strategy and data rules: changing one is a strategy change (see `AGENTS.md`). |
| `.env` | Credentials (git-ignored). |
| `scripts/daily_run.sh`, `scripts/dashboard.sh` | The evening run (what the scheduler calls) and the dashboard. |
| `frontend/` | The dashboard's web pages (Next.js). |
| `README.md` | Status, known limitations and the map of all specifications. |
| `CHANGELOG.md` | What changed, newest first. |
