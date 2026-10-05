"""``vcp compute scores --as-of DATE [--strategy ID]``: setup scores over a scan date (Phase 7
step 3; per strategy since Multi-Strategy step 2, STRATEGY_SPECIFICATION 9).

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
from vcp_scanner.domain.strategy import VCP_STRATEGY_ID, detector_scan_id, score_scan_id


def _scan_id(kind: str, as_of_iso: str, config_hash: str, snapshot: str) -> str:
    sid = f"{kind}-{as_of_iso}-{config_hash[:12]}"
    return sid if snapshot == LIVE_SNAPSHOT_ID else f"{sid}-{snapshot}"


def resolve_strategy(args: argparse.Namespace, cfg: object) -> tuple[str, str] | None:
    """(strategy id, strategy config hash) for ``--strategy`` (default ``vcp``), or None after
    printing why it cannot be used."""
    from vcp_scanner.config.models import ScannerConfig
    from vcp_scanner.config.strategies import strategy_config_hash
    from vcp_scanner.patterns.registry import get_strategy, load_strategies

    assert isinstance(cfg, ScannerConfig)
    strategy_id = getattr(args, "strategy", None) or VCP_STRATEGY_ID
    try:
        get_strategy(strategy_id)
        files = load_strategies(args.config_dir)
    except Exception as e:
        _err(f"Strategy error: {e}")
        return None
    if strategy_id not in files:
        _err(f"Strategy {strategy_id} has no file config/strategies/{strategy_id}.yaml")
        return None
    return strategy_id, strategy_config_hash(cfg, files[strategy_id])


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
    resolved = resolve_strategy(args, cfg)
    if resolved is None:
        return 1
    strategy_id, config_hash = resolved
    if strategy_id != VCP_STRATEGY_ID:  # pragma: no cover - only VCP is registered (step 2)
        _err(f"Scoring for strategy {strategy_id} arrives with its detector (steps 4-6).")
        return 1
    scan_hash = scan_config_hash(cfg)
    scoring = cfg.strategy.scoring

    with _open_store(args.db) as store:
        snapshot = _resolve_data_snapshot(store, getattr(args, "data_snapshot_id", None))
        if snapshot is None:
            return 1
        iso = as_of.isoformat()
        trend_scan = _scan_id("trend", iso, scan_hash, snapshot)
        vcp_scan = detector_scan_id(strategy_id, iso, config_hash, snapshot)
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
        scan_id = score_scan_id(strategy_id, iso, config_hash, snapshot)
        repo.save_scan(scan_id, scored, scoring.version, config_hash, started_at, strategy_id)

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
            scan_config_hash=scan_hash,
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
            strategy_id=strategy_id,
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
