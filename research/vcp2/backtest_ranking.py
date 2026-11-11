"""Portfolio check for ``backtest_compare.py``: the same slot ranking for both detectors.

The 10-slot portfolio takes only about one trade in seven, so when more breakouts arrive than
free slots the ranking decides the result. ``backtest_compare.py`` ranks v1 by its setup score
and v2 by RS rank; this re-runs the portfolio with each detector ranked by its own score (v1),
by RS rank (both) and by nothing (both; ties fall to the stock order). Read-only.

    .venv/bin/python research/vcp2/backtest_ranking.py
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import duckdb

from vcp_scanner.backtest.engine import Signal, run_portfolio, run_signals
from vcp_scanner.backtest.metrics import equity_stats, trade_stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest_compare as b  # noqa: E402

NAMES = {"own": "v1 score", "rs": "RS rank", "none": "none"}


def rescore(sigs: Sequence[Signal], how: str, rs: dict[tuple[str, date], int]) -> list[Signal]:
    out = []
    for s in sigs:
        sc: float | None
        if how == "none":
            sc = None
        elif how == "rs":
            r = rs.get((s.instrument_id, s.scan_date))
            sc = None if r is None else float(r)
        else:
            sc = s.score
        out.append(dataclasses.replace(s, score=sc))
    return out


def main() -> None:
    con = duckdb.connect(str(b.RESEARCH), read_only=True)
    signals, info = b.load(con)
    rs: dict[tuple[str, date], int] = {
        (r[0], r[1]): r[2]
        for r in con.execute(
            "SELECT instrument_id, as_of_date, rs_rank FROM trend_template_results"
            " WHERE as_of_date BETWEEN ? AND ? AND rs_rank IS NOT NULL",
            [b.FIRST, b.SIG_END],
        ).fetchall()
    }
    for r in con.execute(
        "SELECT instrument_id, scan_date, rs_rank FROM vcp2_candidates WHERE config_hash = ?"
        " AND is_candidate AND trend_template_pass AND scan_date BETWEEN ? AND ?"
        " AND rs_rank IS NOT NULL",
        [info["v2_hash"], b.FIRST, b.SIG_END],
    ).fetchall():
        rs.setdefault((r[0], r[1]), r[2])
    con.close()
    bars = info["bars"]
    print(f"{'system':7s} {'detector':12s} {'ranking':9s} {'taken':>6s} {'PF':>5s} {'avg%':>6s}"
          f" {'ret%':>7s} {'CAGR%':>6s} {'maxDD%':>7s} {'Sharpe':>6s}")  # fmt: skip
    for sname, ecfg in b.systems(info["regime"]).items():
        for dname, sigs in signals.items():
            for how in ("own", "rs", "none"):
                if how == "own" and dname != "v1":
                    continue
                trades, _ = run_signals(rescore(sigs, how, rs), bars, ecfg)
                port = run_portfolio(trades, bars, ecfg)
                ts, es = trade_stats(port.taken), equity_stats(port.equity)
                print(
                    f"{sname:7s} {dname:12s} {NAMES[how]:9s} {ts['trades']:6d}"
                    f" {b.fmt(ts['profit_factor'], '5.2f')} {b.fmt(ts['avg_ret'], '6.2f')}"
                    f" {b.fmt(es['total_ret'], '7.1f')} {b.fmt(es['cagr'], '6.1f')}"
                    f" {b.fmt(es['max_drawdown'], '7.1f')} {b.fmt(es['sharpe'], '6.2f')}"
                )


if __name__ == "__main__":
    main()
