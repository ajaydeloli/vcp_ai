"""``vcp paper update|status``: the paper ledger (STRATEGY_SPECIFICATION 21.3; monitoring M3)."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from vcp_scanner.cli_pipeline import _err, _open_store


def add_paper_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("paper", help="Paper ledger of the frozen strategies")
    sub = p.add_subparsers(dest="paper_command")
    for name, text in (("update", "Append the latest sessions' paper decisions"),
                       ("status", "Open paper positions and results so far")):  # fmt: skip
        s = sub.add_parser(name, help=text)
        s.add_argument("--strategy", default=None, help="One strategy (default: every paper one)")
        s.add_argument("--db", default="data/vcp_scanner.duckdb")
        s.add_argument("--config-dir", default="config")
        if name == "update":
            s.add_argument(
                "--through",
                default=None,
                metavar="YYYY-MM-DD",
                help="Last session to write (default: the latest scanned one)",
            )


def run_paper(args: argparse.Namespace) -> int:
    cmd = getattr(args, "paper_command", None)
    if cmd == "update":
        return _update(args)
    if cmd == "status":
        return _status(args)
    _err("Usage: vcp paper {update,status} ...")
    return 1


def _paper_strategies(config_dir: str, only: str | None) -> list[str]:
    from vcp_scanner.patterns.registry import REGISTRY, load_strategies

    files = load_strategies(config_dir)
    ids = [s for s in REGISTRY if s in files and files[s].stage in ("paper", "live")]
    if only is not None:
        if only not in ids:
            raise ValueError(f"{only} is not a paper strategy; paper strategies: {ids}")
        return [only]
    return ids


def _context(config_dir: str) -> dict[str, Any]:
    """Frozen rules and dates shared by every strategy's update."""
    from vcp_scanner.backtest.periods import load_backtest_config

    bcfg = load_backtest_config(config_dir)
    start = next(p.start for p in bcfg.periods if p.role == "test")
    return {"defaults": bcfg.defaults, "start": start}


def _update(args: argparse.Namespace) -> int:  # noqa: C901, PLR0915
    from vcp_scanner.backtest.engine import EngineConfig
    from vcp_scanner.backtest.regime import regime_by_day
    from vcp_scanner.cli_backtest import _strategy_hash
    from vcp_scanner.config import load_scanner_config
    from vcp_scanner.config.loader import scan_config_hash
    from vcp_scanner.data.repositories.duckdb_backtest_repository import (
        DuckDBBacktestRepository,
    )
    from vcp_scanner.data.repositories.duckdb_paper_repository import DuckDBPaperRepository
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION
    from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
    from vcp_scanner.paper.ledger import RULE_SET, derive_events, plan_update
    from vcp_scanner.patterns.registry import load_runtime
    from vcp_scanner.research.outcomes import ENGINE_RULES
    from vcp_scanner.versioning import code_state

    try:
        cfg = load_scanner_config(args.config_dir)
        strategies = _paper_strategies(args.config_dir, args.strategy)
    except Exception as e:
        _err(f"Config error: {e}")
        return 1
    commit, dirty = code_state()
    code = f"{commit}{' (dirty)' if dirty else ''}" if commit else None
    failed = False
    with _open_store(args.db) as store:
        ctx = _context(args.config_dir)
        d = ctx["defaults"]
        start: date = ctx["start"]
        latest = store.conn.execute(
            "SELECT max(as_of_date) FROM setup_scores WHERE strategy_id = 'vcp'"
            " AND config_hash = ?", [scan_config_hash(cfg)],
        ).fetchone()[0]  # type: ignore[index]  # fmt: skip
        through = date.fromisoformat(args.through) if args.through else latest
        if through is None or through < start:
            print(f"Paper ledger: nothing to do (latest scanned session {latest}, paper starts "
                  f"{start})")  # fmt: skip
            return 0
        repo = DuckDBBacktestRepository(store, LIVE_SNAPSHOT_ID)
        rows = repo.breadth(start, through, scan_config_hash(cfg), FEATURES_CALCULATION_VERSION)
        days = [r.day for r in rows if start <= r.day <= through]
        regime = regime_by_day(d.regime, rows)
        rule = next(r for r in ENGINE_RULES if r.name == d.rule)
        ecfg = EngineConfig(rule, min_volume_ratio=cfg.strategy.vcp.breakout.min_volume_ratio,
                            entry=d.entry, regime=regime)  # fmt: skip
        paper = DuckDBPaperRepository(store)
        print(f"Paper ledger {RULE_SET} ({d.entry}, {d.regime}, {d.rule}; {ecfg.max_positions} "
              f"positions) from {start} through {through}")  # fmt: skip
        for sid in strategies:
            try:
                rt = load_runtime(args.config_dir, sid)
                chash = _strategy_hash(cfg, sid, args.config_dir)
                classes = [t for t in rt.tiers if rt.grade(t) >= rt.min_grade]
                signals = repo.signals(start - timedelta(days=7), through, chash,
                                       classes, None, eligible_only=True,
                                       strategy_id=sid)  # fmt: skip
                bars = repo.bars(sorted({s.instrument_id for s in signals}), start, through, 0)
                derived = derive_events(sid, signals, bars, ecfg, start, through, days)
                stored = paper.events(RULE_SET, sid, chash)
                plan = plan_update(sid, derived, stored, through)
                extra = [plan.divergence] if plan.divergence else []
                n = paper.append(RULE_SET, chash, [*plan.new, *extra], code)
            except Exception as exc:  # one strategy failing must not stop the others
                _err(f"  {sid}: ERROR {exc}")
                failed = True
                continue
            kinds: dict[str, int] = defaultdict(int)
            for ev in plan.new:
                kinds[ev.event_type] += 1
            shown = ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()) if k != "DAY_CLOSED")
            note = " DIVERGENCE recorded (see `vcp paper status`)" if plan.divergence else ""
            print(f"  {sid:18} {n} rows appended (sessions after {plan.last_closed or '-'}: "
                  f"{kinds.get('DAY_CLOSED', 0)}; {shown or 'no trades'}){note}")  # fmt: skip
    return 1 if failed else 0


def _status(args: argparse.Namespace) -> int:
    from vcp_scanner.cli_backtest import _strategy_hash
    from vcp_scanner.config import load_scanner_config
    from vcp_scanner.data.repositories.duckdb_paper_repository import DuckDBPaperRepository
    from vcp_scanner.paper.ledger import RULE_SET
    from vcp_scanner.paper.status import summarize

    try:
        cfg = load_scanner_config(args.config_dir)
        strategies = _paper_strategies(args.config_dir, args.strategy)
    except Exception as e:
        _err(f"Config error: {e}")
        return 1
    with _open_store(args.db) as store:
        paper = DuckDBPaperRepository(store)
        closes = dict(store.conn.execute(
            "SELECT instrument_id, arg_max(close_adj, trade_date) FROM"
            " daily_prices_adjusted_current WHERE computed_from_snapshot_id = 'LIVE'"
            " GROUP BY 1").fetchall())  # fmt: skip
        for sid in strategies:
            ev = paper.events(RULE_SET, sid, _strategy_hash(cfg, sid, args.config_dir))
            s = summarize(ev, closes)
            print(
                f"[{sid}] ledger through {s.through or '-'}; closed trades {s.closed}, win "
                f"{s.win_rate:.1f}%, avg {s.avg_ret:+.2f}%, PF {s.profit_factor_text}; open "
                f"{len(s.open)}; skipped (no slot) {s.skipped}; divergences {s.divergences}"
            )
            for o in s.open:
                print(f"    open {o.instrument_id:22} since {o.entry_day} at {o.entry:.2f}, stop "
                      f"{o.stop if o.stop is None else round(o.stop, 2)}, last {o.last}, "
                      f"{o.open_pct:+.2f}%")  # fmt: skip
    return 0
