"""Today's PROVISIONAL Kite bar (audit step 2.5).

Stored before the NSE bhavcopy is published, ignored by every read path unless explicitly
allowed, superseded by the bhavcopy bar, and never allowed to supersede it.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr
from datetime import UTC, date, datetime
from pathlib import Path

from vcp_scanner.cli import main
from vcp_scanner.data.adjustment.builder import AdjustedPriceBuilder
from vcp_scanner.data.ingestion.worker import IngestionWorker
from vcp_scanner.data.providers.fake import FakeMarketDataProvider, make_candle
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import (
    DuckDBMarketDataRepository,
    FinalDailyBar,
)
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.market import Instrument

IID = "NSE_EQ|ABC"
YESTERDAY, TODAY = date(2026, 9, 30), date(2026, 10, 1)
T_MORNING = datetime(2026, 10, 1, 6, tzinfo=UTC)  # 11:30 IST
T_NOON = datetime(2026, 10, 1, 8, tzinfo=UTC)
T_EVENING = datetime(2026, 10, 1, 14, tzinfo=UTC)  # after the bhavcopy


def _store() -> tuple[DuckDBStore, DuckDBMarketDataRepository]:
    store = DuckDBStore(":memory:")
    store.migrate()
    market = DuckDBMarketDataRepository(store)
    market.save_final_daily(
        [FinalDailyBar(IID, YESTERDAY, 100, 102, 99, 101, 1000, "EQ")],
        known_from=T_MORNING,
        source_run_id="bhav",
    )
    return store, market


def _kite(close: float, d: date = TODAY) -> list:  # type: ignore[type-arg]
    candle = make_candle(IID, d, open_=101, high=max(105, close), low=98, close=close,
                         provider="KITE")  # fmt: skip
    return [candle]


def _current(store: DuckDBStore, d: date) -> tuple[str, str, float] | None:
    row = store.conn.execute(
        "SELECT primary_provider, data_status, close_raw FROM daily_prices"
        " WHERE instrument_id = ? AND trade_date = ? AND known_to IS NULL",
        [IID, d],
    ).fetchone()
    return None if row is None else (str(row[0]), str(row[1]), float(row[2]))


def test_provisional_bar_is_hidden_unless_asked_for() -> None:
    store, market = _store()
    assert market.save_daily(_kite(103.0), data_status="PROVISIONAL") == 1
    assert [c.timestamp.date() for c in market.load_daily(IID, YESTERDAY, TODAY)] == [YESTERDAY]
    with_prov = market.load_daily(IID, YESTERDAY, TODAY, include_provisional=True)
    assert [c.timestamp.date() for c in with_prov] == [YESTERDAY, TODAY]
    as_of = market.load_daily_as_of(IID, YESTERDAY, TODAY, datetime(2027, 1, 1, tzinfo=UTC))
    assert len(as_of) == 1


def test_provisional_bar_updates_intraday_then_bhavcopy_supersedes_it() -> None:
    store, market = _store()
    market.save_daily(_kite(103.0), data_status="PROVISIONAL")
    market.save_daily(_kite(104.5), data_status="PROVISIONAL")  # a later intraday fetch
    assert _current(store, TODAY) == ("KITE", "PROVISIONAL", 104.5)

    saved = market.save_final_daily(
        [FinalDailyBar(IID, TODAY, 101, 106, 98, 104.0, 2000, "EQ")],
        known_from=T_EVENING,
        source_run_id="bhav",
    )
    assert saved.superseded == {"KITE": 1}
    assert _current(store, TODAY) == ("NSE_BHAVCOPY", "OK", 104.0)
    # A late provisional fetch cannot overwrite the final bar.
    assert market.save_daily(_kite(104.6), data_status="PROVISIONAL") == 0
    assert _current(store, TODAY) == ("NSE_BHAVCOPY", "OK", 104.0)


def test_adjusted_rows_include_provisional_only_when_allowed_and_are_pruned_after() -> None:
    store, market = _store()
    market.save_daily(_kite(103.0), data_status="PROVISIONAL")
    ca = DuckDBCorporateActionRepository(store)

    def adjusted_dates() -> list[date]:
        return [r.trade_date for r in market.load_adjusted_daily(IID, YESTERDAY, TODAY)]

    AdjustedPriceBuilder(market, ca, include_provisional=True).build_for_instrument(
        IID, computed_at=T_NOON
    )
    assert adjusted_dates() == [YESTERDAY, TODAY]
    AdjustedPriceBuilder(market, ca).build_for_instrument(IID, computed_at=T_EVENING)
    assert adjusted_dates() == [YESTERDAY]


def test_universe_counts_provisional_bar_only_when_allowed() -> None:
    store, market = _store()
    market.save_daily(_kite(103.0), data_status="PROVISIONAL")
    repo = DuckDBUniverseRepository(store)
    at = datetime(2027, 1, 1, tzinfo=UTC)
    (default,) = repo.load_universe_candidates(TODAY, at, ["KITE"])
    assert default.last_trade_date == YESTERDAY
    (allowed,) = repo.load_universe_candidates(TODAY, at, ["KITE"], include_provisional=True)
    assert allowed.last_trade_date == TODAY


def test_worker_stores_provisional_and_always_refetches() -> None:
    store, market = _store()
    provider = FakeMarketDataProvider(candles_by_id={IID: _kite(103.0)})
    worker = IngestionWorker(provider=provider, repository=market)
    instrument = Instrument(IID, "ABC")
    run = worker.ingest_instrument(instrument, TODAY, TODAY, provisional=True)
    assert run.status == "SUCCESS" and run.records_written == 1
    assert _current(store, TODAY) == ("KITE", "PROVISIONAL", 103.0)

    later = FakeMarketDataProvider(candles_by_id={IID: _kite(105.0)})
    worker = IngestionWorker(provider=later, repository=market)
    again = worker.ingest_instrument(instrument, TODAY, TODAY, provisional=True)
    assert again.records_written == 1  # not skipped as "already covered"
    assert _current(store, TODAY)[1:] == ("PROVISIONAL", 105.0)  # type: ignore[index]


def test_market_ingest_needs_an_explicit_mode(tmp_path: Path) -> None:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["ingest", "market", "--db", str(tmp_path / "t.duckdb")])
    assert code == 1
    assert "vcp ingest bhavcopy" in stderr.getvalue()
