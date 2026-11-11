"""VCP v2 on real data, one scan day, read-only (owner go 2026-11-10: build v2).

Runs the v2 detector over every eligible stock of a weekly scan day in the research copy and
compares with v1's patterns of the same day. Nothing is written.

    .venv/bin/python research/vcp2/trial.py 2017-06-30 [--show 8]
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from datetime import date
from pathlib import Path

import duckdb

from vcp_scanner.patterns.vcp2.config import load_vcp2_config
from vcp_scanner.patterns.vcp2.detector import Bars, Detection, detect

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "research" / "database_research.duckdb"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("--show", type=int, default=8)
    a = ap.parse_args()
    day = date.fromisoformat(a.day)
    cfg = load_vcp2_config(ROOT / "config")
    con = duckdb.connect(str(DB), read_only=True)
    ids = [r[0] for r in con.execute("""
        SELECT m.instrument_id FROM universe_memberships m
        JOIN universe_snapshots s USING (universe_snapshot_id)
        WHERE s.as_of_date = ? AND m.eligible""", [day]).fetchall()]
    t0 = time.monotonic()
    rows = con.execute("""
        SELECT instrument_id, trade_date, high_adj, low_adj, close_adj, volume_adj
        FROM daily_prices_adjusted_current
        WHERE instrument_id IN (SELECT unnest(?)) AND trade_date <= ?
          AND trade_date > ? - INTERVAL 900 DAY
        ORDER BY instrument_id, trade_date""", [ids, day, day]).fetchall()
    t_load = time.monotonic() - t0
    series: dict[str, list[tuple]] = {}
    for r in rows:
        series.setdefault(r[0], []).append(r[1:])
    v1 = dict(con.execute("""
        SELECT instrument_id, classification FROM vcp_patterns
        WHERE as_of_date = ? AND is_primary""", [day]).fetchall())
    t1 = time.monotonic()
    results: dict[str, Detection] = {}
    for iid, bars in series.items():
        if not bars or bars[-1][0] != day:
            continue
        b = Bars([x[0] for x in bars], [x[1] for x in bars], [x[2] for x in bars],
                 [x[3] for x in bars], [x[4] for x in bars])  # fmt: skip
        results[iid] = detect(b, cfg)
    t_detect = time.monotonic() - t1
    cands = {i: d.candidate for i, d in results.items() if d.candidate is not None}
    passing = {i: c for i, c in cands.items() if c.is_candidate}
    per = 1000 * t_detect / max(1, len(results))
    print(f"{day}: {len(ids)} eligible, {len(results)} with a bar that day; "
          f"load {t_load:.1f}s, detect {t_detect:.1f}s ({per:.1f} ms/stock)")  # fmt: skip
    print("no base:", dict(Counter(d.reason for d in results.values() if d.reason)))
    print(f"a base: {len(cands)}; meets the loose minimum: {len(passing)}")
    print("unmet:", dict(Counter(u for c in cands.values() for u in c.unmet)))
    counts = Counter(c.contraction_count for c in passing.values())
    print("contractions (candidates):", sorted(counts.items()))
    exts = Counter(c.window_extensions for c in cands.values())
    print("window extensions:", sorted(exts.items()))
    print("undercuts:", sorted(Counter(c.undercuts for c in cands.values()).items()))
    print("final leg ended:", dict(Counter(c.final_ended for c in passing.values())))
    v1c = {i: k for i, k in v1.items() if k in ("VCP", "VCP_LIKE", "A_PLUS_VCP")}
    both = set(v1c) & set(passing)
    print(f"v1 VCP/VCP_LIKE/A+: {len(v1c)}; also a v2 candidate: {len(both)}; "
          f"v2 only: {len(set(passing) - set(v1c))}; v1 only: {len(set(v1c) - set(passing))}")
    shown = sorted(passing.items(), key=lambda kv: kv[1].final_depth_pct)[: a.show]
    for iid, c in shown:
        legs = " | ".join(
            f"{x.peak_date}:{x.peak_price:.1f}->{x.low_date}:{x.low_price:.1f} "
            f"({x.depth_pct:.1f}%, lh {x.lower_highs})" for x in c.contractions)  # fmt: skip
        print(f"\n{iid} v1={v1.get(iid)} base {c.base_start_date} {c.base_high:.1f} "
              f"depth {c.base_depth_pct:.1f}% bars {c.base_bars} ext {c.window_extensions} "
              f"undercuts {c.undercuts} pivot {c.pivot:.1f} ({c.distance_to_pivot_pct:+.1f}%) "
              f"right {c.right_side_range_pct:.1f}% dry {c.final_volume_ratio or 0:.2f}")
        print(f"  {legs}")


if __name__ == "__main__":
    main()
