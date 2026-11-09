"""Market Health pillar B, Breadth: each condition against the VCP paper-rule trades.

Read-only (owner, 2026-11-09: other pillars on 2022-2026 first, 2012-2021 kept for one
confirming look). The pillar scores seven parts on ladders (config/market_health.yaml,
mhs-2.0.0), all over our own universe:

  above_50 / above_150 / above_200 (20/15/15): share of stocks above their 50/150/200-day
      averages, 30% -> 0 points, 70% -> 100;
  ad_trend (15): (advancers - decliners) / (advancers + decliners) summed over 20 sessions,
      -0.15 -> 0, +0.15 -> 100;
  highs_lows (15): new 52-week highs / (highs + lows), 10-session averages, 0.2 -> 0, 0.8 -> 100
      (no highs and no lows: 50);
  change (10): change in the share above the 50-day over 10 sessions, -15 -> 0, +15 -> 100 pp;
  vqi_gap (10): VQI minus NIFTY 500 over 21 sessions, -4 -> 0, +4 -> 100 pp.

Fixed before the run:
- each part is judged on its points: 50 or more (the ladder's midpoint, the scored-good side)
  vs below 50; the pillar the same way (50+ vs below 50);
- direction: the pillar 20 sessions earlier, rising by 15+ points vs falling by 15+;
- descriptive bands: each part at 0 / between / 100 points; the pillar by the label cut-offs
  (below 35, 35-50, 50-65, 65-80, 80+);
- the bar of the pillar review (``common.verdict``): same direction in both periods, at least
  30 trades per group in each period, and it survives dropping any one quarter.
Scope: every trade was taken with the breadth50 regime on (at least 40% of the universe above
its 50-day), so the above_50 reading of a trade is rarely below 40%.
"""

from __future__ import annotations

import common as c
import numpy as np
import pandas as pd

P = "breadth"
PARTS = ["above_50", "above_150", "above_200", "ad_trend", "highs_lows", "change", "vqi_gap"]
t = c.trades()
d = c.market_health()

for part in PARTS:
    pts = d[f"{P}.{part}.points"]
    d[part] = pd.Series(np.where(pts.isna(), None, np.where(pts >= 50, "50+", "<50")),
                        index=d.index)  # fmt: skip
    d[f"{part}_band"] = pd.Series(
        np.where(pts.isna(), None, np.select([pts <= 0, pts >= 100], ["0", "100"], "between")),
        index=d.index)  # fmt: skip
s = d[f"{P}.score"]
d["pillar"] = pd.Series(np.where(s.isna(), None, np.where(s >= 50, "50+", "<50")), index=d.index)
d["pillar_band"] = pd.cut(s, [-1, 35, 50, 65, 80, 101], right=False,
                          labels=["<35", "35-50", "50-65", "65-80", "80+"])  # fmt: skip
diff = s - s.shift(20)
d["pillar_change_20"] = pd.Series(
    np.where(diff.isna(), None, np.select([diff <= -15, diff >= 15], ["falling", "rising"],
                                          "flat")), index=d.index)  # fmt: skip

x = c.at_entry(t, d)
print(f"Trades: {len(x)}; market state missing for {x['state_day'].isna().sum()}; "
      f"breadth pillar missing for {x[f'{P}.score'].isna().sum()}\n")  # fmt: skip
print("Readings at entry (median, 10th-90th percentile):")
for part in PARTS:
    r = x[f"{P}.{part}.reading"]
    print(f"  {part:10} {r.median():8.3f}  ({r.quantile(0.1):.3f} .. {r.quantile(0.9):.3f})")
print()

print("== Pillar band (descriptive)")
print(c.split_table(x, "pillar_band", ["<35", "35-50", "50-65", "65-80", "80+"]).to_string(), "\n")
print("== Pillar 50+ vs below 50")
print(c.split_table(x, "pillar", ["50+", "<50"]).to_string(), "\n")
for part in PARTS:
    print(f"== {part}: points 50+ vs below 50")
    print(c.split_table(x, part, ["50+", "<50"]).to_string())
    print(c.split_table(x, f"{part}_band", ["0", "between", "100"]).to_string(), "\n")
print("== Pillar change over 20 sessions (15+ points)")
print(c.split_table(x, "pillar_change_20", ["rising", "flat", "falling"]).to_string(), "\n")

print("== Verdicts (bar fixed before the run; first side = scored-good)")
for col, a, b in [("pillar", "50+", "<50"), *[(p, "50+", "<50") for p in PARTS],
                  ("pillar_change_20", "rising", "falling")]:  # fmt: skip
    print(f"  {col:18} {a:>7} vs {b:<8}: {c.verdict(x, col, a, b)}")
x.to_csv(c.OUT / "mh_breadth_trades.csv", index=False)
