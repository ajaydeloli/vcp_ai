"""Tests for the provider-identifier mapping (audit P1-1).

Covers the ``provider_instruments`` table and repository (open / unchanged / re-point / close
semantics, point-in-time lookups in both directions), resolution of a provider dump to
permanent instrument ids, the Kite dump, and the provider's own id landing on ``raw_ohlcv``.
All data is synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest

from vcp_scanner.data.ingestion.provider_mapping import resolve_dump, sync_provider_mappings
from vcp_scanner.data.ingestion.worker import IngestionWorker
from vcp_scanner.data.providers.base import ProviderInstrumentSource
from vcp_scanner.data.providers.fake import FakeMarketDataProvider, make_candle
from vcp_scanner.data.providers.kite import KiteProvider
from vcp_scanner.data.repositories.duckdb_instrument_repository import (
    DuckDBInstrumentRepository,
    DuckDBInstrumentResolver,
)
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.repositories.duckdb_provider_instrument_repository import (
    DuckDBProviderInstrumentRepository,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import (
    Instrument,
    ProviderInstrument,
    ProviderInstrumentMapping,
)

PROVIDER = "KITE"
D1 = date(2024, 3, 1)
D2 = date(2024, 3, 8)
D3 = date(2024, 3, 15)
NOW = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)

RELIANCE = "NSE_EQ|RELIANCE"
TCS = "NSE_EQ|TCS"


@pytest.fixture()
def store():
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


@pytest.fixture()
def repo(store: DuckDBStore) -> DuckDBProviderInstrumentRepository:
    return DuckDBProviderInstrumentRepository(store, clock=lambda: NOW)


def _m(token: str, instrument_id: str, symbol: str) -> ProviderInstrumentMapping:
    return ProviderInstrumentMapping(
        provider=PROVIDER,
        provider_instrument_id=token,
        instrument_id=instrument_id,
        provider_symbol=symbol,
    )


def _rows(store: DuckDBStore) -> list[tuple]:
    return store.conn.execute(
        "SELECT provider_instrument_id, instrument_id, provider_symbol, valid_from, valid_to"
        " FROM provider_instruments ORDER BY provider_instrument_id, valid_from"
    ).fetchall()


# ---------------------------------------------------------------------------
# sync_full_dump semantics
# ---------------------------------------------------------------------------


def test_first_sync_opens_every_token(repo, store) -> None:
    result = repo.sync_full_dump(
        PROVIDER, [_m("738561", RELIANCE, "RELIANCE"), _m("2953217", TCS, "TCS")], D1
    )

    assert (result.opened, result.changed, result.closed, result.unchanged) == (2, 0, 0, 0)
    assert _rows(store) == [
        ("2953217", TCS, "TCS", D1, None),
        ("738561", RELIANCE, "RELIANCE", D1, None),
    ]


def test_resync_of_identical_dump_changes_nothing(repo, store) -> None:
    dump = [_m("738561", RELIANCE, "RELIANCE")]
    repo.sync_full_dump(PROVIDER, dump, D1)
    result = repo.sync_full_dump(PROVIDER, dump, D2)

    assert (result.opened, result.unchanged) == (0, 1)
    assert _rows(store) == [("738561", RELIANCE, "RELIANCE", D1, None)]


def test_token_repointed_to_another_instrument_is_closed_and_reopened(repo, store) -> None:
    """Kite reuses tokens: the old interval must stay readable, not be overwritten."""
    repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "RELIANCE")], D1)
    result = repo.sync_full_dump(PROVIDER, [_m("111", TCS, "TCS")], D2)

    assert result.changed == 1
    assert _rows(store) == [
        ("111", RELIANCE, "RELIANCE", D1, D2),
        ("111", TCS, "TCS", D2, None),
    ]
    assert repo.instrument_id_for(PROVIDER, "111", D1) == RELIANCE
    assert repo.instrument_id_for(PROVIDER, "111", D2) == TCS
    assert repo.instrument_id_for(PROVIDER, "111", D3) == TCS


def test_symbol_rename_on_same_instrument_is_a_new_interval(repo, store) -> None:
    repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "OLDNAME")], D1)
    repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "NEWNAME")], D2)

    assert _rows(store) == [
        ("111", RELIANCE, "OLDNAME", D1, D2),
        ("111", RELIANCE, "NEWNAME", D2, None),
    ]
    # Identity survives the rename.
    assert repo.instrument_id_for(PROVIDER, "111", D1) == RELIANCE
    assert repo.instrument_id_for(PROVIDER, "111", D3) == RELIANCE


def test_token_missing_from_dump_is_closed(repo, store) -> None:
    repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "RELIANCE"), _m("222", TCS, "TCS")], D1)
    result = repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "RELIANCE")], D2)

    assert (result.closed, result.unchanged) == (1, 1)
    assert repo.instrument_id_for(PROVIDER, "222", D1) == TCS
    assert repo.instrument_id_for(PROVIDER, "222", D2) is None  # closed at D2 (exclusive)
    assert [m.provider_instrument_id for m in repo.load_open(PROVIDER)] == ["111"]


def test_same_day_repoint_replaces_instead_of_creating_an_empty_interval(repo, store) -> None:
    repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "RELIANCE")], D1)
    repo.sync_full_dump(PROVIDER, [_m("111", TCS, "TCS")], D1)

    assert _rows(store) == [("111", TCS, "TCS", D1, None)]


def test_empty_dump_is_rejected_and_closes_nothing(repo, store) -> None:
    repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "RELIANCE")], D1)

    with pytest.raises(ValueError, match="empty"):
        repo.sync_full_dump(PROVIDER, [], D2)

    assert _rows(store) == [("111", RELIANCE, "RELIANCE", D1, None)]


def test_mappings_for_another_provider_are_rejected(repo) -> None:
    foreign = replace(_m("111", RELIANCE, "RELIANCE"), provider="UPSTOX")
    with pytest.raises(ValueError, match="UPSTOX"):
        repo.sync_full_dump(PROVIDER, [foreign], D1)


def test_sync_is_scoped_to_its_provider(repo, store) -> None:
    upstox = replace(_m("NSE_EQ|INE002A01018", RELIANCE, "RELIANCE"), provider="UPSTOX")
    repo.sync_full_dump("UPSTOX", [upstox], D1)
    repo.sync_full_dump(PROVIDER, [_m("111", TCS, "TCS")], D2)

    assert [m.provider_instrument_id for m in repo.load_open("UPSTOX")] == ["NSE_EQ|INE002A01018"]


def test_failed_sync_rolls_back(repo, store, monkeypatch) -> None:
    repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "RELIANCE")], D1)

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo, "_open", boom)
    with pytest.raises(RuntimeError):
        repo.sync_full_dump(PROVIDER, [_m("111", TCS, "TCS")], D2)

    assert _rows(store) == [("111", RELIANCE, "RELIANCE", D1, None)]


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------


def test_reverse_lookup_returns_the_token_valid_on_the_date(repo) -> None:
    repo.sync_full_dump(PROVIDER, [_m("111", RELIANCE, "RELIANCE")], D1)
    repo.sync_full_dump(PROVIDER, [_m("999", RELIANCE, "RELIANCE")], D2)  # 111 dropped

    assert repo.provider_instrument_id_for(PROVIDER, RELIANCE, D1) == "111"
    assert repo.provider_instrument_id_for(PROVIDER, RELIANCE, D3) == "999"
    assert repo.provider_instrument_id_for(PROVIDER, RELIANCE, date(2024, 2, 1)) is None
    assert repo.provider_instrument_id_for(PROVIDER, TCS, D3) is None


def test_metadata_round_trips_through_load_open(repo) -> None:
    m = replace(_m("111", RELIANCE, "RELIANCE"), metadata={"name": "RELIANCE INDUSTRIES"})
    repo.sync_full_dump(PROVIDER, [m], D1)

    assert repo.load_open(PROVIDER) == [m]


# ---------------------------------------------------------------------------
# dump -> permanent id resolution
# ---------------------------------------------------------------------------


class _Source:
    def __init__(self, rows: list[ProviderInstrument]) -> None:
        self._rows = rows

    def get_provider_instruments(self) -> list[ProviderInstrument]:
        return list(self._rows)


def _row(token: str, symbol: str, isin: str | None = None) -> ProviderInstrument:
    return ProviderInstrument(
        provider=PROVIDER,
        provider_instrument_id=token,
        provider_symbol=symbol,
        isin=isin,
        name=f"{symbol} LTD",
    )


def _known(store: DuckDBStore, instrument_id: str, symbol: str, isin: str | None) -> None:
    DuckDBInstrumentRepository(store).save_instruments(
        [Instrument(instrument_id=instrument_id, symbol=symbol, exchange="NSE", isin=isin)]
    )


def test_dump_resolves_through_isin_so_a_renamed_symbol_keeps_identity(store) -> None:
    _known(store, "NSE_EQ|OLDCO", "OLDCO", "INE000X01010")
    resolved = resolve_dump(
        _Source([_row("111", "NEWCO", isin="INE000X01010")]), DuckDBInstrumentResolver(store)
    )

    assert [m.instrument_id for m in resolved.mappings] == ["NSE_EQ|OLDCO"]
    assert resolved.mappings[0].provider_symbol == "NEWCO"


def test_unknown_instruments_are_counted_and_skipped_not_minted(store) -> None:
    _known(store, RELIANCE, "RELIANCE", None)
    resolved = resolve_dump(
        _Source([_row("1", "RELIANCE"), _row("2", "GHOST")]), DuckDBInstrumentResolver(store)
    )

    assert [m.provider_instrument_id for m in resolved.mappings] == ["1"]
    assert resolved.skipped_unresolved == 1
    assert resolved.unresolved_sample == ["GHOST"]


def test_empty_dump_raises_provider_error(store) -> None:
    with pytest.raises(ProviderError, match="empty"):
        resolve_dump(_Source([]), DuckDBInstrumentResolver(store))


def test_dump_with_nothing_known_locally_raises_and_points_at_security_master(store) -> None:
    with pytest.raises(ProviderError, match="security-master"):
        resolve_dump(_Source([_row("1", "GHOST")]), DuckDBInstrumentResolver(store))


def test_dump_mixing_providers_is_rejected(store) -> None:
    mixed = [_row("1", "RELIANCE"), replace(_row("2", "TCS"), provider="UPSTOX")]
    with pytest.raises(ProviderError, match="mixes"):
        resolve_dump(_Source(mixed), DuckDBInstrumentResolver(store))


def test_sync_provider_mappings_end_to_end(store, repo) -> None:
    _known(store, RELIANCE, "RELIANCE", None)
    result = sync_provider_mappings(
        _Source([_row("738561", "RELIANCE"), _row("9", "GHOST")]),
        repo,
        DuckDBInstrumentResolver(store),
        as_of=D1,
    )

    assert (result.opened, result.skipped_unresolved) == (1, 1)
    assert repo.instrument_id_for(PROVIDER, "738561", D1) == RELIANCE


# ---------------------------------------------------------------------------
# Kite: dump and candle stamping
# ---------------------------------------------------------------------------


@pytest.fixture()
def kite():
    with patch("vcp_scanner.data.providers.kite.KiteConnect") as mock_cls:
        client = mock_cls.return_value
        client.instruments.return_value = [
            {
                "instrument_token": 738561,
                "tradingsymbol": "RELIANCE",
                "name": "RELIANCE INDUSTRIES",
                "instrument_type": "EQ",
                "segment": "NSE",
                "exchange": "NSE",
            },
            {  # an index row in the NSE dump: not a tradable equity
                "instrument_token": 256265,
                "tradingsymbol": "NIFTY 50",
                "instrument_type": "EQ",
                "segment": "INDICES",
                "exchange": "NSE",
            },
        ]
        client.historical_data.return_value = [
            {
                "date": datetime(2024, 1, 2, tzinfo=UTC),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100,
            }
        ]
        yield client


def test_kite_provider_is_a_provider_instrument_source(kite) -> None:
    assert isinstance(KiteProvider("k", "t"), ProviderInstrumentSource)


def test_kite_dump_uses_string_tokens_and_skips_non_equity_rows(kite) -> None:
    dump = KiteProvider("k", "t").get_provider_instruments()

    assert [(r.provider, r.provider_instrument_id, r.provider_symbol) for r in dump] == [
        ("KITE", "738561", "RELIANCE")
    ]


def test_kite_candles_carry_the_token_they_were_fetched_with(kite) -> None:
    provider = KiteProvider("k", "t")
    instrument = Instrument(instrument_id=RELIANCE, symbol="RELIANCE", exchange="NSE")

    candles = provider.get_historical_daily(instrument, date(2024, 1, 2), date(2024, 1, 2))

    assert [c.provider_instrument_id for c in candles] == ["738561"]


# ---------------------------------------------------------------------------
# raw_ohlcv provenance
# ---------------------------------------------------------------------------


def _ingest(store: DuckDBStore, candle) -> None:
    instrument = Instrument(instrument_id=RELIANCE, symbol="RELIANCE", exchange="NSE")
    provider = FakeMarketDataProvider(candles_by_id={RELIANCE: [candle]})
    worker = IngestionWorker(provider=provider, repository=DuckDBMarketDataRepository(store))
    worker.ingest_instrument(
        instrument,
        date(2024, 1, 2),
        date(2024, 1, 2),
        ingestion_time=datetime(2024, 1, 3, tzinfo=UTC),
    )


def test_raw_row_stores_the_providers_own_id(store) -> None:
    candle = replace(make_candle(RELIANCE, date(2024, 1, 2)), provider_instrument_id="738561")
    _ingest(store, candle)

    (stored,) = store.conn.execute("SELECT provider_instrument_id FROM raw_ohlcv").fetchone()
    assert stored == "738561"


def test_raw_row_falls_back_to_internal_id_only_without_a_native_id(store) -> None:
    _ingest(store, make_candle(RELIANCE, date(2024, 1, 2)))

    (stored,) = store.conn.execute("SELECT provider_instrument_id FROM raw_ohlcv").fetchone()
    assert stored == RELIANCE
