"""Shared pieces of the read-only research analyses (owner, 2026-11-08).

Nothing here writes to a database or changes a scan, a score, a strategy or the paper ledger.
Run with the project's Python and pandas from a separate folder, for example:

    PYTHONPATH=~/.cache/vcp-research-pylib .venv/bin/python research/analysis/mh_index_trend.py

The trade set is every trade of the stored paper-rule backtests of VCP (cross_5 entry,
breadth50 regime, hold_low8 exit, 15 bps a side): development 2022-02-01..2024-06-30 (574
trades) and validation 2024-07-01..2026-09-30 (317), rebuilt from ``backtest_events`` of the
research database. Market state is read as of the session before entry (known at the entry).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
# the research copy holds the stored runs below since 2026-11-11 (copied from the retired
# golden_src.duckdb with their ids unchanged)
RESEARCH_DB = ROOT / "data" / "research" / "database_research.duckdb"
SERVING_DB = ROOT / "data" / "serving" / "database_serving.duckdb"
OUT = ROOT / "data" / "research"
RUNS = {"development": "bt-20261006T055416134812Z", "validation": "bt-20261006T055418960095Z"}
EXPECTED = {"development": (574, 2.15), "validation": (317, 0.89)}  # stored every-trade results

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)


def pf(r: pd.Series) -> float:
    """Profit factor: sum of gains / sum of losses (NaN without a loss)."""
    w = r[r > 0].sum()
    lo = -r[r < 0].sum()
    return float(w / lo) if lo > 0 else float("nan")


def summary(g: pd.DataFrame) -> pd.Series:
    r = g["ret"]
    return pd.Series({"trades": len(r), "win%": round((r > 0).mean() * 100, 1),
                      "avg%": round(r.mean(), 2), "PF": round(pf(r), 2)})  # fmt: skip


def _trades_of(con: duckdb.DuckDBPyConnection, bid: str) -> pd.DataFrame:
    ev = con.execute(
        "SELECT instrument_id, event_date, event_type, price, metadata_json FROM backtest_events"
        " WHERE backtest_id = ? AND event_type IN ('ENTRY_SIGNAL','BREAKOUT','STOP','TIME_EXIT',"
        "'OPEN_AT_END') ORDER BY instrument_id, event_date", [bid]).df()  # fmt: skip
    out = []
    for iid, g in ev.groupby("instrument_id"):
        sigs: list[tuple] = []
        held = None
        for r in g.itertuples():
            m = json.loads(r.metadata_json or "{}")
            if r.event_type == "ENTRY_SIGNAL":
                sigs.append((r.event_date, m.get("pivot"), m.get("score")))
            elif r.event_type == "BREAKOUT":
                piv = m.get("pivot")
                c = [s for s in sigs if s[0] <= r.event_date and s[1] is not None
                     and abs(s[1] - piv) < 1e-6]  # fmt: skip
                sd, _, sc = c[-1] if c else (None, None, None)
                held = {"instrument_id": iid, "signal_date": sd, "entry_date": r.event_date,
                        "entry": r.price, "pivot": piv, "score": sc}  # fmt: skip
            elif held is not None:
                if r.event_type != "OPEN_AT_END":
                    held.update(exit_date=r.event_date, exit_type=r.event_type,
                                ret=json.loads(r.metadata_json)["ret_pct"])  # fmt: skip
                    out.append(held)
                held = None
    return pd.DataFrame(out)


def trades() -> pd.DataFrame:
    """The 891 trades, checked against the stored totals."""
    con = duckdb.connect(str(RESEARCH_DB), read_only=True)
    parts = []
    for period, bid in RUNS.items():
        t = _trades_of(con, bid)
        t["period"] = period
        n, want = len(t), EXPECTED[period]
        got = round(pf(t["ret"]), 2)
        if (n, got) != want:
            raise SystemExit(f"{period}: rebuilt {n} trades PF {got}, stored {want}")
        parts.append(t)
    t = pd.concat(parts, ignore_index=True)
    for c in ("signal_date", "entry_date", "exit_date"):
        t[c] = pd.to_datetime(t[c])
    t["quarter"] = t["entry_date"].dt.to_period("Q").astype(str)
    return t


def market_health() -> pd.DataFrame:
    """Daily Market Health rows: one column per pillar score and per part reading/points."""
    con = duckdb.connect(str(SERVING_DB), read_only=True)
    mh = con.execute("SELECT trade_date, raw_score, label, pillars_json, features_json"
                     " FROM market_health_daily ORDER BY trade_date").df()  # fmt: skip
    rows = []
    for r in mh.itertuples():
        row = {"day": pd.Timestamp(r.trade_date), "raw_score": r.raw_score, "label": r.label}
        for p in json.loads(r.pillars_json):
            row[f"{p['id']}.score"] = p.get("score")
            for part in p.get("parts", []):
                row[f"{p['id']}.{part['id']}.reading"] = part.get("reading")
                row[f"{p['id']}.{part['id']}.points"] = part.get("points")
        row.update({f"f.{k}": v for k, v in json.loads(r.features_json).items()
                    if isinstance(v, (int, float, bool)) or v is None})  # fmt: skip
        rows.append(row)
    return pd.DataFrame(rows)


def at_entry(t: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Each trade with the daily state of the session before its entry day."""
    d = daily.rename(columns={"day": "state_day"}).sort_values("state_day")
    t = t.sort_values("entry_date")
    return pd.merge_asof(t, d, left_on="entry_date", right_on="state_day", direction="backward",
                         allow_exact_matches=False)  # fmt: skip


def split_table(df: pd.DataFrame, col: str, order: list | None = None) -> pd.DataFrame:
    """Results by group of ``col`` in each period, side by side."""
    tabs = []
    for p in ("development", "validation"):
        g = df[df["period"] == p].groupby(col, observed=True).apply(summary, include_groups=False)
        g.columns = pd.MultiIndex.from_product([[p[:3]], g.columns])
        tabs.append(g)
    out = pd.concat(tabs, axis=1)
    if order is not None:
        out = out.reindex([o for o in order if o in out.index])
    return out


def verdict(df: pd.DataFrame, col: str, a, b, min_n: int = 30) -> str:
    """The pre-set bar for a two-group comparison ``a`` vs ``b`` of ``col``: PF(a) - PF(b) has
    the same sign in both periods, each group has at least ``min_n`` trades in each period, and
    the sign survives dropping any one quarter."""
    signs = []
    for p in ("development", "validation"):
        g = df[df["period"] == p]
        ga, gb = g[g[col] == a], g[g[col] == b]
        if len(ga) < min_n or len(gb) < min_n:
            return f"too few trades ({p[:3]}: {len(ga)} vs {len(gb)})"
        s = np.sign(pf(ga["ret"]) - pf(gb["ret"]))
        for q in g["quarter"].unique():  # drop one quarter at a time
            h = g[g["quarter"] != q]
            sq = np.sign(pf(h[h[col] == a]["ret"]) - pf(h[h[col] == b]["ret"]))
            if sq != s:
                return f"not robust: {p[:3]} flips without {q}"
        signs.append(s)
    if signs[0] != signs[1]:
        return "opposite in the two periods: noise"
    return f"CONSISTENT: {a!s} {'better' if signs[0] > 0 else 'worse'} than {b!s} in both periods"
