"""``vcp fundamentals fetch``: store NSE result filings (FUNDAMENTALS_SPECIFICATION F1).

Writes only the raw cache (``data/raw/fundamentals/``) and the ``fundamental_filings`` manifest.
Nothing the scan, score, backtest or paper ledger reads is touched.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from vcp_scanner.cli_pipeline import _err

IST = ZoneInfo("Asia/Kolkata")


def add_fundamentals_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("fundamentals", help="Fundamental data (filings from NSE)")
    sub = p.add_subparsers(dest="fundamentals_command")
    s = sub.add_parser("fetch", help="Download result filings not stored yet")
    s.add_argument("--symbol", default=None, help="One NSE symbol (default: all universe stocks)")
    s.add_argument("--from", dest="start", default=None, metavar="YYYY-MM-DD")
    s.add_argument("--to", dest="end", default=None, metavar="YYYY-MM-DD")
    s.add_argument("--db", default="data/vcp_scanner.duckdb")
    s.add_argument("--interval", type=float, default=1.0, help="Seconds between requests")
    q = sub.add_parser("parse", help="Turn stored filings into quarterly values")
    q.add_argument("--db", default="data/vcp_scanner.duckdb")
    v = sub.add_parser("show", help="Metrics of one stock as of a date (read-only)")
    v.add_argument("symbol")
    v.add_argument("--date", default=None, metavar="YYYY-MM-DD", help="Default: today")
    v.add_argument("--db", default="data/vcp_scanner.duckdb")
    v.add_argument("--config-dir", default="config")


def run_fundamentals(args: argparse.Namespace) -> int:
    from vcp_scanner.data.providers.nse_filings import NseFilingProvider
    from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
        DuckDBFundamentalRepository,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.fundamentals.fetch import fetch_filings

    command = getattr(args, "fundamentals_command", None)
    if command == "parse":
        return _run_parse(args)
    if command == "show":
        return _run_show(args)
    if command != "fetch":
        _err("Usage: vcp fundamentals {fetch,parse,show}")
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


def _run_parse(args: argparse.Namespace) -> int:
    from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
        DuckDBFundamentalRepository,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.fundamentals.snapshots import parse_filings

    db = Path(args.db).resolve()
    store = DuckDBStore(db)
    store.migrate()
    report = parse_filings(DuckDBFundamentalRepository(store), db.parent / "raw" / "fundamentals")
    print(
        f"Snapshots created {report.parsed}, estimated {report.estimated}, "
        f"invalid {report.invalid}, parse errors {report.errors}"
    )
    for message in report.messages[:20]:
        print(f"  {message}")
    return 0


def _fmt(value: float | None, unit: str = "") -> str:
    return "N/A" if value is None else f"{value:,.2f}{unit}"


def _run_show(args: argparse.Namespace) -> int:
    import duckdb

    from vcp_scanner.config.loader import load_scanner_config
    from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
        DuckDBFundamentalRepository,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.fundamentals.metrics import SCORE_METRICS, compute_view

    db = Path(args.db).resolve()
    if not db.exists():
        _err(f"No database at {db}")
        return 1
    scoring = load_scanner_config(args.config_dir).strategy.scoring
    min_availability = scoring.fundamentals_min_availability
    as_of = date.fromisoformat(args.date) if args.date else datetime.now(IST).date()
    try:
        store = DuckDBStore.__new__(DuckDBStore)
        store.conn = duckdb.connect(str(db), read_only=True)
        row = store.conn.execute(
            "SELECT instrument_id FROM instruments WHERE symbol = ? ORDER BY is_active DESC",
            [args.symbol],
        ).fetchone()
        if row is None:
            _err(f"Unknown symbol: {args.symbol}")
            return 1
        repo = DuckDBFundamentalRepository(store)
        view = compute_view(
            repo.snapshots_for(row[0]), as_of, repo.share_actions(row[0]),
            min_availability=min_availability,
        )  # fmt: skip
    except duckdb.CatalogException:
        _err("No fundamentals tables yet: run 'vcp fundamentals fetch' and 'parse' first")
        return 1
    print(f"{args.symbol} as of {as_of} (close)")
    if view.period_end is None:
        print("  No results filed yet (N/A)")
        return 0
    flags = [f for f, on in (("STALE", view.stale), ("ESTIMATED", view.estimated),
                             ("RESTATED", view.restated)) if on]  # fmt: skip
    filed = (
        view.available_at.astimezone(IST).strftime("%Y-%m-%d %H:%M") if view.available_at else ""
    )
    basis = view.basis.lower() if view.basis else ""
    print(f"  Latest quarter {view.period_end} {basis}, filed {filed} IST  {' '.join(flags)}")
    print(
        f"  Revenue {_fmt(view.revenue and view.revenue / 1e7)} crore, EPS {_fmt(view.eps)},"
        f" TTM EPS {_fmt(view.ttm_eps)}, operating margin {_fmt(view.operating_margin, ' %')}"
    )
    for name in SCORE_METRICS:
        m = view.metrics[name]
        unit = "" if name == "debt_to_equity" else (" pp" if name in (
            "eps_acceleration", "margin_expansion") else " %")  # fmt: skip
        print(f"  {name:<17} {_fmt(m.value, unit):>14}  {m.status}")
    gate = "pass" if view.hard_gate_pass else f"fail ({view.gate_reason})"
    print(
        f"  Availability {view.availability_score:.0%}, quarters {view.quarters_available},"
        f" hard gate {gate} (shown only, not applied)"
    )
    return 0
