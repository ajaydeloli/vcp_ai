"""Compute the VCP scans of the history research database (owner request 2026-11-08, stage 4).

The same steps, in the same order and with the same config, as ``vcp run daily`` (daily.py),
called in-process through ``vcp_scanner.cli.main``: adjusted prices and features once, then
for every session universe, RS rank, Trend Template, VCP and scores, then forward labels and
Market Health (last, so it can read every session's Trend Template results). VCP only: the
other strategies are not part of the 2012-2021 tests. Only ``data/history_2011.duckdb`` is
written.

Weekly by default: the last session of each ISO week, the cadence of the 2022-2026 research
database that every earlier finding came from. A VCP scan reads the breakout events and statuses
of earlier scans, so daily and weekly scans give different results; never mix the two in one
database.

Resumable: a session that already has a completed score scan is skipped. Progress, the time of
each session and any failed step go to ``data/logs/history_backfill.log``.

    .venv/bin/python research/history/backfill_scans.py [FIRST] [LAST] [--steps once,sessions,after]
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from vcp_scanner.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[2]
DB = str(ROOT / "data" / "research" / "database_research.duckdb")  # history_2011 retired 2026-11-10
CFG = str(ROOT / "config")
LOG = ROOT / "data" / "logs" / "history_backfill.log"
# --db / --log override these (the research copy of the master, 2026-11-10)


def log(text: str) -> None:
    line = f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} {text}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def query(sql: str, params: list[object] | None = None) -> list[tuple]:
    con = duckdb.connect(DB, read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


def run(name: str, argv: list[str]) -> int:
    try:
        code = cli_main(argv)
    except Exception as exc:  # noqa: BLE001 - one bad session must not stop a 9-year backfill
        log(f"FAILED {name}: {type(exc).__name__}: {exc}")
        return 1
    if code:
        log(f"FAILED {name} (exit {code})")
    return code or 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("first", nargs="?", default="2012-07-02")
    ap.add_argument("last", nargs="?", default="2021-06-30")
    ap.add_argument("--steps", default="once,sessions,after")
    ap.add_argument("--cadence", choices=["weekly", "daily"], default="weekly")
    ap.add_argument("--db", default=None, help="database to scan (default: the history database)")
    ap.add_argument("--log", default=None, help="progress log (default: history_backfill.log)")
    a = ap.parse_args()
    global DB, LOG
    if a.db:
        DB = str(Path(a.db).resolve())
    if a.log:
        LOG = Path(a.log).resolve()
    steps = set(a.steps.split(","))
    first, last = date.fromisoformat(a.first), date.fromisoformat(a.last)

    if "once" in steps:
        log("adjusted prices")
        run("adjusted prices", ["ingest", "adjusted-prices", "--db", DB])
        log("features")
        run("features", ["compute", "features", "--db", DB])

    if "sessions" in steps:
        sessions = [r[0] for r in query(
            "SELECT trade_date FROM bhavcopy_files WHERE status = 'OK'"
            " AND trade_date BETWEEN ? AND ? ORDER BY 1", [first, last])]  # fmt: skip
        done = {r[0] for r in query(
            "SELECT as_of_date FROM scan_runs WHERE scan_type = 'SCORE' AND status = 'COMPLETED'"
            " AND as_of_date BETWEEN ? AND ?", [first, last])}  # fmt: skip
        if a.cadence == "weekly":
            # The last session of each ISO week, as in the 2022-2026 research database
            # (248 weekly dates: Friday, or the week's last session when Friday is a holiday).
            week_end: dict[tuple[int, int], date] = {}
            for d in sessions:
                week_end[d.isocalendar()[:2]] = d
            sessions = sorted(week_end.values())
        todo = [d for d in sessions if d not in done]
        log(
            f"{a.cadence} sessions {first}..{last}: {len(sessions)}, already scanned {len(done)},"
            f" to do {len(todo)}"
        )
        started = time.monotonic()
        for k, d in enumerate(todo, 1):
            t = time.monotonic()
            iso = d.isoformat()
            for name, argv in (
                ("universe", ["ingest", "universe", "--as-of", iso, "--db", DB,
                              "--config-dir", CFG]),
                ("RS", ["compute", "rs", "--as-of", iso, "--db", DB, "--config-dir", CFG]),
                ("Trend Template", ["compute", "trend-template", "--as-of", iso, "--db", DB,
                                    "--config-dir", CFG]),
                ("VCP", ["compute", "vcp", "--as-of", iso, "--db", DB, "--config-dir", CFG]),
                ("scores", ["compute", "scores", "--as-of", iso, "--db", DB, "--config-dir", CFG]),
            ):  # fmt: skip
                run(f"{name} {iso}", argv)
            took = time.monotonic() - t
            rate = (time.monotonic() - started) / k
            log(f"{iso} done in {took:.1f}s ({k}/{len(todo)}; about "
                f"{rate * (len(todo) - k) / 3600:.1f} h left)")  # fmt: skip

    if "after" in steps:
        log("forward labels")
        run("forward labels", ["compute", "labels", "--db", DB, "--config-dir", CFG])
        log("market health")
        run("market health", ["health", "build", "--db", DB, "--config-dir", CFG])
    log("finished")


if __name__ == "__main__":
    main()
