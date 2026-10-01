"""Domain enumerations.

Classification, status and confirmation are three independent axes
(VCP_SPECIFICATION section 5, PROJECT_DESIGN section 35).
Data-state values are distinct from "no pattern" (AGENTS.md hard rule 4).
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class VCPClassification(StrEnum):
    """Derived from measurements only. Order: NONE < VCP_LIKE < VCP < A_PLUS_VCP."""

    NONE = "NONE"
    VCP_LIKE = "VCP_LIKE"  # watchlist / research only, never production
    VCP = "VCP"
    A_PLUS_VCP = "A_PLUS_VCP"

    @property
    def rank(self) -> int:
        return _CLASSIFICATION_RANK[self]

    @property
    def is_production(self) -> bool:
        return self in (VCPClassification.VCP, VCPClassification.A_PLUS_VCP)

    def at_least(self, other: VCPClassification) -> bool:
        return self.rank >= other.rank


_CLASSIFICATION_RANK: dict[VCPClassification, int] = {
    VCPClassification.NONE: 0,
    VCPClassification.VCP_LIKE: 1,
    VCPClassification.VCP: 2,
    VCPClassification.A_PLUS_VCP: 3,
}


@unique
class VCPStatus(StrEnum):
    """Lifecycle status, independent of classification."""

    FORMING = "FORMING"
    PIVOT_READY = "PIVOT_READY"
    BREAKOUT = "BREAKOUT"
    FAILED = "FAILED"
    INVALIDATED = "INVALIDATED"
    # Data states: never conflated with "no VCP found".
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    DATA_NOT_READY = "DATA_NOT_READY"
    STALE_DATA = "STALE_DATA"

    @property
    def is_data_state(self) -> bool:
        return self in (
            VCPStatus.INSUFFICIENT_DATA,
            VCPStatus.DATA_NOT_READY,
            VCPStatus.STALE_DATA,
        )


@unique
class ConfirmationState(StrEnum):
    """Swings need right-side bars to confirm, so the newest bars are provisional."""

    CONFIRMED = "CONFIRMED"
    PROVISIONAL = "PROVISIONAL"


@unique
class TrendTemplateStatus(StrEnum):
    """FAIL means data was sufficient and a condition failed. Never used for missing data."""

    PASS = "PASS"
    FAIL = "FAIL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    DATA_NOT_READY = "DATA_NOT_READY"
    #: An unresolved, signal-blocking data-quality event covers the instrument (audit P0-2).
    #: Distinct from INSUFFICIENT_DATA / DATA_NOT_READY: the data exists but cannot be trusted.
    DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"


@unique
class WeeklyStage(StrEnum):
    """Weekly Stage 1-4 (TREND_TEMPLATE_SPECIFICATION section 4). TRANSITION is not Stage 2."""

    STAGE_1 = "STAGE_1"
    STAGE_2 = "STAGE_2"
    STAGE_3 = "STAGE_3"
    STAGE_4 = "STAGE_4"
    TRANSITION = "TRANSITION"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@unique
class SurvivorshipStatus(StrEnum):
    """Stamped on every scan and backtest (PROJECT_DESIGN section 14A).

    Only POINT_IN_TIME_COMPLETE results may validate thresholds or support performance claims.
    """

    POINT_IN_TIME_COMPLETE = "POINT_IN_TIME_COMPLETE"
    PARTIAL = "PARTIAL"
    BIASED = "BIASED"

    @property
    def may_validate_thresholds(self) -> bool:
        return self is SurvivorshipStatus.POINT_IN_TIME_COMPLETE


@unique
class Timeframe(StrEnum):
    DAILY = "1d"
    WEEKLY = "1w"
    MINUTE_1 = "1m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    MINUTE_60 = "60m"


@unique
class DataQualityStatus(StrEnum):
    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@unique
class DataQualityFlag(StrEnum):
    """PROJECT_DESIGN section 57 plus UNEXPLAINED_GAP (DATA_SPECIFICATION 18A)."""

    STALE_PRICE_DATA = "STALE_PRICE_DATA"
    MISSING_CANDLES = "MISSING_CANDLES"
    CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
    FUNDAMENTAL_DATA_MISSING = "FUNDAMENTAL_DATA_MISSING"
    FUNDAMENTAL_DATA_STALE = "FUNDAMENTAL_DATA_STALE"
    SYMBOL_MAPPING_UNCERTAIN = "SYMBOL_MAPPING_UNCERTAIN"
    PROVIDER_CONFLICT = "PROVIDER_CONFLICT"
    UNEXPLAINED_GAP = "UNEXPLAINED_GAP"
    # Back on NSE after a long absence: history restarts (audit P1-2c).
    TRADING_ABSENCE = "TRADING_ABSENCE"
    # NSE listed a price-affecting action the scanner does not model (audit P1-10).
    CORPORATE_ACTION_UNMODELLED = "CORPORATE_ACTION_UNMODELLED"
    FUNDAMENTALS_UNAVAILABLE = "FUNDAMENTALS_UNAVAILABLE"


@unique
class FundamentalDataStatus(StrEnum):
    """PROJECT_DESIGN section 32. Missing fundamentals are NULL plus a status, never zero."""

    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"
    ESTIMATED = "ESTIMATED"
    RESTATED = "RESTATED"
    INVALID = "INVALID"


@unique
class CorporateActionType(StrEnum):
    SPLIT = "SPLIT"
    BONUS = "BONUS"
    DIVIDEND = "DIVIDEND"
    RIGHTS = "RIGHTS"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    NAME_CHANGE = "NAME_CHANGE"
    MERGER = "MERGER"
    DEMERGER = "DEMERGER"
    # Price-affecting but not modelled (capital reduction, merger, scheme, non-equity rights or
    # bonus): kept so it is visible as a warning (audit P1-10); never adjusts prices.
    UNMODELLED = "UNMODELLED"


@unique
class ErrorCategory(StrEnum):
    """Standard error categories (DATA_SPECIFICATION section 96)."""

    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INVALID_INSTRUMENT = "INVALID_INSTRUMENT"
    INVALID_RANGE = "INVALID_RANGE"
    PARTIAL_RESPONSE = "PARTIAL_RESPONSE"
    DATA_VALIDATION_ERROR = "DATA_VALIDATION_ERROR"
    CORPORATE_ACTION_ERROR = "CORPORATE_ACTION_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
