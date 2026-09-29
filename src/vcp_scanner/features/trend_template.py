"""Trend Template engine (``trend-1.0.0``).

Authoritative rules: TREND_TEMPLATE_SPECIFICATION section 2 (ten conditions) and section 1
(data readiness). Design rules honoured here (AGENTS.md):

* All ten conditions are always evaluated and stored, with measurement, threshold and verdict
  (no short-circuiting, hard rule 6).
* Missing input is NULL plus a status, never FAIL and never 0 (hard rule 4).
  ``passed`` is ``None`` for a condition whose inputs are unavailable.
* Every function takes an explicit ``as_of_date``; nothing reads the wall clock (hard rule 1).
* Only repository interfaces are used; no DuckDB import (hard rule 3).
* All thresholds come from ``TrendTemplateConfig`` (hard rule 7).

Status semantics:

``PASS``               data sufficient, all ten conditions passed.
``FAIL``               data sufficient, at least one condition failed.
``INSUFFICIENT_DATA``  not enough history (or a NULL input) to evaluate every condition.
``DATA_NOT_READY``     the as-of bar, feature rows or RS snapshot are missing or misaligned.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from vcp_scanner.config.models import TrendTemplateConfig
from vcp_scanner.data.repositories.base import FeatureRepository, TrendRepository
from vcp_scanner.domain.enums import TrendTemplateStatus
from vcp_scanner.domain.trend import (
    TREND_CONDITION_NAMES,
    TrendConditionResult,
    TrendTemplateResult,
    WeeklyContext,
)

TREND_ALGORITHM_VERSION = "trend-1.0.0"

#: Sessions in the 52-week window used for high252 / low252 (spec section 2).
SESSIONS_52W = 252


@dataclass(frozen=True, slots=True)
class TrendInputs:
    """Raw inputs for the ten conditions. ``None`` means unavailable, never zero."""

    close: float | None = None
    sma_50: float | None = None
    sma_150: float | None = None
    sma_200: float | None = None
    sma_200_prior: float | None = None  # SMA200 at t - L trading days
    high_252: float | None = None
    low_252: float | None = None
    rs_rank: int | None = None


def _compare(
    measurement: float | None,
    threshold: float | None,
    test: Callable[[float, float], bool],
) -> bool | None:
    if measurement is None or threshold is None:
        return None
    return test(measurement, threshold)


def _gt(a: float, b: float) -> bool:
    return a > b


def _ge(a: float, b: float) -> bool:
    return a >= b


def evaluate_conditions(
    inputs: TrendInputs, config: TrendTemplateConfig
) -> tuple[TrendConditionResult, ...]:
    """Evaluate all ten conditions. Pure function: same inputs and config, same rows.

    Comparison operators follow the spec table: conditions 1-7 are strict ``>``;
    conditions 8, 9 and 10 are inclusive ``>=``.
    """
    i = inputs
    low_threshold = (
        i.low_252 * (1 + config.min_above_52w_low_pct / 100) if i.low_252 is not None else None
    )
    high_threshold = (
        i.high_252 * (1 - config.max_below_52w_high_pct / 100) if i.high_252 is not None else None
    )
    rs_measurement = float(i.rs_rank) if i.rs_rank is not None else None
    min_rs = float(config.min_rs_rank)

    # (measurement, threshold, operator) in spec order; names come from the domain tuple.
    specs: list[tuple[float | None, float | None, Callable[[float, float], bool]]] = [
        (i.close, i.sma_150, _gt),  # 1 close_above_sma150
        (i.close, i.sma_200, _gt),  # 2 close_above_sma200
        (i.sma_150, i.sma_200, _gt),  # 3 sma150_above_sma200
        (i.sma_200, i.sma_200_prior, _gt),  # 4 sma200_rising
        (i.sma_50, i.sma_150, _gt),  # 5 sma50_above_sma150
        (i.sma_50, i.sma_200, _gt),  # 6 sma50_above_sma200
        (i.close, i.sma_50, _gt),  # 7 close_above_sma50
        (i.close, low_threshold, _ge),  # 8 above_52w_low
        (i.close, high_threshold, _ge),  # 9 near_52w_high
        (rs_measurement, min_rs, _ge),  # 10 rs_rank_min
    ]
    assert len(specs) == len(TREND_CONDITION_NAMES)

    return tuple(
        TrendConditionResult(
            condition_id=idx,
            name=name,
            measurement=measurement,
            threshold=threshold,
            passed=_compare(measurement, threshold, test),
        )
        for idx, (name, (measurement, threshold, test)) in enumerate(
            zip(TREND_CONDITION_NAMES, specs, strict=True), start=1
        )
    )


def derive_status(conditions: Sequence[TrendConditionResult]) -> TrendTemplateStatus:
    """Any unevaluable condition -> INSUFFICIENT_DATA; otherwise PASS iff all passed."""
    if any(c.passed is None for c in conditions):
        return TrendTemplateStatus.INSUFFICIENT_DATA
    if all(c.passed is True for c in conditions):
        return TrendTemplateStatus.PASS
    return TrendTemplateStatus.FAIL


class TrendTemplateEngine:
    """Evaluates the ten Minervini conditions for an instrument as of a date."""

    def __init__(
        self,
        features: FeatureRepository,
        trend_repo: TrendRepository,
        config: TrendTemplateConfig | None = None,
        *,
        rs_version: str = "rs-1.0.0",
        features_version: str | None = "features-1.1.0",
    ) -> None:
        self._features = features
        self._trend_repo = trend_repo
        self._config = config or TrendTemplateConfig()
        self._rs_version = rs_version
        self._features_version = features_version

    @property
    def algorithm_version(self) -> str:
        return TREND_ALGORITHM_VERSION

    def evaluate_many(
        self,
        instrument_ids: Sequence[str],
        as_of_date: date,
        *,
        weekly_contexts: Mapping[str, WeeklyContext] | None = None,
    ) -> list[TrendTemplateResult]:
        """Evaluate each instrument independently, preserving input order."""
        contexts = weekly_contexts or {}
        return [
            self.evaluate(iid, as_of_date, weekly_context=contexts.get(iid))
            for iid in instrument_ids
        ]

    def evaluate(
        self,
        instrument_id: str,
        as_of_date: date,
        *,
        weekly_context: WeeklyContext | None = None,
    ) -> TrendTemplateResult:
        cfg = self._config
        lookback = cfg.sma200_slope_lookback_days
        needed = SESSIONS_52W + lookback  # spec section 1 warm-up

        closes = self._features.load_adjusted_closes(instrument_id, as_of_date, needed)
        if not closes or closes[0].trade_date != as_of_date:
            # No bar on the as-of date: cannot judge today's close (missing/suspended/stale).
            return self._unavailable(
                instrument_id, as_of_date, TrendTemplateStatus.DATA_NOT_READY, weekly_context
            )
        close = closes[0].close
        if len(closes) < needed:
            # Phase 4 features are NULL until their window is full; this warm-up guard is a
            # second layer so the engine never depends on feature values from a short history.
            return self._unavailable(
                instrument_id,
                as_of_date,
                TrendTemplateStatus.INSUFFICIENT_DATA,
                weekly_context,
                close=close,
            )

        history = self._features.load_daily_feature_history(
            instrument_id, as_of_date, lookback + 1, self._features_version
        )
        if (
            len(history) < lookback + 1
            or history[0].trade_date != as_of_date
            or history[lookback].trade_date != closes[lookback].trade_date
        ):
            return self._unavailable(
                instrument_id,
                as_of_date,
                TrendTemplateStatus.DATA_NOT_READY,
                weekly_context,
                close=close,
            )
        latest, prior = history[0], history[lookback]

        if cfg.extreme_basis == "close":
            window = [c.close for c in closes[:SESSIONS_52W]]
            high_252: float | None = max(window)
            low_252: float | None = min(window)
        else:
            high_252, low_252 = latest.high_252, latest.low_252

        rs = self._trend_repo.load_relative_strength(instrument_id, as_of_date, self._rs_version)
        rs_rank = rs.rs_rank if rs is not None else None

        conditions = evaluate_conditions(
            TrendInputs(
                close=close,
                sma_50=latest.sma_50,
                sma_150=latest.sma_150,
                sma_200=latest.sma_200,
                sma_200_prior=prior.sma_200,
                high_252=high_252,
                low_252=low_252,
                rs_rank=rs_rank,
            ),
            cfg,
        )
        # No RS snapshot at all means RS has not been computed for this date: not ready.
        # A snapshot whose rank is NULL means the instrument lacks RS history: insufficient.
        status = TrendTemplateStatus.DATA_NOT_READY if rs is None else derive_status(conditions)
        return TrendTemplateResult(
            instrument_id=instrument_id,
            as_of_date=as_of_date,
            status=status,
            conditions=conditions,
            algorithm_version=TREND_ALGORITHM_VERSION,
            rs_rank=rs_rank,
            meets_stricter_rs=(rs_rank >= cfg.stricter_rs_rank if rs_rank is not None else None),
            weekly_context=weekly_context,
        )

    def _unavailable(
        self,
        instrument_id: str,
        as_of_date: date,
        status: TrendTemplateStatus,
        weekly_context: WeeklyContext | None,
        *,
        close: float | None = None,
    ) -> TrendTemplateResult:
        """All ten rows are still emitted; unavailable inputs stay NULL, ``passed`` is None."""
        return TrendTemplateResult(
            instrument_id=instrument_id,
            as_of_date=as_of_date,
            status=status,
            conditions=evaluate_conditions(TrendInputs(close=close), self._config),
            algorithm_version=TREND_ALGORITHM_VERSION,
            rs_rank=None,
            weekly_context=weekly_context,
        )
