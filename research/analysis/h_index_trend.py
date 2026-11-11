"""T4 of the approved 2012-2021 list: the Index trend pillar and every component, on T0's trades.

Read-only (owner approval 2026-11-08 19:35 IST; claude/history_2012_2021_preregistration.md).
One period, so the bar is: each group has at least 30 trades and the direction survives
dropping any one calendar year. Each comparison is reported as "Supports the score", "Reverse"
or "Noise"; the predictions below were written before any 2012-2021 result existed.

    PYTHONPATH=~/.cache/vcp-research-pylib .venv/bin/python research/analysis/h_index_trend.py \
        --run <T0 backtest id>

``--db`` and ``--mh-db`` default to the history database. Pointed at the 2022-2026 research
database and a development run, it reproduces the earlier review's tables (a code check only).
"""

from __future__ import annotations

import argparse
import json

import common as c
import duckdb
import numpy as np
import pandas as pd

P = "index_trend"
# the research copy (history_2011 retired 2026-11-10)
HISTORY_DB = c.ROOT / "data" / "research" / "database_research.duckdb"

# (id, column, scored-good side, other side, prediction) -- fixed 2026-11-08 before any look
CHECKS = [
    ("T4a", "reclaim_50", "fresh (below in last 20)", "above all 20", "fresh better"),
    ("T4b", "above_200", "yes", "no", "Reverse"),
    ("T4c", "pillar", "100", "<50", "Reverse"),
    ("d1", "above_50", "yes", "no", "Noise"),
    ("d2", "above_150", "yes", "no", "Noise"),
    ("d3", "stacked_full", "yes", "no", "Noise"),
    ("d4", "rising_200", "yes", "no", "Noise"),
    ("d5", "nifty50_above_50", "yes", "no", "Noise"),
    ("d6", "smallcap_above_50", "yes", "no", "Noise"),
    ("d7", "pillar_change_20", "rising", "falling", "Noise"),
    ("d8", "pillar_path", "turned strong", "strong, stayed", "Noise"),
    ("d9", "pillar_path", "turned weak", "weak, stayed", "Noise"),
]


def trades(db: str, run: str) -> pd.DataFrame:
    con = duckdb.connect(db, read_only=True)
    t = c._trades_of(con, run)
    m = json.loads(con.execute("SELECT metrics_json FROM backtest_runs WHERE backtest_id = ?",
                               [run]).fetchone()[0])["every_trade"]  # fmt: skip
    con.close()
    got = (len(t), round(c.pf(t["ret"]), 2))
    want = (m["trades"], round(m["profit_factor"], 2))
    if got != want:
        raise SystemExit(f"rebuilt {got}, stored {want}")
    for col in ("signal_date", "entry_date", "exit_date"):
        t[col] = pd.to_datetime(t[col])
    t["year"] = t["entry_date"].dt.year
    return t


def market_health(db: str) -> pd.DataFrame:
    c.SERVING_DB = db  # common.market_health reads the module-level path
    return c.market_health()


def components(d: pd.DataFrame) -> pd.DataFrame:
    """The pillar's conditions exactly as scored, with the bands of the 2022-2026 review."""
    names = ("above_50", "above_150", "above_200", "stacked", "rising_200", "nifty50",
             "smallcap250")  # fmt: skip
    r = {k: d[f"{P}.{k}.reading"] for k in names}

    def yn(s: pd.Series) -> pd.Series:
        return pd.Series(np.where(s.isna(), None, np.where(s > 0, "yes", "no")), index=d.index)

    d["above_50"], d["above_150"], d["above_200"] = (
        yn(r["above_50"]), yn(r["above_150"]), yn(r["above_200"]))  # fmt: skip
    d["stacked"] = r["stacked"].map({2.0: "full", 1.0: "half", 0.0: "none"})
    d["stacked_full"] = pd.Series(np.where(r["stacked"].isna(), None,
                                           np.where(r["stacked"] == 2.0, "yes", "no")),
                                  index=d.index)  # fmt: skip
    d["rising_200"] = yn(r["rising_200"])
    d["nifty50_above_50"] = yn(r["nifty50"])
    d["smallcap_above_50"] = yn(r["smallcap250"])
    s = d[f"{P}.score"]
    d["pillar"] = pd.Series(np.where(s.isna(), None, np.select([s >= 100, s >= 50],
                                                               ["100", "50-99"], "<50")),
                            index=d.index)  # fmt: skip
    d["dist_50"] = pd.cut(r["above_50"], [-100, 0, 3, 6, 100],
                          labels=["below", "0-3%", "3-6%", "6%+"])  # fmt: skip
    d["dist_150"] = pd.cut(r["above_150"], [-100, 0, 5, 10, 100],
                           labels=["below", "0-5%", "5-10%", "10%+"])  # fmt: skip
    d["dist_200"] = pd.cut(r["above_200"], [-100, 0, 5, 10, 100],
                           labels=["below", "0-5%", "5-10%", "10%+"])  # fmt: skip
    d["slope_200"] = pd.cut(r["rising_200"], [-100, 0, 1.5, 100],
                            labels=["falling", "0-1.5%", "1.5%+"])  # fmt: skip
    prev = s.shift(20)
    diff = s - prev
    d["pillar_change_20"] = np.where(
        diff.isna(), None, np.select([diff <= -25, diff >= 25], ["falling", "rising"], "flat"))
    now_hi, was_hi = s >= 80, prev >= 80
    d["pillar_path"] = np.where(
        prev.isna() | s.isna(), None,
        np.select([now_hi & was_hi, now_hi & ~was_hi, ~now_hi & was_hi],
                  ["strong, stayed", "turned strong", "turned weak"], "weak, stayed"))  # fmt: skip
    a50 = r["above_50"]
    below_any = (a50 <= 0).astype(float).where(a50.notna()).rolling(20).max().shift(1)
    d["reclaim_50"] = np.where(
        a50.isna() | below_any.isna(), None,
        np.select([(a50 > 0) & (below_any == 1), a50 > 0],
                  ["fresh (below in last 20)", "above all 20"], "below now"))  # fmt: skip
    return d


def table(x: pd.DataFrame, col: str, order: list[str] | None = None) -> pd.DataFrame:
    g = x.dropna(subset=[col]).groupby(col, observed=True).apply(c.summary, include_groups=False)
    if order is not None:
        g = g.reindex([o for o in order if o in g.index])
    return g


def verdict(x: pd.DataFrame, col: str, a: str, b: str, min_n: int = 30) -> tuple[str, str]:
    """("first" | "second" | "noise", detail): which side has the higher PF under the bar."""
    ga, gb = x[x[col] == a], x[x[col] == b]
    head = f"{len(ga)} vs {len(gb)} trades, PF {c.pf(ga['ret']):.2f} vs {c.pf(gb['ret']):.2f}"
    if len(ga) < min_n or len(gb) < min_n:
        return "noise", f"too few trades; {head}"
    s = np.sign(c.pf(ga["ret"]) - c.pf(gb["ret"]))
    for y in sorted(x["year"].unique()):
        h = x[x["year"] != y]
        if np.sign(c.pf(h[h[col] == a]["ret"]) - c.pf(h[h[col] == b]["ret"])) != s:
            return "noise", f"flips without {y}; {head}"
    return ("first" if s > 0 else "second"), head


def outcome(tid: str, side: str) -> str:
    if side == "noise":
        return "Noise"
    if tid == "T4a":  # not a scored condition
        return "fresh better" if side == "first" else "above-all-20 better"
    return "Supports the score" if side == "first" else "Reverse"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--db", default=str(HISTORY_DB))
    ap.add_argument("--mh-db", default=str(HISTORY_DB))
    ap.add_argument("--out", default="h_index_trend")
    a = ap.parse_args()

    t = trades(a.db, a.run)
    d = components(market_health(a.mh_db))
    x = c.at_entry(t, d)
    no_state = x[f"{P}.score"].isna()
    print(f"Run {a.run}: {len(x)} trades, every-trade PF {c.pf(x['ret']):.2f}; "
          f"{int(no_state.sum())} without an Index trend state (left out of T4)\n")  # fmt: skip
    x = x[~no_state].copy()
    print("== Trades with an Index trend state, by year")
    print(table(x, "year").to_string(), "\n")

    tables = [
        ("Pillar band (descriptive)", "pillar", ["100", "50-99", "<50"]),
        ("Fresh reclaim of the 50-day (T4a)", "reclaim_50",
         ["fresh (below in last 20)", "above all 20", "below now"]),
        ("NIFTY 500 above 200-day (T4b)", "above_200", ["yes", "no"]),
        ("  distance from 200-day (descriptive)", "dist_200", ["below", "0-5%", "5-10%", "10%+"]),
        ("NIFTY 500 above 50-day (d1)", "above_50", ["yes", "no"]),
        ("  distance from 50-day (descriptive)", "dist_50", ["below", "0-3%", "3-6%", "6%+"]),
        ("NIFTY 500 above 150-day (d2)", "above_150", ["yes", "no"]),
        ("  distance from 150-day (descriptive)", "dist_150",
         ["below", "0-5%", "5-10%", "10%+"]),
        ("Stacked 50>150>200 (d3)", "stacked", ["full", "half", "none"]),
        ("200-day rising (d4)", "rising_200", ["yes", "no"]),
        ("  200-day slope (descriptive)", "slope_200", ["falling", "0-1.5%", "1.5%+"]),
        ("NIFTY 50 above its 50-day (d5)", "nifty50_above_50", ["yes", "no"]),
        ("Smallcap 250 above its 50-day (d6)", "smallcap_above_50", ["yes", "no"]),
        ("Pillar change over 20 sessions (d7)", "pillar_change_20",
         ["rising", "flat", "falling"]),
        ("Path (d8, d9)", "pillar_path",
         ["strong, stayed", "turned strong", "turned weak", "weak, stayed"]),
    ]  # fmt: skip
    for title, col, order in tables:
        print(f"== {title}")
        print(table(x, col, order).to_string(), "\n")

    print("== Verdicts (bar and predictions fixed 2026-11-08, before the look)")
    for tid, col, a_, b_, pred in CHECKS:
        side, detail = verdict(x, col, a_, b_)
        got = outcome(tid, side)
        print(f"  {tid:4} {a_!s:>26} vs {b_!s:<16} predicted {pred:12} result {got:18}"
              f" {'MATCHES' if got == pred else 'differs':8} ({detail})")  # fmt: skip
    c.OUT.mkdir(parents=True, exist_ok=True)
    x.to_csv(c.OUT / f"{a.out}_trades.csv", index=False)


if __name__ == "__main__":
    main()
