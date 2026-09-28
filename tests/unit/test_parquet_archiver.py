"""Unit tests for ParquetArchiver."""

from __future__ import annotations

from datetime import UTC, date, datetime
import pytest

from vcp_scanner.data.schema import RawOHLCVRow
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.storage.parquet_archiver import ParquetArchiver


@pytest.fixture()
def setup_store(tmp_path) -> tuple[DuckDBStore, DuckDBMarketDataRepository, ParquetArchiver]:
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBMarketDataRepository(store)
    
    raw_dir = tmp_path / "raw"
    canon_dir = tmp_path / "canonical"
    
    archiver = ParquetArchiver(store, raw_dir, canon_dir)
    return store, repo, archiver


def test_archive_raw_ohlcv(setup_store, tmp_path) -> None:
    _, repo, archiver = setup_store
    
    row = RawOHLCVRow(
        provider="KITE",
        provider_instrument_id="RELIANCE",
        instrument_id="RELIANCE",
        timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        interval="1d",
        open=100.0,
        high=105.0,
        low=98.0,
        close=102.0,
        volume=1000,
        oi=None,
        received_at=datetime(2024, 1, 2, 10, 0, tzinfo=UTC),
        ingestion_run_id="run-1",
        source_hash="hash-1",
    )
    repo.save_raw_ohlcv([row])
    
    archiver.archive_raw_ohlcv()
    
    # Check if partition folders were created
    raw_dir = tmp_path / "raw" / "raw_ohlcv"
    provider_dir = raw_dir / "provider=KITE"
    assert provider_dir.exists()
    
    instrument_dir = provider_dir / "instrument_id=RELIANCE"
    assert instrument_dir.exists()
    
    parquet_files = list(instrument_dir.glob("*.parquet"))
    assert len(parquet_files) > 0


def test_archive_daily_prices(setup_store, tmp_path) -> None:
    _, repo, archiver = setup_store
    
    # Needs to be a Candle but we can just insert directly for speed or use save_daily
    # Let's use save_daily with a mock Candle to ensure the bitemporal logic holds
    from vcp_scanner.domain.market import Candle
    from vcp_scanner.domain.enums import Timeframe
    
    candle = Candle(
        instrument_id="TCS",
        timestamp=datetime(2024, 1, 2, tzinfo=UTC),
        timeframe=Timeframe.DAILY,
        open=3000.0,
        high=3050.0,
        low=2990.0,
        close=3020.0,
        volume=5000,
        provider="KITE"
    )
    
    repo.save_daily([candle])
    
    archiver.archive_daily_prices()
    
    canon_dir = tmp_path / "canonical" / "daily_prices"
    instrument_dir = canon_dir / "instrument_id=TCS"
    assert instrument_dir.exists()
    
    parquet_files = list(instrument_dir.glob("*.parquet"))
    assert len(parquet_files) > 0
