"""The unexplained down-gaps that can matter for the backtest: stocks at Rs 20 or more with an
average traded value of at least Rs 1 crore before the gap (the universe floor). For each, the
NSE corporate-action records of that symbol within 15 days (saved raw files). Read-only."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "analysis"))
import ca_parse_report  # noqa: E402

con = duckdb.connect(str(ROOT / "data" / "history_2011.duckdb"), read_only=True)
g = pd.read_csv(ROOT / "data" / "research" / "history_gap_triage.csv", parse_dates=["day"])
g = g[g["ratio"] < 0.75].copy()  # blocking down-gaps only
liq = []
for r in g.itertuples():
    x = con.execute("""
        SELECT median(close_raw), median(close_raw * volume_raw) FROM (
          SELECT close_raw, volume_raw FROM daily_prices WHERE instrument_id = ? AND trade_date < ?
          ORDER BY trade_date DESC LIMIT 20)""", [r.instrument_id, r.day.date()]).fetchone()
    liq.append(x)
g["price"], g["traded_value"] = [a for a, _ in liq], [b for _, b in liq]
L = g[(g["price"] >= 20) & (g["traded_value"] >= 1e7)].copy()
print(f"blocking down-gaps: {len(g)}; in universe range (Rs 20+, Rs 1 cr+): {len(L)}; "
      f"split-like among them: {int(L['split_like'].sum())}")

recs: dict[str, list] = {}
for it in ca_parse_report.records():
    recs.setdefault(it.get("symbol"), []).append(it)


def nse_near(symbol: str, day: pd.Timestamp) -> str:
    out = []
    for it in recs.get(symbol, []):
        try:
            ex = datetime.strptime(it.get("exDate") or "", "%d-%b-%Y")  # noqa: DTZ007 - a date only
        except ValueError:
            continue
        if abs(ex - day.to_pydatetime()) <= timedelta(days=15):
            out.append(f"{it['exDate']}: {(it.get('subject') or '').strip()[:70]}")
    return " | ".join(out) or "-"


L["symbol"] = L["instrument_id"].str.replace("NSE_EQ|", "", regex=False)
L["nse"] = [nse_near(s, d) for s, d in zip(L["symbol"], L["day"], strict=True)]
L["year"] = L["day"].dt.year
print(pd.crosstab(L["year"], L["split_like"]).to_string())
cols = ["symbol", "day", "ratio", "vol_x", "later", "split_like", "nse"]
print(L.sort_values(["split_like", "day"], ascending=[False, True])[cols].round(3)
      .to_string(index=False, max_colwidth=90))  # fmt: skip
L.to_csv(ROOT / "data" / "research" / "history_gap_triage_liquid.csv", index=False)
