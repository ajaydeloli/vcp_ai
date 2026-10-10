"""The evening run: bring the database up to date and scan every new session (daily run).

``vcp run daily`` chains the existing commands. It is safe to run late or after skipped
evenings: every step catches up on its own.

0. Health check and backup (owner decision 2026-10-02): the database must open and answer a
   query, then it is copied to ``<db folder>/backups/`` (newest ``--backup-keep`` copies kept).
   A database that fails the check, or a backup that fails, stops the run before anything is
   written, and the message names the newest backup to restore (``vcp_scanner.backup``).
1. ``ingest security-master``: listings, delistings and today's ASM/GSM/T2T lists. NSE
   publishes only the *current* surveillance lists, so this is the one thing a skipped evening
   loses for good; the day is recorded in ``surveillance_collections`` either way.
2. ``ingest bhavcopy`` from the day after the last settled file: every missed session is
   loaded in date order; today's file not being published yet just stops the load (PENDING).
3. ``ingest corporate-actions`` over the 60 days before the first new session.
4. ``ingest adjusted-prices`` and ``compute features`` (full history, cheap).
5. ``ingest universe`` / ``compute rs`` / ``compute trend-template`` / ``compute vcp`` /
   ``compute scores`` for every session after the last scanned one (one session on the first
   run), in date order (VCP breakout tracking reads the previous date, VCP_SPECIFICATION 61B);
   then ``compute labels`` once (forward labels of earlier observations, Phase 9).
6. Paper strategies (STRATEGY_SPECIFICATION 21.2): for every other strategy whose file says
   ``enabled: true`` (all ``stage: paper`` in the monitoring phase), ``compute setups`` and
   ``compute scores --strategy ID`` after each date's VCP scores, and ``compute labels
   --strategy ID`` after VCP's labels. A strategy file that fails to load is a failed step;
   VCP still runs.
7. ``paper update``: the paper ledger appends the new sessions' decisions of every paper
   strategy (STRATEGY_SPECIFICATION 21.3).

8. Serving copy (FRONTEND_SPECIFICATION 67.2; only with ``--serving-copy``): the checkpointed
   database is copied atomically to ``<db folder>/serving/vcp_serving.duckdb`` for the
   read-only dashboard API. A failed copy fails the run but leaves the previous copy in place.

One line per run is appended to ``<db folder>/logs/daily_runs.log``.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from vcp_scanner.backup import (
    DEFAULT_KEEP,
    backup_database,
    check_database,
    default_backup_dir,
    restore_hint,
)
from vcp_scanner.data.providers._time import IST
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.serving import default_serving_path, refresh_serving_copy

logger = logging.getLogger(__name__)

CA_LOOKBACK_DAYS = 60


def _query(db: str, sql: str, params: list[object] | None = None) -> list[tuple[object, ...]]:
    with DuckDBStore(db) as store:
        store.migrate()
        return store.conn.execute(sql, params or []).fetchall()


def _date(value: object) -> date | None:
    return value if isinstance(value, date) else None


def sessions_to_scan(db: str) -> list[date]:
    """Sessions with prices that have no Trend Template result yet (latest only on a first run)."""
    last_scan = _date(_query(db, "SELECT max(as_of_date) FROM trend_template_results")[0][0])
    rows = _query(
        db,
        "SELECT trade_date FROM bhavcopy_files WHERE status = 'OK'"
        " GROUP BY trade_date ORDER BY trade_date",
    )
    sessions = [d for (d,) in rows if isinstance(d, date)]
    if last_scan is None:
        return sessions[-1:]
    return [d for d in sessions if d > last_scan]


def paper_strategies(config_dir: str) -> list[str]:
    """Strategies other than VCP that the daily run executes (``enabled: true``), in registry
    order. Raises when a strategy file is invalid (section 17)."""
    from vcp_scanner.domain.strategy import VCP_STRATEGY_ID
    from vcp_scanner.patterns.registry import REGISTRY, load_strategies

    files = load_strategies(config_dir)
    return [sid for sid in REGISTRY
            if sid != VCP_STRATEGY_ID and sid in files and files[sid].enabled]  # fmt: skip


def run_daily(args: argparse.Namespace, cli_main: Callable[[list[str]], int]) -> int:
    db, cfg, env = args.db, args.config_dir, args.env_file
    started = datetime.now(UTC)
    today = started.astimezone(IST).date()
    results: list[tuple[str, int]] = []

    if not getattr(args, "no_backup", False):
        backup_dir = Path(getattr(args, "backup_dir", None) or default_backup_dir(db))
        keep = int(getattr(args, "backup_keep", None) or DEFAULT_KEEP)
        print(f"=== database check and backup ({datetime.now(UTC).astimezone(IST):%H:%M} IST)")
        error = check_database(db)
        try:
            if error is not None:
                raise RuntimeError(f"database failed its health check: {error}")
            backup = backup_database(db, backup_dir, keep=keep, now=started)
        except Exception as exc:
            message = f"ERROR: {exc}. Nothing was run.\n{restore_hint(db, backup_dir)}"
            print(message)
            _append_summary(
                db, f"{started.astimezone(IST):%Y-%m-%d %H:%M} IST | FAILED: database check/backup"
            )
            return 1
        print(f"Backup: {backup} ({backup.stat().st_size / 1e9:.2f} GB, newest {keep} kept)")

    def step(name: str, argv: list[str]) -> int:
        print(f"\n=== {name} ({datetime.now(UTC).astimezone(IST):%H:%M} IST)")
        code = cli_main(argv)
        results.append((name, code))
        return code

    step(
        "security master + ASM/GSM/T2T lists",
        ["ingest", "security-master", "--start", args.history_start, "--db", db],
    )

    last_settled = _date(
        _query(
            db,
            "SELECT max(trade_date) FROM bhavcopy_files WHERE status IN ('OK', 'NO_SESSION')",
        )[0][0]
    )
    bhav_start = (
        last_settled + timedelta(days=1) if last_settled else date.fromisoformat(args.history_start)
    )
    if bhav_start <= today:
        step(
            "NSE bhavcopy",
            ["ingest", "bhavcopy", "--start", bhav_start.isoformat(), "--db", db,
             "--config-dir", cfg],
        )  # fmt: skip
    ca_start = min(bhav_start, today) - timedelta(days=CA_LOOKBACK_DAYS)
    step(
        "corporate actions",
        ["ingest", "corporate-actions", "--start", ca_start.isoformat(), "--db", db,
         "--config-dir", cfg, "--env-file", env],
    )  # fmt: skip
    step("adjusted prices", ["ingest", "adjusted-prices", "--db", db])
    step("features", ["compute", "features", "--db", db])

    try:
        strategies = paper_strategies(cfg)
    except Exception as exc:  # a bad strategy file: VCP still runs, the run is marked failed
        print(f"\n=== strategy config\nERROR: {exc}")
        results.append(("strategy config", 1))
        strategies = []
    scan_dates = sessions_to_scan(db)
    for d in scan_dates:
        iso = d.isoformat()
        step(f"universe {iso}", ["ingest", "universe", "--as-of", iso, "--db", db,
                                 "--config-dir", cfg])  # fmt: skip
        step(f"RS {iso}", ["compute", "rs", "--as-of", iso, "--db", db, "--config-dir", cfg])
        step(f"Trend Template {iso}", ["compute", "trend-template", "--as-of", iso, "--db", db,
                                       "--config-dir", cfg])  # fmt: skip
        step(f"VCP {iso}", ["compute", "vcp", "--as-of", iso, "--db", db,
                            "--config-dir", cfg])  # fmt: skip
        step(f"scores {iso}", ["compute", "scores", "--as-of", iso, "--db", db,
                               "--config-dir", cfg])  # fmt: skip
        for sid in strategies:
            step(f"{sid} setups {iso}", ["compute", "setups", "--strategy", sid, "--as-of", iso,
                                         "--db", db, "--config-dir", cfg])  # fmt: skip
            step(f"{sid} scores {iso}", ["compute", "scores", "--strategy", sid, "--as-of", iso,
                                         "--db", db, "--config-dir", cfg])  # fmt: skip
    if scan_dates:
        step("forward labels", ["compute", "labels", "--db", db, "--config-dir", cfg])
        for sid in strategies:
            step(f"{sid} forward labels", ["compute", "labels", "--strategy", sid, "--db", db,
                                           "--config-dir", cfg])  # fmt: skip
        step("paper ledger", ["paper", "update", "--db", db, "--config-dir", cfg])

    if getattr(args, "serving_copy", False):
        target = Path(getattr(args, "serving_path", None) or default_serving_path(db))
        print(f"\n=== serving copy ({datetime.now(UTC).astimezone(IST):%H:%M} IST)")
        try:
            copy = refresh_serving_copy(db, target)
            print(f"Serving copy: {copy} ({copy.stat().st_size / 1e9:.2f} GB)")
            results.append(("serving copy", 0))
        except Exception as exc:  # the dashboard keeps the previous copy
            print(f"ERROR: serving copy not refreshed: {exc}. The dashboard keeps the old copy.")
            results.append(("serving copy", 1))
        else:
            _write_reports(db, cfg, target)

    collected = sorted(
        str(r[0])
        for r in _query(
            db,
            "SELECT flag_type FROM surveillance_collections WHERE collected_on = ?",
            [started.date()],
        )
    )
    latest = _date(
        _query(db, "SELECT max(trade_date) FROM bhavcopy_files WHERE status = 'OK'")[0][0]
    )
    failed = [name for name, code in results if code != 0]
    summary = (
        f"{started.astimezone(IST):%Y-%m-%d %H:%M} IST | prices to {latest} | "
        f"scanned {', '.join(d.isoformat() for d in scan_dates) or 'nothing new'} | "
        f"surveillance lists collected: {', '.join(collected) or 'NONE'} | "
        + (f"FAILED: {', '.join(failed)}" if failed else "all steps OK")
    )
    print("\n=== Daily run summary\n" + summary)
    if "ASM" not in collected or "GSM" not in collected:
        print(
            "WARNING: today's ASM/GSM lists were not collected. NSE publishes only the current "
            "lists, so today's universe stays PARTIAL. Re-run `vcp run daily` today if possible."
        )
    _append_summary(db, summary)
    return 1 if failed else 0


def _write_reports(db: str, config_dir: str, serving: Path) -> None:
    """Daily (and Friday weekly) HTML report. Never fails the run and never writes data."""
    try:
        from vcp_scanner.reporting.build import write_reports

        root = Path(db).resolve()
        from vcp_scanner.data.providers.nse_bhavcopy import NseBhavcopyProvider

        def holidays() -> set[date]:
            return NseBhavcopyProvider(root.parent / "raw" / "bhavcopy").get_trading_holidays()

        out = root.parent.parent / "reports"
        for path in write_reports(serving, config_dir, root.parent, out, holidays):
            print(f"Report written: {path}")
    except Exception as exc:
        print(f"WARNING: report not written: {exc}")


def _append_summary(db: str, line: str) -> None:
    log = Path(db).resolve().parent / "logs" / "daily_runs.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
