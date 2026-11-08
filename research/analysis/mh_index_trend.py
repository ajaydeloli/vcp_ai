"""Market Health pillar A, Index trend: each condition against the VCP paper-rule trades.

Read-only (owner, 2026-11-08). The pillar scores seven conditions (config/market_health.yaml,
mhs-2.0.0): NIFTY 500 above its 50-, 150- and 200-day averages (20 points each), the averages
stacked 50 > 150 > 200 (20; half for one of the two), the 200-day average higher than 20
sessions ago (10), NIFTY 50 and Smallcap 250 above their own 50-day averages (5 each).

Bands and the bar are fixed before the run: a condition counts only if it separates results in
the same direction in both periods, with at least 30 trades per group in each period, and the
direction survives dropping any one quarter (``common.verdict``).
"""

from __future__ import annotations

import common as c
import numpy as np
import pandas as pd

P = "index_trend"
t = c.trades()
d = c.market_health()

# --- conditions exactly as scored, plus fixed bands of the readings --------------------------
d["above_50"] = np.where(d[f"{P}.above_50.reading"] > 0, "yes", "no")
d["above_150"] = np.where(d[f"{P}.above_150.reading"] > 0, "yes", "no")
d["above_200"] = np.where(d[f"{P}.above_200.reading"] > 0, "yes", "no")
d["stacked"] = d[f"{P}.stacked.reading"].map({2.0: "full", 1.0: "half", 0.0: "none"})
d["stacked_full"] = np.where(d[f"{P}.stacked.reading"] == 2.0, "yes", "no")
d["rising_200"] = np.where(d[f"{P}.rising_200.reading"] > 0, "yes", "no")
d["nifty50_above_50"] = np.where(d[f"{P}.nifty50.reading"] > 0, "yes", "no")
d["smallcap_above_50"] = np.where(d[f"{P}.smallcap250.reading"] > 0, "yes", "no")
s = d[f"{P}.score"]
d["pillar"] = np.select([s >= 100, s >= 50], ["100", "50-99"], "<50")

d["dist_50"] = pd.cut(d[f"{P}.above_50.reading"], [-100, 0, 3, 6, 100],
                      labels=["below", "0-3%", "3-6%", "6%+"])  # fmt: skip
d["dist_150"] = pd.cut(d[f"{P}.above_150.reading"], [-100, 0, 5, 10, 100],
                       labels=["below", "0-5%", "5-10%", "10%+"])  # fmt: skip
d["dist_200"] = pd.cut(d[f"{P}.above_200.reading"], [-100, 0, 5, 10, 100],
                       labels=["below", "0-5%", "5-10%", "10%+"])  # fmt: skip
d["slope_200"] = pd.cut(d[f"{P}.rising_200.reading"], [-100, 0, 1.5, 100],
                        labels=["falling", "0-1.5%", "1.5%+"])  # fmt: skip

# --- direction: where the pillar and the index were 20 sessions before ------------------------
prev = s.shift(20)
diff = s - prev
d["pillar_change_20"] = np.select([diff <= -25, diff >= 25], ["falling", "rising"], "flat")
now_hi, was_hi = s >= 80, prev >= 80
d["pillar_path"] = np.select(
    [now_hi & was_hi, now_hi & ~was_hi, ~now_hi & was_hi], ["strong, stayed", "turned strong",
     "turned weak"], "weak, stayed")  # fmt: skip
below_any = (d[f"{P}.above_50.reading"] <= 0).astype(int).rolling(20).max().shift(1)
d["reclaim_50"] = np.select(
    [(d[f"{P}.above_50.reading"] > 0) & (below_any == 1), d[f"{P}.above_50.reading"] > 0],
    ["fresh (below in last 20)", "above all 20"], "below now")  # fmt: skip
d["above50_x_200"] = d["above_50"].str.cat(d["above_200"], sep=" 50 / 200 ")

x = c.at_entry(t, d)
print(f"Trades: {len(x)}; market state missing for {x['state_day'].isna().sum()}\n")

TABLES = [
    ("Pillar score", "pillar", ["100", "50-99", "<50"]),
    ("NIFTY 500 above 50-day (20 pts)", "above_50", ["yes", "no"]),
    ("  distance from 50-day", "dist_50", ["below", "0-3%", "3-6%", "6%+"]),
    ("NIFTY 500 above 150-day (20 pts)", "above_150", ["yes", "no"]),
    ("  distance from 150-day", "dist_150", ["below", "0-5%", "5-10%", "10%+"]),
    ("NIFTY 500 above 200-day (20 pts)", "above_200", ["yes", "no"]),
    ("  distance from 200-day", "dist_200", ["below", "0-5%", "5-10%", "10%+"]),
    ("Stacked 50>150>200 (20 pts)", "stacked", ["full", "half", "none"]),
    ("200-day rising over 20 sessions (10 pts)", "rising_200", ["yes", "no"]),
    ("  200-day slope", "slope_200", ["falling", "0-1.5%", "1.5%+"]),
    ("NIFTY 50 above its 50-day (5 pts)", "nifty50_above_50", ["yes", "no"]),
    ("Smallcap 250 above its 50-day (5 pts)", "smallcap_above_50", ["yes", "no"]),
    ("Direction: pillar change over 20 sessions (25+ pts)", "pillar_change_20",
     ["rising", "flat", "falling"]),
    ("Path: pillar 80+ now vs 20 sessions ago", "pillar_path",
     ["strong, stayed", "turned strong", "turned weak", "weak, stayed"]),
    ("Fresh reclaim of the 50-day", "reclaim_50", ["fresh (below in last 20)", "above all 20",
                                                   "below now"]),
    ("Disagreement: above 50-day / above 200-day", "above50_x_200", None),
]  # fmt: skip
for title, col, order in TABLES:
    print(f"== {title}")
    print(c.split_table(x, col, order).to_string(), "\n")

print("== Verdicts (bar fixed before the run)")
CHECKS = [
    ("pillar", "100", "<50"), ("above_50", "yes", "no"), ("above_150", "yes", "no"),
    ("above_200", "yes", "no"), ("stacked_full", "yes", "no"), ("rising_200", "yes", "no"),
    ("nifty50_above_50", "yes", "no"), ("smallcap_above_50", "yes", "no"),
    ("pillar_change_20", "rising", "falling"),
    ("reclaim_50", "fresh (below in last 20)", "above all 20"),
    ("pillar_path", "turned strong", "strong, stayed"),
    ("pillar_path", "turned weak", "weak, stayed"),
]  # fmt: skip
for col, a, b in CHECKS:
    print(f"  {col:18} {a!s:>26} vs {b!s:<16}: {c.verdict(x, col, a, b)}")
x.to_csv(c.OUT / "mh_index_trend_trades.csv", index=False)
