"""Provider protocols and interfaces (PROJECT_DESIGN sections 8.1, 14A, 31).

Strategy and scanning engines interact only with these interfaces, never
with broker SDKs (AGENTS.md hard rule 3).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, runtime_checkable

from vcp_scanner.domain.corporate_actions import CorporateAction
from vcp_scanner.domain.fundamentals import FundamentalSnapshot
from vcp_scanner.domain.market import (
    Candle,
    Instrument,
    ProviderCapabilities,
    ProviderHealth,
    ProviderInstrument,
    Quote,
    SecurityRecord,
    SurveillanceRecord,
)


@runtime_checkable
class MarketDataProvider(Protocol):
    """Primary market data interface (PROJECT_DESIGN section 8.1)."""

    def get_instruments(self) -> list[Instrument]:
        """Fetch current active tradeable instruments."""
        ...

    def get_historical_daily(
        self,
        instrument: Instrument,
        start: date,
        end: date,
    ) -> list[Candle]:
        """Fetch raw daily OHLCV bars between start and end (inclusive)."""
        ...

    def get_historical_intraday(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Fetch raw intraday OHLCV bars between start and end."""
        ...

    def health_check(self) -> ProviderHealth:
        """Check provider connectivity and credential status."""
        ...

    def get_capabilities(self) -> ProviderCapabilities:
        """Return provider-specific rate limits, history constraints and features."""
        ...

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        """Optional/interim breakout quote poller (PROJECT_DESIGN section 8.1, Phase 12)."""
        ...


@runtime_checkable
class ProviderInstrumentSource(Protocol):
    """A provider that publishes a full instrument dump with its own identifiers (audit P1-1).

    Kite lists every tradable instrument with an instrument token; that dump is what
    ``provider_instruments`` is synced from. Providers keyed by ISIN (Upstox) need no dump.
    """

    def get_provider_instruments(self) -> list[ProviderInstrument]:
        """The provider's complete current instrument list, in the provider's own ids."""
        ...


@runtime_checkable
class SecurityMasterProvider(Protocol):
    """Historical security-master and delistings provider (PROJECT_DESIGN section 14A)."""

    def get_security_history(self, start: date, end: date) -> list[SecurityRecord]:
        """Return historical listings, delistings, series and symbol changes."""
        ...


@runtime_checkable
class CorporateActionProvider(Protocol):
    """Provider for splits, bonuses, dividends, symbol changes (PROJECT_DESIGN section 14A)."""

    def get_actions(
        self, start: date, end: date, instruments: list[Instrument] | None = None
    ) -> list[CorporateAction]:
        """Fetch corporate actions reported by this provider's source.

        Args:
            start: Start date for the query.
            end: End date for the query.
            instruments: Optional list of specific instruments to query. Providers like Upstox
                that query by ISIN will require this.
        """
        ...


@runtime_checkable
class SurveillanceProvider(Protocol):
    """Surveillance flags provider, e.g. ASM/GSM/T2T/BE history (PROJECT_DESIGN section 14A)."""

    def get_flags(self, start: date, end: date) -> list[SurveillanceRecord]:
        """Fetch surveillance flag events in the date range.

        Contract: a provider that returns the full set of flags active *now*, regardless of
        the window, sets ``returns_active_snapshot = True`` (the default assumed by the
        ingestion worker); flags absent from such a feed are closed. A provider that returns
        only events inside the window must set it to ``False``, so absent flags are kept.
        """
        ...


@runtime_checkable
class FundamentalProvider(Protocol):
    """Point-in-time fundamental provider (PROJECT_DESIGN section 31)."""

    def get_snapshot(
        self,
        instrument: Instrument,
        as_of: date,
    ) -> FundamentalSnapshot:
        """Fetch fundamental data as-of a specific date (available_at <= as_of)."""
        ...
