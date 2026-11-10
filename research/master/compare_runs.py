"""Step 6: compare a stored backtest's trades with its re-run on the research copy.

Read-only (owner, 2026-11-10). Trades are matched by stock (history ids mapped to master ids
through ``master_idmap``) and entry date. Reports trades in both, only in the old run and only
in the new one, with when they were entered and how they did.

    PYTHONPATH=~/.cache/vcp-research-pylib:research/analysis .venv/bin/python \
        research/master/compare_runs.py OLD_DB OLD_RUN NEW_DB NEW_RUN
"""

from __future__ import annotations

import sys

import common as c
import duckdb
import h_index_trend as h
import pandas as pd


def main() -> None:
    old_db, old_run, new_db, new_run = sys.argv[1:5]
    old = h.trades(old_db, old_run)
    new = h.trades(new_db, new_run)
    idmap = dict(duckdb.connect(new_db, read_only=True).execute(
        "SELECT hid, lid FROM master_idmap").fetchall())  # fmt: skip
    old["iid"] = old["instrument_id"].map(lambda i: idmap.get(i, i))
    new["iid"] = new["instrument_id"]
    key = ["iid", "entry_date"]
    m = old.merge(new, on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
    both = m[m["_merge"] == "both"]
    only_old = m[m["_merge"] == "left_only"]
    only_new = m[m["_merge"] == "right_only"]
    print(f"old {len(old)} trades PF {c.pf(old['ret']):.2f}; "
          f"new {len(new)} PF {c.pf(new['ret']):.2f}")
    print(f"in both {len(both)}; only old {len(only_old)}; only new {len(only_new)}")
    same = (both["ret_old"] - both["ret_new"]).abs() < 0.01
    print(f"in both with the same return: {int(same.sum())}; different: {int((~same).sum())}")
    print(f"PF of the shared trades: old {c.pf(both['ret_old']):.2f}, "
          f"new {c.pf(both['ret_new']):.2f}")
    for name, g, col in (("only old", only_old, "ret_old"), ("only new", only_new, "ret_new")):
        if len(g):
            r = g[col]
            print(f"\n== {name}: {len(g)} trades, PF {c.pf(r):.2f}, avg {r.mean():.2f}%, "
                  f"sum {r.sum():.0f} pts")  # fmt: skip
            print(g.groupby(g["entry_date"].dt.to_period("Q").astype(str))[col]
                  .agg(["count", "sum"]).round(1).to_string())  # fmt: skip
    diff = both[~same]
    if len(diff):
        print("\n== shared trades with a different return (first 15)")
        print(diff[["iid", "entry_date", "ret_old", "ret_new", "exit_type_old", "exit_type_new"]]
              .head(15).to_string(index=False))  # fmt: skip
    pd.DataFrame(m).to_csv(c.OUT / f"compare_{new_run}.csv", index=False)


if __name__ == "__main__":
    main()
