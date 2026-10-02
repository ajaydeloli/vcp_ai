"""``vcp verify scan``: rebuild a recorded scan at its cutoff and compare (audit P1-8, D4).

Nothing is frozen every evening: a full frozen snapshot costs about 0.9 GB. Instead a scan
run records its data cutoff, universe snapshot, config hashes, code commit and a hash of
its verdicts, and this command proves the record on demand:

1. copy the database to a work file (the main database is only read);
2. on the copy, freeze the data at the run's cutoff (``ingest adjusted-prices --known-at``,
   ``compute features``), unless the run already used a frozen snapshot;
3. rebuild the universe "as known at" the run's universe cutoff (deterministic id, P1-8b),
   then RS and the Trend Template over exactly that universe and snapshot;
4. compare the rebuilt run's ``results_hash`` with the recorded one and list differences.

The copy is deleted afterwards unless ``--keep`` is given; a kept copy is the "frozen scan
worth keeping". A different code commit than the run's is reported, since the code is part
of what made the result.
"""

from __future__ import annotations

import argparse
import shutil
from collections.abc import Callable
from datetime import UTC
from pathlib import Path
from typing import Any

from vcp_scanner.data.repositories.duckdb_scan_run_repository import DuckDBScanRunRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID, snapshot_id_for
from vcp_scanner.versioning import code_state

MAX_DIFFS_SHOWN = 20


def _iso(value: Any) -> str:
    return str(value.astimezone(UTC).isoformat(timespec="microseconds"))


def run_verify_scan(args: argparse.Namespace, cli_main: Callable[[list[str]], int]) -> int:
    """Exit 0 when the rebuilt result matches the record, 2 when it differs, 1 on error."""
    db = Path(args.db)
    with DuckDBStore(str(db)) as store:
        repo = DuckDBScanRunRepository(store)
        if args.scan_run_id is None:
            for r in repo.list_runs(limit=args.limit):
                mark = "+dirty" if r[5] else ""
                print(f"{r[0]}  as-of {r[1]}  data {r[2]}  universe {r[3]}  "
                      f"code {str(r[4])[:12]}{mark}  hash {str(r[6])[:16]}")  # fmt: skip
            return 0
        run = repo.load(args.scan_run_id)
        if run is None:
            print(f"Error: no scan run {args.scan_run_id}.")
            return 1
        if run["scan_type"] != "TREND_TEMPLATE":
            print(
                f"Error: {args.scan_run_id} is a {run['scan_type']} run; `verify scan` rebuilds "
                "Trend Template runs only."
            )
            return 1
        recorded = repo.load_results(args.scan_run_id)

    commit, dirty = code_state()
    print(f"Verifying {run['scan_run_id']} (as of {run['as_of_date']})")
    print(f"  recorded: data {run['data_snapshot_id']} cut off {_iso(run['data_cutoff'])}, "
          f"universe {run['universe_snapshot_id']}, code {run['code_commit'][:12]}"
          f"{' (uncommitted changes)' if run['code_dirty'] else ''}")  # fmt: skip
    if commit != run["code_commit"] or dirty or run["code_dirty"]:
        note = " (uncommitted changes)" if dirty else ""
        print(f"  WARNING: running code {commit[:12]}{note} differs from the run's code;"
              " a mismatch may come from the code, not the data.")  # fmt: skip

    work = Path(args.work_dir) if args.work_dir else db.parent
    copy = work / f"verify-{run['scan_run_id']}.duckdb"
    print(f"  copying the database to {copy} ...")
    shutil.copyfile(db, copy)
    try:
        return _rebuild_and_compare(run, recorded, str(copy), args.config_dir, cli_main)
    finally:
        if args.keep:
            print(f"  kept the rebuilt copy: {copy}")
        else:
            copy.unlink(missing_ok=True)


def _rebuild_and_compare(
    run: dict[str, Any],
    recorded: list[tuple[Any, ...]],
    copy: str,
    config_dir: str,
    cli_main: Callable[[list[str]], int],
) -> int:
    as_of = run["as_of_date"].isoformat()
    snap = run["data_snapshot_id"]
    common = ["--db", copy]
    steps: list[tuple[str, list[str]]] = []
    if snap == LIVE_SNAPSHOT_ID:
        cutoff = run["data_cutoff"]
        snap = snapshot_id_for(cutoff)
        steps += [
            ("freeze adjusted prices", ["ingest", "adjusted-prices", "--known-at", _iso(cutoff),
                                        *common]),
            ("features", ["compute", "features", "--data-snapshot-id", snap, *common]),
        ]  # fmt: skip
    universe_cutoff = run["universe_cutoff"] or run["data_cutoff"]
    steps.append(
        ("universe", ["ingest", "universe", "--as-of", as_of, "--known-at", _iso(universe_cutoff),
                      "--config-dir", config_dir, *common])
    )  # fmt: skip
    for name, argv in steps:
        print(f"  {name} ...")
        if cli_main(argv) != 0:
            print(f"Error: step '{name}' failed.")
            return 1

    with DuckDBStore(copy) as store:
        row = store.conn.execute(
            "SELECT universe_snapshot_id FROM universe_snapshots"
            " WHERE as_of_date = ? AND created_at = ?",
            [run["as_of_date"], universe_cutoff],
        ).fetchone()
    if row is None:
        print("Error: the rebuilt universe snapshot was not found.")
        return 1
    universe = str(row[0])
    if universe != run["universe_snapshot_id"]:
        print(f"  NOTE: rebuilt universe id {universe} differs from the recorded "
              f"{run['universe_snapshot_id']} (ids made before P1-8b were random).")  # fmt: skip

    for name, argv in (
        ("RS", ["compute", "rs", "--as-of", as_of, "--universe-snapshot-id", universe,
                "--data-snapshot-id", snap, "--config-dir", config_dir, "--db", copy]),
        ("Trend Template", ["compute", "trend-template", "--as-of", as_of,
                            "--universe-snapshot-id", universe, "--data-snapshot-id", snap,
                            "--config-dir", config_dir, "--db", copy]),
    ):  # fmt: skip
        print(f"  {name} ...")
        if cli_main(argv) != 0:
            print(f"Error: step '{name}' failed.")
            return 1

    with DuckDBStore(copy) as store:
        repo = DuckDBScanRunRepository(store)
        rebuilt_id = repo.list_runs(run["as_of_date"], limit=1, scan_type="TREND_TEMPLATE")[0][0]
        rebuilt = repo.load(rebuilt_id)
        rebuilt_rows = repo.load_results(rebuilt_id)
    assert rebuilt is not None
    if rebuilt["scan_config_hash"] != run["scan_config_hash"]:
        print("  WARNING: the current config differs from the run's (scan config hash).")
    if rebuilt["results_hash"] == run["results_hash"]:
        print(f"MATCH: {len(rebuilt_rows)} verdicts, results hash {run['results_hash'][:16]}")
        return 0
    old = {r[0]: r for r in recorded}
    new = {r[0]: r for r in rebuilt_rows}
    diffs = sorted(i for i in old.keys() | new.keys() if old.get(i) != new.get(i))
    print(f"MISMATCH: recorded {run['results_hash'][:16]} ({len(old)} verdicts), "
          f"rebuilt {rebuilt['results_hash'][:16]} ({len(new)}); "
          f"{len(diffs)} instrument(s) differ:")  # fmt: skip
    for iid in diffs[:MAX_DIFFS_SHOWN]:
        print(f"    {iid}: recorded {old.get(iid, ())[1:]} rebuilt {new.get(iid, ())[1:]}")
    return 2
