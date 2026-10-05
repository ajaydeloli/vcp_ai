"""``vcp compute setups --strategy ID --as-of DATE`` for file-configured strategies
(STRATEGY_SPECIFICATION 12B; Multi-Strategy step 4).

Reads the Trend Template scan of the date (``trend-<date>-<scan hash12>``); the strategy's gate
population is its PASS rows (section 5). Loads the adjusted bars of the same data snapshot,
runs the strategy's detector, records new breakouts, and writes ``strategy_setups`` (scan id
``setup-<id>-<date>-<strategy hash12>``), the status history, breakout events and an immutable
``scan_runs`` row (type ``SETUP``) with a copy of the verdicts. Dates must be computed in order
(breakouts read earlier dates), as for VCP.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime

from vcp_scanner.cli_pipeline import (
    _err,
    _matches,
    _open_store,
    _parse_date,
    _resolve_data_snapshot,
)
from vcp_scanner.config import load_scanner_config
from vcp_scanner.config.loader import scan_config_hash, section_config_hashes
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.domain.strategy import detector_scan_id


def run_compute_strategy_setups(args: argparse.Namespace) -> int:
    from vcp_scanner.config.strategies import strategy_config_hash
    from vcp_scanner.data.repositories.duckdb_scan_run_repository import (
        DuckDBScanRunRepository,
        ScanRun,
    )
    from vcp_scanner.data.repositories.duckdb_snapshot_repository import (
        DuckDBSnapshotRepository,
    )
    from vcp_scanner.data.repositories.duckdb_strategy_repository import (
        DuckDBStrategyRepository,
        results_hash,
        verdict_row,
    )
    from vcp_scanner.domain.strategy import StrategyResult
    from vcp_scanner.patterns.registry import load_runtime
    from vcp_scanner.patterns.strategy_base import StrategyContext
    from vcp_scanner.versioning import code_state, version_manifest

    started_at = datetime.now(UTC)
    as_of = _parse_date(args.as_of)
    if as_of is None:
        return 1
    try:
        cfg = load_scanner_config(args.config_dir)
        rt = load_runtime(args.config_dir, args.strategy)
    except Exception as e:
        _err(f"Configuration error: {e}")
        return 1
    if rt.detector is None:
        _err(f"Strategy {rt.strategy_id} has its own scan path (`vcp compute vcp`).")
        return 1
    scan_hash = scan_config_hash(cfg)
    config_hash = strategy_config_hash(cfg, rt.file)
    ratio = cfg.strategy.vcp.breakout.min_volume_ratio  # one breakout rule (section 10.1)

    with _open_store(args.db) as store:
        snapshot = _resolve_data_snapshot(store, getattr(args, "data_snapshot_id", None))
        if snapshot is None:
            return 1
        trend_scan = f"trend-{as_of.isoformat()}-{scan_hash[:12]}"
        if snapshot != LIVE_SNAPSHOT_ID:
            trend_scan += f"-{snapshot}"
        gates = store.conn.execute(
            "SELECT instrument_id, weekly_stage2_pass FROM trend_template_results"
            " WHERE scan_id = ? AND status = 'PASS' ORDER BY instrument_id",
            [trend_scan],
        ).fetchall()
        known = store.conn.execute(
            "SELECT count(*) FROM trend_template_results WHERE scan_id = ?", [trend_scan]
        ).fetchone()
        if not known or not known[0]:
            _err(f"Error: no Trend Template results for scan {trend_scan}. Run `vcp compute "
                 f"trend-template --as-of {as_of}` with the same config first.")  # fmt: skip
            return 1
        if args.instrument:
            gates = [g for g in gates if _matches(g[0], args.instrument)]
        trend_run = store.conn.execute(
            "SELECT universe_snapshot_id, universe_cutoff, survivorship_status,"
            " survivorship_detail FROM scan_runs WHERE scan_id = ? AND scan_type ="
            " 'TREND_TEMPLATE' ORDER BY started_at DESC LIMIT 1",
            [trend_scan],
        ).fetchone()
        repo = DuckDBStrategyRepository(store, rt.strategy_id, snapshot)
        ids = [g[0] for g in gates]
        bars = repo.load_bars(ids, as_of, rt.detector.lookback_bars())
        events = repo.breakouts(ids, as_of, config_hash)
        results: list[StrategyResult] = []
        for iid, stage2 in gates:
            b = bars.get(iid)
            if b is None or not b.dates or b.dates[-1] != as_of:
                results.append(StrategyResult(iid, as_of, None, None, "STALE_DATA"))
                continue
            ctx = StrategyContext(iid, as_of, b, True, stage2, events.get(iid, {}), ratio)
            results.append(rt.detector.detect(ctx))
        scan_id = detector_scan_id(rt.strategy_id, as_of.isoformat(), config_hash, snapshot)
        later = repo.later_scan_dates(as_of, config_hash)
        new_events = repo.save_scan(scan_id, as_of, config_hash, rt.algorithm_version, results,
                                    started_at)  # fmt: skip

        verdicts = [verdict_row(r) for r in results]
        counts = Counter(
            f"{r.setup.classification}/{r.setup.status}" if r.setup
            else f"NO_SETUP/{r.no_setup_reason or r.data_state}"
            for r in results
        )  # fmt: skip
        if snapshot == LIVE_SNAPSHOT_ID:
            data_cutoff = started_at
        else:
            frozen = DuckDBSnapshotRepository(store).load(snapshot)
            assert frozen is not None
            data_cutoff = frozen.known_at
        commit, dirty = code_state()
        run = ScanRun(
            scan_run_id=f"setuprun-{rt.strategy_id}-{as_of:%Y%m%d}-{started_at:%Y%m%dT%H%M%S%f}Z",
            scan_type="SETUP", as_of_date=as_of, scan_id=scan_id, data_snapshot_id=snapshot,
            data_cutoff=data_cutoff,
            universe_snapshot_id=trend_run[0] if trend_run else trend_scan,
            universe_cutoff=trend_run[1] if trend_run else None,
            scan_config_hash=scan_hash, section_hashes=section_config_hashes(cfg),
            code_commit=commit, code_dirty=dirty,
            versions={**version_manifest(), f"{rt.strategy_id}_algorithm_version":
                      rt.algorithm_version, "strategy_config_hash": config_hash},
            survivorship_status=trend_run[2] if trend_run else None,
            survivorship_detail=trend_run[3] if trend_run else None,
            counts={"considered": len(results), "breakout_events": new_events, **counts},
            results_hash=results_hash(verdicts), started_at=started_at,
            completed_at=datetime.now(UTC), strategy_id=rt.strategy_id,
        )  # fmt: skip
        DuckDBScanRunRepository(store).record(run, [])
        repo.record_run_results(run.scan_run_id, verdicts)

    setups = [r.setup for r in results if r.setup]
    by_class = Counter(s.classification for s in setups)
    by_status = Counter(s.status for s in setups)
    print(f"{rt.strategy_id} setups for {as_of}")
    print(f"  Scan run    : {run.scan_run_id}")
    print(f"  Scan ID     : {scan_id} (gates from {trend_scan})")
    print(f"  Considered  : {len(results)} Trend Template passers; setups {len(setups)}")
    print(f"  Classes     : {dict(sorted(by_class.items()))}")
    print(f"  Statuses    : {dict(sorted(by_status.items()))}")
    print(f"  New breakout events: {new_events}")
    print(f"  Results hash: {run.results_hash[:16]}")
    if later:
        print(f"  NOTE: later {rt.strategy_id} scans exist ({', '.join(map(str, later))}); "
              "their breakout events were removed. Rerun them in date order.")  # fmt: skip
    return 0
