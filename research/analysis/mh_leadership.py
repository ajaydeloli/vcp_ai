"""Market Health pillar C, Leadership: each condition against the VCP paper-rule trades.

Read-only (owner, 2026-11-09: method A, 2022-2026 first, 2012-2021 kept for one confirming
look). The pillar scores six parts on ladders (config/market_health.yaml, mhs-2.0.0); leaders
are Trend Template passers with RS rank 80+:

  tt_share (25): share of the universe passing the Trend Template, 3% -> 0, 20% -> 100;
  leader_change (20): change in the number of leaders over 20 sessions, -40% -> 0, +40% -> 100;
  highs_20 (10): share at a 20-day high, 10-session average, 2% -> 0, 12% -> 100;
  highs_52 (10): share at a 52-week high, 10-session average, 0.5% -> 0, 5% -> 100;
  sectors (20): share of sector indices in an uptrend (above their 50-day with positive 1/3/6
      month returns, averaged), 20% -> 0, 80% -> 100;
  leaders_vs_vqi (15): the leaders of 20 sessions ago followed forward, their mean return minus
      VQI's, -3 -> 0, +5 -> 100 pp.

Fixed before the run, exactly as for Breadth (mh_breadth.py):
- each part on its points: 50 or more (the scored-good side) vs below 50; the pillar the same;
- direction: the pillar vs 20 sessions earlier, rising by 15+ points vs falling by 15+;
- descriptive: each part at 0 / between / 100 points; the pillar by the label cut-offs;
- the bar of the pillar review (``common.verdict``).
"""

from __future__ import annotations

import common as c
import numpy as np
import pandas as pd

P = "leadership"
PARTS = ["tt_share", "leader_change", "highs_20", "highs_52", "sectors", "leaders_vs_vqi"]
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
      f"leadership pillar missing for {x[f'{P}.score'].isna().sum()}\n")  # fmt: skip
print("Readings at entry (median, 10th-90th percentile):")
for part in PARTS:
    r = x[f"{P}.{part}.reading"]
    print(f"  {part:15} {r.median():8.3f}  ({r.quantile(0.1):.3f} .. {r.quantile(0.9):.3f})")
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
x.to_csv(c.OUT / "mh_leadership_trades.csv", index=False)
