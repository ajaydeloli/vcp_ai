"""Unit tests for UpstoxProvider (mocked HTTP)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from vcp_scanner.data.providers.base import MarketDataProvider
from vcp_scanner.data.providers.upstox import UpstoxProvider, upstox_instrument_key
from vcp_scanner.domain.enums import Timeframe
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import Instrument

RELIANCE = Instrument("NSE_EQ|RELIANCE", "RELIANCE", "NSE", isin="INE002A01018")


def _provider(status: int = 200, payload: dict | None = None) -> UpstoxProvider:
    provider = UpstoxProvider("key", "token")
    response = MagicMock(status_code=status, text="body")
    response.json.return_value = payload or {}
    provider._session = MagicMock()
    provider._session.get.return_value = response
    return provider


def test_satisfies_market_data_protocol() -> None:
    assert isinstance(UpstoxProvider("key"), MarketDataProvider)


def test_instrument_key_uses_isin_not_symbol() -> None:
    assert upstox_instrument_key(RELIANCE) == "NSE_EQ|INE002A01018"
    assert upstox_instrument_key(Instrument("NSE_EQ|X", "X")) is None


def test_daily_history_is_oldest_first_and_on_ist_trade_date() -> None:
    payload = {
        "data": {
            "candles": [  # Upstox returns newest first
                ["2024-01-03T00:00:00+05:30", 10, 12, 9, 11, 200, 0],
                ["2024-01-02T00:00:00+05:30", 9, 11, 8, 10, 100, 0],
            ]
        }
    }
    provider = _provider(payload=payload)
    candles = provider.get_historical_daily(RELIANCE, date(2024, 1, 2), date(2024, 1, 3))

    assert [c.timestamp for c in candles] == [
        datetime(2024, 1, 2, tzinfo=UTC),
        datetime(2024, 1, 3, tzinfo=UTC),
    ]
    assert candles[0].timeframe == Timeframe.DAILY
    assert candles[0].instrument_id == "NSE_EQ|RELIANCE"
    assert candles[0].volume == 100
    url = provider._session.get.call_args.args[0]
    assert "NSE_EQ%7CINE002A01018" in url
    assert url.endswith("/day/2024-01-03/2024-01-02")


def test_daily_history_missing_volume_is_none_not_zero() -> None:
    payload = {"data": {"candles": [["2024-01-02T00:00:00+05:30", 9, 11, 8, 10, None, 0]]}}
    candles = _provider(payload=payload).get_historical_daily(
        RELIANCE, date(2024, 1, 2), date(2024, 1, 2)
    )
    assert candles[0].volume is None


def test_http_failure_raises_instead_of_returning_empty() -> None:
    with pytest.raises(ProviderError):
        _provider(status=500).get_historical_daily(RELIANCE, date(2024, 1, 2), date(2024, 1, 3))


def test_no_isin_raises() -> None:
    with pytest.raises(ProviderError):
        _provider().get_historical_daily(
            Instrument("NSE_EQ|X", "X"), date(2024, 1, 2), date(2024, 1, 3)
        )


def test_health_check_fields() -> None:
    ok = _provider(status=200).health_check()
    assert ok.healthy and ok.provider == "UPSTOX"
    bad = _provider(status=401).health_check()
    assert not bad.healthy and bad.detail == "Invalid or expired access token"


def test_capabilities_construct() -> None:
    caps = UpstoxProvider("key").get_capabilities()
    assert caps.daily_history and caps.historical_requests_per_second > 0


def test_quotes_map_back_via_instrument_token_and_skip_missing_price() -> None:
    payload = {
        "data": {
            "NSE_EQ:RELIANCE": {
                "instrument_token": "NSE_EQ|INE002A01018",
                "last_price": 2500.5,
                "volume": 1000,
            },
            "NSE_EQ:OTHER": {"instrument_token": "NSE_EQ|INE000000000", "last_price": None},
        }
    }
    quotes = _provider(payload=payload).get_quotes([RELIANCE])
    assert len(quotes) == 1
    assert quotes[0].instrument_id == "NSE_EQ|RELIANCE"
    assert quotes[0].last_price == 2500.5
