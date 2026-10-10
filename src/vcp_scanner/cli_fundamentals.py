"""``vcp fundamentals fetch``: store NSE result filings (FUNDAMENTALS_SPECIFICATION F1).

Writes only the raw cache (``data/raw/fundamentals/``) and the ``fundamental_filings`` manifest.
Nothing the scan, score, backtest or paper ledger reads is touched.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from vcp_scanner.cli_pipeline import _err

if TYPE_CHECKING:
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

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
    for name, text in (
        ("backfill", "Fetch, parse and compute everything since history_from (resumable)"),
        ("update", "Fetch, parse and compute the filings of the last days (daily step)"),
    ):
        b = sub.add_parser(name, help=text)
        b.add_argument("--db", default="data/vcp_scanner.duckdb")
        b.add_argument("--config-dir", default="config")
        b.add_argument("--days", type=int, default=None, help="update: days back (config)")
        b.add_argument("--limit", type=int, default=None, help="backfill: first N stocks only")
    st = sub.add_parser("status", help="What is stored (read-only)")
    st.add_argument("--db", default="data/vcp_scanner.duckdb")
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
    if command in ("backfill", "update"):
        return _run_bulk(args, command)
    if command == "status":
        return _run_status(args)
    if command != "fetch":
        _err("Usage: vcp fundamentals {backfill,update,status,show,fetch,parse}")
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


def _say(message: str) -> None:
    print(f"[{datetime.now(IST):%H:%M:%S}] {message}", flush=True)


def _open_store(db: Path, *, read_only: bool) -> DuckDBStore:
    """A DuckDBStore on ``db``; read-only stores skip the migration."""
    import duckdb

    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

    if read_only:
        store = DuckDBStore.__new__(DuckDBStore)
        store.conn = duckdb.connect(str(db), read_only=True)
        return store
    store = DuckDBStore(db)
    store.migrate()
    return store


def _run_bulk(args: argparse.Namespace, command: str) -> int:
    """``backfill`` and ``update``: the network part runs with the database closed.

    ``update`` lists from a few days before the last date listed completely (so a week without
    a daily run skips nothing) and lists the whole history of stocks new to the scope (at most
    ``new_stock_limit`` per run; the rest follow on the next runs).
    """
    import duckdb

    from vcp_scanner.config.loader import load_scanner_config
    from vcp_scanner.data.providers.nse_filings import NseFilingProvider
    from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
        DuckDBFundamentalRepository,
    )
    from vcp_scanner.fundamentals.pipeline import (
        Listing,
        download_missing,
        list_histories,
        list_range,
        min_period_end,
        record_downloads,
        select_targets,
        update_start,
        update_views,
    )
    from vcp_scanner.fundamentals.snapshots import parse_filings

    config = load_scanner_config(args.config_dir)
    fcfg = config.data.fundamentals
    min_availability = config.strategy.scoring.fundamentals_min_availability
    db = Path(args.db).resolve()
    cache_root = db.parent / "raw" / "fundamentals"
    today = datetime.now(IST).date()
    oldest = min_period_end(fcfg.history_from)

    # 1. What to fetch (database read-only, briefly).
    try:
        store = _open_store(db, read_only=True)
        repo = DuckDBFundamentalRepository(store)
        ids = repo.universe_symbols(fcfg.history_from)
        through = repo.last_complete_through()
        have_history = repo.stocks_with_history()
        store.conn.close()
    except duckdb.Error as exc:
        _err(f"Database not readable now ({exc}). Is another job running? Try again later.")
        return 1
    if args.limit:
        ids = dict(sorted(ids.items())[: args.limit])
    if command == "backfill":
        start = oldest + timedelta(days=1)
        new_symbols: list[str] = []
    else:
        first = args.days or fcfg.update_days
        start = max(update_start(today, through, first), oldest + timedelta(days=1))
        if args.days:
            start = today - timedelta(days=args.days)
        new_symbols = sorted(s for s, iid in ids.items() if iid not in have_history)
        new_symbols = new_symbols[: fcfg.new_stock_limit]
    _say(
        f"{command}: {len(ids)} stocks, filings broadcast {start}..{today}, periods >= {oldest}"
        + (f"; whole history of {len(new_symbols)} new stocks" if new_symbols else "")
    )

    # 2. List and download (network and raw cache only).
    provider = NseFilingProvider(interval_seconds=fcfg.request_interval_seconds)
    listing = list_range(provider, start, today, progress=_say if command == "backfill" else _quiet)
    histories = list_histories(provider, new_symbols, progress=_say) if new_symbols else Listing()
    targets = select_targets(listing.refs + histories.refs, ids, oldest)
    _say(f"listed {len(listing.refs) + len(histories.refs)} filings, {len(targets)} for our stocks")
    downloads = download_missing(provider, targets, cache_root, progress=_say)
    failed = sum(1 for d in downloads if d.error)
    _say(f"downloads done: {len(downloads) - failed} in cache, {failed} failed")

    # 3. Record, parse, views (database read-write, a few minutes at most).
    try:
        store = _open_store(db, read_only=False)
    except duckdb.Error as exc:
        _err(f"Database busy ({exc}). The downloads are kept; run the command again later.")
        return 1
    repo = DuckDBFundamentalRepository(store)
    recorded, errors = record_downloads(repo, downloads)
    parsed = parse_filings(repo, cache_root)
    views = update_views(repo, staleness_days=fcfg.max_staleness_days,
                         min_availability=min_availability)  # fmt: skip
    if command == "backfill" and listing.complete_through == today:
        repo.mark_history(list(ids.values()))  # every stock's filings since then were listed
    repo.mark_history([ids[s] for s in histories.histories_listed])
    repo.record_fetch_run(command, start, listing.complete_through,
                          len(listing.incomplete) + len(histories.incomplete),
                          len(histories.histories_listed))  # fmt: skip
    store.conn.close()
    total = parsed.parsed + parsed.errors
    rate = parsed.errors / total if total else 0.0
    _say(f"recorded {recorded} new filings ({errors} fetch errors); snapshots {parsed.parsed}"
         f" (estimated {parsed.estimated}, invalid {parsed.invalid}), parse errors"
         f" {parsed.errors} ({rate:.1%}); views written {views};"
         f" listed completely through {listing.complete_through}")  # fmt: skip
    for message in (listing.incomplete + histories.incomplete + parsed.messages)[:20]:
        print(f"  WARNING {message}")
    return 0


def _quiet(_: str) -> None:
    return None


def _run_status(args: argparse.Namespace) -> int:
    import duckdb

    from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
        DuckDBFundamentalRepository,
    )

    db = Path(args.db).resolve()
    try:
        store = _open_store(db, read_only=True)
        counts = DuckDBFundamentalRepository(store).status_counts()
    except duckdb.CatalogException:
        print("No fundamentals stored yet (run 'vcp fundamentals backfill').")
        return 0
    except duckdb.Error as exc:
        _err(f"Database not readable now: {exc}")
        return 1
    filings = counts["filings"]
    assert isinstance(filings, dict)
    done = sum(filings.values())
    errors = filings.get("PARSE_ERROR", 0) + filings.get("FETCH_ERROR", 0)
    print(f"Filings {done}: " + ", ".join(f"{k} {v}" for k, v in sorted(filings.items())))
    print(f"  error rate {errors / done:.1%}" if done else "  none")
    print(f"Snapshots: {counts['snapshots']}; stocks with data {counts['stocks']};"
          f" stored views {counts['views']}")  # fmt: skip
    print(f"Newest filing broadcast {counts['last_broadcast']}; last fetch {counts['last_fetch']}")
    return 0
