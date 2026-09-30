"""Ingestion and audit code stamps times from an injected clock (AGENTS.md rule 1)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock, patch

import pytest

from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider
from vcp_scanner.data.providers.nse_surveillance import NSESurveillanceProvider
from vcp_scanner.data.providers.upstox_ca import UpstoxCorporateActionProvider
from vcp_scanner.data.reconciliation.gap_detector import GapDetector
from vcp_scanner.data.repositories.duckdb_instrument_repository import (
    DuckDBInstrumentRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import UniverseBuilder
from vcp_scanner.domain.market import Candle, Instrument

FIXED = datetime(2024, 6, 3, 9, 30, tzinfo=UTC)


def _fixed_clock() -> datetime:
    return FIXED


@pytest.fixture()
def store():
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


def _candle(d: date, o: float, c: float) -> Candle:
    return Candle(
        instrument_id="NSE_EQ|TEST",
        timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
        timeframe="D",
        open=o,
        high=max(o, c),
        low=min(o, c),
        close=c,
        volume=1000,
        provider="INTERNAL",
    )


def test_gap_detector_stamps_injected_clock() -> None:
    candles = [_candle(date(2024, 1, 1), 100.0, 100.0), _candle(date(2024, 1, 2), 50.0, 50.0)]
    events = GapDetector(clock=_fixed_clock).detect(candles, [])
    assert [e.detected_at for e in events] == [FIXED]


def test_market_repository_known_from_uses_injected_clock(store: DuckDBStore) -> None:
    repo = DuckDBMarketDataRepository(store, clock=_fixed_clock)
    assert repo.save_daily([_candle(date(2024, 1, 2), 100.0, 101.0)]) == 1
    (known_from,) = store.conn.execute(
        "SELECT known_from FROM daily_prices WHERE known_to IS NULL"
    ).fetchone()
    assert known_from.replace(tzinfo=UTC) == FIXED


def test_instrument_repository_timestamps_use_injected_clock(store: DuckDBStore) -> None:
    repo = DuckDBInstrumentRepository(store, clock=_fixed_clock)
    repo.save_instruments([Instrument(instrument_id="NSE_EQ|TEST", symbol="TEST", exchange="NSE")])
    (created_at, updated_at) = store.conn.execute(
        "SELECT created_at, updated_at FROM instruments"
    ).fetchone()
    assert created_at.replace(tzinfo=UTC) == FIXED
    assert updated_at.replace(tzinfo=UTC) == FIXED


def _response(payload: object = None, text: str = "") -> MagicMock:
    response = MagicMock(status_code=200, text=text)
    response.json.return_value = payload
    return response


def test_surveillance_flags_valid_from_uses_injected_clock() -> None:
    provider = NSESurveillanceProvider(clock=_fixed_clock)
    asm = _response({"longterm": {"data": [{"symbol": "AAA", "asmSurvIndicator": "I"}]}})
    sec_list = _response(text="Symbol,Series,Band\nBBB,BE,5\n")

    def fake_get(url: str, **_: object) -> MagicMock:
        if url == provider.ASM_URL:
            return asm
        if url == provider.SEC_LIST_URL:
            return sec_list
        return _response()

    with patch.object(provider._session, "get", side_effect=fake_get):
        records = provider.get_flags(date(2024, 1, 1), date(2024, 6, 1))

    assert {r.flag for r in records} == {"ASM", "T2T"}
    assert {r.valid_from for r in records} == {FIXED.date()}


def test_upstox_ca_created_at_uses_injected_clock() -> None:
    provider = UpstoxCorporateActionProvider(access_token="t", clock=_fixed_clock)
    payload = {"data": [{"name": "Bonus", "expiry_date": "20 Feb 2024", "ratio": "1:1"}]}
    instrument = Instrument("NSE_EQ|RELIANCE", "RELIANCE", "NSE", isin="INE002A01018")

    with patch.object(provider._session, "get", return_value=_response(payload)):
        actions = provider.get_actions(date(2024, 1, 1), date(2024, 3, 1), [instrument])

    assert actions
    assert {a.created_at for a in actions} == {FIXED}


def test_nse_ca_created_at_uses_injected_clock() -> None:
    provider = NSECorporateActionProvider(clock=_fixed_clock)
    item = {
        "symbol": "RELIANCE",
        "isin": "INE002A01018",
        "subject": "Bonus 1:1",
        "exDate": "20-Feb-2024",
    }
    action = provider._parse_nse_action(item)
    assert action is not None
    assert action.created_at == FIXED


def test_universe_snapshot_created_at_uses_injected_clock(store: DuckDBStore) -> None:
    config = UniverseConfig(
        exchange="NSE",
        min_close_price=10.0,
        min_daily_turnover_inr=5_000_000.0,
        min_avg_traded_value_50d_inr=5_000_000.0,
        eligible_series=["EQ"],
    )
    snapshot, _ = UniverseBuilder(
        DuckDBUniverseRepository(store), config, clock=_fixed_clock
    ).build_snapshot(date(2024, 1, 2))
    assert snapshot.created_at == FIXED
