"""Upstox API market data provider adapter.

Implements MarketDataProvider for Upstox.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from vcp_scanner.domain.enums import Timeframe
from vcp_scanner.domain.market import (
    Candle,
    Instrument,
    ProviderCapabilities,
    ProviderHealth,
    Quote,
)

logger = logging.getLogger(__name__)


class UpstoxProvider:
    """Upstox API provider adapter.
    
    Implements MarketDataProvider protocol using standard requests.
    """

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
        
        headers = {
            "Accept": "application/json",
            "Api-Version": "2.0"
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
            
        self._session.headers.update(headers)

    def get_instruments(self) -> list[Instrument]:
        """Fetch current active tradeable instruments.
        
        Upstox provides a gzip CSV file for instruments. We could parse it here.
        For this implementation, we return a mock/stub since the full CSV parser
        is large, but the pattern is established.
        """
        logger.warning("Upstox get_instruments() CSV download not fully implemented in adapter.")
        return []

    def get_historical_daily(
        self,
        instrument: Instrument,
        start: date,
        end: date,
    ) -> list[Candle]:
        """Fetch raw daily OHLCV bars between start and end (inclusive)."""
        # Upstox endpoint: /historical-candle/{instrumentKey}/{interval}/{to_date}/{from_date}
        # interval: 'day'
        # dates format: YYYY-mm-dd
        
        url = f"{self.BASE_URL}/historical-candle/{instrument.instrument_id}/day/{end.isoformat()}/{start.isoformat()}"
        
        try:
            response = self._session.get(url, timeout=10)
            if response.status_code != 200:
                logger.error(f"Upstox daily history failed for {instrument.symbol}: {response.text}")
                return []
                
            # Upstox returns data in reverse chronological order (newest first). We need chronological.
            data = response.json().get("data", {}).get("candles", [])
            
            candles = []
            for row in reversed(data):
                # row format: [timestamp, open, high, low, close, volume, open_interest]
                # timestamp is ISO8601 string like "2023-01-01T00:00:00+05:30"
                
                # Parse timestamp and convert to aware datetime, then to UTC
                ts_str = row[0]
                try:
                    ts = datetime.fromisoformat(ts_str).astimezone(UTC)
                except ValueError:
                    # Fallback if timezone not present
                    ts = datetime.fromisoformat(ts_str).replace(tzinfo=UTC)
                    
                candles.append(Candle(
                    instrument_id=instrument.instrument_id,
                    timestamp=ts,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=int(row[5]),
                    timeframe=Timeframe.DAY_1,
                    provider=self.PROVIDER_NAME
                ))
            return candles
            
        except Exception as e:
            logger.error(f"Upstox daily history error for {instrument.symbol}: {e}")
            return []

    def get_historical_intraday(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]:
        """Fetch raw intraday OHLCV bars between start and end."""
        # Upstox /historical-candle/intraday/{instrumentKey}/{interval} doesn't take dates natively,
        # or takes them slightly differently depending on the exact v2 endpoint.
        # We'll map standard interval to Upstox specific (e.g., '1minute').
        logger.warning("Upstox get_historical_intraday not fully mapped.")
        return []

    def health_check(self) -> ProviderHealth:
        """Check provider connectivity and credential status."""
        try:
            # We can check the profile endpoint to verify the token
            url = f"{self.BASE_URL}/user/profile"
            response = self._session.get(url, timeout=5)
            
            if response.status_code == 200:
                return ProviderHealth(is_connected=True, error_message=None)
            elif response.status_code in (401, 403):
                return ProviderHealth(is_connected=False, error_message="Invalid or expired access token")
            else:
                return ProviderHealth(is_connected=False, error_message=f"HTTP {response.status_code}: {response.text}")
        except Exception as e:
            return ProviderHealth(is_connected=False, error_message=str(e))

    def get_capabilities(self) -> ProviderCapabilities:
        """Return provider-specific rate limits, history constraints and features."""
        return ProviderCapabilities(
            provider_name=self.PROVIDER_NAME,
            max_days_per_request_daily=365,
            max_days_per_request_intraday=30,
            has_split_adjustment_built_in=False, # Upstox behavior to be verified in spike
            requests_per_second_limit=10.0,
        )

    def get_quotes(self, instruments: list[Instrument]) -> list[Quote]:
        """Optional/interim breakout quote poller."""
        if not instruments:
            return []
            
        instrument_keys = ",".join(i.instrument_id for i in instruments)
        url = f"{self.BASE_URL}/market-quote/quotes"
        
        try:
            response = self._session.get(url, params={"instrument_key": instrument_keys}, timeout=10)
            if response.status_code != 200:
                logger.error(f"Upstox quotes failed: {response.text}")
                return []
                
            data = response.json().get("data", {})
            quotes = []
            
            for key, val in data.items():
                instrument = next((i for i in instruments if i.instrument_id == key), None)
                if not instrument:
                    continue
                    
                quotes.append(Quote(
                    instrument_id=key,
                    timestamp=datetime.now(UTC),
                    last_price=float(val.get("last_price", 0)),
                    volume=int(val.get("volume", 0)) if val.get("volume") else None,
                    provider=self.PROVIDER_NAME
                ))
            return quotes
            
        except Exception as e:
            logger.error(f"Upstox quotes error: {e}")
            return []
