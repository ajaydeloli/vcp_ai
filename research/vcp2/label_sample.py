"""Build the blind chart sample for labelling VCP detectors (spec section 5; owner go 2026-11-10).

Picks (stock, scan day) charts from the discovery years (2012-07 .. 2017-12, so no confirmation
year is touched) on v1's weekly scan days, where both detectors have a verdict:

  A. 50 v2 candidates, with bases whose window was extended and bases with an undercut
     over-sampled (15 each when available);
  B. 50 v1 primary patterns classed VCP, VCP_LIKE or A+ VCP;
  C. 50 Trend Template passers that neither detector calls a VCP that day.

A stock appears at most once per 90 days, so the same base is not shown twice. The order is
shuffled with a fixed seed, so the owner cannot tell which group a chart came from. Each chart's
bars (the 420 sessions up to and including the scan day, never later) are copied into the labels
database, so labelling never opens the research copy or live. The detectors' verdicts are kept in
a separate table the labelling page never reads.

    .venv/bin/python research/vcp2/label_sample.py [--seed 20261110] [--per-group 50]
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.patterns.vcp2.config import load_vcp2_config, vcp2_config_hash

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "data" / "research" / "database_research.duckdb"
LABELS = ROOT / "data" / "research" / "vcp2_labels.duckdb"
FIRST, LAST = date(2012, 7, 1), date(2017, 12, 31)
CHART_BARS = 420
SEP_DAYS = 90

LABEL_DDL = (
    """CREATE TABLE IF NOT EXISTS samples (
        sample_id INTEGER PRIMARY KEY, instrument_id VARCHAR NOT NULL, symbol VARCHAR,
        scan_date DATE NOT NULL, position INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sample_bars (
        sample_id INTEGER NOT NULL, trade_date DATE NOT NULL, open DOUBLE, high DOUBLE,
        low DOUBLE, close DOUBLE, volume DOUBLE, PRIMARY KEY (sample_id, trade_date))""",
    """CREATE TABLE IF NOT EXISTS sample_truth (
        sample_id INTEGER PRIMARY KEY, grp VARCHAR NOT NULL, v1_class VARCHAR,
        v2_candidate BOOLEAN, v2_json VARCHAR, v1_json VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS labels (
        sample_id INTEGER PRIMARY KEY, verdict VARCHAR NOT NULL, t1_date DATE, t1_price DOUBLE,
        lows_json VARCHAR, pivot_date DATE, pivot_price DOUBLE, note VARCHAR,
        labelled_at TIMESTAMP WITH TIME ZONE NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sample_meta (key VARCHAR PRIMARY KEY, value VARCHAR)""",
)


def records(cur: duckdb.DuckDBPyConnection) -> str:
    """The cursor's rows as a JSON list of objects; dates as ISO text (no pandas needed)."""
    cols = [d[0] for d in cur.description or []]
    return json.dumps([dict(zip(cols, r, strict=True)) for r in cur.fetchall()], default=str)


def pick(rows: list[tuple[Any, ...]], n: int, taken: dict[str, list[date]],
         rng: random.Random) -> list[tuple[Any, ...]]:  # fmt: skip
    """Up to ``n`` rows (instrument_id, scan_date, ...) in random order, a stock at most once
    per SEP_DAYS across everything taken so far."""
    rows = rows[:]
    rng.shuffle(rows)
    out: list[tuple[Any, ...]] = []
    for r in rows:
        if len(out) >= n:
            break
        iid, day = r[0], r[1]
        if any(abs((day - d).days) < SEP_DAYS for d in taken.get(iid, [])):
            continue
        taken.setdefault(iid, []).append(day)
        out.append(r)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261110)
    ap.add_argument("--per-group", type=int, default=50)
    a = ap.parse_args()
    if LABELS.exists():
        raise SystemExit(f"{LABELS} exists; move it away to draw a new sample")
    rng = random.Random(a.seed)
    chash = vcp2_config_hash(load_vcp2_config(ROOT / "config"))
    src = duckdb.connect(str(RESEARCH), read_only=True)
    weekly = [r[0] for r in src.execute(
        "SELECT DISTINCT as_of_date FROM scan_runs WHERE scan_type = 'VCP' AND status = "
        "'COMPLETED' AND as_of_date BETWEEN ? AND ? ORDER BY 1",
        [FIRST, LAST]).fetchall()]  # fmt: skip
    src.execute("CREATE TEMP TABLE wk AS SELECT unnest(?) AS d", [weekly])
    v1 = {
        (r[0], r[1]): r[2]
        for r in src.execute("""
        SELECT instrument_id, as_of_date, classification FROM vcp_patterns
        WHERE is_primary AND as_of_date IN (SELECT d FROM wk)""").fetchall()
    }
    v2 = {(r[0], r[1]): r[2:] for r in src.execute("""
        SELECT instrument_id, scan_date, is_candidate, window_extensions, undercuts
        FROM vcp2_candidates WHERE config_hash = ? AND scan_date IN (SELECT d FROM wk)""",
        [chash]).fetchall()}  # fmt: skip
    tt = [
        (r[0], r[1])
        for r in src.execute("""
        SELECT instrument_id, as_of_date FROM trend_template_results
        WHERE status = 'PASS' AND as_of_date IN (SELECT d FROM wk)""").fetchall()
    ]
    vcp_classes = {"VCP", "VCP_LIKE", "A_PLUS_VCP"}
    is_v1 = {k for k, c in v1.items() if c in vcp_classes}
    is_v2 = {k for k, v in v2.items() if v[0]}
    taken: dict[str, list[date]] = {}
    n = a.per_group
    ext = [(k[0], k[1]) for k in is_v2 if v2[k][1] > 0]
    und = [(k[0], k[1]) for k in is_v2 if v2[k][2] > 0]
    grp_a = pick(ext, 15, taken, rng) + pick(und, 15, taken, rng)
    grp_a += pick([(k[0], k[1]) for k in is_v2], n - len(grp_a), taken, rng)
    grp_b = pick([(k[0], k[1]) for k in is_v1], n, taken, rng)
    grp_c = pick([k for k in tt if k not in is_v1 and k not in is_v2], n, taken, rng)
    chosen = ([(*r, "A") for r in grp_a] + [(*r, "B") for r in grp_b]
              + [(*r, "C") for r in grp_c])  # fmt: skip
    rng.shuffle(chosen)
    lab = duckdb.connect(str(LABELS))
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
        v2row = src.execute("""
            SELECT * FROM vcp2_candidates WHERE config_hash = ? AND instrument_id = ?
              AND scan_date = ?""", [chash, iid, day])  # fmt: skip
        v2json = records(v2row)
        v1row = src.execute("""
            SELECT base_start_date, pivot_price, contraction_count, classification
            FROM vcp_patterns WHERE instrument_id = ? AND as_of_date = ? AND is_primary""",
            [iid, day])  # fmt: skip
        v1json = records(v1row)
        lab.execute("INSERT INTO samples VALUES (?,?,?,?,?)",
                    [pos, iid, sym[0] if sym else iid, day, pos])  # fmt: skip
        lab.executemany("INSERT INTO sample_bars VALUES (?,?,?,?,?,?,?)",
                        [[pos, *b] for b in bars])  # fmt: skip
        lab.execute("INSERT INTO sample_truth VALUES (?,?,?,?,?,?)", [
            pos, grp, v1.get((iid, day)), bool(v2.get((iid, day), (False,))[0]),
            v2json, v1json])  # fmt: skip
    meta = {"seed": a.seed, "vcp2_config_hash": chash, "first": FIRST.isoformat(),
            "last": LAST.isoformat(), "chart_bars": CHART_BARS, "sep_days": SEP_DAYS,
            "groups": {"A": len(grp_a), "B": len(grp_b), "C": len(grp_c)},
            "created": datetime.now(UTC).isoformat(timespec="seconds")}  # fmt: skip
    lab.execute("INSERT INTO sample_meta VALUES ('meta', ?)", [json.dumps(meta)])
    lab.close()
    print(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
