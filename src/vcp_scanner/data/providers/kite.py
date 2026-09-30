"""Kite Connect market data provider adapter.

Implements MarketDataProvider for Zerodha Kite.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from vcp_scanner.data.identity import mint_instrument_id
from vcp_scanner.data.providers._time import daily_bar_timestamp
from vcp_scanner.domain.enums import Timeframe
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import (
    Candle,
    Instrument,
    ProviderCapabilities,
    ProviderHealth,
    ProviderInstrument,
    Quote,
)

logger = logging.getLogger(__name__)

try:
    from kiteconnect import KiteConnect
    from kiteconnect.exceptions import DataException, NetworkException, TokenException
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
        self._provider_instruments: list[ProviderInstrument] = []

    def _ensure_instruments_loaded(self) -> None:
        if self._instruments_cache is not None:
            return

        logger.info("Fetching instruments dump from Kite...")
        raw_instruments = self._kite.instruments("NSE")

        instruments = []
        provider_instruments: list[ProviderInstrument] = []
        for row in raw_instruments:
            # The token is kept out of the domain Instrument: it is persisted through
            # ``get_provider_instruments`` -> ``provider_instruments`` (audit P1-1) and stamped on
            # every bar in ``get_historical_daily``.
            # The NSE dump also lists indices and other non-equity rows; only cash equities
            # are tradeable instruments for this scanner.
            if row.get("instrument_type", "EQ") != "EQ" or row.get("segment", "NSE") != "NSE":
                continue

            symbol = row["tradingsymbol"]
            token = row["instrument_token"]
            exchange = row.get("exchange", "NSE")

            self._symbol_to_token[symbol] = token
            provider_instruments.append(
                ProviderInstrument(
                    provider=self.PROVIDER_NAME,
                    provider_instrument_id=str(token),
                    provider_symbol=symbol,
                    exchange=exchange,
                    isin=row.get("isin"),
                    name=row.get("name"),
                )
            )

            # Canonical ID minted by the shared identity module. Kite has no ISIN, so the
            # ingestion boundary later remaps via the resolver where an ISIN is known.
            instruments.append(
                Instrument(
                    instrument_id=mint_instrument_id(exchange, symbol),
                    symbol=symbol,
                    exchange=exchange,
                    name=row.get("name"),
                    isin=row.get("isin"),
                    # Kite reports the market segment ("NSE"), not the series (EQ/BE/...).
                    # Series comes from the NSE security master.
                    series=None,
                )
            )

        self._instruments_cache = instruments
        self._provider_instruments = provider_instruments
        logger.info("Loaded %d NSE instruments from Kite", len(instruments))

    def get_instruments(self) -> list[Instrument]:
        """Fetch current active tradeable instruments."""
        self._ensure_instruments_loaded()
        return self._instruments_cache or []

    def get_provider_instruments(self) -> list[ProviderInstrument]:
        """The complete NSE cash-equity dump in Kite's own terms (token, tradingsymbol)."""
        self._ensure_instruments_loaded()
        return list(self._provider_instruments)

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
            # An unmapped symbol is a provider failure, not "no data": returning [] would
            # let the ingestion worker record a SUCCESS run with zero bars.
            raise ProviderError(f"No Kite instrument token found for {instrument.symbol}")

        # Kite API expects datetime strings or datetime objects
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.max.time())

        logger.debug(
            "Fetching Kite history for token %s (%s) from %s to %s",
            token,
            instrument.symbol,
            start,
            end,
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
            # Kite stamps daily bars at IST midnight; see daily_bar_timestamp for why the
            # trade date must come from the IST calendar day, not the UTC conversion.
            dt_utc = daily_bar_timestamp(r["date"])

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
                    provider_instrument_id=str(token),
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
            daily_history_max_request_days=2000,  # Kite allows ~10 years for daily
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
