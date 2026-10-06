"""What the API needs from the configuration: the paper strategies, their config hashes and
tiers. Read once at start-up (the strategies are frozen, STRATEGY_SPECIFICATION 21.1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from vcp_scanner.backtest.periods import load_backtest_config
from vcp_scanner.config import load_scanner_config
from vcp_scanner.config.loader import scan_config_hash
from vcp_scanner.config.strategies import strategy_config_hash
from vcp_scanner.patterns.registry import REGISTRY, load_strategies

#: Review criteria (STRATEGY_SPECIFICATION 21.6).
MIN_CLOSED_TRADES = 30
MIN_PROFIT_FACTOR = 1.3
MAX_DRAWDOWN_PCT = -20.0


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    algorithm_version: str
    config_hash: str
    stage: str
    min_grade: int
    grades: dict[str, int]  # tier name -> grade, lowest first


@dataclass(frozen=True, slots=True)
class Context:
    scan_hash: str  # the Trend Template / universe scan config hash
    regime_rule: str  # the frozen paper regime (config/backtest.yaml defaults)
    strategies: tuple[StrategySpec, ...]
    data_dir: Path  # the main database's folder (logs, backups)

    def strategy(self, strategy_id: str) -> StrategySpec | None:
        return next((s for s in self.strategies if s.strategy_id == strategy_id), None)


def build_context(config_dir: str | Path, data_dir: str | Path) -> Context:
    cfg = load_scanner_config(str(config_dir))
    files = load_strategies(config_dir)
    specs = []
    for sid, entry in REGISTRY.items():
        file = files.get(sid)
        if file is None or file.stage not in ("paper", "live"):
            continue
        rt = entry.build(file)
        specs.append(
            StrategySpec(
                sid, rt.algorithm_version, strategy_config_hash(cfg, file), file.stage,
                rt.min_grade, dict(rt.grades),
            )
        )  # fmt: skip
    bcfg = load_backtest_config(str(config_dir))
    regime = bcfg.defaults.regime if bcfg.defaults.regime != "none" else "breadth50"
    return Context(scan_config_hash(cfg), regime, tuple(specs), Path(data_dir))
