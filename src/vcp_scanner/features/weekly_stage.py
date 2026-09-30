"""Weekly Stage 1-4 classifier (``stage-1.0.0``).

Authoritative rules: TREND_TEMPLATE_SPECIFICATION section 4.

    sma_w      = SMA(weekly close, sma_weeks)
    slope_pct  = (sma_w(t) / sma_w(t - slope_lookback_weeks) - 1) * 100
    prior_pct  = (sma_w(t - slope_lookback_weeks) / sma_w(t - sma_weeks) - 1) * 100

The spec writes the prior look-back as a literal "30w" and the SMA length is also 30, so
``sma_weeks`` is reused for it; there is no separate config key (see completion report).

Decision order, first match wins: Stage 4, Stage 2, Stage 3, Stage 1, TRANSITION.

The in-progress week is a partial bar: its close is the as-of daily adjusted close, so nothing
after ``as_of_date`` is ever read (AGENTS.md hard rule 1). Stored weekly bars for the as-of
week are ignored for the same reason.

Missing history is ``INSUFFICIENT_DATA`` with NULL measurements, never a Stage and never 0
(hard rule 4). Stage 2 and 4 need only ``sma_weeks + slope_lookback_weeks`` bars; Stage 1/3
also need the prior SMA, so a flat SMA without it is INSUFFICIENT_DATA rather than a guess.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from vcp_scanner.config.models import StageConfig
from vcp_scanner.data.repositories.base import FeatureRepository
from vcp_scanner.domain.enums import WeeklyStage
from vcp_scanner.domain.trend import WeeklyContext

STAGE_ALGORITHM_VERSION = "stage-1.0.0"

#: Extra calendar weeks fetched beyond the bars needed, to absorb holiday-only weeks.
_FETCH_MARGIN_WEEKS = 8


@dataclass(frozen=True, slots=True)
class StageComputation:
    """Pure classification output: stage plus the stored measurements."""

    stage: WeeklyStage
    sma_w: float | None
    slope_pct: float | None
    prior_pct: float | None


def _pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old <= 0:
        return None
    return (new / old - 1.0) * 100.0


def classify_weekly_stage(closes: Sequence[float], config: StageConfig) -> StageComputation:
    """Classify from weekly closes ordered oldest -> newest; the last is the current week."""
    n = config.sma_weeks
    band = config.flat_band_pct

    # A non-finite close would poison every SMA it touches and compare False everywhere
    # (audit P1-9): that is missing data, not a stage.
    if not all(math.isfinite(c) for c in closes):
        return StageComputation(WeeklyStage.INSUFFICIENT_DATA, None, None, None)

    def sma_back(weeks_back: int) -> float | None:
        end = len(closes) - weeks_back
        start = end - n
        if start < 0:
            return None
        return sum(closes[start:end]) / n

    sma_t = sma_back(0)
    sma_slope_ref = sma_back(config.slope_lookback_weeks)
    sma_prior_ref = sma_back(n)  # t - sma_weeks (spec: t - 30w)

    slope_pct = _pct_change(sma_t, sma_slope_ref)
    prior_pct = _pct_change(sma_slope_ref, sma_prior_ref)

    if sma_t is None or slope_pct is None:
        return StageComputation(WeeklyStage.INSUFFICIENT_DATA, sma_t, slope_pct, prior_pct)

    close = closes[-1]
    if close < sma_t and slope_pct < -band:
        stage = WeeklyStage.STAGE_4
    elif close > sma_t and slope_pct > band:
        stage = WeeklyStage.STAGE_2
    elif abs(slope_pct) <= band:
        if prior_pct is None:
            stage = WeeklyStage.INSUFFICIENT_DATA
        elif prior_pct >= config.prior_advance_min_pct:
            stage = WeeklyStage.STAGE_3
        else:
            stage = WeeklyStage.STAGE_1
    else:
        stage = WeeklyStage.TRANSITION
    return StageComputation(stage, sma_t, slope_pct, prior_pct)


class WeeklyStageEngine:
    """Builds the weekly close series for an instrument and classifies its Stage."""

    def __init__(self, features: FeatureRepository, config: StageConfig | None = None) -> None:
        self._features = features
        self._config = config or StageConfig()

    @property
    def algorithm_version(self) -> str:
        return STAGE_ALGORITHM_VERSION

    def classify_many(self, instrument_ids: Sequence[str], as_of_date: date) -> list[WeeklyContext]:
        return [self.classify(iid, as_of_date) for iid in instrument_ids]

    def classify(self, instrument_id: str, as_of_date: date) -> WeeklyContext:
        cfg = self._config
        # Mon-Thu as-of dates are mid-week. A Friday-holiday week ending Thursday is flagged
        # partial too: no exchange calendar is available yet, and the flag errs conservative.
        is_partial = as_of_date.weekday() < 4

        latest = self._features.load_adjusted_closes(instrument_id, as_of_date, 1)
        if not latest or latest[0].trade_date != as_of_date:
            return self._context(
                instrument_id,
                as_of_date,
                StageComputation(WeeklyStage.INSUFFICIENT_DATA, None, None, None),
                is_partial,
            )

        needed = cfg.sma_weeks + max(cfg.slope_lookback_weeks, cfg.sma_weeks)
        start = as_of_date - timedelta(weeks=needed + _FETCH_MARGIN_WEEKS)
        bars = self._features.load_weekly_prices(instrument_id, start, as_of_date)

        iso = as_of_date.isocalendar()
        current_week = (iso.year, iso.week)
        completed: dict[date, float] = {}
        for bar in bars:
            bar_iso = bar.week_end.isocalendar()
            if (bar_iso.year, bar_iso.week) != current_week:
                completed[bar.week_end] = bar.close  # one close per week even if versions repeat
        closes = [completed[week_end] for week_end in sorted(completed)]
        closes.append(latest[0].close)  # current week: partial bar closing at the as-of close

        return self._context(
            instrument_id, as_of_date, classify_weekly_stage(closes, cfg), is_partial
        )

    @staticmethod
    def _context(
        instrument_id: str,
        as_of_date: date,
        computation: StageComputation,
        is_partial: bool,
    ) -> WeeklyContext:
        return WeeklyContext(
            instrument_id=instrument_id,
            as_of_date=as_of_date,
            weekly_stage=computation.stage,
            sma_w=computation.sma_w,
            slope_pct=computation.slope_pct,
            prior_pct=computation.prior_pct,
            is_partial_week=is_partial,
            algorithm_version=STAGE_ALGORITHM_VERSION,
        )
