"""Tests for canonical instrument identity across providers, workers and tables.

The review found that Kite used ``RELIANCE`` while the NSE providers used
``NSE_EQ|RELIANCE``, so corporate actions never reconciled, adjustments never joined to
prices, and the universe builder's security-master joins matched nothing. These tests pin
the fix: one minting function, and a resolver (ISIN first, then exchange + symbol) applied
at every ingestion boundary. All data is synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.identity import (
    canonical_instrument_id,
    mint_instrument_id,
    symbol_from_instrument_id,
)
from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
from vcp_scanner.data.ingestion.sm_worker import SecurityMasterIngestionWorker
from vcp_scanner.data.ingestion.worker import IngestionWorker
from vcp_scanner.data.providers.fake import FakeMarketDataProvider, make_candle
from vcp_scanner.data.providers.kite import KiteProvider
from vcp_scanner.data.providers.upstox_ca import UpstoxCorporateActionProvider
from vcp_scanner.data.reconciliation.engine import ReconciliationEngine
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_instrument_repository import (
    DuckDBInstrumentRepository,
    DuckDBInstrumentResolver,
)
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import UniverseBuilder
from vcp_scanner.domain.corporate_actions import CorporateAction, CorporateActionStatus
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.market import Instrument, SecurityRecord

ISIN_X = "INE000X01010"
OLD_ID = "NSE_EQ|OLDCO"  # permanent ID, minted when the company traded as OLDCO
NEW_ID = "NSE_EQ|NEWCO"  # what a provider would mint after the ticker is renamed
NOW = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)
IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture()
def store():
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


@pytest.fixture()
def resolver(store: DuckDBStore) -> DuckDBInstrumentResolver:
    return DuckDBInstrumentResolver(store)


def _known_instrument(store: DuckDBStore, instrument_id: str = OLD_ID, isin: str = ISIN_X) -> None:
    symbol = symbol_from_instrument_id(instrument_id) or instrument_id
    DuckDBInstrumentRepository(store).save_instruments(
        [Instrument(instrument_id=instrument_id, symbol=symbol, exchange="NSE", isin=isin)]
    )


# ---------------------------------------------------------------------------
# identity module
# ---------------------------------------------------------------------------


def test_mint_is_one_canonical_format() -> None:
    assert mint_instrument_id("NSE", "RELIANCE") == "NSE_EQ|RELIANCE"
    assert mint_instrument_id(" nse ", " reliance ") == "NSE_EQ|RELIANCE"


def test_symbol_from_instrument_id_round_trips() -> None:
    assert symbol_from_instrument_id("NSE_EQ|TCS") == "TCS"
    assert symbol_from_instrument_id("TCS") is None


def test_canonical_id_without_resolver_or_unknown_keeps_reported_id(
    resolver: DuckDBInstrumentResolver,
) -> None:
    assert canonical_instrument_id(None, NEW_ID, isin=ISIN_X) == NEW_ID
    assert canonical_instrument_id(resolver, NEW_ID, isin=ISIN_X) == NEW_ID  # nothing known yet


# ---------------------------------------------------------------------------
# DuckDBInstrumentResolver
# ---------------------------------------------------------------------------


def test_resolver_prefers_isin_so_a_renamed_symbol_keeps_its_id(
    store: DuckDBStore, resolver: DuckDBInstrumentResolver
) -> None:
    _known_instrument(store)
    assert resolver.resolve(isin=ISIN_X, symbol="NEWCO", exchange="NSE") == OLD_ID
    assert canonical_instrument_id(resolver, NEW_ID, isin=ISIN_X) == OLD_ID


def test_resolver_falls_back_to_exchange_and_symbol(
    store: DuckDBStore, resolver: DuckDBInstrumentResolver
) -> None:
    _known_instrument(store)
    assert resolver.resolve(isin=None, symbol="OLDCO", exchange="NSE") == OLD_ID
    assert resolver.resolve(isin=None, symbol="OLDCO", exchange="BSE") is None
    assert resolver.resolve(isin=None, symbol="UNKNOWN", exchange="NSE") is None


def test_resolver_falls_back_to_current_security_master_rows(
    store: DuckDBStore, resolver: DuckDBInstrumentResolver
) -> None:
    store.conn.execute(
        """
        INSERT INTO security_master_history (
            instrument_id, isin, symbol, exchange, valid_from, known_from
        ) VALUES ('NSE_EQ|SMCO', 'INE111Y01011', 'SMCO', 'NSE', '2000-01-01', ?)
        """,
        [NOW],
    )
    assert resolver.resolve(isin="INE111Y01011", symbol=None) == "NSE_EQ|SMCO"
    assert resolver.resolve(isin=None, symbol="SMCO") == "NSE_EQ|SMCO"


def test_resolver_ignores_superseded_security_master_rows(
    store: DuckDBStore, resolver: DuckDBInstrumentResolver
) -> None:
    store.conn.execute(
        """
        INSERT INTO security_master_history (
            instrument_id, isin, symbol, exchange, valid_from, known_from, known_to
        ) VALUES ('NSE_EQ|OLDROW', 'INE222Z01012', 'OLDROW', 'NSE', '2000-01-01', ?, ?)
        """,
        [NOW, NOW + timedelta(days=1)],
    )
    assert resolver.resolve(isin="INE222Z01012", symbol="OLDROW") is None


def test_saving_an_isinless_instrument_never_erases_a_known_isin(
    store: DuckDBStore, resolver: DuckDBInstrumentResolver
) -> None:
    """Kite carries no ISIN; re-saving the Kite dump must not wipe the ISIN mapping."""
    _known_instrument(store)
    DuckDBInstrumentRepository(store).save_instruments(
        [Instrument(instrument_id=OLD_ID, symbol="OLDCO", exchange="NSE", isin=None)]
    )
    assert resolver.resolve(isin=ISIN_X, symbol=None) == OLD_ID


# ---------------------------------------------------------------------------
# Market-data ingestion remaps to the permanent ID
# ---------------------------------------------------------------------------


def _fake_bars(instrument_id: str) -> list:
    return [make_candle(instrument_id, date(2024, 1, 2) + timedelta(days=i)) for i in range(3)]


def test_market_ingestion_stores_bars_under_the_permanent_id(
    store: DuckDBStore, resolver: DuckDBInstrumentResolver
) -> None:
    _known_instrument(store)
    repo = DuckDBMarketDataRepository(store)
    provider = FakeMarketDataProvider(candles_by_id={OLD_ID: _fake_bars(OLD_ID)})
    worker = IngestionWorker(provider=provider, repository=repo, resolver=resolver)

    renamed = Instrument(instrument_id=NEW_ID, symbol="NEWCO", exchange="NSE", isin=ISIN_X)
    run = worker.ingest_instrument(
        renamed, date(2024, 1, 2), date(2024, 1, 4), ingestion_time=NOW
    )

    assert run.status == "SUCCESS"
    assert len(repo.load_daily(OLD_ID, date(2024, 1, 1), date(2024, 1, 31))) == 3
    assert repo.load_daily(NEW_ID, date(2024, 1, 1), date(2024, 1, 31)) == []


def test_market_ingestion_without_resolver_keeps_the_provider_id(store: DuckDBStore) -> None:
    repo = DuckDBMarketDataRepository(store)
    provider = FakeMarketDataProvider(candles_by_id={NEW_ID: _fake_bars(NEW_ID)})
    worker = IngestionWorker(provider=provider, repository=repo)

    worker.ingest_instrument(
        Instrument(instrument_id=NEW_ID, symbol="NEWCO", exchange="NSE", isin=ISIN_X),
        date(2024, 1, 2),
        date(2024, 1, 4),
        ingestion_time=NOW,
    )
    assert len(repo.load_daily(NEW_ID, date(2024, 1, 1), date(2024, 1, 31))) == 3


# ---------------------------------------------------------------------------
# Corporate actions: NSE and Upstox must land on the same instrument
# ---------------------------------------------------------------------------


class _FakeCAProvider:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self._actions = actions

    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return self._actions


def _action(source: str, instrument_id: str, ca_id: str) -> CorporateAction:
    return CorporateAction(
        corporate_action_id=ca_id,
        instrument_id=instrument_id,
        action_type=CorporateActionType.SPLIT,
        source=source,
        created_at=NOW,
        isin=ISIN_X,
        ex_date=date(2024, 2, 15),
        ratio_numerator=5.0,
        ratio_denominator=1.0,
    )


def _ca_worker(store: DuckDBStore, resolver) -> tuple[CorporateActionIngestionWorker, object]:  # noqa: ANN001
    repo = DuckDBCorporateActionRepository(store)
    worker = CorporateActionIngestionWorker(
        primary_provider=_FakeCAProvider([_action("NSE", NEW_ID, "NSE-1")]),
        secondary_provider=_FakeCAProvider([_action("UPSTOX", OLD_ID, "UPX-1")]),
        repository=repo,
        reconciliation_engine=ReconciliationEngine(),
        adjustment_engine=AdjustmentEngine(),
        resolver=resolver,
    )
    return worker, repo


def test_nse_and_upstox_reconcile_to_confirmed_on_the_permanent_id(
    store: DuckDBStore, resolver: DuckDBInstrumentResolver
) -> None:
    _known_instrument(store)
    worker, repo = _ca_worker(store, resolver)

    worker.run(date(2024, 1, 1), date(2024, 3, 31))

    resolutions = repo.load_resolutions(OLD_ID)
    assert len(resolutions) == 1
    assert resolutions[0].status == CorporateActionStatus.CONFIRMED
    assert repo.load_resolutions(NEW_ID) == []
    assert len(repo.load_corporate_actions(OLD_ID)) == 2  # both sources, one instrument
    assert len(repo.load_adjustments(OLD_ID)) == 1  # so adjustments can join to prices


def test_without_the_resolver_sources_split_apart_as_the_review_found(
    store: DuckDBStore,
) -> None:
    """Control: this is the pre-fix behaviour the resolver exists to prevent."""
    _known_instrument(store)
    worker, repo = _ca_worker(store, None)

    worker.run(date(2024, 1, 1), date(2024, 3, 31))

    # Each source sits alone on its own instrument, so the two never see each other and
    # nothing can be confirmed. (The exact non-confirmed status depends on the separate
    # grace-period logic, so it is deliberately not asserted here.)
    new_side = repo.load_resolutions(NEW_ID)
    old_side = repo.load_resolutions(OLD_ID)
    assert len(new_side) == 1
    assert new_side[0].nse_action_id is not None and new_side[0].upstox_action_id is None
    assert len(old_side) == 1
    assert old_side[0].upstox_action_id is not None and old_side[0].nse_action_id is None
    assert CorporateActionStatus.CONFIRMED not in {new_side[0].status, old_side[0].status}


# ---------------------------------------------------------------------------
# Security master
# ---------------------------------------------------------------------------


class _FakeSMProvider:
    def __init__(self, records: list[SecurityRecord]) -> None:
        self._records = records

    def get_security_history(self, start, end):  # noqa: ANN001, ANN201
        return self._records


class _NoSurveillance:
    def get_flags(self, start, end):  # noqa: ANN001, ANN201
        return []


def _sm_record(symbol: str) -> SecurityRecord:
    return SecurityRecord(
        instrument_id=mint_instrument_id("NSE", symbol),
        symbol=symbol,
        exchange="NSE",
        valid_from=date(2000, 1, 1),
        isin=ISIN_X,
        series="EQ",
    )


def test_security_master_rename_does_not_fork_a_second_instrument(
    store: DuckDBStore, resolver: DuckDBInstrumentResolver
) -> None:
    first = SecurityMasterIngestionWorker(
        store, _FakeSMProvider([_sm_record("OLDCO")]), _NoSurveillance(), resolver=resolver
    )
    first.run(date(2000, 1, 1), date(2024, 1, 1))

    renamed = SecurityMasterIngestionWorker(
        store, _FakeSMProvider([_sm_record("NEWCO")]), _NoSurveillance(), resolver=resolver
    )
    renamed.run(date(2000, 1, 1), date(2024, 3, 1))

    ids = {
        r[0]
        for r in store.conn.execute(
            "SELECT DISTINCT instrument_id FROM security_master_history"
        ).fetchall()
    }
    assert ids == {OLD_ID}


# ---------------------------------------------------------------------------
# Upstox: keyed by ISIN
# ---------------------------------------------------------------------------


def test_upstox_queries_by_isin_and_skips_instruments_without_one() -> None:
    provider = UpstoxCorporateActionProvider(access_token="token")
    response = MagicMock(status_code=200)
    response.json.return_value = {"data": []}
    instruments = [
        Instrument("NSE_EQ|RELIANCE", "RELIANCE", "NSE", isin="INE002A01018"),
        Instrument("NSE_EQ|NOISIN", "NOISIN", "NSE", isin=None),
    ]

    with patch.object(provider._session, "get", return_value=response) as get:
        provider.get_actions(date(2024, 1, 1), date(2024, 3, 1), instruments)

    assert get.call_count == 1  # the ISIN-less instrument was skipped, not guessed at
    assert get.call_args.kwargs["params"]["instrument_key"] == "NSE_EQ|INE002A01018"


# ---------------------------------------------------------------------------
# End to end: Kite prices join the security master and the universe (review finding 3)
# ---------------------------------------------------------------------------


def test_kite_prices_join_security_master_and_universe_builder(store: DuckDBStore) -> None:
    """Before the fix Kite stored ``RELIANCE`` while the master held ``NSE_EQ|RELIANCE``,
    so the universe joins matched nothing and every instrument was excluded."""
    with patch("vcp_scanner.data.providers.kite.KiteConnect") as mock_kite_cls:
        kite = mock_kite_cls.return_value
        kite.instruments.return_value = [
            {
                "instrument_token": 738561,
                "tradingsymbol": "RELIANCE",
                "name": "RELIANCE INDUSTRIES",
                "instrument_type": "EQ",
                "segment": "NSE",
                "exchange": "NSE",
            }
        ]
        kite.historical_data.return_value = [
            {
                "date": datetime(2024, 1, 2, 0, 0, tzinfo=IST),
                "open": 2500.0,
                "high": 2550.0,
                "low": 2490.0,
                "close": 2520.0,
                "volume": 100_000,
            }
        ]
        provider = KiteProvider("api_key", "access_token")
        instrument = provider.get_instruments()[0]

        repo = DuckDBMarketDataRepository(store)
        run = IngestionWorker(provider=provider, repository=repo).ingest_instrument(
            instrument, date(2024, 1, 1), date(2024, 1, 5), ingestion_time=NOW
        )
    assert run.status == "SUCCESS"

    SecurityMasterIngestionWorker(
        store, _FakeSMProvider([_sm_record("RELIANCE")]), _NoSurveillance()
    ).run(date(2000, 1, 1), date(2024, 1, 5))

    _, memberships = UniverseBuilder(
        store, UniverseConfig(exchange="NSE", eligible_series=["EQ"])
    ).build_snapshot(as_of_date=date(2024, 1, 10))

    assert [m.instrument_id for m in memberships] == ["NSE_EQ|RELIANCE"]
    assert memberships[0].eligible is True, memberships[0].exclusion_reason
    assert memberships[0].series == "EQ"  # came from the security-master join
