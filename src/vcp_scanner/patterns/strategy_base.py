"""Detector contract for file-configured strategies (STRATEGY_SPECIFICATION 8, 12B).

A detector is pure: it gets the bars of one instrument up to and including the as-of bar, the
Trend Template verdict, and the breakouts already recorded for its bases, and returns a
``StrategyResult``. No storage, provider, clock or network access (AGENTS.md rule 3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from vcp_scanner.domain.strategy import RANKED_STATUSES, Breakout, StrategyResult

#: Bars before a breakout day whose mean volume is the breakout baseline (VCP_SPECIFICATION 61B).
BREAKOUT_VOLUME_BASE = 50


@dataclass(frozen=True, slots=True)
class DailyBars:
    """Adjusted daily bars, oldest first, ending at the as-of bar. ``volume`` is ``None``
    where missing (never 0-filled)."""

    dates: Sequence[date]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    volume: Sequence[float | None]

    def __post_init__(self) -> None:
        n = len(self.dates)
        if not all(len(x) == n for x in (self.high, self.low, self.close, self.volume)):
            raise ValueError("DailyBars columns differ in length")


@dataclass(frozen=True, slots=True)
class StrategyContext:
    instrument_id: str
    as_of_date: date
    bars: DailyBars
    trend_template_pass: bool
    weekly_stage2_pass: bool | None
    #: Breakouts recorded before this date, by base start (immutable, VCP_SPECIFICATION 47).
    breakouts: Mapping[date, Breakout] = field(default_factory=dict)
    breakout_volume_ratio: float = 1.5


@runtime_checkable
class StrategyDetector(Protocol):
    strategy_id: str
    algorithm_version: str

    def lookback_bars(self) -> int:
        """Bars the detector needs, as-of bar included."""
        ...

    def detect(self, ctx: StrategyContext) -> StrategyResult: ...


def find_breakout(
    bars: DailyBars, pivot: float, first: int, ratio: float
) -> Breakout | None:  # fmt: skip
    """First bar ``k`` >= ``first`` whose close is above ``pivot`` on volume >= ``ratio`` x the
    mean of the ``BREAKOUT_VOLUME_BASE`` bars before it (all volumes present). Searches up to
    the as-of bar, so weekly scans still find a breakout between scan dates."""
    for k in range(max(first, BREAKOUT_VOLUME_BASE), len(bars.dates)):
        if bars.close[k] <= pivot:
            continue
        v = bars.volume[k]
        prior = bars.volume[k - BREAKOUT_VOLUME_BASE : k]
        if v is None or any(x is None for x in prior):
            continue
        mean = sum(x for x in prior if x is not None) / BREAKOUT_VOLUME_BASE
        if mean > 0 and v >= ratio * mean:
            return Breakout(bars.dates[k], pivot, v / mean)
    return None


def setup_status(
    close: float, pivot: float, grade: int, breakout: Breakout | None, ready_pct: float
) -> str:
    """STRATEGY_SPECIFICATION 6.2 for a setup without a data state or invalidation: BREAKOUT
    while the close is at or above the breakout pivot, FAILED below it; else PIVOT_READY for
    grade >= 2 with the close 0 - ``ready_pct`` % below the pivot; else FORMING."""
    if breakout is not None:
        return "BREAKOUT" if close >= breakout.pivot_price else "FAILED"
    if grade >= 2 and close <= pivot and (pivot - close) / close * 100.0 <= ready_pct:
        return "PIVOT_READY"
    return "FORMING"


def is_ranked(grade: int, status: str, min_grade: int) -> bool:
    return grade >= min_grade and status in RANKED_STATUSES
