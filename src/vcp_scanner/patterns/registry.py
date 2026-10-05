"""Strategy registry (STRATEGY_SPECIFICATION 3.1).

One entry per strategy: its id, the algorithm version the code implements, its tiers on the
common grade scale (section 6.3), the lowest ranked grade, and its pattern-score part (section
9.2). VCP is strategy #1; its detector keeps its own scan path (``vcp compute vcp``), so its
stored results are unchanged. New strategies (steps 4-6) add an entry here and a file under
``config/strategies/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from vcp_scanner.config.models import StrategyFileConfig
from vcp_scanner.config.strategies import load_strategy_files
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.domain.strategy import VCP_GRADES, VCP_STRATEGY_ID
from vcp_scanner.scoring.engine import VCP_PATTERN_SCORING, PatternScoring
from vcp_scanner.versioning import VCP_ALGORITHM_VERSION


@dataclass(frozen=True, slots=True)
class StrategyEntry:
    strategy_id: str
    algorithm_version: str
    grades: Mapping[str, int]  # tier name -> grade 0..3, lowest tier first
    min_grade: int  # lowest grade that is ranked
    scoring: PatternScoring

    def grade(self, classification: str | None) -> int:
        return self.grades.get(classification or "", 0)

    @property
    def tiers(self) -> tuple[str, ...]:
        return tuple(self.grades)


REGISTRY: dict[str, StrategyEntry] = {
    VCP_STRATEGY_ID: StrategyEntry(
        strategy_id=VCP_STRATEGY_ID,
        algorithm_version=VCP_ALGORITHM_VERSION,
        grades=VCP_GRADES,
        min_grade=1,
        scoring=VCP_PATTERN_SCORING,
    ),
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
    return load_strategy_files(config_dir, registered_versions())
