"""``vcp backtest run`` / ``vcp backtest walk-forward`` (Phase 9 steps 3-4).

One strategy per run (``--strategy``, default ``vcp``; STRATEGY_SPECIFICATION 10.3). Signals are
the eligible setups of that strategy's stored score scans (current config) in a date range; the
engine and its rules are described in ``backtest/engine.py``, the periods in
``backtest/periods.py``. Every run, its settings, summary numbers and events are stored
(``backtest_runs`` / ``backtest_events``).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from vcp_scanner.cli_pipeline import _err, _open_store, _resolve_data_snapshot
from vcp_scanner.config import load_scanner_config
from vcp_scanner.config.loader import scan_config_hash

CLASSES = ("VCP_LIKE", "VCP", "A_PLUS_VCP")


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--strategy", default="vcp", help="Strategy to replay (default: vcp)")
    p.add_argument("--rule", default=None,
                   help="Exit rule (default: config/backtest.yaml defaults.rule)")  # fmt: skip
    p.add_argument("--classes", default=None,
                   help="Classifications to trade, the strategy's own tier names "
                   "(comma-separated; default: its ranked tiers, for VCP "
                   f"{','.join(CLASSES)})")  # fmt: skip
    p.add_argument("--min-score", type=float, default=None)
    p.add_argument("--entry", choices=["breakout", "cross_5"], default=None,
                   help="Entry rule: breakout or cross_5 (a real cross of the pivot, close at "
                   "most 5%% above it; STRATEGY_SPECIFICATION 20.2). Default: "
                   "config/backtest.yaml defaults.entry")  # fmt: skip
    p.add_argument("--regime", choices=["none", "breadth50", "ew50"], default=None,
                   help="Market regime gate for new entries (20.3). Default: "
                   "config/backtest.yaml defaults.regime")  # fmt: skip
    p.add_argument("--baseline", action="store_true",
                   help="Trade every passer with a pivot (any class or status): the comparison "
                   "baseline for the VCP classes")  # fmt: skip
    p.add_argument("--watch-days", type=int, default=20)
    p.add_argument("--max-positions", type=int, default=10)
    p.add_argument("--cost-bps", type=float, default=15.0, help="Cost per side, basis points")
    p.add_argument("--db", default="data/vcp_scanner.duckdb")
    p.add_argument("--config-dir", default="config")


def add_backtest_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    bt = subparsers.add_parser("backtest", help="Event-engine backtests over stored scans")
    sub = bt.add_subparsers(dest="backtest_command")
    r = sub.add_parser("run", help="Replay the eligible setups of a date range")
    r.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD")
    r.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD")
    r.add_argument("--period", default=None, help="Name stored with the run")
    _common(r)
    w = sub.add_parser("walk-forward", help="Run each period of config/backtest.yaml")
    w.add_argument("--validation", action="store_true",
                   help="Also show the validation period (looks are logged)")  # fmt: skip
    w.add_argument("--test", action="store_true",
                   help="Also show the test period (live paper; needs enough data)")  # fmt: skip
    _common(w)
    br = sub.add_parser(
        "bias-report", help="Survivorship, corporate-action and period-look exposure"
    )
    br.add_argument("--from", dest="date_from", required=True, metavar="YYYY-MM-DD")
    br.add_argument("--to", dest="date_to", required=True, metavar="YYYY-MM-DD")
    br.add_argument("--db", default="data/vcp_scanner.duckdb")
    br.add_argument("--config-dir", default="config")
    la = sub.add_parser(
        "lookahead-check", help="Rebuild a stored scan from a copy without later data; compare"
    )
    la.add_argument("--as-of", required=True, metavar="YYYY-MM-DD")
    la.add_argument("--variant", choices=["prices", "corporate-actions"], default="prices")
    la.add_argument("--work-dir", default="data/tmp", help="Where the temporary copy goes")
    la.add_argument("--db", default="data/vcp_scanner.duckdb")
    la.add_argument("--config-dir", default="config")


def run_backtest(args: argparse.Namespace) -> int:
    cmd = getattr(args, "backtest_command", None)
    if cmd == "run":
        return _run(args)
    if cmd == "walk-forward":
        return _walk_forward(args)
    if cmd == "lookahead-check":
        return _lookahead(args)
    if cmd == "bias-report":
        return _bias(args)
    _err("Usage: vcp backtest {run,walk-forward,lookahead-check,bias-report} ...")
    return 1


def _settings(args: argparse.Namespace) -> dict[str, Any] | None:
    from vcp_scanner.backtest.periods import load_backtest_config
    from vcp_scanner.patterns.registry import load_runtime
    from vcp_scanner.research.outcomes import ENGINE_RULES

    try:
        defaults = load_backtest_config(args.config_dir).defaults
    except Exception as e:
        _err(f"config/backtest.yaml error: {e}")
        return None
    rules = {r.name for r in ENGINE_RULES}
    rule = args.rule or defaults.rule
    if rule not in rules:
        _err(f"Unknown rule {rule}; rules: {', '.join(sorted(rules))}")
        return None
    try:
        entry = load_runtime(args.config_dir, args.strategy)
    except Exception as e:
        _err(f"Strategy error: {e}")
        return None
    if args.classes is None:
        classes = [t for t in entry.tiers if entry.grade(t) >= entry.min_grade]
    else:
        classes = [c.strip() for c in args.classes.split(",") if c.strip()]
        unknown = [c for c in classes if c not in entry.grades]
        if unknown:
            _err(f"Unknown classes for {entry.strategy_id}: {', '.join(unknown)}; "
                 f"tiers: {', '.join(entry.tiers)}")  # fmt: skip
            return None
    return {"strategy": entry.strategy_id, "config_dir": args.config_dir, "rule": rule,
            "classes": classes,
            "min_score": args.min_score, "watch_days": args.watch_days,
            "max_positions": args.max_positions, "cost_bps": args.cost_bps,
            "baseline": args.baseline, "entry": args.entry or defaults.entry,
            "regime": args.regime or defaults.regime}  # fmt: skip


def _strategy_hash(cfg: Any, strategy_id: str, config_dir: str) -> str:
    from vcp_scanner.config.strategies import strategy_config_hash
    from vcp_scanner.patterns.registry import load_strategies

    return strategy_config_hash(cfg, load_strategies(config_dir)[strategy_id])


def _execute(
    store: Any, cfg: Any, start: date, end: date, settings: dict[str, Any], period: str | None,
    config_hash: str,
) -> dict[str, Any] | None:  # fmt: skip
    """One backtest over [start, end]; stores it and returns its summary (None: no signals)."""
    from vcp_scanner.backtest.engine import EngineConfig, run_portfolio, run_signals
    from vcp_scanner.backtest.metrics import equity_stats, trade_stats
    from vcp_scanner.backtest.regime import regime_by_day
    from vcp_scanner.data.repositories.duckdb_backtest_repository import (
        DuckDBBacktestRepository,
    )
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
    from vcp_scanner.patterns.registry import load_runtime
    from vcp_scanner.research.outcomes import ENGINE_RULES
    from vcp_scanner.versioning import STRATEGY_VERSION, code_state

    started = datetime.now(UTC)
    entry = load_runtime(settings["config_dir"], settings["strategy"])
    rule = next(r for r in ENGINE_RULES if r.name == settings["rule"])
    snapshot = _resolve_data_snapshot(store, None)
    if snapshot is None:
        return None
    repo = DuckDBBacktestRepository(store, snapshot)
    regime_kind = settings.get("regime", "none")
    regime = None
    if regime_kind != "none":  # up to the last watch day of the last signals
        rows = repo.breadth(start, end + timedelta(days=45), scan_config_hash(cfg),
                            FEATURES_CALCULATION_VERSION)  # fmt: skip
        regime = regime_by_day(regime_kind, rows)
    ecfg = EngineConfig(
        rule, watch_days=settings["watch_days"],
        min_volume_ratio=cfg.strategy.vcp.breakout.min_volume_ratio,
        cost_bps=settings["cost_bps"], max_positions=settings["max_positions"],
        entry=settings.get("entry", "breakout"), regime=regime,
    )  # fmt: skip
    classes = settings["classes"]
    if settings["baseline"]:
        classes = list(entry.tiers)  # every tier (VCP: NONE, VCP_LIKE, VCP, A_PLUS_VCP)
    signals = repo.signals(start, end, config_hash, classes, settings["min_score"],
                           eligible_only=not settings["baseline"],
                           strategy_id=entry.strategy_id)  # fmt: skip
    if not signals:
        return None
    scan_dates = sorted({s.scan_date for s in signals})
    horizon = rule.horizon or ecfg.horizon  # 60 sessions ~ 130 calendar days after ``end``
    after = 130 if horizon <= ecfg.horizon else 130 + 2 * (horizon - ecfg.horizon)
    bars = repo.bars(sorted({s.instrument_id for s in signals}), start, end, after)
    trades, events = run_signals(signals, bars, ecfg)
    port = run_portfolio(trades, bars, ecfg)
    port_stats: dict[str, Any] = {**trade_stats(port.taken), **equity_stats(port.equity)}
    port_stats.update(skipped=port.skipped, avg_exposure=port.avg_exposure)
    surv = store.conn.execute(
        "SELECT string_agg(DISTINCT survivorship_status, ',') FROM scan_runs"
        " WHERE scan_type = 'TREND_TEMPLATE' AND as_of_date BETWEEN ? AND ?", [start, end]
    ).fetchone()  # fmt: skip
    commit, dirty = code_state()
    out = {"backtest_id": f"bt-{started:%Y%m%dT%H%M%S%f}Z", "start": start, "end": end,
           "period": period, "signals": len(signals), "scan_dates": len(scan_dates),
           "every_trade": trade_stats(trades), "portfolio": port_stats,
           "survivorship": surv[0] if surv else None}  # fmt: skip
    repo.save({
        "backtest_id": out["backtest_id"], "strategy_id": entry.strategy_id,
        "algorithm_version": entry.algorithm_version, "started_at": started,
        "completed_at": datetime.now(UTC), "start_date": start, "end_date": end,
        "universe_definition": f"stored {entry.strategy_id} score scans {config_hash[:12]}; "
                               f"{len(scan_dates)} scan dates",
        "strategy_version": STRATEGY_VERSION, "config_hash": config_hash,
        "data_snapshot_id": snapshot, "execution_model": "close-of-breakout-day",
        "research_mode": True, "survivorship_status": out["survivorship"],
        "status": "COMPLETED", "period_name": period,
        "settings_json": json.dumps({**{k: v for k, v in settings.items() if k != "config_dir"},
                                     "horizon": ecfg.horizon,
                                     "min_volume_ratio": ecfg.min_volume_ratio}, sort_keys=True),
        "metrics_json": json.dumps({"every_trade": out["every_trade"], "portfolio": port_stats},
                                   sort_keys=True, default=str),
        "code_commit": f"{commit}{' (dirty)' if dirty else ''}" if commit else None,
    }, events)  # fmt: skip
    return out


def _f(x: Any, scale: float = 1.0, d: int = 2) -> str:
    return "-" if x is None else f"{x * scale:.{d}f}"


def _print(o: dict[str, Any], settings: dict[str, Any]) -> None:
    name = f" ({o['period']})" if o["period"] else ""
    print(f"Backtest {o['backtest_id']} [{settings['strategy']}]: {o['start']} .. {o['end']}"
          f"{name}")  # fmt: skip
    kind = "setups (baseline: every passer with a pivot)" if settings["baseline"] else (
        "eligible setups")  # fmt: skip
    print(f"  Signals     : {o['signals']} {kind} on {o['scan_dates']} scan dates; "
          f"rule {settings['rule']}; entry {settings.get('entry', 'breakout')}; regime "
          f"{settings.get('regime', 'none')}; costs {settings['cost_bps']:g} bps "
          "per side")  # fmt: skip
    s = o["every_trade"]
    print(f"  Every trade : {s['trades']} trades, win {_f(s['win_rate'], 100, 1)}%, avg "
          f"{_f(s['avg_ret'])}%, median {_f(s['median_ret'])}%, profit factor "
          f"{_f(s['profit_factor'])}, avg hold {_f(s['avg_hold_days'], d=0)} days")  # fmt: skip
    p = o["portfolio"]
    print(f"  Portfolio   : max {settings['max_positions']} positions; {p['trades']} taken, "
          f"{p['skipped']} skipped; total {_f(p['total_ret'], d=1)}%, CAGR "
          f"{_f(p['cagr'], d=1)}%, max drawdown {_f(p['max_drawdown'], d=1)}%, Sharpe "
          f"{_f(p['sharpe'])}, exposure {_f(p['avg_exposure'], 100, 0)}%")  # fmt: skip
    print(f"  Survivorship: {o['survivorship'] or 'unknown'}")


def _run(args: argparse.Namespace) -> int:
    settings = _settings(args)
    if settings is None:
        return 1
    cfg = load_scanner_config(args.config_dir)
    config_hash = _strategy_hash(cfg, settings["strategy"], args.config_dir)
    start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    with _open_store(args.db) as store:
        o = _execute(store, cfg, start, end, settings, args.period, config_hash)
    if o is None:
        _err(f"No eligible scored setups between {start} and {end} for the current config. Run "
             "the scans (`vcp compute scores`) for those dates first.")  # fmt: skip
        return 1
    _print(o, settings)
    return 0


def _walk_forward(args: argparse.Namespace) -> int:
    from vcp_scanner.backtest.periods import load_backtest_config, months_between

    settings = _settings(args)
    if settings is None:
        return 1
    cfg = load_scanner_config(args.config_dir)
    bcfg = load_backtest_config(args.config_dir)
    strategy_id = settings["strategy"]
    config_hash = _strategy_hash(cfg, strategy_id, args.config_dir)
    with _open_store(args.db) as store:
        latest = store.conn.execute(
            "SELECT max(as_of_date) FROM setup_scores WHERE config_hash = ? AND strategy_id = ?",
            [config_hash, strategy_id],
        ).fetchone()[0]  # type: ignore[index]
        if latest is None:
            _err("No stored score scans for the current config.")
            return 1
        for p in bcfg.periods:
            end = min(p.end or latest, latest)
            if p.role == "validation" and not args.validation:
                print(f"[{p.name}] hidden: add --validation to look (looks are logged)")
                continue
            if p.role == "test":
                months = months_between(p.start, latest) if latest >= p.start else 0
                if not args.test or months < bcfg.min_test_months:
                    print(f"[{p.name}] hidden: test period ({months} of {bcfg.min_test_months} "
                          "months of data; needs --test)")  # fmt: skip
                    continue
            if p.role == "validation":
                # Looks are counted per strategy (STRATEGY_SPECIFICATION 10.4).
                looks = store.conn.execute(
                    "SELECT count(*) FROM backtest_runs WHERE period_name = ? AND strategy_id = ?",
                    [p.name, strategy_id],
                ).fetchone()[0]  # type: ignore[index]
                print(f"[{p.name}] {strategy_id} validation look number {looks + 1}")
            o = _execute(store, cfg, p.start, end, settings, p.name, config_hash)
            if o is None:
                print(f"[{p.name}] no eligible scored setups between {p.start} and {end}")
                continue
            _print(o, settings)
    return 0


def _lookahead(args: argparse.Namespace) -> int:
    from pathlib import Path

    from vcp_scanner.backtest.lookahead import run_check
    from vcp_scanner.cli import main as cli_main

    cfg = load_scanner_config(args.config_dir)
    as_of = date.fromisoformat(args.as_of)
    diffs = run_check(Path(args.db), as_of, args.variant, Path(args.work_dir), args.config_dir,
                      scan_config_hash(cfg)[:12], cli_main)  # fmt: skip
    print(f"Look-ahead check {as_of} ({args.variant}): stored scan vs rebuilt without later data")
    for d in diffs:
        print(f"  {d.name:15} {d.compared:5d} compared, {d.differing:4d} differ")
        for e in d.examples:
            print(f"      {e}")
    # Clean: nothing differs, and the scan existed (the Trend Template has rows).
    clean = all(d.differing == 0 for d in diffs) and diffs[0].compared > 0
    print("  Result      : " + ("IDENTICAL" if clean else "DIFFERENCES (see above)"))
    return 0 if clean else 2


def _bias(args: argparse.Namespace) -> int:
    from vcp_scanner.backtest.bias import bias_report

    cfg = load_scanner_config(args.config_dir)
    start, end = date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    with _open_store(args.db) as store:
        r = bias_report(store, start, end, scan_config_hash(cfg))
    print(f"Bias report {start} .. {end} (stored scans of the current config)")
    surv = ", ".join(f"{k} {v}" for k, v in sorted(r["survivorship_dates"].items())) or "no scans"
    print(f"  Survivorship : scan dates by universe status: {surv}")
    print(f"  Corporate actions: {r['ca_exposed']} of {r['observations']} observations "
          f"({_f(r['ca_exposed_share'], 100, 1)}%) have a price adjustment effective later; "
          f"{r['ca_exposed_eligible']} of {r['eligible']} ranked setups")  # fmt: skip
    looks = ", ".join(f"{k} {v}" for k, v in sorted(r["period_looks"].items())) or "none"
    print(f"  Period looks : stored backtest runs by period: {looks}")
    return 0
