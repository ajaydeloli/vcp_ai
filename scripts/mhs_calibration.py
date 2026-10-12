"""Market Health Score calibration table (MARKET_HEALTH_SPECIFICATION §7): for each label, what
followed — VQI's and NIFTY 500's return over the next 20 sessions and the success of the
breakouts of the next 20 sessions — in sample (to 30 Jun 2024) and out of sample (after).

    .venv/bin/python scripts/mhs_calibration.py [data/vcp_scanner.duckdb]
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from datetime import date
from typing import Any

import duckdb

SPLIT = date(2024, 7, 1)
AHEAD = 20  # sessions of forward return
BREAKOUTS_KNOWN = 30  # the breakouts of the next 20 sessions are all judged 30 sessions later
ORDER = ["Strong", "Constructive", "Mixed", "Weak", "Adverse"]
PILLARS = ["index_trend", "breadth", "leadership", "accumulation", "breakouts"]


def _mean(xs: list[float | None]) -> str:
    v = [x for x in xs if x is not None]
    return f"{statistics.mean(v):6.2f}" if v else "     -"


def _corr(a: list[float | None], b: list[float | None]) -> float | None:
    p = [(x, y) for x, y in zip(a, b, strict=True) if x is not None and y is not None]
    if len(p) < 3:
        return None
    xs, ys = zip(*p, strict=True)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return round(sum((x - mx) * (y - my) for x, y in p) / den, 3) if den else None


def main(db: str) -> None:
    c = duckdb.connect(db, read_only=True)
    rows = c.execute(
        "SELECT trade_date, raw_score, raw_label, label, complete, vqi, nifty500, features_json,"
        " pillars_json FROM market_health_daily ORDER BY trade_date"
    ).fetchall()
    data: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        fwd = rows[i + AHEAD] if i + AHEAD < len(rows) else None
        later = json.loads(rows[i + BREAKOUTS_KNOWN][7]) if i + BREAKOUTS_KNOWN < len(rows) else {}
        data.append({
            "d": r[0], "raw": r[1], "raw_label": r[2], "label": r[3],
            "vqi": (fwd[5] / r[5] - 1) * 100 if fwd and r[5] and fwd[5] else None,
            "n500": (fwd[6] / r[6] - 1) * 100 if fwd and r[6] and fwd[6] else None,
            "success": later.get("success"),
            "pillars": {p["id"]: p["score"] for p in json.loads(r[8])},
        })  # fmt: skip
    for name, part in (
        ("in sample", [x for x in data if x["d"] < SPLIT]),
        ("out of sample", [x for x in data if x["d"] >= SPLIT]),
    ):
        print(f"== {name}: {len(part)} sessions from {part[0]['d']}")
        for key in ("raw_label", "label"):
            print(f"  by {key}: days  VQI+20  N500+20  breakout success")
            for lab in ORDER:
                g = [x for x in part if x[key] == lab]
                vqi, n500 = _mean([x["vqi"] for x in g]), _mean([x["n500"] for x in g])
                print(f"    {lab:13s} {len(g):4d}  {vqi}  {n500}  "
                      f"{_mean([x['success'] for x in g])}")  # fmt: skip
        raw = [x["raw"] for x in part]
        print("  corr raw vs VQI+20", _corr(raw, [x["vqi"] for x in part]),
              "N500+20", _corr(raw, [x["n500"] for x in part]),
              "success", _corr(raw, [x["success"] for x in part]))  # fmt: skip
        for pid in PILLARS:
            ps = [x["pillars"][pid] for x in part]
            print(f"   {pid:13s} VQI+20 {_corr(ps, [x['vqi'] for x in part])}  "
                  f"success {_corr(ps, [x['success'] for x in part])}")  # fmt: skip


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/vcp_scanner.duckdb")
