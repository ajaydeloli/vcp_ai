"""Unit tests for ingestion pipeline — FakeMarketDataProvider + in-memory DuckDB.

All tests are deterministic and offline.  No network calls or broker SDKs.

Coverage:
- Round-trip insert + load (test_ingest_creates_daily_rows)
- Idempotency: re-ingesting same range doesn't double-insert (test_ingest_idempotent)
- Bitemporal correction: corrected candle closes old row, opens new
  (test_bitemporal_correction)
- Invalid OHLC rejected at canonical layer (test_invalid_ohlc_rejected)
- ingestion_runs row recorded with correct status (test_ingestion_run_recorded)
- Superseded rows invisible to load_daily (test_load_daily_hides_superseded_rows)
- Point-in-time read works correctly (test_load_daily_as_of)
- Chunk boundary respected: provider called ≤ max_request_days (test_chunk_boundary)
- FakeMarketDataProvider satisfies MarketDataProvider Protocol
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.data.ingestion.worker import IngestionWorker, _date_chunks
from vcp_scanner.data.providers.base import MarketDataProvider
from vcp_scanner.data.providers.fake import SYNTHETIC_PROVIDER, FakeMarketDataProvider, make_candle
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.market import Candle, Instrument

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

INSTRUMENT = Instrument(instrument_id="RELIANCE", symbol="RELIANCE", exchange="NSE")

START = date(2024, 1, 2)
END = date(2024, 1, 5)  # 4 trading days (Mon–Thu)


def _make_candles(instrument_id: str, dates: list[date]) -> list[Candle]:
    return [make_candle(instrument_id, d) for d in dates]


@pytest.fixture()
def store_and_repo() -> tuple[DuckDBStore, DuckDBMarketDataRepository]:
    s = DuckDBStore(":memory:")
    s.migrate()
    repo = DuckDBMarketDataRepository(s)
    return s, repo


@pytest.fixture()
def basic_provider() -> FakeMarketDataProvider:
    dates = [START + timedelta(days=i) for i in range(4)]
    return FakeMarketDataProvider(
        candles_by_id={INSTRUMENT.instrument_id: _make_candles(INSTRUMENT.instrument_id, dates)}
    )


@pytest.fixture()
def worker(
    basic_provider: FakeMarketDataProvider,
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> IngestionWorker:
    _, repo = store_and_repo
    return IngestionWorker(provider=basic_provider, repository=repo)


# ---------------------------------------------------------------------------
# FakeMarketDataProvider satisfies Protocol
# ---------------------------------------------------------------------------


def test_fake_provider_satisfies_protocol() -> None:
    """FakeMarketDataProvider must be a valid MarketDataProvider at runtime."""
    provider = FakeMarketDataProvider()
    assert isinstance(provider, MarketDataProvider)


# ---------------------------------------------------------------------------
# Round-trip: ingest → load
# ---------------------------------------------------------------------------


def test_ingest_creates_daily_rows(
    worker: IngestionWorker,
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """Ingesting 4 candles should produce 4 current rows in daily_prices."""
    _, repo = store_and_repo
    fixed_time = datetime(2024, 1, 10, 9, 0, tzinfo=UTC)

    run = worker.ingest_instrument(INSTRUMENT, START, END, ingestion_time=fixed_time)

    assert run.status == "SUCCESS", f"Expected SUCCESS, got {run.status}"
    assert run.records_written == 4

    loaded = repo.load_daily(INSTRUMENT.instrument_id, START, END)
    assert len(loaded) == 4
    assert all(c.instrument_id == INSTRUMENT.instrument_id for c in loaded)
    assert all(c.provider == SYNTHETIC_PROVIDER for c in loaded)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_ingest_idempotent(
    worker: IngestionWorker,
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """Re-ingesting the exact same candles must not create duplicate rows."""
    _, repo = store_and_repo
    fixed_time = datetime(2024, 1, 10, 9, 0, tzinfo=UTC)

    worker.ingest_instrument(INSTRUMENT, START, END, ingestion_time=fixed_time, force=True)
    worker.ingest_instrument(INSTRUMENT, START, END, ingestion_time=fixed_time, force=True)

    # Only current rows
    count = repo._store.conn.execute(
        "SELECT COUNT(*) FROM daily_prices WHERE instrument_id = ? AND known_to IS NULL",
        [INSTRUMENT.instrument_id],
    ).fetchone()
    assert count is not None and count[0] == 4, f"Expected 4 current rows, got {count[0]}"


# ---------------------------------------------------------------------------
# Bitemporal correction
# ---------------------------------------------------------------------------


def test_bitemporal_correction(
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """A corrected candle must close the old row and open a new current row."""
    _, repo = store_and_repo
    trade_date = date(2024, 1, 2)
    t1 = datetime(2024, 1, 3, 9, 0, tzinfo=UTC)
    t2 = datetime(2024, 1, 4, 9, 0, tzinfo=UTC)

    original = make_candle(INSTRUMENT.instrument_id, trade_date, close=100.0)
    corrected = make_candle(INSTRUMENT.instrument_id, trade_date, close=101.0)

    # Set explicit ingested_at so known_from is deterministic
    from dataclasses import replace

    original = replace(original, ingested_at=t1)
    corrected = replace(corrected, ingested_at=t2)

    repo.save_daily([original])
    repo.save_daily([corrected])

    # Should be exactly 1 current row
    current = repo.load_daily(INSTRUMENT.instrument_id, trade_date, trade_date)
    assert len(current) == 1
    assert current[0].close == 101.0, f"Expected 101.0 close, got {current[0].close}"

    # And 2 total rows (old closed + new current)
    all_rows = repo._store.conn.execute(
        "SELECT COUNT(*) FROM daily_prices WHERE instrument_id = ? AND trade_date = ?",
        [INSTRUMENT.instrument_id, trade_date],
    ).fetchone()
    assert all_rows is not None and all_rows[0] == 2

    closed = repo._store.conn.execute(
        """
        SELECT known_to FROM daily_prices
        WHERE instrument_id = ? AND trade_date = ? AND known_to IS NOT NULL
        """,
        [INSTRUMENT.instrument_id, trade_date],
    ).fetchone()
    assert closed is not None


# ---------------------------------------------------------------------------
# OHLC validation — reject invalid bars
# ---------------------------------------------------------------------------


def test_invalid_ohlc_rejected(
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """A candle with low > close must be rejected from daily_prices (but kept in raw_ohlcv)."""
    _, repo = store_and_repo
    bad_candle = make_candle(
        INSTRUMENT.instrument_id,
        date(2024, 1, 2),
        open_=100.0,
        high=110.0,
        low=108.0,  # low > close → invalid
        close=105.0,
    )
    inserted = repo.save_daily([bad_candle])
    assert inserted == 0, "Invalid candle should not be inserted into daily_prices"

    loaded = repo.load_daily(INSTRUMENT.instrument_id, date(2024, 1, 2), date(2024, 1, 2))
    assert len(loaded) == 0


def test_negative_volume_rejected(
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """A candle with negative volume must be rejected."""
    _, repo = store_and_repo
    bad = make_candle(INSTRUMENT.instrument_id, date(2024, 1, 2), volume=-1)
    inserted = repo.save_daily([bad])
    assert inserted == 0


def test_none_volume_accepted(
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """A candle with volume=None is valid — provider may omit volume (rule 4)."""
    _, repo = store_and_repo
    no_vol = make_candle(INSTRUMENT.instrument_id, date(2024, 1, 2), volume=None)
    inserted = repo.save_daily([no_vol])
    assert inserted == 1


# ---------------------------------------------------------------------------
# Ingestion run tracking
# ---------------------------------------------------------------------------


def test_ingestion_run_recorded(
    worker: IngestionWorker,
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """After ingest, a SUCCESS ingestion_run row must be present in the DB."""
    _, repo = store_and_repo
    fixed_time = datetime(2024, 1, 10, 9, 0, tzinfo=UTC)

    run = worker.ingest_instrument(INSTRUMENT, START, END, ingestion_time=fixed_time)

    stored = repo.load_ingestion_run(run.ingestion_run_id)
    assert stored is not None
    assert stored.status == "SUCCESS"
    assert stored.provider == "FakeMarketDataProvider"
    assert stored.dataset == "daily_ohlcv"
    assert stored.records_written == 4
    assert stored.records_rejected == 0


# ---------------------------------------------------------------------------
# Superseded rows invisible to load_daily
# ---------------------------------------------------------------------------


def test_load_daily_hides_superseded_rows(
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """load_daily() must return only current (known_to IS NULL) rows."""
    _, repo = store_and_repo
    from dataclasses import replace

    trade_date = date(2024, 1, 2)
    t1 = datetime(2024, 1, 3, 9, 0, tzinfo=UTC)
    t2 = datetime(2024, 1, 4, 9, 0, tzinfo=UTC)

    v1 = replace(make_candle(INSTRUMENT.instrument_id, trade_date, close=100.0), ingested_at=t1)
    v2 = replace(make_candle(INSTRUMENT.instrument_id, trade_date, close=99.0), ingested_at=t2)

    repo.save_daily([v1])
    repo.save_daily([v2])

    loaded = repo.load_daily(INSTRUMENT.instrument_id, trade_date, trade_date)
    assert len(loaded) == 1
    assert loaded[0].close == 99.0


# ---------------------------------------------------------------------------
# Point-in-time read (as_of)
# ---------------------------------------------------------------------------


def test_load_daily_as_of(
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """load_daily_as_of should return the version current at the specified system time."""
    _, repo = store_and_repo
    from dataclasses import replace

    trade_date = date(2024, 1, 2)
    t1 = datetime(2024, 1, 3, 9, 0, tzinfo=UTC)
    t2 = datetime(2024, 1, 4, 9, 0, tzinfo=UTC)

    v1 = replace(make_candle(INSTRUMENT.instrument_id, trade_date, close=100.0), ingested_at=t1)
    v2 = replace(make_candle(INSTRUMENT.instrument_id, trade_date, close=99.0), ingested_at=t2)

    repo.save_daily([v1])
    repo.save_daily([v2])

    # At t1 + 1 second, should see close=100
    at_t1 = repo.load_daily_as_of(
        INSTRUMENT.instrument_id,
        trade_date,
        trade_date,
        known_at=datetime(2024, 1, 3, 9, 0, 1, tzinfo=UTC),
    )
    assert len(at_t1) == 1
    assert at_t1[0].close == 100.0

    # After t2, should see close=99
    at_t2 = repo.load_daily_as_of(
        INSTRUMENT.instrument_id,
        trade_date,
        trade_date,
        known_at=datetime(2024, 1, 4, 9, 0, 1, tzinfo=UTC),
    )
    assert len(at_t2) == 1
    assert at_t2[0].close == 99.0


# ---------------------------------------------------------------------------
# Chunk boundary: provider called ≤ max_request_days per call
# ---------------------------------------------------------------------------


def test_chunk_boundary_respected() -> None:
    """Provider must never be called with a date range > max_request_days."""
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(10)]
    candles = _make_candles(INSTRUMENT.instrument_id, dates)
    provider = FakeMarketDataProvider(
        candles_by_id={INSTRUMENT.instrument_id: candles},
        max_request_days=3,
    )

    s = DuckDBStore(":memory:")
    s.migrate()
    repo = DuckDBMarketDataRepository(s)
    w = IngestionWorker(provider=provider, repository=repo)

    fixed_time = datetime(2024, 2, 1, 9, 0, tzinfo=UTC)
    w.ingest_instrument(
        INSTRUMENT,
        dates[0],
        dates[-1],
        ingestion_time=fixed_time,
        force=True,
    )

    daily_calls = [c for c in provider.call_log if c["method"] == "get_historical_daily"]
    for call in daily_calls:
        span = (call["end"] - call["start"]).days + 1
        assert span <= 3, f"Provider called with {span} days, limit is 3"

    # At least ceil(10/3) = 4 calls
    assert len(daily_calls) >= 4


# ---------------------------------------------------------------------------
# _date_chunks helper
# ---------------------------------------------------------------------------


def test_date_chunks_single_chunk() -> None:
    chunks = _date_chunks(date(2024, 1, 1), date(2024, 1, 5), max_days=10)
    assert chunks == [(date(2024, 1, 1), date(2024, 1, 5))]


def test_date_chunks_exact_boundary() -> None:
    chunks = _date_chunks(date(2024, 1, 1), date(2024, 1, 3), max_days=3)
    assert chunks == [(date(2024, 1, 1), date(2024, 1, 3))]


def test_date_chunks_splits_correctly() -> None:
    chunks = _date_chunks(date(2024, 1, 1), date(2024, 1, 9), max_days=3)
    assert len(chunks) == 3
    assert chunks[0] == (date(2024, 1, 1), date(2024, 1, 3))
    assert chunks[1] == (date(2024, 1, 4), date(2024, 1, 6))
    assert chunks[2] == (date(2024, 1, 7), date(2024, 1, 9))


def test_date_chunks_no_overlap() -> None:
    """Consecutive chunks must not overlap."""
    chunks = _date_chunks(date(2024, 1, 1), date(2024, 1, 15), max_days=4)
    for i in range(len(chunks) - 1):
        _, end_i = chunks[i]
        start_next, _ = chunks[i + 1]
        assert start_next == end_i + timedelta(days=1)


# ---------------------------------------------------------------------------
# latest_timestamp — no data → None
# ---------------------------------------------------------------------------


def test_latest_timestamp_none_when_empty(
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """latest_timestamp should return None, not 0 or a default date, for unknown instruments."""
    _, repo = store_and_repo
    result = repo.latest_timestamp("NONEXISTENT")
    assert result is None


def test_latest_timestamp_correct(
    store_and_repo: tuple[DuckDBStore, DuckDBMarketDataRepository],
) -> None:
    """latest_timestamp must return the most recent trade_date."""
    _, repo = store_and_repo
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    candles = [make_candle(INSTRUMENT.instrument_id, d) for d in dates]
    repo.save_daily(candles)

    latest = repo.latest_timestamp(INSTRUMENT.instrument_id)
    assert latest is not None
    assert latest.date() == date(2024, 1, 4)


# ---------------------------------------------------------------------------
# Review fixes: backfill of older history, incremental tail, rate-limit throttle
# ---------------------------------------------------------------------------


def _calls(provider: FakeMarketDataProvider) -> list[tuple[date, date]]:
    return [
        (c["start"], c["end"]) for c in provider.call_log if c["method"] == "get_historical_daily"
    ]


def test_backfill_fetches_history_older_than_stored_data() -> None:
    """Requesting 2020-2024 after 2023-2024 was ingested must fetch the missing 2020-2022."""
    all_dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(20)]
    provider = FakeMarketDataProvider(
        candles_by_id={INSTRUMENT.instrument_id: _make_candles(INSTRUMENT.instrument_id, all_dates)}
    )
    s = DuckDBStore(":memory:")
    s.migrate()
    repo = DuckDBMarketDataRepository(s)
    w = IngestionWorker(provider=provider, repository=repo)
    t = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)

    # First: only the later part.
    w.ingest_instrument(INSTRUMENT, date(2024, 1, 10), date(2024, 1, 20), ingestion_time=t)
    provider.call_log.clear()

    # Second: a wider window that starts earlier. Old history must be requested.
    w.ingest_instrument(INSTRUMENT, date(2024, 1, 1), date(2024, 1, 20), ingestion_time=t)

    assert _calls(provider) == [(date(2024, 1, 1), date(2024, 1, 9))]
    earliest = repo.earliest_timestamp(INSTRUMENT.instrument_id)
    assert earliest is not None and earliest.date() == date(2024, 1, 1)


def test_incremental_fetch_only_requests_new_tail() -> None:
    all_dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(20)]
    provider = FakeMarketDataProvider(
        candles_by_id={INSTRUMENT.instrument_id: _make_candles(INSTRUMENT.instrument_id, all_dates)}
    )
    s = DuckDBStore(":memory:")
    s.migrate()
    repo = DuckDBMarketDataRepository(s)
    w = IngestionWorker(provider=provider, repository=repo)
    t = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)

    w.ingest_instrument(INSTRUMENT, date(2024, 1, 1), date(2024, 1, 10), ingestion_time=t)
    provider.call_log.clear()
    w.ingest_instrument(INSTRUMENT, date(2024, 1, 1), date(2024, 1, 20), ingestion_time=t)

    assert _calls(provider) == [(date(2024, 1, 11), date(2024, 1, 20))]


def test_fully_covered_request_makes_no_provider_call() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(10)]
    provider = FakeMarketDataProvider(
        candles_by_id={INSTRUMENT.instrument_id: _make_candles(INSTRUMENT.instrument_id, dates)}
    )
    s = DuckDBStore(":memory:")
    s.migrate()
    w = IngestionWorker(provider=provider, repository=DuckDBMarketDataRepository(s))
    t = datetime(2024, 3, 1, 9, 0, tzinfo=UTC)

    w.ingest_instrument(INSTRUMENT, dates[0], dates[-1], ingestion_time=t)
    provider.call_log.clear()
    w.ingest_instrument(INSTRUMENT, dates[0], dates[-1], ingestion_time=t)

    assert _calls(provider) == []


def test_requests_are_throttled_to_provider_rate_limit() -> None:
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(10)]
    provider = FakeMarketDataProvider(
        candles_by_id={INSTRUMENT.instrument_id: _make_candles(INSTRUMENT.instrument_id, dates)},
        max_request_days=2,  # 5 chunks
    )
    caps = provider.get_capabilities()
    assert caps.historical_requests_per_second > 0

    now = [0.0]
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    s = DuckDBStore(":memory:")
    s.migrate()
    w = IngestionWorker(
        provider=provider,
        repository=DuckDBMarketDataRepository(s),
        sleep=fake_sleep,
        monotonic=lambda: now[0],
    )
    w.ingest_instrument(
        INSTRUMENT, dates[0], dates[-1], ingestion_time=datetime(2024, 2, 1, tzinfo=UTC), force=True
    )

    interval = 1.0 / caps.historical_requests_per_second
    assert len(sleeps) == 4  # 5 requests -> 4 waits (none before the first)
    assert all(abs(x - interval) < 1e-9 for x in sleeps)
