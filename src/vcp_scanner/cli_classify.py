"""``vcp classify update``: sector of each stock from NSE (DATA_SPECIFICATION §48.1, S1), then the
hand-entered sectors of ``config/manual_sectors.yaml`` for stocks NSE's lists do not cover."""

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
    u = sub.add_parser("update", help="Read NSE's index lists and the manual sectors file")
    u.add_argument("--db", default="data/vcp_scanner.duckdb")
    u.add_argument("--config-dir", default="config")


def run_classify(args: argparse.Namespace) -> int:
    from vcp_scanner.data.providers.manual_sectors import FILE_NAME, load_manual_sectors
    from vcp_scanner.data.providers.nse_index_lists import NseIndexListProvider
    from vcp_scanner.data.repositories.duckdb_classification_repository import (
        SOURCE_MANUAL,
        Classification,
        DuckDBClassificationRepository,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.domain.errors import ProviderError

    if getattr(args, "classify_command", None) != "update":
        _err("Usage: vcp classify update")
        return 1
    today = datetime.now(IST).date()
    store = DuckDBStore(Path(args.db).resolve())
    store.migrate()
    repo = DuckDBClassificationRepository(store)
    symbol_of = dict(store.conn.execute(
        "SELECT instrument_id, symbol FROM instruments WHERE is_active"
    ).fetchall())  # fmt: skip
    try:
        entries = NseIndexListProvider().entries()
    except ProviderError as exc:  # a warning, never a failed daily run
        print(f"WARNING: sector lists not read: {exc}")
        entries = []
    if entries:
        ids = repo.match_instruments([(e.symbol, e.isin) for e in entries])
        items = [Classification(instrument_id=ids[e.symbol], sector=e.sector)
                 for e in entries if e.symbol in ids]  # fmt: skip
        report = repo.apply(items, today)
        print(f"Sector lists: {len(entries)} stocks, {len(items)} matched; new {report.inserted},"
              f" changed {report.changed}, unchanged {report.unchanged}.")  # fmt: skip
        for iid, was, nse in report.replaced_manual:
            sym = symbol_of.get(iid, iid)
            print(f"NSE replaced a manual sector: {sym} (manual {was!r}, NSE {nse!r});"
                  " the manual entry can be removed.")  # fmt: skip

    manual, problems = load_manual_sectors(Path(args.config_dir) / FILE_NAME)
    for p in problems:
        print(f"WARNING: {FILE_NAME}: {p}")
    if manual:
        ids = repo.match_instruments([(e.symbol, None) for e in manual])
        unknown = [e.symbol for e in manual if e.symbol not in ids]
        for sym in unknown:
            print(f"WARNING: {FILE_NAME}: {sym} is not an active NSE symbol")
        items = [Classification(instrument_id=ids[e.symbol], sector=e.sector,
                                basic_industry=e.industry, source=SOURCE_MANUAL)
                 for e in manual if e.symbol in ids]  # fmt: skip
        report = repo.apply(items, today)
        print(f"Manual sectors: {len(items)} entries; new {report.inserted}, changed "
              f"{report.changed}, unchanged {report.unchanged}, already classified by NSE "
              f"{report.kept_nse}.")  # fmt: skip

    universe = [r[0] for r in store.conn.execute(
        "SELECT m.instrument_id FROM universe_memberships m WHERE m.exclusion_reason IS NULL"
        " AND m.universe_snapshot_id = (SELECT universe_snapshot_id FROM universe_snapshots"
        " ORDER BY as_of_date DESC LIMIT 1)"
    ).fetchall()]  # fmt: skip
    print(f"Universe covered: {repo.coverage(universe)} of {len(universe)}.")
    store.conn.close()
    return 0
