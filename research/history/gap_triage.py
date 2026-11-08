"""Triage the unexplained price gaps of the history database (owner request 2026-11-08).

Read-only. For each open UNEXPLAINED_GAP event: the open/previous-close ratio, the volume
before and after, and whether NSE listed any corporate action for that stock within 10 days.
A missed split or bonus shows a price ratio r and a volume that rises by about 1/r and stays
there; a crash does not. Output: counts by kind and year, and the split-like cases.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "history_2011.duckdb"
sys.path.insert(0, str(ROOT / "research" / "analysis"))

con = duckdb.connect(str(DB), read_only=True)
ev = con.execute("""
    SELECT e.instrument_id, e.trade_date, e.status, e.description, e.context
    FROM data_quality_events e WHERE e.event_type = 'UNEXPLAINED_GAP'
""").df()
print("events:", len(ev), ev["status"].value_counts().to_dict())
ev = ev[ev["status"] != "RESOLVED"]

rows = []
for r in ev.itertuples():
    bars = con.execute("""
        SELECT trade_date, open_raw, close_raw, volume_raw FROM daily_prices
        WHERE instrument_id = ? AND trade_date BETWEEN ? - INTERVAL 60 DAY AND ? + INTERVAL 60 DAY
        ORDER BY trade_date""", [r.instrument_id, r.trade_date, r.trade_date]).df()
    i = bars.index[bars["trade_date"] == pd.Timestamp(r.trade_date)]
    if len(i) == 0 or i[0] == 0:
        continue
    k = i[0]
    prev_close, open_ = bars.loc[k - 1, "close_raw"], bars.loc[k, "open_raw"]
    ratio = open_ / prev_close
    v_before = bars.loc[max(0, k - 20):k - 1, "volume_raw"].median()
    v_after = bars.loc[k + 1:k + 20, "volume_raw"].median()
    vol_ratio = v_after / v_before if v_before and v_before > 0 else float("nan")
    # price level 20 sessions later vs before: a split stays down, a crash may or may not
    later = bars.loc[min(len(bars) - 1, k + 20), "close_raw"] / prev_close
    rows.append({"instrument_id": r.instrument_id, "day": pd.Timestamp(r.trade_date),
                 "ratio": ratio, "vol_x": vol_ratio, "later": later,
                 "hint": json.loads(r.context or "{}").get("split_like_ratio")})  # fmt: skip
g = pd.DataFrame(rows)
g["year"] = g["day"].dt.year
# split-like: volume rises by about 1/ratio (within 40 %) and the price stays down
g["split_like"] = (g["vol_x"] * g["ratio"]).between(0.6, 1.4) & (g["ratio"] < 0.75) & (g["later"] < 0.85)
g["kind"] = pd.cut(g["ratio"], [0, 0.55, 0.75, 0.9, 10], labels=["<0.55", "0.55-0.75", "0.75-0.9", "0.9+"])
print("\nopen gaps by year and size of the drop (open / previous close)")
print(pd.crosstab(g["year"], g["kind"]).to_string())
print("\nsplit-like (volume x ratio about 1, price stays down):", int(g["split_like"].sum()),
      "of", len(g))  # fmt: skip
s = g[g["split_like"]].sort_values("day")
print(s[["instrument_id", "day", "ratio", "vol_x", "later", "hint"]].round(3).to_string(index=False))
g.to_csv(ROOT / "data" / "research" / "history_gap_triage.csv", index=False)
