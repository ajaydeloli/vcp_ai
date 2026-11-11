"""v1 vs VCP v2.3: the same trading rules on both detectors (owner request 2026-11-11).

Discovery years only (claude/research_v2_plan_draft.md, section 3): signals from 2012-07-01 to
2017-08-31, prices read only up to 2017-12-31 so every trade settles inside discovery and no
confirmation-year (2018-2021) price is read. Read-only: nothing is stored.

Detectors (every signal needs the Trend Template passed on its scan day):
  * v1      - eligible v1 setups classed VCP_LIKE, VCP or A+ VCP on v1's weekly scans;
  * v2.3 d  - v2.3 candidates from the daily scan, a signal when a setup starts
              (``research/vcp2_signals.py``);
  * v2.3 w  - v2.3 candidates on v1's weekly scan days only (same cadence as v1).

Trading systems (event engine, ``backtest/engine.py``, 15 bps a side, 10 slots for portfolio):
  * base    - the research plan's base system: first close above the pivot within 20
              sessions, no volume rule, no market filter; stop at the last contraction's
              low or 8 % (tighter), time exit after 60 sessions;
  * frozen  - v1's paper rules: the same exit, plus a real cross with the close at most 5 %
              above the pivot (cross_5), breakout volume >= 1.5x, and the breadth regime.
  * base+reg, froz-reg - the market regime on its own (owner question 2026-11-11): the base
              system with the breadth regime added, and the frozen rules without it.

    .venv/bin/python research/vcp2/backtest_compare.py
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb

from vcp_scanner.backtest.engine import (
    EngineConfig,
    Signal,
    Trade,
    run_portfolio,
    run_signals,
)
from vcp_scanner.backtest.metrics import equity_stats, trade_stats
from vcp_scanner.backtest.regime import regime_by_day
from vcp_scanner.data.repositories.duckdb_backtest_repository import DuckDBBacktestRepository
from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.patterns.vcp2.config import VCP2_VERSION, load_vcp2_config, vcp2_config_hash
from vcp_scanner.research.outcomes import ENGINE_RULES
from vcp_scanner.research.vcp2_signals import CandidateDay, daily_signals

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "data" / "research" / "database_research.duckdb"
OUT = ROOT / "data" / "research" / "vcp2_backtest_trades.csv"
FIRST, SIG_END, DATA_END = date(2012, 7, 1), date(2017, 8, 31), date(2017, 12, 31)
V1_CLASSES = ["VCP_LIKE", "VCP", "A_PLUS_VCP"]


@dataclass
class ReadOnly:
    """What the backtest repository needs of a store: a connection (opened read-only)."""

    conn: duckdb.DuckDBPyConnection


def one(con: duckdb.DuckDBPyConnection, sql: str, args: Sequence[Any] = ()) -> Any:
    rows = con.execute(sql, list(args)).fetchall()
    if len(rows) != 1:
        raise SystemExit(f"expected one value, got {rows!r} for: {sql}")
    return rows[0][0]


def load(con: duckdb.DuckDBPyConnection) -> tuple[dict[str, list[Signal]], dict[str, Any]]:
    v2_hash = vcp2_config_hash(load_vcp2_config(ROOT / "config"))
    scan_days = [
        r[0]
        for r in con.execute(
            "SELECT scan_date FROM vcp2_runs WHERE config_hash = ? AND completed_at IS NOT NULL"
            " AND scan_date BETWEEN ? AND ? ORDER BY 1",
            [v2_hash, FIRST, SIG_END],
        ).fetchall()
    ]
    prices = one(con, "SELECT count(DISTINCT trade_date) FROM daily_prices_adjusted_current"
                      " WHERE computed_from_snapshot_id = ? AND trade_date BETWEEN ? AND ?",
                 [LIVE_SNAPSHOT_ID, date(2012, 7, 2), SIG_END])  # fmt: skip
    if len(scan_days) != prices:
        raise SystemExit(f"v2 scan {v2_hash[:12]} has {len(scan_days)} of {prices} days; wait")
    tt = {(r[0], r[1]) for r in con.execute(
        "SELECT instrument_id, as_of_date FROM trend_template_results WHERE status = 'PASS'"
        " AND as_of_date BETWEEN ? AND ?", [FIRST, SIG_END]).fetchall()}  # fmt: skip
    weekly = {r[0] for r in con.execute(
        "SELECT DISTINCT as_of_date FROM scan_runs WHERE scan_type = 'VCP' AND status ="
        " 'COMPLETED' AND as_of_date BETWEEN ? AND ?", [FIRST, SIG_END]).fetchall()}  # fmt: skip
    v1_hash = one(con, "SELECT DISTINCT config_hash FROM setup_scores WHERE strategy_id = 'vcp'"
                       " AND as_of_date BETWEEN ? AND ?", [FIRST, SIG_END])  # fmt: skip
    repo = DuckDBBacktestRepository(ReadOnly(con), LIVE_SNAPSHOT_ID)  # type: ignore[arg-type]
    v1_all = repo.signals(FIRST, SIG_END, v1_hash, V1_CLASSES, eligible_only=True)
    v1 = [s for s in v1_all if (s.instrument_id, s.scan_date) in tt]
    rows = []
    for iid, d, pivot, legs, rs in con.execute("""
            SELECT instrument_id, scan_date, pivot_price, contractions_json, rs_rank
            FROM vcp2_candidates WHERE config_hash = ? AND is_candidate
              AND trend_template_pass AND scan_date BETWEEN ? AND ?""",
            [v2_hash, FIRST, SIG_END]).fetchall():  # fmt: skip
        final_low = json.loads(legs)[-1]["low_price"] if legs else None
        rows.append(CandidateDay(iid, d, float(pivot), final_low, None if rs is None else rs))
    v2_daily = daily_signals(rows, scan_days)
    v2_weekly = [Signal(r.instrument_id, r.scan_date, r.pivot, r.score, r.final_low, "VCP2")
                 for r in rows if r.scan_date in weekly]  # fmt: skip
    tt_hash = one(
        con,
        "SELECT DISTINCT scan_config_hash FROM scan_runs WHERE scan_type ="
        " 'TREND_TEMPLATE' AND as_of_date BETWEEN ? AND ?",
        [FIRST, SIG_END],
    )
    breadth = repo.breadth(FIRST, SIG_END + timedelta(days=45), tt_hash,
                           FEATURES_CALCULATION_VERSION)  # fmt: skip
    signals = {"v1": v1, "v2.3 daily": v2_daily, "v2.3 weekly": v2_weekly}
    ids = sorted({s.instrument_id for ss in signals.values() for s in ss})
    info = {
        "v2_hash": v2_hash, "v1_hash": v1_hash, "v1_eligible": len(v1_all),
        "v1_tt": len(v1), "v2_candidate_days": len(rows), "scan_days": len(scan_days),
        "weekly_days": len(weekly), "regime": regime_by_day("breadth50", breadth),
        "bars": repo.bars(ids, FIRST, SIG_END, after_days=(DATA_END - SIG_END).days),
    }  # fmt: skip
    return signals, info


def systems(regime: Any) -> dict[str, EngineConfig]:
    rule = next(r for r in ENGINE_RULES if r.name == "hold_low8")
    return {
        "base": EngineConfig(rule, watch_days=20, min_volume_ratio=0.0, entry="breakout"),
        "base+reg": EngineConfig(
            rule, watch_days=20, min_volume_ratio=0.0, entry="breakout", regime=regime
        ),
        "frozen": EngineConfig(
            rule, watch_days=20, min_volume_ratio=1.5, entry="cross_5", regime=regime
        ),  # fmt: skip
        "froz-reg": EngineConfig(rule, watch_days=20, min_volume_ratio=1.5, entry="cross_5"),
    }


def fmt(x: float | int | None, spec: str) -> str:
    return "—" if x is None else format(x, spec)


def by_year(trades: Sequence[Trade]) -> dict[int, dict[str, Any]]:
    out: dict[int, list[Trade]] = defaultdict(list)
    for t in trades:
        out[t.entry_day.year].append(t)
    return {y: trade_stats(ts) for y, ts in sorted(out.items())}


def drop_one_year(trades: Sequence[Trade]) -> tuple[float | None, float | None]:
    """Lowest and highest every-trade profit factor with any one entry year left out."""
    years = sorted({t.entry_day.year for t in trades})
    pfs = [trade_stats([t for t in trades if t.entry_day.year != y])["profit_factor"]
           for y in years]  # fmt: skip
    vals = [float(p) for p in pfs if p is not None]
    return (min(vals), max(vals)) if vals else (None, None)


def main() -> None:
    con = duckdb.connect(str(RESEARCH), read_only=True)
    signals, info = load(con)
    con.close()
    bars = info["bars"]
    print(f"VCP v1 vs v2 ({VCP2_VERSION}, config {info['v2_hash'][:12]}); signals "
          f"{FIRST}..{SIG_END}, prices to {DATA_END} (discovery only)")  # fmt: skip
    print(
        f"v1 eligible setups {info['v1_eligible']}, with Trend Template {info['v1_tt']}; "
        f"v2.3 trending candidate-days {info['v2_candidate_days']} over "
        f"{info['scan_days']} sessions ({info['weekly_days']} v1 weekly scan days)\n"
    )
    head = (f"{'system':7s} {'detector':12s} {'signals':>7s} {'trades':>6s} {'win%':>5s} "
            f"{'avg%':>6s} {'med%':>6s} {'PF':>5s} {'PF drop-1yr':>12s} | {'port ret%':>9s} "
            f"{'CAGR%':>6s} {'maxDD%':>7s} {'Sharpe':>6s} {'expo':>5s} {'skip':>5s}")  # fmt: skip
    print(head)
    print("-" * len(head))
    rows_out: list[list[Any]] = []
    yearly: dict[str, dict[int, dict[str, Any]]] = {}
    for sname, ecfg in systems(info["regime"]).items():
        for dname, sigs in signals.items():
            trades, _ = run_signals(sigs, bars, ecfg)
            port = run_portfolio(trades, bars, ecfg)
            ts, ps = trade_stats(trades), equity_stats(port.equity)
            lo, hi = drop_one_year(trades)
            print(f"{sname:7s} {dname:12s} {len(sigs):7d} {ts['trades']:6d} "
                  f"{fmt(ts['win_rate'] and ts['win_rate'] * 100, '5.1f')} "
                  f"{fmt(ts['avg_ret'], '6.2f')} {fmt(ts['median_ret'], '6.2f')} "
                  f"{fmt(ts['profit_factor'], '5.2f')} "
                  f"{fmt(lo, '5.2f') + '–' + fmt(hi, '.2f'):>12s} | "
                  f"{fmt(ps['total_ret'], '9.1f')} {fmt(ps['cagr'], '6.1f')} "
                  f"{fmt(ps['max_drawdown'], '7.1f')} {fmt(ps['sharpe'], '6.2f')} "
                  f"{port.avg_exposure * 100:5.0f} {port.skipped:5d}")  # fmt: skip
            yearly[f"{sname} · {dname}"] = by_year(trades)
            rows_out += [[sname, dname, t.instrument_id, t.scan_date, t.entry_day, t.entry,
                          t.exit_day, t.exit, t.exit_kind, round(t.ret_pct, 4)]
                         for t in trades]  # fmt: skip
    print("\nEvery-trade results by entry year: trades / PF / average %")
    years = sorted({y for v in yearly.values() for y in v})
    print(f"{'':24s}" + "".join(f"{y:>20d}" for y in years))
    for name, ys in yearly.items():
        cells = [
            f"{ys[y]['trades']:4d} / {fmt(ys[y]['profit_factor'], '5.2f')} / "
            f"{fmt(ys[y]['avg_ret'], '+5.2f')}"
            if y in ys
            else "—"
            for y in years
        ]
        print(f"{name:24s}" + "".join(f"{c:>20s}" for c in cells))
    with OUT.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["system", "detector", "instrument_id", "scan_date", "entry_day", "entry",
                    "exit_day", "exit", "exit_kind", "ret_pct"])  # fmt: skip
        w.writerows(rows_out)
    print(f"\nTrades: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
