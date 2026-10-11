"""``vcp classify update``: sector of each stock from NSE (DATA_SPECIFICATION §48.1, S1)."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from vcp_scanner.cli_pipeline import _err

IST = ZoneInfo("Asia/Kolkata")


def add_classify_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("classify", help="Sector and industry of each stock (NSE)")
    sub = p.add_subparsers(dest="classify_command")
    u = sub.add_parser("update", help="Read NSE's index lists and record changes")
    u.add_argument("--db", default="data/vcp_scanner.duckdb")


def run_classify(args: argparse.Namespace) -> int:
    from vcp_scanner.data.providers.nse_index_lists import NseIndexListProvider
    from vcp_scanner.data.repositories.duckdb_classification_repository import (
        Classification,
        DuckDBClassificationRepository,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.domain.errors import ProviderError

    if getattr(args, "classify_command", None) != "update":
        _err("Usage: vcp classify update")
        return 1
    try:
        entries = NseIndexListProvider().entries()
    except ProviderError as exc:  # a warning, never a failed daily run
        print(f"WARNING: sector lists not read: {exc}")
        return 0
    store = DuckDBStore(Path(args.db).resolve())
    store.migrate()
    repo = DuckDBClassificationRepository(store)
    ids = repo.match_instruments([(e.symbol, e.isin) for e in entries])
    items = [Classification(instrument_id=ids[e.symbol], sector=e.sector)
             for e in entries if e.symbol in ids]  # fmt: skip
    report = repo.apply(items, datetime.now(IST).date())
    universe = [r[0] for r in store.conn.execute(
        "SELECT m.instrument_id FROM universe_memberships m WHERE m.exclusion_reason IS NULL"
        " AND m.universe_snapshot_id = (SELECT universe_snapshot_id FROM universe_snapshots"
        " ORDER BY as_of_date DESC LIMIT 1)"
    ).fetchall()]  # fmt: skip
    covered = repo.coverage(universe)
    print(f"Sector lists: {len(entries)} stocks, {len(items)} matched; new {report.inserted},"
          f" changed {report.changed}, unchanged {report.unchanged}."
          f" Universe covered: {covered} of {len(universe)}.")  # fmt: skip
    store.conn.close()
    return 0
