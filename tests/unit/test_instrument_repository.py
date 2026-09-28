"""Unit tests for DuckDBInstrumentRepository.

Tests UPSERT capability and loading of active instruments.
"""

from __future__ import annotations

import pytest

from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.repositories.duckdb_instrument_repository import DuckDBInstrumentRepository
from vcp_scanner.domain.market import Instrument


@pytest.fixture()
def store_and_repo() -> tuple[DuckDBStore, DuckDBInstrumentRepository]:
    s = DuckDBStore(":memory:")
    s.migrate()
    repo = DuckDBInstrumentRepository(s)
    return s, repo


def test_save_and_load_instruments(store_and_repo: tuple[DuckDBStore, DuckDBInstrumentRepository]) -> None:
    _, repo = store_and_repo
    
    instruments = [
        Instrument("RELIANCE", "RELIANCE", "NSE", "INE002A01018", "Reliance Industries", "EQ"),
        Instrument("TCS", "TCS", "NSE", "INE467B01029", "Tata Consultancy", "EQ"),
    ]
    
    # Insert
    inserted = repo.save_instruments(instruments)
    assert inserted == 2
    
    loaded = repo.load_instruments()
    assert len(loaded) == 2
    loaded_ids = {i.instrument_id for i in loaded}
    assert "RELIANCE" in loaded_ids
    assert "TCS" in loaded_ids


def test_upsert_instruments(store_and_repo: tuple[DuckDBStore, DuckDBInstrumentRepository]) -> None:
    """Testing that saving an instrument twice overwrites/updates without failing."""
    _, repo = store_and_repo
    
    inst1 = Instrument("RELIANCE", "RELIANCE", "NSE", "INE002A01018", "Reliance", "EQ")
    repo.save_instruments([inst1])
    
    # Upsert with new name
    inst1_updated = Instrument("RELIANCE", "RELIANCE", "NSE", "INE002A01018", "Reliance Industries Ltd", "EQ")
    repo.save_instruments([inst1_updated])
    
    loaded = repo.load_instruments()
    assert len(loaded) == 1
    assert loaded[0].name == "Reliance Industries Ltd"
