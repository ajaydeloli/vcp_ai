"""Compare the stored results of two databases, row by row (STRATEGY_SPECIFICATION 11.3).

Usage::

    python scripts/compare_results.py OLD.duckdb NEW.duckdb [--backtests-after ISO_TIME]

Used for the Multi-Strategy step-2 exit test: the old code rebuilds some dates on one copy of
a database, the new code on another, and every VCP row, score, label and scan-run hash must be
identical. Floats are compared exactly (``EXCEPT ALL``: a multiset difference, NULLs equal).
Timestamps, run ids, code commits and the new ``strategy_id`` column are ignored; the
``strategy_id`` of the new database is checked separately to be ``vcp`` everywhere.

With ``--backtests-after``, the backtest runs started after that time are paired in start order
and their ``metrics_json`` and event lists compared.

Exit code 0 when everything is identical, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

# table -> columns to ignore
TABLES: dict[str, set[str]] = {
    "vcp_patterns": {"created_at"},
    "vcp_contractions": set(),
    "vcp_pivots": set(),
    "vcp_status_history": set(),
    "vcp_breakout_events": {"created_at"},
    "vcp_scan_run_results": {"scan_run_id"},
    "setup_scores": {"created_at", "strategy_id"},
    "score_components": {"strategy_id"},
    "forward_labels": {"computed_at", "strategy_id"},
    "trend_template_results": set(),
}
SCAN_RUN_COLUMNS = (
    "scan_type", "as_of_date", "scan_id", "data_snapshot_id", "universe_snapshot_id",
    "scan_config_hash", "section_hashes", "survivorship_status", "counts", "results_hash",
    "status",
)  # fmt: skip
STRATEGY_TABLES = ("setup_scores", "score_components", "forward_labels", "backtest_runs")


def _cols(c: duckdb.DuckDBPyConnection, db: str, table: str) -> list[str]:
    return [r[0] for r in c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_catalog = ?"
        " AND table_schema = 'main' AND table_name = ? ORDER BY ordinal_position", [db, table],
    ).fetchall()]  # fmt: skip


def _diff(c: duckdb.DuckDBPyConnection, table: str, cols: list[str]) -> tuple[int, int, int, int]:
    sel = ", ".join(cols)
    n_old = c.execute(f"SELECT count(*) FROM old.{table}").fetchone()[0]  # type: ignore[index]
    n_new = c.execute(f"SELECT count(*) FROM new.{table}").fetchone()[0]  # type: ignore[index]
    only_old = c.execute(
        f"SELECT count(*) FROM (SELECT {sel} FROM old.{table} EXCEPT ALL"
        f" SELECT {sel} FROM new.{table})"
    ).fetchone()[0]  # type: ignore[index]
    only_new = c.execute(
        f"SELECT count(*) FROM (SELECT {sel} FROM new.{table} EXCEPT ALL"
        f" SELECT {sel} FROM old.{table})"
    ).fetchone()[0]  # type: ignore[index]
    return n_old, n_new, only_old, only_new


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--backtests-after", default=None)
    args = ap.parse_args(argv)
    c = duckdb.connect()
    c.execute(f"ATTACH '{args.old}' AS old (READ_ONLY)")
    c.execute(f"ATTACH '{args.new}' AS new (READ_ONLY)")
    ok = True
    for table, ignore in TABLES.items():
        old_cols = _cols(c, "old", table)
        new_cols = set(_cols(c, "new", table))
        cols = [x for x in old_cols if x in new_cols and x not in ignore]
        missing = [x for x in old_cols if x not in new_cols]
        n_old, n_new, a, b = _diff(c, table, cols)
        same = a == 0 and b == 0 and not missing
        ok &= same
        print(f"{table:24} old {n_old:>9,} new {n_new:>9,} only-old {a:>6} only-new {b:>6} "
              f"{'IDENTICAL' if same else 'DIFFERENT'}"
              + (f" (missing columns: {missing})" if missing else ""))  # fmt: skip
    sel = ", ".join(SCAN_RUN_COLUMNS)
    a = c.execute(f"SELECT count(*) FROM (SELECT {sel} FROM old.scan_runs EXCEPT ALL"
                  f" SELECT {sel} FROM new.scan_runs)").fetchone()[0]  # type: ignore[index]  # fmt: skip
    b = c.execute(f"SELECT count(*) FROM (SELECT {sel} FROM new.scan_runs EXCEPT ALL"
                  f" SELECT {sel} FROM old.scan_runs)").fetchone()[0]  # type: ignore[index]  # fmt: skip
    ok &= a == 0 and b == 0
    print(f"{'scan_runs (hashes)':24} only-old {a} only-new {b} "
          f"{'IDENTICAL' if a == b == 0 else 'DIFFERENT'}")  # fmt: skip
    for table in STRATEGY_TABLES:
        if "strategy_id" not in _cols(c, "new", table):
            print(f"{table:24} new database has no strategy_id column")
            ok = False
            continue
        other = c.execute(f"SELECT count(*) FROM new.{table} WHERE strategy_id IS DISTINCT FROM"
                          " 'vcp'").fetchone()[0]  # type: ignore[index]  # fmt: skip
        ok &= other == 0
        print(f"{table + '.strategy_id':24} rows not 'vcp': {other}")
    if args.backtests_after:
        ok &= _backtests(c, args.backtests_after)
    print("RESULT: " + ("IDENTICAL" if ok else "DIFFERENCES FOUND"))
    return 0 if ok else 1


def _backtests(c: duckdb.DuckDBPyConnection, after: str) -> bool:
    runs = {}
    for db in ("old", "new"):
        runs[db] = c.execute(
            f"SELECT backtest_id, period_name, metrics_json FROM {db}.backtest_runs"
            " WHERE started_at > CAST(? AS TIMESTAMPTZ) ORDER BY started_at", [after],
        ).fetchall()  # fmt: skip
    if len(runs["old"]) != len(runs["new"]) or not runs["old"]:
        print(f"backtests: {len(runs['old'])} old runs vs {len(runs['new'])} new runs")
        return False
    ok = True
    for (bo, po, mo), (bn, pn, mn) in zip(runs["old"], runs["new"], strict=True):
        ev = "instrument_id, event_date, event_type, price, quantity, signal_id, metadata_json"
        diff = c.execute(
            f"SELECT count(*) FROM ((SELECT {ev} FROM old.backtest_events WHERE backtest_id = ?"
            f" EXCEPT ALL SELECT {ev} FROM new.backtest_events WHERE backtest_id = ?) UNION ALL"
            f" (SELECT {ev} FROM new.backtest_events WHERE backtest_id = ? EXCEPT ALL"
            f" SELECT {ev} FROM old.backtest_events WHERE backtest_id = ?))", [bo, bn, bn, bo],
        ).fetchone()[0]  # type: ignore[index]  # fmt: skip
        n = c.execute("SELECT count(*) FROM old.backtest_events WHERE backtest_id = ?",
                      [bo]).fetchone()[0]  # type: ignore[index]  # fmt: skip
        same = po == pn and mo == mn and diff == 0
        ok &= same
        print(f"backtest {po}: {bo} vs {bn}: metrics {'equal' if mo == mn else 'DIFFER'}, "
              f"{n} events, {diff} differ -> {'IDENTICAL' if same else 'DIFFERENT'}")  # fmt: skip
    return ok


if __name__ == "__main__":
    sys.exit(main())
