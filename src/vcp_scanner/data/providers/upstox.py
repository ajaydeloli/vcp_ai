"""Upstox API market data provider adapter.

Implements ``MarketDataProvider`` for Upstox (secondary source for daily history and quotes).

Upstox keys equities by ISIN (``NSE_EQ|<ISIN>``), never by trading symbol, so an instrument
without an ISIN cannot be requested. The instrument CSV download is not implemented; the
internal ``instrument_id`` comes from the NSE security master / Kite (see ``identity.py``).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.data.providers._time import daily_bar_timestamp
from vcp_scanner.domain.enums import Timeframe
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import (
    Candle,
    Instrument,
    ProviderCapabilities,
    ProviderHealth,
    Quote,
)

logger = logging.getLogger(__name__)


def upstox_instrument_key(instrument: Instrument) -> str | None:
    """Upstox key for an NSE equity (``NSE_EQ|<ISIN>``), or ``None`` without an ISIN."""
    if not instrument.isin:
        return None
    return f"{instrument.exchange.upper()}_EQ|{instrument.isin}"


class UpstoxProvider:
    """Upstox API provider adapter using plain ``requests``."""

    PROVIDER_NAME = "UPSTOX"
    BASE_URL = "https://api.upstox.com/v2"

    def __init__(self, api_key: str, access_token: str | None = None) -> None:
        self._api_key = api_key
        self._access_token = access_token

        self._session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retries))

        headers = {"Accept": "application/json", "Api-Version": "2.0"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        self._session.headers.update(headers)

    def get_instruments(self) -> list[Instrument]:
        """Not implemented: Upstox publishes a gzip CSV that this adapter does not parse."""
        logger.warning("Upstox get_instruments() CSV download is not implemented.")
        return []

    def get_historical_daily(
        self,
        instrument: Instrument,
        start: date,
        end: date,
    ) -> list[Candle]:
        """Fetch raw daily OHLCV bars between start and end (inclusive), oldest first.

        Raises ``ProviderError`` on an HTTP or parsing failure so the ingestion worker
        records the failed chunk instead of mistaking it for "no data".
        """
        key = upstox_instrument_key(instrument)
        if key is None:
            raise ProviderError(f"Upstox needs an ISIN to request {instrument.symbol}; none known")

        # /historical-candle/{instrumentKey}/{interval}/{to_date}/{from_date}
        url = (
            f"{self.BASE_URL}/historical-candle/{quote(key, safe='')}"
            f"/day/{end.isoformat()}/{start.isoformat()}"
        )
        try:
            response = self._session.get(url, timeout=10)
            if response.status_code != 200:
                raise ProviderError(
                    f"Upstox daily history failed for {instrument.symbol}: "
                    f"HTTP {response.status_code} {response.text}"
                )
            rows: list[list[Any]] = response.json().get("data", {}).get("candles", [])
            # Upstox returns newest first.
            return [self._to_candle(instrument, row) for row in reversed(rows)]
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Upstox daily history error for {instrument.symbol}: {e}") from e

    def _to_candle(self, instrument: Instrument, row: list[Any]) -> Candle:
        # row: [timestamp, open, high, low, close, volume, open_interest]
        # timestamp is ISO8601 at IST midnight, e.g. "2023-01-02T00:00:00+05:30".
        volume = int(row[5]) if row[5] is not None else None
        return Candle(
            instrument_id=instrument.instrument_id,
            timestamp=daily_bar_timestamp(datetime.fromisoformat(row[0])),
            timeframe=Timeframe.DAILY,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=volume,
            provider=self.PROVIDER_NAME,
            provider_instrument_id=upstox_instrument_key(instrument),
        )

    def get_historical_intraday(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        raise NotImplementedError("Upstox intraday history is not implemented yet")

    def health_check(self) -> ProviderHealth:
        """Check provider connectivity and credential status via the profile endpoint."""
        checked_at = datetime.now(UTC)
        try:
            response = self._session.get(f"{self.BASE_URL}/user/profile", timeout=5)
        except Exception as e:
            return ProviderHealth(self.PROVIDER_NAME, False, checked_at, str(e))

        if response.status_code == 200:
            return ProviderHealth(self.PROVIDER_NAME, True, checked_at, "Profile fetched")
        if response.status_code in (401, 403):
            return ProviderHealth(
                self.PROVIDER_NAME, False, checked_at, "Invalid or expired access token"
            )
        return ProviderHealth(
            self.PROVIDER_NAME,
            False,
            checked_at,
            f"HTTP {response.status_code}: {response.text}",
        )

    def get_capabilities(self) -> ProviderCapabilities:
        """Return provider-specific rate limits, history constraints and features."""
        return ProviderCapabilities(
            daily_history_max_request_days=365,
            intraday_history_max_request_days={"1minute": 30, "30minute": 30},
            historical_requests_per_second=10.0,
            supports_bulk_historical=False,
            supports_websocket=True,
            supports_quotes=True,
            daily_history=True,
            intraday_history=False,  # adapter does not implement it yet
            corporate_actions=False,  # served by UpstoxCorporateActionProvider
            adjusted_prices=False,  # to be verified in the provider spike
            instrument_master=False,  # CSV not parsed
            delisted_history=False,
        )

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        """Optional/interim breakout quote poller."""
        keys = {k: i for i in instruments if (k := upstox_instrument_key(i)) is not None}
        if not keys:
            return []

        try:
            response = self._session.get(
                f"{self.BASE_URL}/market-quote/quotes",
                params={"instrument_key": ",".join(keys)},
                timeout=10,
            )
            if response.status_code != 200:
                raise ProviderError(f"Upstox quotes failed: HTTP {response.status_code}")
            data: dict[str, dict[str, Any]] = response.json().get("data", {})
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Upstox quotes error: {e}") from e

        quotes: list[Quote] = []
        now = datetime.now(UTC)
        for response_key, val in data.items():
            # The response is keyed "NSE_EQ:SYMBOL"; the request key is in instrument_token.
            instrument = keys.get(val.get("instrument_token", response_key))
            last_price = val.get("last_price")
            if instrument is None or last_price is None:
                continue  # missing is not zero: never invent a price
            volume = val.get("volume")
            quotes.append(
                Quote(
                    instrument_id=instrument.instrument_id,
                    timestamp=now,
                    last_price=float(last_price),
                    volume=int(volume) if volume is not None else None,
                )
            )
        return quotes
