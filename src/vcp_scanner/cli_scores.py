"""``vcp compute scores --as-of DATE``: setup scores over a scan date (Phase 7 step 3).

Reads the Trend Template scan (``trend-<date>-<hash12>``) and the VCP scan (``vcp-<date>-
<hash12>``) of the same date, config and data snapshot, scores every Trend Template passer and
ranks the eligible ones (SCORING_SPECIFICATION 1, 11). Writes ``setup_scores`` and
``score_components`` (scan id ``score-<date>-<hash12>``; a rerun replaces it) and an immutable
``scan_runs`` row (scan type ``SCORE``).
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime

from vcp_scanner.cli_pipeline import _err, _open_store, _parse_date, _resolve_data_snapshot
from vcp_scanner.config import load_scanner_config
from vcp_scanner.config.loader import scan_config_hash, section_config_hashes
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID


def _scan_id(kind: str, as_of_iso: str, config_hash: str, snapshot: str) -> str:
    sid = f"{kind}-{as_of_iso}-{config_hash[:12]}"
    return sid if snapshot == LIVE_SNAPSHOT_ID else f"{sid}-{snapshot}"


def run_compute_scores(args: argparse.Namespace) -> int:
    from vcp_scanner.data.repositories.duckdb_scan_run_repository import (
        DuckDBScanRunRepository,
        ScanRun,
    )
    from vcp_scanner.data.repositories.duckdb_score_repository import (
        DuckDBScoreRepository,
        score_results_hash,
    )
    from vcp_scanner.data.repositories.duckdb_snapshot_repository import (
        DuckDBSnapshotRepository,
    )
    from vcp_scanner.scoring.engine import score_scan
    from vcp_scanner.versioning import code_state, version_manifest

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
    scoring = cfg.strategy.scoring

    with _open_store(args.db) as store:
        snapshot = _resolve_data_snapshot(store, getattr(args, "data_snapshot_id", None))
        if snapshot is None:
            return 1
        iso = as_of.isoformat()
        trend_scan = _scan_id("trend", iso, config_hash, snapshot)
        vcp_scan = _scan_id("vcp", iso, config_hash, snapshot)
        have = {r[0] for r in store.conn.execute(
            "SELECT scan_type FROM scan_runs WHERE scan_id IN (?, ?)", [trend_scan, vcp_scan]
        ).fetchall()}  # fmt: skip
        if "TREND_TEMPLATE" not in have or "VCP" not in have:
            _err(f"Error: scores need the Trend Template and VCP scans {trend_scan} and "
                 f"{vcp_scan}. Run `vcp compute trend-template` and `vcp compute vcp --as-of "
                 f"{iso}` with the same config first.")  # fmt: skip
            return 1
        trend_run = store.conn.execute(
            "SELECT universe_snapshot_id, universe_cutoff, survivorship_status,"
            " survivorship_detail FROM scan_runs WHERE scan_id = ? AND scan_type ="
            " 'TREND_TEMPLATE' ORDER BY started_at DESC LIMIT 1",
            [trend_scan],
        ).fetchone()
        repo = DuckDBScoreRepository(store, snapshot)
        inputs = repo.load_inputs(as_of, trend_scan, vcp_scan, cfg.strategy.vcp.volatility.measure)
        scored = score_scan(inputs, scoring, float(cfg.strategy.trend_template.min_rs_rank))
        scan_id = _scan_id("score", iso, config_hash, snapshot)
        repo.save_scan(scan_id, scored, scoring.version, config_hash, started_at)

        if snapshot == LIVE_SNAPSHOT_ID:
            data_cutoff = started_at
        else:
            frozen = DuckDBSnapshotRepository(store).load(snapshot)
            assert frozen is not None
            data_cutoff = frozen.known_at
        commit, dirty = code_state()
        eligible = [s for s in scored if s.eligible]
        run = ScanRun(
            scan_run_id=f"scorerun-{as_of:%Y%m%d}-{started_at:%Y%m%dT%H%M%S%f}Z",
            scan_type="SCORE",
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
            counts={"scored": len(scored), "eligible": len(eligible),
                    **Counter(f"class/{s.classification}" for s in scored)},
            results_hash=score_results_hash(scored),
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )  # fmt: skip
        DuckDBScanRunRepository(store).record(run, [])

    top = sorted((s for s in eligible if s.final.final is not None),
                 key=lambda s: -(s.final.final or 0.0))[:5]  # fmt: skip
    print(f"Setup scores for {as_of}")
    print(f"  Scan run    : {run.scan_run_id}")
    print(f"  Scan ID     : {scan_id} ({scoring.version})")
    print(f"  Scored      : {len(scored)} Trend Template passers; ranked {len(eligible)}")
    print("  Top ranked  : " + (", ".join(
        f"{s.instrument_id} {s.final.final:.1f}" for s in top) or "none"))  # fmt: skip
    print(f"  Results hash: {run.results_hash[:16]}")
    return 0
