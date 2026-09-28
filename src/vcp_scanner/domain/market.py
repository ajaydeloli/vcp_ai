"""Market-data domain objects: instruments, candles, corporate actions, provider metadata.

Candles here are RAW provider candles with provenance. Adjusted prices are derived
elsewhere from stored adjustment factors and never replace raw values
(AGENTS.md hard rule 2, PROJECT_DESIGN sections 12-13).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from vcp_scanner.domain.enums import CorporateActionType, Timeframe


@dataclass(frozen=True, slots=True)
class Instrument:
    """Internal instrument identity. ISIN is a mapped attribute, not the permanent identity."""

    instrument_id: str
    symbol: str
    exchange: str = "NSE"
    isin: str | None = None
    name: str | None = None
    series: str | None = None


@dataclass(frozen=True, slots=True)
class Candle:
    """A raw OHLCV bar exactly as received, with provenance (PROJECT_DESIGN section 9)."""

    instrument_id: str
    timestamp: datetime
    timeframe: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: int | None  # None = missing. Never coerced to 0.
    provider: str
    source_timestamp: datetime | None = None
    ingested_at: datetime | None = None
    provider_request_id: str | None = None
    data_version: str | None = None


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """PROJECT_DESIGN section 13 ``corporate_actions`` row."""

    action_id: str
    instrument_id: str
    action_type: CorporateActionType
    ex_date: date
    source: str
    record_date: date | None = None
    ratio_numerator: Decimal | None = None
    ratio_denominator: Decimal | None = None
    cash_amount: Decimal | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Quote:
    """Latest quote, optional provider capability used for interim breakout polling (Phase 12)."""

    instrument_id: str
    timestamp: datetime
    last_price: float
    volume: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """PROJECT_DESIGN section 10. The ingestion scheduler chunks requests from this object.

    Limits are provider constraints, never strategy assumptions.
    """

    daily_history_max_request_days: int
    intraday_history_max_request_days: dict[str, int]
    historical_requests_per_second: float
    supports_bulk_historical: bool
    supports_websocket: bool
    supports_quotes: bool
    daily_history: bool
    intraday_history: bool
    corporate_actions: bool  # Kite: expected False
    adjusted_prices: bool
    instrument_master: bool
    delisted_history: bool  # Kite: expected False


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    healthy: bool
    checked_at: datetime
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SecurityRecord:
    """Historical security-master row (PROJECT_DESIGN section 14A)."""

    instrument_id: str
    symbol: str
    exchange: str
    valid_from: date
    valid_to: date | None = None
    isin: str | None = None
    series: str | None = None
    listing_date: date | None = None
    delisting_date: date | None = None
    source: str | None = None


@dataclass(frozen=True, slots=True)
class CorporateActionRecord:
    """A corporate action as reported by ONE source, before reconciliation."""

    instrument_id: str
    action_type: CorporateActionType
    ex_date: date
    source: str
    source_record_id: str | None = None
    ratio_numerator: Decimal | None = None
    ratio_denominator: Decimal | None = None
    cash_amount: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SurveillanceRecord:
    """Surveillance flag history row (ASM/GSM/T2T/BE)."""

    instrument_id: str
    flag: str
    valid_from: date
    valid_to: date | None = None
    source: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
