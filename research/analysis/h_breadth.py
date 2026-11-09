"""T5.1 of the approved 2012-2021 list: Breadth's VQI gap, one confirming look on T0's trades.

Read-only (owner, 2026-11-09, method A; prediction written in
claude/history_2012_2021_preregistration.md before this ran). Compared: the vqi_gap part at 50
points or more (our universe's equal-weight index level with or ahead of NIFTY 500 over 21
sessions) vs below 50. Bar: each group at least 30 trades, direction survives dropping any one
calendar year (``h_index_trend.verdict``).

    PYTHONPATH=~/.cache/vcp-research-pylib:research/analysis .venv/bin/python \
        research/analysis/h_breadth.py --run <T0 backtest id>
"""

from __future__ import annotations

import argparse

import common as c
import h_index_trend as h
import numpy as np
import pandas as pd

P = "breadth"
PREDICTION = "Supports the score"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--db", default=str(h.HISTORY_DB))
    ap.add_argument("--mh-db", default=str(h.HISTORY_DB))
    a = ap.parse_args()

    t = h.trades(a.db, a.run)
    d = h.market_health(a.mh_db)
    pts = d[f"{P}.vqi_gap.points"]
    d["vqi_gap"] = pd.Series(np.where(pts.isna(), None, np.where(pts >= 50, "50+", "<50")),
                             index=d.index)  # fmt: skip
    d["vqi_gap_band"] = pd.Series(
        np.where(pts.isna(), None, np.select([pts <= 0, pts >= 100], ["0", "100"], "between")),
        index=d.index)  # fmt: skip
    x = c.at_entry(t, d)
    missing = x["vqi_gap"].isna()
    print(f"Run {a.run}: {len(x)} trades; {int(missing.sum())} without a VQI gap (left out)\n")
    x = x[~missing].copy()
    r = x[f"{P}.vqi_gap.reading"]
    print(f"VQI gap at entry: median {r.median():.2f} pp ({r.quantile(0.1):.2f} .. "
          f"{r.quantile(0.9):.2f})\n")  # fmt: skip
    print("== VQI gap points 50+ vs below 50")
    print(h.table(x, "vqi_gap", ["50+", "<50"]).to_string(), "\n")
    print("== Bands (descriptive)")
    print(h.table(x, "vqi_gap_band", ["0", "between", "100"]).to_string(), "\n")
    print("== By year, VQI gap 50+ / below 50 (descriptive)")
    print(x.groupby(["year", "vqi_gap"]).apply(c.summary, include_groups=False)
          .unstack("vqi_gap").to_string(), "\n")  # fmt: skip
    side, detail = h.verdict(x, "vqi_gap", "50+", "<50")
    got = h.outcome("T5.1", side)
    print(f"== Verdict T5.1 (fixed 2026-11-09): predicted {PREDICTION}, result {got} "
          f"{'MATCHES' if got == PREDICTION else 'differs'} ({detail})")  # fmt: skip
    x.to_csv(c.OUT / "h_breadth_trades.csv", index=False)


if __name__ == "__main__":
    main()
