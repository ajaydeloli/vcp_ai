"""``vcp shareholding``: quarterly shareholding pattern from NSE filings (F8, display only)."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from vcp_scanner.data.providers._time import IST


def add_shareholding_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "shareholding", help="Shareholding pattern (NSE filings, display only)"
    )
    sub = p.add_subparsers(dest="shareholding_command")
    for name, text in (
        ("backfill", "List, download and parse every filing since history_from (resumable)"),
        ("update", "List, download and parse the filings of the last days (daily step)"),
    ):
        b = sub.add_parser(name, help=text)
        b.add_argument("--db", default="data/vcp_scanner.duckdb")
        b.add_argument("--config-dir", default="config")
        b.add_argument("--days", type=int, default=None, help="update: list this many days back")
        b.add_argument("--limit", type=int, default=None, help="first N stocks only (a trial)")
        b.add_argument(
            "--resume",
            action="store_true",
            help="backfill: the saved target list of the last backfill (no listing)",
        )
    st = sub.add_parser("status", help="What is stored (read-only)")
    st.add_argument("--db", default="data/vcp_scanner.duckdb")
    v = sub.add_parser("show", help="One stock's shareholding by quarter (read-only)")
    v.add_argument("symbol")
    v.add_argument("--quarters", type=int, default=8)
    v.add_argument("--db", default="data/vcp_scanner.duckdb")


def _say(message: str) -> None:
    print(f"[{datetime.now(IST):%H:%M:%S}] {message}", flush=True)


def _err(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def run_shareholding(args: argparse.Namespace) -> int:
    cmd = getattr(args, "shareholding_command", None)
    if cmd in ("backfill", "update"):
        return _run_bulk(args, cmd)
    if cmd == "status":
        return _run_status(args)
    if cmd == "show":
        return _run_show(args)
    print("usage: vcp shareholding {backfill,update,status,show} ...", file=sys.stderr)
    return 2


def _connect(db: Path, *, read_only: bool):  # type: ignore[no-untyped-def]
    import duckdb

    if read_only:
        return duckdb.connect(str(db), read_only=True)
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore

    store = DuckDBStore(db)
    store.migrate()
    return store.conn


def _run_bulk(args: argparse.Namespace, command: str) -> int:
    import duckdb

    from vcp_scanner.data.providers.nse_shareholding import NseShareholdingProvider
    from vcp_scanner.domain.errors import ConfigError
    from vcp_scanner.shareholding import pipeline as pl
    from vcp_scanner.shareholding.config import load_shareholding_config

    try:
        cfg = load_shareholding_config(args.config_dir)
    except ConfigError as exc:
        _err(str(exc))
        return 1
    db = Path(args.db).resolve()
    root = db.parent / "raw" / "shareholding"
    today = datetime.now(IST).date()

    # 1. What to fetch (database read-only, briefly).
    try:
        conn = _connect(db, read_only=True)
        tables = {
            r[0]
            for r in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()
        }
        ids = pl.scope(conn, cfg.scope_from)
        if "shareholding_filings" in tables:
            done, have = pl.stored_ok(conn), pl.stocks_with_filings(conn)
            through = pl.complete_through(conn)
        else:
            done, have, through = set(), set(), None
        conn.close()
    except duckdb.Error as exc:
        _err(f"Database not readable now ({exc}). Is another job running? Try again later.")
        return 1
    if args.limit:
        ids = dict(sorted(ids.items())[: args.limit])
    if command == "backfill":
        start: date | None = cfg.history_from
        new_symbols: list[str] = []
    else:
        start = pl.update_start(today, through, args.days or cfg.update_days, cfg.history_from)
        if args.days:
            start = max(cfg.history_from, today - timedelta(days=args.days))
        new_symbols = sorted(s for s, iid in ids.items() if iid not in have)[: cfg.new_stock_limit]
        if not have:
            new_symbols = []  # nothing stored yet: that is the backfill's job, not an update's
    extra = f"; whole history of {len(new_symbols)} new stocks" if new_symbols else ""
    _say(
        f"{command}: {len(ids)} stocks, broadcast {start}..{today},"
        f" as on >= {cfg.history_from}{extra}"
    )

    # 2. List and download (network and raw cache only).
    provider = NseShareholdingProvider(
        site_interval_seconds=cfg.site_interval_seconds,
        archive_interval_seconds=cfg.archive_interval_seconds,
    )
    saved = root / "backfill_targets.json"
    if command == "backfill" and args.resume:
        if not saved.is_file():
            _err("No saved backfill target list; run 'vcp shareholding backfill' without --resume.")
            return 1
        listing = pl.Listing()
        targets = [t for t in pl.load_targets(saved) if t[0].filing_id not in done]
        _say(f"resuming the saved target list: {len(targets)} filings not stored yet")
    else:
        assert start is not None
        progress = _say if command == "backfill" else (lambda _m: None)
        listing = pl.list_filings(provider, start, today, new_symbols, progress=progress)
        targets = pl.select(listing.refs, ids, cfg.history_from, done)
        if command == "backfill":
            pl.save_targets(saved, targets)
        _say(f"listed {len(listing.refs)} filings, {len(targets)} of our stocks not stored yet")
    downloads = pl.download(provider, targets, root, progress=_say)
    failed = sum(1 for d in downloads if d.error)
    _say(f"downloads done: {len(downloads) - failed} in cache, {failed} failed")

    # 3. Record and parse (database read-write, briefly).
    try:
        conn = _connect(db, read_only=False)
    except duckdb.Error as exc:
        _err(f"Database busy ({exc}). The downloads are kept; run the command again later.")
        return 1
    rec = pl.record(conn, downloads)
    pl.record_run(conn, command, start, listing, rec)
    conn.close()
    total = rec.parsed + rec.parse_errors
    rate = rec.parse_errors / total if total else 0.0
    _say(
        f"recorded {rec.new} filings: parsed {rec.parsed}, parse errors {rec.parse_errors}"
        f" ({rate:.1%}), fetch errors {rec.fetch_errors}, differing from NSE's listing"
        f" {rec.listing_mismatches}; listed completely through {listing.complete_through}"
    )
    for message in (listing.errors + rec.messages)[:20]:
        print(f"  WARNING {message}")
    return 0


def _run_status(args: argparse.Namespace) -> int:
    import duckdb

    try:
        conn = _connect(Path(args.db), read_only=True)
    except duckdb.Error as exc:
        _err(str(exc))
        return 1
    try:
        rows = conn.execute(
            "SELECT status, count(*), count(DISTINCT instrument_id), min(as_on), max(as_on)"
            " FROM shareholding_filings GROUP BY status ORDER BY status"
        ).fetchall()
        run = conn.execute(
            "SELECT run_at, command, complete_through, filings_new, parse_errors, fetch_errors"
            " FROM shareholding_fetch_runs ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        layouts = conn.execute(
            "SELECT layout, count(*) FROM shareholding_snapshots GROUP BY 1 ORDER BY 1"
        ).fetchall()
    except duckdb.Error:
        print("No shareholding data yet: run 'vcp shareholding backfill'.")
        return 0
    finally:
        conn.close()
    if not rows:
        print("No shareholding data yet: run 'vcp shareholding backfill'.")
        return 0
    print("Filings by status:")
    for status, n, stocks, first, last in rows:
        print(f"  {status:12s} {n:7,d} filings, {stocks:5,d} stocks, as on {first} .. {last}")
    print("Snapshots by layout: " + ", ".join(f"{k} {n:,d}" for k, n in layouts))
    if run:
        print(f"Last run: {run[1]} at {run[0]:%Y-%m-%d %H:%M}, listed completely through"
              f" {run[2]}, {run[3]} new, {run[4]} parse errors, {run[5]} fetch errors")  # fmt: skip
    return 0


def _fmt(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "  —  "
    return f"{v:+6.2f}" if signed else f"{v:6.2f}"


def _run_show(args: argparse.Namespace) -> int:
    import duckdb

    try:
        conn = _connect(Path(args.db), read_only=True)
        rows = conn.execute(
            """
            SELECT s.as_on, s.promoter_pct, s.promoter_pledged_pct, s.fii_pct, s.dii_pct, s.mf_pct,
                   s.non_institutions_pct, s.shareholders, s.revision_number, s.available_at
            FROM shareholding_snapshots s JOIN instruments i USING (instrument_id)
            WHERE upper(i.symbol) = upper(?) AND s.is_quarter_end
            QUALIFY row_number() OVER (PARTITION BY s.as_on ORDER BY s.available_at DESC) = 1
            ORDER BY s.as_on DESC LIMIT ?
            """,
            [args.symbol, args.quarters],
        ).fetchall()
        conn.close()
    except duckdb.Error as exc:
        _err(str(exc))
        return 1
    if not rows:
        print(f"No shareholding stored for {args.symbol}.")
        return 0
    print(f"{args.symbol.upper()}: % of shares (pledge: % of promoter shares), latest revision")
    print("  quarter     promoter  pledged      FII      DII       MF   public* holders")
    for d, pr, pl_, fii, dii, mf, ni, holders, rev, _at in rows:
        print(f"  {d}  {_fmt(pr)}   {_fmt(pl_)}  {_fmt(fii)}  {_fmt(dii)}  {_fmt(mf)}  {_fmt(ni)}"
              f"  {holders or 0:>9,d}" + (f"  (revision {rev})" if rev else ""))  # fmt: skip
    print("  * public = non-institutional holders (retail, HNIs, bodies corporate, NRIs)")
    return 0
