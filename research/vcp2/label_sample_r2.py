"""Second blind labelling round: 50 fresh charts of trending stocks (owner go 2026-11-11).

Round 1 (``label_sample.py``) mixed trending and non-trending stocks; the owner's Yes depended
mostly on the trend. Round 2 tests detector v2 where it matters: only stocks passing the Trend
Template (v1's stored result) on v1's weekly scan days, in the discovery years (2012-07 ..
2017-12, so no confirmation year is touched), with the current v2 configuration:

  A. 25 charts v2 calls a candidate;
  B. 25 charts v2 does not.

Fresh: no stock within 90 days of a round-1 chart, nor of another round-2 chart. Shuffled with a
fixed seed; the group and both detectors' readings go to ``sample_truth``, which the labelling
page never reads. Refuses to run until the v2 scan of the current configuration covers every
discovery week, and if the round-2 labels database already exists.

    .venv/bin/python research/vcp2/label_sample_r2.py [--seed 20261111] [--per-group 25]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.patterns.vcp2.config import VCP2_VERSION, load_vcp2_config, vcp2_config_hash

sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_sample import (  # noqa: E402
    CHART_BARS,
    FIRST,
    LABEL_DDL,
    LABELS,
    LAST,
    RESEARCH,
    ROOT,
    SEP_DAYS,
    pick,
    records,
)

LABELS_R2 = ROOT / "data" / "research" / "vcp2_labels_r2.duckdb"
VCP_CLASSES = {"VCP", "VCP_LIKE", "A_PLUS_VCP"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--per-group", type=int, default=25)
    a = ap.parse_args()
    if LABELS_R2.exists():
        raise SystemExit(f"{LABELS_R2} exists; move it away to draw a new sample")
    rng = random.Random(a.seed)
    chash = vcp2_config_hash(load_vcp2_config(ROOT / "config"))
    src = duckdb.connect(str(RESEARCH), read_only=True)
    # v1 weekly scan days that have prices (v1 has one, Sunday 2012-11-11, without any)
    weekly = [r[0] for r in src.execute("""
        SELECT DISTINCT as_of_date FROM scan_runs WHERE scan_type = 'VCP' AND status =
          'COMPLETED' AND as_of_date BETWEEN ? AND ? AND as_of_date IN (
            SELECT DISTINCT trade_date FROM daily_prices_adjusted_current
            WHERE computed_from_snapshot_id = ? AND trade_date BETWEEN ? AND ?)
        ORDER BY 1""", [FIRST, LAST, LIVE_SNAPSHOT_ID, FIRST, LAST]).fetchall()]  # fmt: skip
    src.execute("CREATE TEMP TABLE wk AS SELECT unnest(?) AS d", [weekly])
    scanned = src.execute(
        "SELECT count(DISTINCT scan_date) FROM vcp2_runs WHERE config_hash = ? "
        "AND completed_at IS NOT NULL AND scan_date IN (SELECT d FROM wk)", [chash]
    ).fetchone()[0]  # type: ignore[index]  # fmt: skip
    if scanned != len(weekly):
        raise SystemExit(f"v2 scan {chash[:12]} covers {scanned} of {len(weekly)} weeks; wait")
    trending = src.execute("""
        SELECT t.instrument_id, t.as_of_date, coalesce(c.is_candidate, false)
        FROM trend_template_results t
        LEFT JOIN vcp2_candidates c ON c.config_hash = ? AND c.instrument_id = t.instrument_id
          AND c.scan_date = t.as_of_date
        WHERE t.status = 'PASS' AND t.as_of_date IN (SELECT d FROM wk)""",
        [chash]).fetchall()  # fmt: skip
    v1 = {(r[0], r[1]): r[2] for r in src.execute("""
        SELECT instrument_id, as_of_date, classification FROM vcp_patterns
        WHERE is_primary AND as_of_date IN (SELECT d FROM wk)""").fetchall()}  # fmt: skip
    # round 1's charts are taken first, so no round-2 chart repeats one of their bases
    r1 = duckdb.connect(str(LABELS), read_only=True)
    taken: dict[str, list[date]] = {}
    for iid, day in r1.execute("SELECT instrument_id, scan_date FROM samples").fetchall():
        taken.setdefault(iid, []).append(day)
    r1.close()
    n = a.per_group
    grp_a = pick([(i, d) for i, d, c in trending if c], n, taken, rng)
    grp_b = pick([(i, d) for i, d, c in trending if not c], n, taken, rng)
    chosen = [(*r, "A") for r in grp_a] + [(*r, "B") for r in grp_b]
    rng.shuffle(chosen)
    lab = duckdb.connect(str(LABELS_R2))
    for ddl in LABEL_DDL:
        lab.execute(ddl)
    for pos, (iid, day, grp) in enumerate(chosen, 1):
        sym = src.execute("SELECT symbol FROM instruments WHERE instrument_id = ?",
                          [iid]).fetchone()  # fmt: skip
        bars = src.execute("""
            SELECT trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj
            FROM daily_prices_adjusted_current
            WHERE instrument_id = ? AND computed_from_snapshot_id = ? AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT ?""",
            [iid, LIVE_SNAPSHOT_ID, day, CHART_BARS]).fetchall()[::-1]  # fmt: skip
        v2json = records(src.execute("""
            SELECT * FROM vcp2_candidates WHERE config_hash = ? AND instrument_id = ?
              AND scan_date = ?""", [chash, iid, day]))  # fmt: skip
        v1json = records(src.execute("""
            SELECT base_start_date, pivot_price, contraction_count, classification
            FROM vcp_patterns WHERE instrument_id = ? AND as_of_date = ? AND is_primary""",
            [iid, day]))  # fmt: skip
        lab.execute("INSERT INTO samples VALUES (?,?,?,?,?)",
                    [pos, iid, sym[0] if sym else iid, day, pos])  # fmt: skip
        lab.executemany("INSERT INTO sample_bars VALUES (?,?,?,?,?,?,?)",
                        [[pos, *b] for b in bars])  # fmt: skip
        lab.execute("INSERT INTO sample_truth VALUES (?,?,?,?,?,?)",
                    [pos, grp, v1.get((iid, day)), grp == "A", v2json, v1json])  # fmt: skip
    meta = {"round": 2, "seed": a.seed, "vcp2_config_hash": chash, "vcp2_version": VCP2_VERSION,
            "first": FIRST.isoformat(), "last": LAST.isoformat(), "trend_template": "PASS",
            "chart_bars": CHART_BARS, "sep_days": SEP_DAYS,
            "groups": {"A": len(grp_a), "B": len(grp_b)}, "pool": {
                "candidates": sum(1 for x in trending if x[2]),
                "not_candidates": sum(1 for x in trending if not x[2])},
            "created": datetime.now(UTC).isoformat(timespec="seconds")}  # fmt: skip
    lab.execute("INSERT INTO sample_meta VALUES ('meta', ?)", [json.dumps(meta)])
    lab.close()
    src.close()
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
