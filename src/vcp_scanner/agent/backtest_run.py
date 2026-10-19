"""``run_backtest`` (AGENT_SPECIFICATION §3 tier 1, step A2): the existing backtester
(``vcp backtest run``) on the research database, with checked settings and a time limit.

It runs as a separate process on the research database only (``--db``), so it can never write
to the scanner's database or the frozen paper ledger; the run is stored with the other
backtests (``backtest_runs``) under the period name ``agent-H<id>-<window>``.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

TIMEOUT_SECONDS = 900
ENTRIES = ("breakout", "cross_5")


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    strategy: str
    start: date
    end: date
    period: str
    rule: str | None = None
    classes: tuple[str, ...] = ()
    min_score: float | None = None
    entry: str | None = None
    regime: str | None = None
    max_positions: int | None = None
    cost_bps: float | None = None


def validate(req: BacktestRequest, config_dir: str) -> None:
    """ValueError naming the setting the backtester would refuse (checked before it starts)."""
    from vcp_scanner.backtest.regime import REGIMES
    from vcp_scanner.patterns.registry import load_runtime
    from vcp_scanner.research.outcomes import ENGINE_RULES

    rules = sorted(r.name for r in ENGINE_RULES)
    if req.rule is not None and req.rule not in rules:
        raise ValueError(f"rule must be one of {', '.join(rules)}")
    if req.entry is not None and req.entry not in ENTRIES:
        raise ValueError(f"entry must be one of {', '.join(ENTRIES)}")
    if req.regime is not None and req.regime not in REGIMES:
        raise ValueError(f"regime must be one of {', '.join(REGIMES)}")
    if req.min_score is not None and not 0 <= req.min_score <= 100:
        raise ValueError("min_score must be 0..100")
    if req.max_positions is not None and not 1 <= req.max_positions <= 50:
        raise ValueError("max_positions must be 1..50")
    if req.cost_bps is not None and not 0 <= req.cost_bps <= 100:
        raise ValueError("cost_bps must be 0..100")
    entry = load_runtime(config_dir, req.strategy)
    unknown = [c for c in req.classes if c not in entry.grades]
    if unknown:
        raise ValueError(f"unknown classes {', '.join(unknown)}; tiers: {', '.join(entry.tiers)}")


def command(req: BacktestRequest, research_db: Path, config_dir: str) -> list[str]:
    exe = Path(sys.executable).parent / "vcp"
    cmd = [str(exe) if exe.exists() else "vcp", "backtest", "run", "--db", str(research_db),
           "--config-dir", config_dir, "--from", req.start.isoformat(), "--to",
           req.end.isoformat(), "--period", req.period, "--strategy", req.strategy]  # fmt: skip
    for flag, value in (("--rule", req.rule), ("--min-score", req.min_score),
                        ("--entry", req.entry), ("--regime", req.regime),
                        ("--max-positions", req.max_positions),
                        ("--cost-bps", req.cost_bps)):  # fmt: skip
        if value is not None:
            cmd += [flag, str(value)]
    if req.classes:
        cmd += ["--classes", ",".join(req.classes)]
    return cmd


@dataclass(frozen=True, slots=True)
class BacktestOutcome:
    ok: bool
    backtest_id: str | None
    summary_lines: list[str]
    error: str | None


def run(req: BacktestRequest, research_db: Path, config_dir: str, cwd: Path,
        timeout: int = TIMEOUT_SECONDS) -> BacktestOutcome:  # fmt: skip
    if not research_db.exists():
        return BacktestOutcome(False, None, [], f"research database not found: {research_db}")
    try:
        done = subprocess.run(command(req, research_db, config_dir), cwd=cwd,
                              capture_output=True, text=True, timeout=timeout)  # fmt: skip
    except subprocess.TimeoutExpired:
        return BacktestOutcome(False, None, [], f"stopped after {timeout} s (time limit)")
    lines = [ln.strip() for ln in done.stdout.splitlines()
             if ln.strip() and " INFO " not in ln and " DEBUG " not in ln]  # fmt: skip
    m = re.search(r"Backtest (bt-\S+)", done.stdout)
    if done.returncode != 0 or m is None:
        err = (done.stderr.strip().splitlines() or lines or ["no output"])[-1]
        if "lock" in err.lower():
            err += " (the research database is busy; try again in a minute)"
        return BacktestOutcome(False, None, lines[-8:], err[:500])
    return BacktestOutcome(True, m.group(1), lines[-8:], None)
