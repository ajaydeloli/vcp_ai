# VCP Scanner

Institutional-grade NSE VCP scanner (Minervini Trend Template + Volatility Contraction Pattern).
See `PROJECT_DESIGN.md` for architecture and `AGENTS.md` for agent rules.

## Status

Phases 0–5 are built: data ingestion, corporate-action adjustment, universe snapshots, daily
features, relative strength, weekly stage and the Trend Template. **The VCP detector (Phase 6),
scoring (Phase 7), API and frontend do not exist yet.**

An independent audit of Phases 0–5 (`reports/phase0-5-independent-audit.md`, kept locally and
git-ignored) concluded that Phase 6 should not start until its blockers are closed. Read
[Known limitations](#known-limitations) before trusting any output for research.

## Setup

Requires Python 3.11+ (developed on 3.12).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then fill in credentials; .env is git-ignored
vcp config validate         # checks config/*.yaml
```

Run all commands from the repository root: the defaults `--config-dir config`,
`--db data/vcp_scanner.duckdb` and `--env-file .env` are relative paths.

### Credentials

| Variable | Used by | Notes |
|---|---|---|
| `KITE_API_KEY`, `KITE_API_SECRET` | `vcp auth kite` | Kite Connect app credentials. |
| `KITE_ACCESS_TOKEN` | `vcp ingest market` | Written to `.env` by `vcp auth kite`. Kite tokens are short-lived; re-run auth when it expires. |
| `UPSTOX_ACCESS_TOKEN` | `vcp ingest corporate-actions` | Optional. Without it, corporate actions come from NSE only and stay `SINGLE_SOURCE` (not cross-confirmed, never escalated to a conflict). With it, an NSE-only split/bonus that Upstox was asked about and still lacks after 3 days becomes `PROVIDER_CONFLICT` and blocks. |

`.env.example` also lists reserved settings (`DHAN_*`, `UPSTOX_API_KEY`/`UPSTOX_API_SECRET`,
`VCP_ENV`, `VCP_LOG_LEVEL`, `VCP_LOG_JSON`). **The code does not read these yet.** Configure logging
with the CLI flags or `config/logging.yaml` (see below).

## Running the pipeline

Nothing runs automatically; each stage is a manual command. Later stages read what earlier ones
stored in DuckDB, so order matters.

| # | Command | What it does | Needs |
|---|---|---|---|
| 1 | `vcp auth kite` | Interactive Kite login; stores the access token in `.env`. | Kite API key/secret |
| 2 | `vcp ingest security-master --start YYYY-MM-DD` | Seeds `instruments` from NSE's current listing and loads the NSE security master, NSE's delisted-companies list and ASM/T2T surveillance flags into `security_master_history` / `surveillance_flags_history`. `--delisted-file PATH` uses a downloaded copy of the delisted list; `--no-delisted` skips it. | Network |
| 3 | `vcp ingest market --start YYYY-MM-DD` | Fetches daily OHLCV from Kite, re-fetches gaps, reports incomplete instruments. | Instruments (see note), Kite token |
| 4 | `vcp ingest corporate-actions --start YYYY-MM-DD` | Ingests and reconciles splits/bonuses (NSE primary, Upstox secondary), then runs the gap safety net and publishes conflicts to the signal gate. | Instruments (see note) |
| 4a | `vcp verify kite-adjustment [--action SYMBOL:DATE:SPLIT:NUM:DEN]` | Read-only check that Kite returns split/bonus-adjusted history, which the adjustment engine assumes (exit 2 = assumption wrong, stop). Run once after `vcp auth kite`. | Kite token |
| 5 | `vcp ingest adjusted-prices [--known-at ISO]` | Builds adjusted daily prices from raw prices and stored adjustment factors. With `--known-at` it freezes a data snapshot and prints its id; otherwise it builds `LIVE`. | Steps 3–4 |
| 6 | `vcp compute features [--data-snapshot-id ID]` | Daily features (SMA, ATR, volume, volatility, highs/lows) and weekly bars. | Step 5 |
| 7 | `vcp ingest universe --as-of YYYY-MM-DD` | Builds the eligible-universe snapshot (liquidity, series, surveillance). | Steps 2–3 |
| 8 | `vcp compute rs --as-of YYYY-MM-DD [--data-snapshot-id ID]` | Relative-strength ranks over that universe snapshot. | Steps 5–7 |
| 9 | `vcp compute trend-template --as-of YYYY-MM-DD [--data-snapshot-id ID]` | Weekly stage plus the ten Trend Template conditions for eligible members. | Steps 6–8 |
| 10 | `vcp compute vcp --as-of YYYY-MM-DD [--data-snapshot-id ID]` | VCP detection over that date's Trend Template scan (same config): base, contractions, measurements, pivots, class (A+ / VCP / VCP-like), status and breakouts, stored in the `vcp_*` tables with a `VCP` scan-run record. Compute dates in order: breakout tracking reads the previous date. | Step 9 |
| 11 | `vcp compute scores --as-of YYYY-MM-DD [--data-snapshot-id ID]` | Setup scores (trend, VCP shape, volume, RS; fundamentals from Phase 8) for every Trend Template passer of that date, stored with every sub-score in `setup_scores` / `score_components` with a `SCORE` scan-run record; VCP-like-or-better setups that are forming, pivot-ready or broken out get a ranking percentile. | Step 10 |

> Step 2 also fills the `instruments` table that steps 3 and 4 iterate over: every security in
> NSE's current listing is upserted (ISIN first, so a renamed symbol keeps its id) and anything
> that left the listing is marked inactive. `tests/integration/test_pipeline_e2e.py` runs steps
> 2–9 from an empty database with synthetic providers.

Steps 6 and 7 are independent of each other. Several commands accept `--instrument` (repeatable) to
work on a subset, and the two ingest commands also accept `--limit`; run `vcp <command> --help` for
the full options.

```bash
vcp ingest security-master --start 2020-01-01
vcp ingest market --start 2020-01-01
vcp ingest corporate-actions --start 2020-01-01
vcp ingest adjusted-prices
vcp compute features
vcp ingest universe --as-of 2026-09-29
vcp compute rs --as-of 2026-09-29
vcp compute trend-template --as-of 2026-09-29
vcp compute vcp --as-of 2026-09-29
vcp compute scores --as-of 2026-09-29
vcp backtest run --from 2022-02-01 --to 2024-06-30      # event-engine backtest over stored scans (Phase 9)
vcp backtest walk-forward [--validation]             # each period of config/backtest.yaml
vcp backtest lookahead-check --as-of 2023-06-23      # rebuild a stored scan without later data; must be IDENTICAL
vcp backtest bias-report --from 2022-02-01 --to 2026-09-30
vcp compute labels                    # forward labels (returns after 5-60 sessions, breakouts) of earlier observations
vcp scores list                       # ranked setups of the latest scored date (--all adds unranked passers)
vcp scores explain SIYSIL             # how one stock's score was built, sub-score by sub-score
```

### Golden dataset (VCP labels)

```bash
vcp research labelling-sheet --from 2024-10-01 --to 2026-09-30 --out data/labelling   # needs Trend Template scans for those dates
# open data/labelling/labelling_sheet.html, label, Download CSV
vcp research import-labels --candidates data/labelling/candidates.json --labels vcp_labels.csv
vcp research golden --record          # record detector baselines, then commit tests/fixtures/vcp
vcp research golden --split development
# Phase 6 validation (owner, 2026-10-03): mark check + outcome study
vcp research review-sheet --from 2024-10-01 --to 2026-09-30 --out data/review   # detector marks drawn; answer yes/partly/no/unsure
vcp research score-outcomes --from 2022-01-01 --to 2026-09-30 --split 2025-09-30   # do higher scores do better? (Phase 7)
vcp research outcomes --from 2024-10-01 --to 2026-09-30 --split 2025-09-30 [--variant rs10:vcp.pivot.max_right_side_range_pct=10] [--validate-rule hold_s7] [--csv out.csv]
```

## Configuration

Strategy and data behavior live in `config/*.yaml` (`strategy`, `scoring`, `universe`, `data`,
`monitoring`, `logging`). Thresholds are hypotheses: changing one is a strategy change (update the
spec, bump the version, note old/new/why; see `AGENTS.md`). `vcp config hash` prints the
deterministic configuration hash stored with Trend Template results.

Some values are still hard-coded outside config (feature windows, weekly aggregation rules,
RS return columns); the audit report lists them.

## Logging

Logs are structured, go to **stderr** (command results stay on stdout), and are configured by
`config/logging.yaml` with these global flags overriding it. Give the flags before the command:

```bash
vcp --log-level DEBUG ingest market --start 2026-09-01
vcp --log-format json --log-file logs/vcp.log compute features
```

## Development

```bash
pytest -q                     # full suite; takes about a minute
ruff check src tests
ruff format --check src tests
mypy src
```

Tests use synthetic fixtures only (labelled as such); `tests/regression` and `tests/backtest` are
placeholders; `tests/integration` holds the end-to-end pipeline test. Coverage is not measured
and there is no CI yet, so run the four commands above yourself before committing.

## Known limitations

These are the ones that matter for interpreting results. The audit report has the full list and
current status of each.

- **Point-in-time only when you use a data snapshot.** Build adjusted prices with
  `vcp ingest adjusted-prices --known-at <ISO time>` and pass `--data-snapshot-id <id>` to the
  compute commands; that freezes raw prices and adjustment factors "as known then". Without a
  snapshot everything runs as `LIVE` (unfrozen) and a later correction or corporate action can
  change a past signal, so LIVE results must not be used to validate thresholds. Snapshots have no
  content-hash manifest or per-dataset cutoffs yet, and fundamentals/universe are not covered by
  the snapshot cutoff (audit P0-1 core fixed; P1-5 open).
- **Universe is survivorship-biased (audit P0-3, partly addressed).** `vcp ingest security-master`
  now also loads NSE's list of delisted companies, so snapshots are stamped `PARTIAL` instead of
  `BIASED`. That list omits merger delistings and is thin before 2016, and delisted names have no
  price history in the database, so they cannot enter a universe yet. There is still no verified
  historical surveillance data. Neither `PARTIAL` nor `BIASED` may validate thresholds.
- **Data-quality gate (audit P0-2, fixed).** Unresolved corporate-action conflicts, split-like
  unexplained gaps and missing sessions are stored in `data_quality_events` and block signals: the
  universe marks the symbol ineligible, RS leaves it out of the ranking, and the Trend Template
  reports `DATA_QUALITY_BLOCKED`. Review with `vcp quality list`; `vcp quality scan` re-runs the
  gap check; `vcp quality resolve EVENT_ID --by NAME --note TEXT` closes a gap you have confirmed
  is genuine. A split/bonus conflict, or an applied split/bonus whose ratio could not be read,
  cannot be closed by hand and blocks until the feeds agree or a ratio arrives (no
  manual-override command yet). Dividend and rights disagreements are warnings only. The gate only knows what has been scanned: run
  `vcp quality scan` after ingesting prices. Missing-session detection infers sessions from the
  data (no official NSE holiday calendar), and bad-bar and stale-data events are not recorded.
- **Provider mapping is Kite-only.** `provider_instruments` records Kite token to instrument
  mappings (audit P1-1, fixed); Upstox bars carry their instrument key but have no mapping rows.
  NSE corporate-action ratio parsing is best-effort and has no real-world golden fixtures
  (audit P1-2).
- **Features:** EMA fields are always `NULL`. ETFs cannot be excluded from the universe (no data
  source; they trade as `EQ`). Parquet export exists but is not used for reads.
- **Scan lineage without stored copies:** every Trend Template scan writes an immutable
  `scan_runs` record (data cutoff, universe snapshot, config hashes, git commit, results hash)
  and `vcp verify scan RUN_ID` rebuilds it on a copy of the database and compares the hash
  (audit P1-8). There is still no `snapshot_manifest` with per-table content hashes, and
  results made by code with uncommitted changes are flagged but cannot be rebuilt exactly.

## Research restrictions

- Only results stamped `POINT_IN_TIME_COMPLETE` may be used to validate thresholds. Today none are.
- Thresholds must not be tuned against this data until the point-in-time and survivorship items
  above are closed.
- The project never places orders. There is no live-execution path and there must not be one.

## Documentation map

| Topic | Document |
|---|---|
| Architecture, phases, scope | `PROJECT_DESIGN.md` |
| Tables, columns, bitemporal rules | `DATABASE_SCHEMA.md` |
| Ingestion, providers, data quality, snapshots | `DATA_SPECIFICATION.md` |
| Trend Template, RS formula, weekly Stage | `TREND_TEMPLATE_SPECIFICATION.md` |
| VCP detection and classification (Phase 6) | `VCP_SPECIFICATION.md` |
| Scoring and ranking (Phase 7) | `SCORING_SPECIFICATION.md` |
| Agent entry point / detailed agent rules | `AGENTS.md` / `AI_AGENT_RULES.md` |
| Change history | `CHANGELOG.md` |

## Daily run, backups and recovery

`scripts/daily_run.sh` runs `vcp run daily` (Windows Task Scheduler, Mon–Fri 19:15 and 22:00 IST; one run at a time). As its last step it refreshes the dashboard's read-only serving copy (`data/serving/vcp_serving.duckdb`, about 2 GB, 9 seconds; `--serving-copy`). Before any step it checks that `data/vcp_scanner.duckdb` opens and answers a query, then copies it to `data/backups/vcp_scanner_<UTC date>_<time>.duckdb`, keeping the newest 3 (`--backup-keep`, `--backup-dir`, `--no-backup`). A damaged database or a failed backup stops the run before anything is written; `data/logs/daily_runs.log` records `FAILED: database check/backup` and the run's output names the newest backup with the restore command:

```bash
mv data/vcp_scanner.duckdb data/vcp_scanner.duckdb.damaged
cp data/backups/vcp_scanner_<newest>.duckdb data/vcp_scanner.duckdb
.venv/bin/vcp run daily --db data/vcp_scanner.duckdb --config-dir config --env-file .env
```

A run that stops halfway (power cut, crash) needs no restore: DuckDB keeps every completed write and rolls back the unfinished one, and the next run catches up.

