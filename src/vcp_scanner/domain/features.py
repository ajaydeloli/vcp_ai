"""Feature domain objects for indicators and technical data (PROJECT_DESIGN section 29)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class DailyFeatures:
    """Row in technical_features_daily. Values can be None if not enough history."""

    instrument_id: str
    trade_date: date

    sma_20: float | None
    sma_50: float | None
    sma_150: float | None
    sma_200: float | None

    ema_10: float | None
    ema_20: float | None
    ema_50: float | None

    atr_14: float | None
    atr_pct_14: float | None

    high_20: float | None
    high_50: float | None
    high_252: float | None

    low_20: float | None
    low_50: float | None
    low_252: float | None

    volume_avg_5: float | None
    volume_avg_10: float | None
    volume_avg_20: float | None
    volume_avg_50: float | None

    volume_ratio_20: float | None
    volume_ratio_50: float | None

    daily_return: float | None
    rolling_volatility_20: float | None
    rolling_volatility_50: float | None

    calculation_version: str


@dataclass(frozen=True, slots=True)
class WeeklyPrice:
    """Row in weekly_prices (PROJECT_DESIGN section 27)."""

    instrument_id: str
    week_end: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    source_daily_version: str


@dataclass(frozen=True, slots=True)
class AdjustedClose:
    """One adjusted daily close, read from ``daily_prices_adjusted``."""

    trade_date: date
    close: float
