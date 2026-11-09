"""Step 3 checks of the merged master (owner request 2026-11-09), on the build copy only.

Compares the master (data/master_build/vcp_master.duckdb) with the pre-merge backup of live:

  prices    adjusted closes from 2021 on, stock by stock (they should not move: an action
            before 2021 does not scale a later bar)
  quality   open data-quality events dated 2021 on (added or removed by the merge)
  health    Market Health rows from 2021 on (the build only adds missing sessions)
  rescan    the live steps of the last sessions re-run on the master (universe, RS, Trend
            Template, VCP, scores, each paper strategy), result hashes vs the stored live scans
  paper     `vcp paper update` on the master: nothing new and no divergence for any ledger

    .venv/bin/python research/master/check_master.py [--sessions 3] [--skip rescan,paper]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

from vcp_scanner.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "data" / "master_build" / "vcp_master.duckdb"
BACKUP = ROOT / "data" / "backups_manual" / "vcp_scanner_premerge_20261109.duckdb"
CFG = str(ROOT / "config")


def connect() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(str(MASTER), read_only=True)
    c.execute(f"ATTACH '{BACKUP}' AS L (READ_ONLY)")  # the live backup compared against
    return c


def check_prices(c: duckdb.DuckDBPyConnection) -> None:
    r = c.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE abs(m.close_adj / l.close_adj - 1) > 1e-9),
               count(DISTINCT m.instrument_id)
                   FILTER (WHERE abs(m.close_adj / l.close_adj - 1) > 1e-9)
        FROM daily_prices_adjusted_current m
        JOIN L.daily_prices_adjusted_current l USING (instrument_id, trade_date)
        WHERE m.trade_date >= '2021-01-01'""").fetchone()  # fmt: skip
    missing = c.execute("""
        SELECT count(*) FROM L.daily_prices_adjusted_current l
        WHERE NOT EXISTS (SELECT 1 FROM daily_prices_adjusted_current m
                          WHERE m.instrument_id = l.instrument_id
                            AND m.trade_date = l.trade_date)""").fetchone()[0]  # fmt: skip
    print(f"prices: {r[0]} adjusted bars from 2021 in both; {r[1]} differ ({r[2]} stocks); "
          f"{missing} live bars missing in the master")  # fmt: skip
    for row in c.execute("""
        SELECT m.instrument_id, min(m.trade_date), max(m.trade_date), count(*),
               round(min(m.close_adj / l.close_adj), 4), round(max(m.close_adj / l.close_adj), 4)
        FROM daily_prices_adjusted_current m
        JOIN L.daily_prices_adjusted_current l USING (instrument_id, trade_date)
        WHERE m.trade_date >= '2021-01-01' AND abs(m.close_adj / l.close_adj - 1) > 1e-9
        GROUP BY 1 ORDER BY 4 DESC LIMIT 25""").fetchall():  # fmt: skip
        print("   ", row)


def check_quality(c: duckdb.DuckDBPyConnection) -> None:
    q = """SELECT instrument_id, trade_date, event_type, blocks_signal FROM {}data_quality_events
           WHERE status = 'OPEN' AND trade_date >= '2021-01-01'"""
    added = c.execute(f"{q.format('')} EXCEPT {q.format('L.')}").fetchall()
    removed = c.execute(f"{q.format('L.')} EXCEPT {q.format('')}").fetchall()
    total = len(c.execute(q.format("")).fetchall())
    print(f"quality: {total} open events dated 2021 on in the master; {len(added)} new, "
          f"{len(removed)} gone vs live")  # fmt: skip
    for tag, rows in (("new", added), ("gone", removed)):
        for row in sorted(rows)[:15]:
            print(f"    {tag:4}", row)
    pre = c.execute("""SELECT count(*), count(*) FILTER (WHERE blocks_signal)
                       FROM data_quality_events WHERE status = 'OPEN'
                       AND trade_date < '2021-01-01'""").fetchone()  # fmt: skip
    print(f"quality: {pre[0]} open events before 2021 ({pre[1]} block signals)")


def check_health(c: duckdb.DuckDBPyConnection) -> None:
    r = c.execute("""
        SELECT count(*), count(*) FILTER (WHERE m.raw_score IS DISTINCT FROM l.raw_score
                                          OR m.label IS DISTINCT FROM l.label)
        FROM market_health_daily m JOIN L.market_health_daily l USING (trade_date)""").fetchone()
    n = c.execute("SELECT count(*), min(trade_date), max(trade_date) FROM market_health_daily")
    print(f"health: {r[0]} live days compared, {r[1]} changed; master holds {n.fetchone()}")


def rescan(sessions: int) -> None:
    from vcp_scanner.daily import paper_strategies

    c = connect()
    days = [r[0] for r in c.execute(
        "SELECT DISTINCT as_of_date FROM L.scan_runs WHERE scan_type = 'SCORE' AND status = "
        "'COMPLETED' ORDER BY 1 DESC LIMIT ?", [sessions]).fetchall()][::-1]  # fmt: skip
    c.close()
    strategies = paper_strategies(CFG)
    db = str(MASTER)
    t0 = time.monotonic()
    for d in days:
        iso = d.isoformat()
        t = time.monotonic()
        steps = [["ingest", "universe", "--as-of", iso], ["compute", "rs", "--as-of", iso],
                 ["compute", "trend-template", "--as-of", iso], ["compute", "vcp", "--as-of", iso],
                 ["compute", "scores", "--as-of", iso]]  # fmt: skip
        for sid in strategies:
            steps += [["compute", "setups", "--strategy", sid, "--as-of", iso],
                      ["compute", "scores", "--strategy", sid, "--as-of", iso]]  # fmt: skip
        for argv in steps:
            code = cli_main([*argv, "--db", db, "--config-dir", CFG])
            if code:
                print(f"rescan: {' '.join(argv)} exit {code}")
        print(f"rescan: {iso} in {time.monotonic() - t:.0f}s")
    print(f"rescan: {len(days)} sessions in {time.monotonic() - t0:.0f}s")
    c = connect()
    rows = c.execute("""
        WITH m AS (SELECT as_of_date, scan_type, coalesce(strategy_id, 'vcp') sid, results_hash,
                          row_number() OVER (PARTITION BY as_of_date, scan_type,
                                             coalesce(strategy_id, 'vcp')
                                             ORDER BY completed_at DESC) rk
                   FROM scan_runs WHERE status = 'COMPLETED' AND as_of_date IN (SELECT unnest(?))),
             l AS (SELECT as_of_date, scan_type, coalesce(strategy_id, 'vcp') sid, results_hash,
                          row_number() OVER (PARTITION BY as_of_date, scan_type,
                                             coalesce(strategy_id, 'vcp')
                                             ORDER BY completed_at DESC) rk
                   FROM L.scan_runs WHERE status = 'COMPLETED' AND as_of_date IN (SELECT unnest(?)))
        SELECT m.as_of_date, m.scan_type, m.sid, l.results_hash = m.results_hash, l.results_hash,
               m.results_hash
        FROM m JOIN l USING (as_of_date, scan_type, sid) WHERE m.rk = 1 AND l.rk = 1
        ORDER BY 1, 2, 3""", [days, days]).fetchall()  # fmt: skip
    same = sum(1 for r in rows if r[3])
    print(f"rescan: {len(rows)} scans compared, {same} identical result hashes")
    for r in rows:
        if not r[3]:
            print("    DIFFERS", r)
    u = c.execute("""
        SELECT s.as_of_date,
               (SELECT count(*) FROM universe_memberships x JOIN universe_snapshots y
                USING (universe_snapshot_id) WHERE y.as_of_date = s.as_of_date AND x.eligible),
               (SELECT count(*) FROM L.universe_memberships x JOIN L.universe_snapshots y
                USING (universe_snapshot_id) WHERE y.as_of_date = s.as_of_date AND x.eligible)
        FROM (SELECT DISTINCT unnest(?) AS as_of_date) s ORDER BY 1""", [days]).fetchall()
    print("rescan: eligible universe master vs live:", u)


def paper() -> None:
    t = time.monotonic()
    code = cli_main(["paper", "update", "--db", str(MASTER), "--config-dir", CFG])
    print(f"paper: exit {code} in {time.monotonic() - t:.0f}s")


def main() -> None:
    global BACKUP
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--skip", default="")
    ap.add_argument("--backup", default=str(BACKUP), help="live backup to compare against")
    a = ap.parse_args()
    BACKUP = Path(a.backup)
    skip = set(a.skip.split(","))
    c = connect()
    for name, fn in (("prices", check_prices), ("quality", check_quality),
                     ("health", check_health)):  # fmt: skip
        if name not in skip:
            fn(c)
    c.close()
    if "rescan" not in skip:
        rescan(a.sessions)
    if "paper" not in skip:
        paper()


if __name__ == "__main__":
    main()
