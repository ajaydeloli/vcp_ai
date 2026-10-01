"""Trend Template, relative strength and weekly-stage result objects.

Every condition keeps measurement, threshold and verdict (PROJECT_DESIGN section 17,
TREND_TEMPLATE_SPECIFICATION section 2). Missing data is never expressed as FAIL or 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from vcp_scanner.domain.enums import TrendTemplateStatus, WeeklyStage

#: The ten condition names, in spec order (TREND_TEMPLATE_SPECIFICATION section 2).
TREND_CONDITION_NAMES: tuple[str, ...] = (
    "close_above_sma150",
    "close_above_sma200",
    "sma150_above_sma200",
    "sma200_rising",
    "sma50_above_sma150",
    "sma50_above_sma200",
    "close_above_sma50",
    "above_52w_low",
    "near_52w_high",
    "rs_rank_min",
)


@dataclass(frozen=True, slots=True)
class TrendConditionResult:
    condition_id: int  # 1..10
    name: str
    measurement: float | None
    threshold: float | None
    passed: bool | None  # None when the input was unavailable


@dataclass(frozen=True, slots=True)
class TrendTemplateResult:
    instrument_id: str
    as_of_date: date
    status: TrendTemplateStatus
    conditions: tuple[TrendConditionResult, ...]
    algorithm_version: str
    #: RS rank used by condition 10 (None when RS was unavailable). Never 0-filled.
    rs_rank: int | None = None
    #: Research flag: rs_rank >= ``stricter_rs_rank`` (None when RS was unavailable). It never
    #: affects ``status`` or the ten conditions; it is not persisted yet.
    meets_stricter_rs: bool | None = None
    #: Weekly Stage context for the summary row (DATABASE_SCHEMA section 30).
    weekly_context: WeeklyContext | None = None
    #: Data-quality flags that blocked this result (status DATA_QUALITY_BLOCKED); else empty.
    blocked_by: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """True only if data was sufficient and all ten conditions passed."""
        return (
            self.status is TrendTemplateStatus.PASS
            and len(self.conditions) == len(TREND_CONDITION_NAMES)
            and all(c.passed is True for c in self.conditions)
        )

    @property
    def trend_template_pass(self) -> bool | None:
        """Tri-state gate flag for storage: True / False, or None when data is missing.

        Missing data is NULL, never False (AGENTS.md hard rule 4).
        """
        if self.status is TrendTemplateStatus.PASS:
            return self.passed
        if self.status is TrendTemplateStatus.FAIL:
            return False
        return None


@dataclass(frozen=True, slots=True)
class RelativeStrengthResult:
    """``rs-1.1.0`` output. ``rs_raw`` and ``rs_rank`` are None for INSUFFICIENT_DATA."""

    instrument_id: str
    as_of_date: date
    return_63d: float | None
    return_126d: float | None
    return_189d: float | None
    return_252d: float | None
    rs_raw: float | None
    rs_rank: int | None  # 1..99
    population_size: int
    calculation_version: str


@dataclass(frozen=True, slots=True)
class WeeklyContext:
    instrument_id: str
    as_of_date: date
    weekly_stage: WeeklyStage
    sma_w: float | None
    slope_pct: float | None
    prior_pct: float | None
    is_partial_week: bool
    algorithm_version: str

    @property
    def weekly_stage2_pass(self) -> bool:
        return self.weekly_stage is WeeklyStage.STAGE_2


@dataclass(frozen=True, slots=True)
class RSPriceInput:
    """Adjusted closes one instrument contributes to an RS calculation (``rs-1.1.0``).

    ``last_close`` is the newest bar on or before the as-of date; ``lagged_closes[k]`` is the
    close ``windows_days[k]`` sessions before it, or None when the history is too short.
    """

    instrument_id: str
    last_trade_date: date
    last_close: float
    lagged_closes: tuple[float | None, ...]


@dataclass(frozen=True, slots=True)
class RSRow:
    """One ``relative_strength_snapshots`` row before it is tagged with its snapshot ids."""

    instrument_id: str
    returns: tuple[float | None, ...]
    rs_raw: float | None
    rs_rank: int | None
    rs_percentile: float | None
    population_size: int
    rs_status: str  # PASS | INSUFFICIENT_DATA | STALE_DATA
