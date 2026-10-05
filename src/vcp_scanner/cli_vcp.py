"""``vcp compute vcp --as-of DATE``: VCP detection over a Trend Template scan (Phase 6 step 7).

Reads the Trend Template scan of the same date and config (``trend-<date>-<hash12>``) for the
gates: its instruments are the universe; PASS / weekly Stage 2 feed classification; data
statuses become VCP data states (INSUFFICIENT_DATA; DATA_NOT_READY for DATA_NOT_READY and
DATA_QUALITY_BLOCKED). Prices are the adjusted bars of the same data snapshot with the feature
engine's ATR%. Breakout events (VCP_SPECIFICATION 47, 61B) need the previous scan date, so dates
must be computed in order (the daily run does). Writes ``vcp_*`` tables and an immutable
``scan_runs`` row (scan type ``VCP``) with a copy of the verdicts.
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
from vcp_scanner.domain.enums import VCPStatus
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.domain.strategy import VCP_STRATEGY_ID, detector_scan_id

_DATA_STATES = {
    "INSUFFICIENT_DATA": VCPStatus.INSUFFICIENT_DATA,
    "DATA_NOT_READY": VCPStatus.DATA_NOT_READY,
    "DATA_QUALITY_BLOCKED": VCPStatus.DATA_NOT_READY,
}


def vcp_scan_id(as_of_iso: str, config_hash: str, data_snapshot_id: str) -> str:
    return detector_scan_id(VCP_STRATEGY_ID, as_of_iso, config_hash, data_snapshot_id)


def run_compute_setups(args: argparse.Namespace) -> int:
    """``vcp compute setups --strategy ID``: one strategy's detector (STRATEGY_SPECIFICATION
    11.2, 12B). VCP keeps its own scan path; the others run through ``cli_setups``."""
    from vcp_scanner.patterns.registry import get_strategy

    strategy_id = getattr(args, "strategy", None) or VCP_STRATEGY_ID
    try:
        get_strategy(strategy_id)
    except Exception as e:
        _err(f"Strategy error: {e}")
        return 1
    if strategy_id == VCP_STRATEGY_ID:
        return run_compute_vcp(args)
    from vcp_scanner.cli_setups import run_compute_strategy_setups

    return run_compute_strategy_setups(args)


def run_compute_vcp(args: argparse.Namespace) -> int:
    from vcp_scanner.data.repositories.duckdb_scan_run_repository import (
        DuckDBScanRunRepository,
        ScanRun,
    )
    from vcp_scanner.data.repositories.duckdb_snapshot_repository import (
        DuckDBSnapshotRepository,
    )
    from vcp_scanner.data.repositories.duckdb_vcp_repository import (
        DuckDBVCPRepository,
        vcp_results_hash,
        vcp_verdict_row,
    )
    from vcp_scanner.patterns.vcp.detector import VCPDetection, VCPDetector
    from vcp_scanner.patterns.vcp.monitor import track_breakouts
    from vcp_scanner.versioning import VCP_ALGORITHM_VERSION, code_state, version_manifest

    started_at = datetime.now(UTC)
    as_of = _parse_date(args.as_of)
    if as_of is None:
        return 1
    try:
        cfg = load_scanner_config(args.config_dir)
    except Exception as e:
        _err(f"Configuration error: {e}")
        return 1
    config_hash = scan_config_hash(cfg)
    vcp_cfg, cls_cfg = cfg.strategy.vcp, cfg.strategy.classification

    with _open_store(args.db) as store:
        snapshot = _resolve_data_snapshot(store, getattr(args, "data_snapshot_id", None))
        if snapshot is None:
            return 1
        trend_scan = f"trend-{as_of.isoformat()}-{config_hash[:12]}"
        if snapshot != LIVE_SNAPSHOT_ID:
            trend_scan += f"-{snapshot}"
        gates = store.conn.execute(
            "SELECT instrument_id, status, weekly_stage2_pass FROM trend_template_results"
            " WHERE scan_id = ? ORDER BY instrument_id",
            [trend_scan],
        ).fetchall()
        if args.instrument:
            gates = [g for g in gates if _matches(g[0], args.instrument)]
        if not gates:
            _err(
                f"Error: no Trend Template results for scan {trend_scan}. Run "
                f"`vcp compute trend-template --as-of {as_of}` with the same config first."
            )
            return 1
        trend_run = store.conn.execute(
            "SELECT universe_snapshot_id, universe_cutoff, survivorship_status,"
            " survivorship_detail FROM scan_runs WHERE scan_id = ? AND scan_type ="
            " 'TREND_TEMPLATE' ORDER BY started_at DESC LIMIT 1",
            [trend_scan],
        ).fetchone()

        repo = DuckDBVCPRepository(store, snapshot)
        ids = [g[0] for g in gates]
        series = repo.load_series(ids, as_of, vcp_cfg.lookback_bars())
        priors = repo.prior_patterns(ids, as_of, config_hash)
        events = repo.events(ids, as_of, config_hash)
        detector = VCPDetector(vcp_cfg, cls_cfg, config_hash=config_hash)

        detections = []
        new_events = []
        for iid, tt_status, stage2 in gates:
            s = series.get(iid)
            state = _DATA_STATES.get(str(tt_status))
            if state is None and (s is None or not s.dates or s.dates[-1] != as_of):
                # Signals need the as-of bar (DATA_SPECIFICATION staleness): no bar today.
                state = VCPStatus.STALE_DATA
            if s is None or not s.dates:
                assert state is not None
                detections.append(VCPDetection(iid, as_of, None, state, f"DATA:{state.value}"))
                continue
            det = detector.detect(
                iid, s, as_of, trend_template_pass=(tt_status == "PASS"),
                weekly_stage2_pass=stage2, data_state=state,
            )  # fmt: skip
            pattern = det.pattern
            event = events.get((iid, pattern.base_start)) if pattern else None
            det, new = track_breakouts(
                det, s, prior=priors.get(iid), event=event, config_hash=config_hash, vcp=vcp_cfg
            )
            detections.append(det)
            if new is not None:
                new_events.append(new)

        scan_id = vcp_scan_id(as_of.isoformat(), config_hash, snapshot)
        later = repo.later_scan_dates(as_of, config_hash)
        repo.save_scan(scan_id, as_of, config_hash, VCP_ALGORITHM_VERSION, detections,
                       new_events, started_at)  # fmt: skip

        verdicts = [vcp_verdict_row(d) for d in detections]
        counts = Counter(
            f"{d.pattern.classification.value}/{d.pattern.status.value}"
            if d.pattern else f"NO_PATTERN/{d.no_pattern_reason}"
            for d in detections
        )  # fmt: skip
        if snapshot == LIVE_SNAPSHOT_ID:
            data_cutoff = started_at
        else:
            frozen = DuckDBSnapshotRepository(store).load(snapshot)
            assert frozen is not None
            data_cutoff = frozen.known_at
        commit, dirty = code_state()
        run = ScanRun(
            scan_run_id=f"vcprun-{as_of:%Y%m%d}-{started_at:%Y%m%dT%H%M%S%f}Z",
            scan_type="VCP",
            as_of_date=as_of,
            scan_id=scan_id,
            data_snapshot_id=snapshot,
            data_cutoff=data_cutoff,
            universe_snapshot_id=trend_run[0] if trend_run else trend_scan,
            universe_cutoff=trend_run[1] if trend_run else None,
            scan_config_hash=config_hash,
            section_hashes=section_config_hashes(cfg),
            code_commit=commit,
            code_dirty=dirty,
            versions=version_manifest(),
            survivorship_status=trend_run[2] if trend_run else None,
            survivorship_detail=trend_run[3] if trend_run else None,
            counts={"considered": len(detections), "breakout_events": len(new_events),
                    **counts},
            results_hash=vcp_results_hash(verdicts),
            started_at=started_at,
            completed_at=datetime.now(UTC),
            strategy_id=VCP_STRATEGY_ID,
        )  # fmt: skip
        DuckDBScanRunRepository(store).record(run, [])
        repo.record_run_results(run.scan_run_id, verdicts)

    by_class = Counter(d.pattern.classification.value for d in detections if d.pattern)
    by_status = Counter(d.pattern.status.value for d in detections if d.pattern)
    print(f"VCP detection for {as_of}")
    print(f"  Scan run    : {run.scan_run_id}")
    print(f"  Scan ID     : {scan_id} (gates from {trend_scan})")
    print(f"  Considered  : {len(detections)}; patterns {sum(by_class.values())}")
    print(f"  Classes     : {dict(sorted(by_class.items()))}")
    print(f"  Statuses    : {dict(sorted(by_status.items()))}")
    print(f"  New breakout events: {len(new_events)}")
    print(f"  Results hash: {run.results_hash[:16]}")
    if later:
        print(
            f"  NOTE: later VCP scans exist ({', '.join(d.isoformat() for d in later)}); their "
            "breakout events were removed. Rerun them in date order."
        )
    return 0
