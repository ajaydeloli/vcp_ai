"""Kite Connect market data provider adapter.

Implements MarketDataProvider for Zerodha Kite.
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

try:
    from kiteconnect import KiteConnect
    from kiteconnect.exceptions import TokenException, DataException, NetworkException
except ImportError:
    KiteConnect = None
    TokenException = Exception
    DataException = Exception
    NetworkException = Exception


class KiteProvider:
    """Kite Connect provider adapter.

    Requires `kiteconnect` package to be installed.
    """

    PROVIDER_NAME = "KITE"

    def __init__(self, api_key: str, access_token: str | None = None) -> None:
        if KiteConnect is None:
            raise ImportError("kiteconnect package is required for KiteProvider")

        self._api_key = api_key
        self._access_token = access_token
        self._kite = KiteConnect(api_key=api_key)
        if access_token:
            self._kite.set_access_token(access_token)

        self._symbol_to_token: dict[str, int] = {}
        self._instruments_cache: list[Instrument] | None = None

    def _ensure_instruments_loaded(self) -> None:
        if self._instruments_cache is not None:
            return

        logger.info("Fetching instruments dump from Kite...")
        raw_instruments = self._kite.instruments("NSE")
        
        instruments = []
        for row in raw_instruments:
            # We map Kite's instrument_token to a provider-specific mapping eventually,
            # but for now we store the symbol in the domain Instrument.
            symbol = row["tradingsymbol"]
            token = row["instrument_token"]
            
            self._symbol_to_token[symbol] = token
            
            # Using symbol as our internal instrument_id for V1
            instruments.append(
                Instrument(
                    instrument_id=symbol,
                    symbol=symbol,
                    exchange=row.get("exchange", "NSE"),
                    name=row.get("name"),
                    isin=row.get("isin"),
                    series=row.get("segment"),
                )
            )
            
        self._instruments_cache = instruments
        logger.info("Loaded %d NSE instruments from Kite", len(instruments))

    def get_instruments(self) -> list[Instrument]:
        """Fetch current active tradeable instruments."""
        self._ensure_instruments_loaded()
        return self._instruments_cache or []

    def get_historical_daily(
        self,
        instrument: Instrument,
        start: date,
        end: date,
    ) -> list[Candle]:
        """Fetch raw daily OHLCV bars."""
        self._ensure_instruments_loaded()
        
        token = self._symbol_to_token.get(instrument.symbol)
        if not token:
            logger.warning("No Kite instrument token found for %s", instrument.symbol)
            return []

        # Kite API expects datetime strings or datetime objects
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.max.time())

        logger.debug(
            "Fetching Kite history for token %s (%s) from %s to %s", 
            token, instrument.symbol, start, end
        )
        
        try:
            records = self._kite.historical_data(
                instrument_token=token,
                from_date=start_dt,
                to_date=end_dt,
                interval="day",
                continuous=False,
                oi=False,
            )
        except Exception as e:
            logger.error("Kite historical data error for %s: %s", instrument.symbol, e)
            raise

        candles = []
        for r in records:
            # Kite returns timezone-aware datetimes with timezone +0530
            # We convert to UTC
            record_dt: datetime = r["date"]
            dt_utc = record_dt.astimezone(UTC)
            
            candles.append(
                Candle(
                    instrument_id=instrument.instrument_id,
                    timestamp=dt_utc,
                    timeframe=Timeframe.DAILY,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=int(r["volume"]),
                    provider=self.PROVIDER_NAME,
                )
            )

        return candles

    def get_historical_intraday(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        raise NotImplementedError("Intraday historical not implemented yet")

    def health_check(self) -> ProviderHealth:
        """Check provider connectivity and credential status."""
        try:
            self._kite.profile()
            return ProviderHealth(
                provider=self.PROVIDER_NAME,
                healthy=True,
                checked_at=datetime.now(UTC),
                detail="Profile fetched successfully",
            )
        except TokenException as e:
            return ProviderHealth(
                provider=self.PROVIDER_NAME,
                healthy=False,
                checked_at=datetime.now(UTC),
                detail=f"Authentication failed: {e}",
            )
        except Exception as e:
            return ProviderHealth(
                provider=self.PROVIDER_NAME,
                healthy=False,
                checked_at=datetime.now(UTC),
                detail=str(e),
            )

    def get_capabilities(self) -> ProviderCapabilities:
        """Return provider-specific rate limits, history constraints and features."""
        return ProviderCapabilities(
            daily_history_max_request_days=2000, # Kite allows ~10 years for daily
            intraday_history_max_request_days={"5minute": 100, "15minute": 200},
            historical_requests_per_second=3.0,
            supports_bulk_historical=False,
            supports_websocket=True,
            supports_quotes=True,
            daily_history=True,
            intraday_history=True,
            corporate_actions=False,
            adjusted_prices=False,
            instrument_master=True,
            delisted_history=False,
        )

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        raise NotImplementedError("Quotes not implemented yet")
