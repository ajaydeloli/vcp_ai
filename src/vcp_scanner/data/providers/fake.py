"""Fake (deterministic) market data provider for tests (AGENTS.md rule 10).

``FakeMarketDataProvider`` is the canonical test double for anything needing
``MarketDataProvider``.  It implements the full Protocol using a plain dict
of pre-loaded fixture candles.

Rules:
- Zero network calls or broker SDK imports (rule 3).
- Fixtures must be labelled as synthetic in the provider field (rule 10).
- Capabilities are configurable so chunk-boundary tests can exercise small limits.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from vcp_scanner.domain.enums import Timeframe
from vcp_scanner.domain.market import (
    Candle,
    Instrument,
    ProviderCapabilities,
    ProviderHealth,
    Quote,
)

SYNTHETIC_PROVIDER = "SYNTHETIC_FAKE"


def make_candle(
    instrument_id: str,
    trade_date: date,
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 98.0,
    close: float = 102.0,
    volume: int | None = 100_000,
    provider: str = SYNTHETIC_PROVIDER,
) -> Candle:
    """Helper: build a valid daily Candle from a date and OHLCV values.

    All synthetic fixtures must use ``SYNTHETIC_FAKE`` as provider (rule 10).
    """
    ts = datetime(trade_date.year, trade_date.month, trade_date.day, tzinfo=UTC)
    return Candle(
        instrument_id=instrument_id,
        timestamp=ts,
        timeframe=Timeframe.DAILY,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        provider=provider,
    )


class FakeMarketDataProvider:
    """Deterministic ``MarketDataProvider`` backed by in-memory fixtures.

    Usage::

        candles = [make_candle("RELIANCE", date(2024, 1, 2)), ...]
        provider = FakeMarketDataProvider({"RELIANCE": candles})

    Instruments and candles are pre-loaded at construction.  No real API is
    called.  This is the *only* provider tests should use.

    The ``call_log`` attribute records every method call for assertion in tests.
    """

    def __init__(
        self,
        candles_by_id: dict[str, list[Candle]] | None = None,
        instruments: list[Instrument] | None = None,
        max_request_days: int = 365,
    ) -> None:
        self._candles: dict[str, list[Candle]] = candles_by_id or {}
        self._instruments: list[Instrument] = instruments or []
        self._max_request_days = max_request_days
        self.call_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # MarketDataProvider protocol implementation
    # ------------------------------------------------------------------

    def get_instruments(self) -> list[Instrument]:
        self.call_log.append({"method": "get_instruments"})
        return list(self._instruments)

    def get_historical_daily(
        self,
        instrument: Instrument,
        start: date,
        end: date,
    ) -> list[Candle]:
        self.call_log.append(
            {
                "method": "get_historical_daily",
                "instrument_id": instrument.instrument_id,
                "start": start,
                "end": end,
            }
        )
        all_candles = self._candles.get(instrument.instrument_id, [])
        return [c for c in all_candles if start <= c.timestamp.date() <= end]

    def get_historical_intraday(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        self.call_log.append({"method": "get_historical_intraday", "interval": interval})
        return []  # intraday not required for Phase 1 / VCP V1

    def health_check(self) -> ProviderHealth:
        self.call_log.append({"method": "health_check"})
        return ProviderHealth(
            provider=SYNTHETIC_PROVIDER,
            healthy=True,
            checked_at=datetime(2024, 1, 1, tzinfo=UTC),
        )

    def get_capabilities(self) -> ProviderCapabilities:
        self.call_log.append({"method": "get_capabilities"})
        return ProviderCapabilities(
            daily_history_max_request_days=self._max_request_days,
            intraday_history_max_request_days={},
            historical_requests_per_second=100.0,  # no rate limit in tests
            supports_bulk_historical=False,
            supports_websocket=False,
            supports_quotes=False,
            daily_history=True,
            intraday_history=False,
            corporate_actions=False,
            adjusted_prices=False,
            instrument_master=True,
            delisted_history=False,
        )

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        self.call_log.append({"method": "get_quotes"})
        return []
