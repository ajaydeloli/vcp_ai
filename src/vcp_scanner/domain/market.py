"""Market-data domain objects: instruments, candles, corporate actions, provider metadata.

Candles here are RAW provider candles with provenance. Adjusted prices are derived
elsewhere from stored adjustment factors and never replace raw values
(AGENTS.md hard rule 2, PROJECT_DESIGN sections 12-13).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from vcp_scanner.domain.enums import Timeframe


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
    #: The provider's own identifier for the instrument this bar was fetched for (Kite
    #: instrument token, Upstox instrument key). Stored in ``raw_ohlcv`` for provenance.
    #: ``None`` only for providers that have no native identifier.
    provider_instrument_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderInstrument:
    """One row of a provider's instrument dump, in the provider's own terms.

    ``provider_instrument_id`` is a string whatever the provider uses natively (Kite's
    integer token is stored as text), so mappings from different brokers share one shape.
    """

    provider: str
    provider_instrument_id: str
    provider_symbol: str
    exchange: str = "NSE"
    isin: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderInstrumentMapping:
    """Provider identifier -> permanent ``instrument_id`` (DATABASE_SCHEMA section 10).

    Broker-specific ids stop at this mapping: strategy code only sees ``instrument_id``.
    """

    provider: str
    provider_instrument_id: str
    instrument_id: str
    provider_symbol: str
    exchange: str = "NSE"
    metadata: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ProviderMappingSyncResult:
    """Outcome of syncing a provider's full instrument dump into ``provider_instruments``."""

    opened: int = 0
    changed: int = 0
    closed: int = 0
    unchanged: int = 0
    skipped_unresolved: int = 0


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
    delisting_reason: str | None = None


@dataclass(frozen=True, slots=True)
class SurveillanceRecord:
    """Surveillance flag history row (ASM/GSM/T2T/BE)."""

    instrument_id: str
    flag: str
    valid_from: date
    valid_to: date | None = None
    source: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
