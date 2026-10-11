"""``vcp indices update``: NSE's daily index closes (Market Overview, FRONTEND_SPECIFICATION
§67.28). Fetches every trading day (a day with a stored bhavcopy) since ``--from`` that has not
been asked for yet, one request a second from the archive host. Display only."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

DEFAULT_DAYS_BACK = 730  # two years: a 250-session chart with room for its 200-day average


def add_indices_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("indices", help="NSE index closes (NIFTY 50, 500, sectors)")
    sub = p.add_subparsers(dest="indices_command")
    u = sub.add_parser("update", help="Fetch the index closes of trading days not stored yet")
    u.add_argument("--db", default="data/vcp_scanner.duckdb")
    u.add_argument("--from", dest="start", default=None, metavar="YYYY-MM-DD",
                   help=f"First day (default: {DEFAULT_DAYS_BACK} days ago)")  # fmt: skip
    u.add_argument("--interval", type=float, default=1.0, help="Seconds between requests")


def _say(text: str) -> None:
    print(text, flush=True)


def update_indices(db: Path, start: date, interval: float = 1.0, provider: object = None) -> int:
    """Fetch and store; returns the number of days stored. A failed request is a warning and
    the day is tried again on the next run."""
    from vcp_scanner.data.providers.nse_index_closes import (
        NseIndexCloseProvider,
        parse_index_closes,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.domain.errors import ProviderError

    store = DuckDBStore(db)
    store.migrate()
    c = store.conn
    todo = [
        r[0]
        for r in c.execute(
            "SELECT trade_date FROM bhavcopy_files WHERE status = 'OK' AND trade_date >= ?"
            " AND trade_date NOT IN (SELECT trade_date FROM index_files) ORDER BY 1",
            [start],
        ).fetchall()
    ]
    src = provider or NseIndexCloseProvider(db.parent / "raw" / "indices", min_interval_s=interval)
    stored = failed = 0
    for k, day in enumerate(todo, 1):
        try:
            got = src.fetch_day(day)  # type: ignore[attr-defined]
            rows = parse_index_closes(got.payload, day) if got.status == "OK" else []
        except ProviderError as exc:
            failed += 1
            _say(f"WARNING: {exc}")
            if failed >= 5:
                _say("Stopping after 5 failures; the rest is fetched on the next run.")
                break
            continue
        c.execute("BEGIN")
        try:
            c.execute("DELETE FROM index_daily WHERE trade_date = ?", [day])
            if rows:  # a day without a file records only its status
                c.executemany(
                    "INSERT INTO index_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(r.index_name, r.trade_date, r.open, r.high, r.low, r.close, r.volume,
                      r.turnover_cr, r.pe, r.pb, r.div_yield) for r in rows],
                )  # fmt: skip
            c.execute(
                "INSERT OR REPLACE INTO index_files VALUES (?, ?, ?, ?, ?)",
                [day, got.status, got.sha256, len(rows), datetime.now(UTC)],
            )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        stored += 1
        if k % 50 == 0:
            _say(f"{k}/{len(todo)} days")
    _say(f"Index closes: {stored} days stored, {failed} failed, {len(todo) - stored} left")
    store.conn.close()
    return stored


def run_indices(args: argparse.Namespace) -> int:
    if getattr(args, "indices_command", None) != "update":
        print("Usage: vcp indices update [--from YYYY-MM-DD]", file=sys.stderr)
        return 2
    start = (
        date.fromisoformat(args.start)
        if args.start
        else datetime.now(UTC).date() - timedelta(days=DEFAULT_DAYS_BACK)
    )
    update_indices(Path(args.db).resolve(), start, args.interval)
    return 0
