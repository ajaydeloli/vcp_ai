"""Strategy registry (STRATEGY_SPECIFICATION 3.1).

One entry per strategy: its id, the algorithm version the code implements, and how to build its
runtime from its config file (``config/strategies/<id>.yaml``). VCP is the only strategy; it
keeps its own scan path (``vcp compute vcp``) and its thresholds in ``strategy.yaml`` /
``scoring.yaml`` (decision O3).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from vcp_scanner.config.models import StrategyFileConfig
from vcp_scanner.config.strategies import load_strategy_files
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.domain.strategy import VCP_GRADES, VCP_STRATEGY_ID
from vcp_scanner.scoring.engine import VCP_PATTERN_SCORING, PatternScoring
from vcp_scanner.versioning import VCP_ALGORITHM_VERSION


@dataclass(frozen=True, slots=True)
class StrategyRuntime:
    """A strategy as configured: what scans, scores and backtests need."""

    strategy_id: str
    algorithm_version: str
    grades: Mapping[str, int]  # tier name -> grade 0..3, lowest tier first
    min_grade: int  # lowest grade that is ranked
    scoring: PatternScoring
    file: StrategyFileConfig

    def grade(self, classification: str | None) -> int:
        return self.grades.get(classification or "", 0)

    @property
    def tiers(self) -> tuple[str, ...]:
        return tuple(self.grades)


@dataclass(frozen=True, slots=True)
class StrategyEntry:
    strategy_id: str
    algorithm_version: str
    build: Callable[[StrategyFileConfig], StrategyRuntime]


def _vcp(file: StrategyFileConfig) -> StrategyRuntime:
    return StrategyRuntime(VCP_STRATEGY_ID, VCP_ALGORITHM_VERSION, VCP_GRADES, 1,
                           VCP_PATTERN_SCORING, file)  # fmt: skip


REGISTRY: dict[str, StrategyEntry] = {
    VCP_STRATEGY_ID: StrategyEntry(VCP_STRATEGY_ID, VCP_ALGORITHM_VERSION, _vcp),
}


def registered_versions() -> dict[str, str]:
    return {k: e.algorithm_version for k, e in REGISTRY.items()}


def get_strategy(strategy_id: str) -> StrategyEntry:
    entry = REGISTRY.get(strategy_id)
    if entry is None:
        raise ConfigError(
            f"Unknown strategy {strategy_id!r}; registered: {', '.join(sorted(REGISTRY))}"
        )
    return entry


def load_strategies(config_dir: str | Path) -> dict[str, StrategyFileConfig]:
    """The strategy files of ``config_dir``, checked against this registry."""
    files = load_strategy_files(config_dir, registered_versions())
    for f in files.values():
        REGISTRY[f.strategy_id].build(f)
    return files


def load_runtime(config_dir: str | Path, strategy_id: str) -> StrategyRuntime:
    """The configured runtime of one strategy; refused when it is unknown or has no file."""
    entry = get_strategy(strategy_id)
    files = load_strategies(config_dir)
    if strategy_id not in files:
        raise ConfigError(
            f"Strategy {strategy_id} has no file config/strategies/{strategy_id}.yaml"
        )
    return entry.build(files[strategy_id])
