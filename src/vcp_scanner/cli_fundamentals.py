"""``vcp fundamentals fetch``: store NSE result filings (FUNDAMENTALS_SPECIFICATION F1).

Writes only the raw cache (``data/raw/fundamentals/``) and the ``fundamental_filings`` manifest.
Nothing the scan, score, backtest or paper ledger reads is touched.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from vcp_scanner.cli_pipeline import _err


def add_fundamentals_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("fundamentals", help="Fundamental data (filings from NSE)")
    sub = p.add_subparsers(dest="fundamentals_command")
    s = sub.add_parser("fetch", help="Download result filings not stored yet")
    s.add_argument("--symbol", default=None, help="One NSE symbol (default: all universe stocks)")
    s.add_argument("--from", dest="start", default=None, metavar="YYYY-MM-DD")
    s.add_argument("--to", dest="end", default=None, metavar="YYYY-MM-DD")
    s.add_argument("--db", default="data/vcp_scanner.duckdb")
    s.add_argument("--interval", type=float, default=1.0, help="Seconds between requests")


def run_fundamentals(args: argparse.Namespace) -> int:
    from vcp_scanner.data.providers.nse_filings import NseFilingProvider
    from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
        DuckDBFundamentalRepository,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.fundamentals.fetch import fetch_filings

    if getattr(args, "fundamentals_command", None) != "fetch":
        _err("Usage: vcp fundamentals fetch [--symbol X] [--from D --to D]")
        return 1
    if bool(args.start) != bool(args.end):
        _err("--from and --to go together")
        return 1
    db = Path(args.db).resolve()
    store = DuckDBStore(db)
    store.migrate()
    query = "SELECT symbol, instrument_id FROM instruments WHERE is_active"
    params: list[str] = []
    if args.symbol:
        query += " AND symbol = ?"
        params.append(args.symbol)
    ids = dict(store.conn.execute(query, params).fetchall())
    if args.symbol and not ids:
        _err(f"Unknown symbol: {args.symbol}")
        return 1
    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None
    report = fetch_filings(
        NseFilingProvider(interval_seconds=args.interval),
        DuckDBFundamentalRepository(store),
        db.parent / "raw" / "fundamentals",
        ids,
        symbol=args.symbol,
        start=start,
        end=end,
    )
    print(
        f"Filings listed {report.listed}, fetched {report.fetched}, already stored "
        f"{report.already_stored}, errors {report.errors}, "
        f"other symbols {report.skipped_no_instrument}"
    )
    for message in report.messages:
        print(f"  {message}")
    return 0
