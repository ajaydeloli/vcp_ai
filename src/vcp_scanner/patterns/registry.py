"""Strategy registry (STRATEGY_SPECIFICATION 3.1).

One entry per strategy: its id, the algorithm version the code implements, and how to build its
runtime from its config file (``config/strategies/<id>.yaml``): tiers on the common grade scale
(section 6.3), the lowest ranked grade, the pattern-score part (section 9.2) and, for strategies
other than VCP, the detector (section 8). VCP keeps its own scan path (``vcp compute vcp``) and
its thresholds in ``strategy.yaml`` / ``scoring.yaml`` (decision O3).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from vcp_scanner.config.models import (
    FlatBaseSettings,
    StrategyFileConfig,
    ThreeWeeksTightSettings,
)
from vcp_scanner.config.strategies import load_strategy_files
from vcp_scanner.domain.errors import ConfigError
from vcp_scanner.domain.strategy import VCP_GRADES, VCP_STRATEGY_ID
from vcp_scanner.patterns.flat_base import detector as flat_base
from vcp_scanner.patterns.strategy_base import StrategyDetector
from vcp_scanner.patterns.three_weeks_tight import detector as three_weeks_tight
from vcp_scanner.scoring.engine import VCP_PATTERN_SCORING, FilePatternScoring, PatternScoring
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
    detector: StrategyDetector | None = None  # None: VCP (own scan path)
    settings: BaseModel | None = None

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


def _file_strategy(
    settings_model: type[FlatBaseSettings] | type[ThreeWeeksTightSettings],
    detector_cls: type[flat_base.FlatBaseDetector]
    | type[three_weeks_tight.ThreeWeeksTightDetector],
    score_subs: tuple[str, ...],
) -> Callable[[StrategyFileConfig], StrategyRuntime]:
    def build(file: StrategyFileConfig) -> StrategyRuntime:
        raw = {k: v for k in ("detector", "classification", "ranking", "scoring")
               if (v := getattr(file, k)) is not None}  # fmt: skip
        try:
            settings = settings_model(**raw)
            scoring = FilePatternScoring(score_subs, settings.scoring, settings.ranking.min_grade)
        except (ValidationError, ValueError) as err:
            raise ConfigError(f"Invalid settings for strategy {file.strategy_id}:\n{err}") from err
        grades = {"NONE": 0, **{k.upper(): t.grade for k, t in settings.classification.items()}}
        detector = detector_cls(settings)  # type: ignore[arg-type]
        return StrategyRuntime(file.strategy_id, file.algorithm_version, grades,
                               settings.ranking.min_grade, scoring, file, detector,
                               settings)  # fmt: skip

    return build


REGISTRY: dict[str, StrategyEntry] = {
    VCP_STRATEGY_ID: StrategyEntry(VCP_STRATEGY_ID, VCP_ALGORITHM_VERSION, _vcp),
    flat_base.STRATEGY_ID: StrategyEntry(
        flat_base.STRATEGY_ID, flat_base.ALGORITHM_VERSION,
        _file_strategy(FlatBaseSettings, flat_base.FlatBaseDetector, flat_base.SCORE_SUBS),
    ),
    three_weeks_tight.STRATEGY_ID: StrategyEntry(
        three_weeks_tight.STRATEGY_ID, three_weeks_tight.ALGORITHM_VERSION,
        _file_strategy(ThreeWeeksTightSettings, three_weeks_tight.ThreeWeeksTightDetector,
                       three_weeks_tight.SCORE_SUBS),
    ),
}  # fmt: skip


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
    """The strategy files of ``config_dir``, checked against this registry; every file's
    strategy settings are validated too (a bad file blocks every scan, section 17)."""
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
