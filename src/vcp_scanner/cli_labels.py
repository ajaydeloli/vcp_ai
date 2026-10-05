"""``vcp compute labels [--strategy ID]``: fill in forward labels (PROJECT_DESIGN 39; Phase 9
step 2; keyed by strategy since Multi-Strategy step 2, STRATEGY_SPECIFICATION 10.2).

Every scored observation of the current config without a complete label gets its labels
(re)computed from the adjusted bars after its as-of date. Labels become complete after 60
sessions (and the failed-breakout window); until then they are refreshed on every run, so the
daily run calls this once after scoring.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime

from vcp_scanner.cli_pipeline import _err, _open_store, _resolve_data_snapshot
from vcp_scanner.config import load_scanner_config


def run_compute_labels(args: argparse.Namespace) -> int:
    from vcp_scanner.backtest.labels import LABEL_VERSION, VOLUME_BASE, forward_labels
    from vcp_scanner.data.repositories.duckdb_label_repository import DuckDBLabelRepository

    started = datetime.now(UTC)
    try:
        cfg = load_scanner_config(args.config_dir)
    except Exception as e:
        _err(f"Configuration error: {e}")
        return 1
    from vcp_scanner.cli_scores import resolve_strategy

    resolved = resolve_strategy(args, cfg)
    if resolved is None:
        return 1
    strategy_id, config_hash = resolved
    ratio = cfg.strategy.vcp.breakout.min_volume_ratio
    with _open_store(args.db) as store:
        snapshot = _resolve_data_snapshot(store, getattr(args, "data_snapshot_id", None))
        if snapshot is None:
            return 1
        repo = DuckDBLabelRepository(store, snapshot)
        pending = repo.pending(config_hash, strategy_id)
        if not pending:
            print(f"Forward labels ({LABEL_VERSION}): nothing pending for {strategy_id} config "
                  f"{config_hash[:12]}")  # fmt: skip
            return 0
        bars = repo.bars(sorted({o.instrument_id for o in pending}), repo.bars_start(pending))
        index = {iid: {b[0]: k for k, b in enumerate(bs)} for iid, bs in bars.items()}
        items = []
        missing = 0
        for o in pending:
            bs = bars.get(o.instrument_id, [])
            k = index.get(o.instrument_id, {}).get(o.as_of)
            if k is None or bs[k][3] is None:
                missing += 1  # no bar on the as-of date: nothing to label from
                continue
            fwd = bs[k + 1 :]
            base = bs[max(0, k + 1 - VOLUME_BASE) : k + 1]
            lab = forward_labels(
                float(bs[k][3]), [float(b[1]) for b in fwd], [float(b[2]) for b in fwd],
                [float(b[3]) for b in fwd], [None if b[4] is None else float(b[4]) for b in fwd],
                [None if b[4] is None else float(b[4]) for b in base],
                o.pivot, ratio,
            )  # fmt: skip
            items.append((o, float(bs[k][3]), lab))
        repo.save(config_hash, items, started, strategy_id)
    done = Counter("complete" if lab.complete else "open" for _, _, lab in items)
    print(f"Forward labels ({LABEL_VERSION}, {strategy_id} config {config_hash[:12]})")
    print(f"  Updated     : {len(items)} ({done['complete']} complete, {done['open']} still open)")
    if missing:
        print(f"  Skipped     : {missing} without a bar on their as-of date")
    return 0
