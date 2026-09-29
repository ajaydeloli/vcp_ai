"""Unit tests for KiteProvider (Mocked).

Tests parsing of historical data, capabilities and instrument loading.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from vcp_scanner.data.providers.kite import KiteProvider
from vcp_scanner.domain.market import Instrument


@pytest.fixture
def mock_kiteconnect():
    with patch("vcp_scanner.data.providers.kite.KiteConnect") as MockKite:
        mock_instance = MockKite.return_value

        # Mock instrument dump
        mock_instance.instruments.return_value = [
            {
                "instrument_token": 738561,
                "exchange_token": "2885",
                "tradingsymbol": "RELIANCE",
                "name": "RELIANCE INDUSTRIES",
                "last_price": 0.0,
                "expiry": "",
                "strike": 0.0,
                "tick_size": 0.05,
                "lot_size": 1,
                "instrument_type": "EQ",
                "segment": "NSE",
                "exchange": "NSE",
            }
        ]

        # Mock historical data
        mock_instance.historical_data.return_value = [
            {
                "date": datetime(2024, 1, 2, 0, 0, tzinfo=UTC),
                "open": 2500.0,
                "high": 2550.0,
                "low": 2490.0,
                "close": 2520.0,
                "volume": 100000,
            },
            {
                "date": datetime(2024, 1, 3, 0, 0, tzinfo=UTC),
                "open": 2520.0,
                "high": 2560.0,
                "low": 2510.0,
                "close": 2540.0,
                "volume": 120000,
            },
        ]

        yield mock_instance


def test_get_instruments(mock_kiteconnect) -> None:
    provider = KiteProvider("api_key", "access_token")
    instruments = provider.get_instruments()

    assert len(instruments) == 1
    assert instruments[0].symbol == "RELIANCE"
    assert instruments[0].instrument_id == "RELIANCE"

    mock_kiteconnect.instruments.assert_called_once_with("NSE")


def test_get_historical_daily(mock_kiteconnect) -> None:
    provider = KiteProvider("api_key", "access_token")
    # KiteProvider ensures instruments are loaded before historical request
    # to find the instrument token.

    instrument = Instrument("RELIANCE", "RELIANCE", "NSE")
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)

    candles = provider.get_historical_daily(instrument, start, end)

    assert len(candles) == 2
    assert candles[0].close == 2520.0
    assert candles[1].volume == 120000
    assert candles[0].provider == "KITE"

    mock_kiteconnect.historical_data.assert_called_once()
    call_args = mock_kiteconnect.historical_data.call_args[1]
    assert call_args["instrument_token"] == 738561
    assert call_args["interval"] == "day"


def test_capabilities() -> None:
    provider = KiteProvider("api_key")
    caps = provider.get_capabilities()

    assert caps.daily_history is True
    assert caps.historical_requests_per_second == 3.0


def test_historical_daily_uses_ist_trade_date(mock_kiteconnect) -> None:
    """Regression: Kite stamps daily bars at IST midnight (+05:30).

    Converting that instant to UTC gives 18:30 of the previous day, which used to
    shift every bar back one calendar day (Monday stored as Sunday).
    """
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    mock_kiteconnect.historical_data.return_value = [
        {
            "date": datetime(2024, 1, 1, 0, 0, tzinfo=ist),  # Monday
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 105.0,
            "volume": 1000,
        },
        {
            "date": datetime(2024, 1, 5, 0, 0, tzinfo=ist),  # Friday
            "open": 105.0,
            "high": 112.0,
            "low": 101.0,
            "close": 110.0,
            "volume": 2000,
        },
    ]

    provider = KiteProvider("api_key", "access_token")
    instrument = Instrument("RELIANCE", "RELIANCE", "NSE")
    candles = provider.get_historical_daily(instrument, date(2024, 1, 1), date(2024, 1, 5))

    assert [c.timestamp.date() for c in candles] == [date(2024, 1, 1), date(2024, 1, 5)]
    assert candles[0].timestamp.weekday() == 0  # Monday, not Sunday
    assert candles[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)
    assert candles[1].timestamp.weekday() == 4  # Friday


def test_historical_daily_naive_timestamp_treated_as_ist(mock_kiteconnect) -> None:
    mock_kiteconnect.historical_data.return_value = [
        {
            "date": datetime(2024, 1, 1, 0, 0),  # noqa: DTZ001 - naive on purpose
            "open": 100.0,
            "high": 110.0,
            "low": 95.0,
            "close": 105.0,
            "volume": 1000,
        },
    ]
    provider = KiteProvider("api_key", "access_token")
    instrument = Instrument("RELIANCE", "RELIANCE", "NSE")
    candles = provider.get_historical_daily(instrument, date(2024, 1, 1), date(2024, 1, 1))

    assert candles[0].timestamp == datetime(2024, 1, 1, tzinfo=UTC)
