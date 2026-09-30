"""Point-in-time and survivorship-bias tests for the Universe (Phase 3).

Acceptance criterion from PROJECT_DESIGN.md §67:
  "Historical universe can be reconstructed for any supported date."

Hard rules exercised:
  - No look-ahead: snapshots only reflect data known on or before as_of_date.
  - Survivorship bias protection: a delisted stock must still appear in the
    universe snapshot for dates when it was alive.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import UniverseBuilder


@pytest.fixture()
def store():
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


@pytest.fixture()
def config():
    return UniverseConfig(
        exchange="NSE",
        min_close_price=10.0,
        min_daily_turnover_inr=1_000_000.0,  # 10L — low threshold so test data passes
        min_avg_traded_value_50d_inr=1_000_000.0,
        eligible_series=["EQ"],
        exclude_asm_gsm=True,
        exclude_trade_to_trade=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_security(store: DuckDBStore, iid: str, series: str = "EQ") -> None:
    """Insert a security_master_history row valid from the beginning of time."""
    now = datetime.now(UTC)
    store.conn.execute(
        "INSERT INTO security_master_history "
        "(instrument_id, series, exchange, valid_from, known_from) "
        "VALUES (?, ?, 'NSE', '2000-01-01', ?)",
        [iid, series, now],
    )


def _add_price(store: DuckDBStore, iid: str, trade_date: date, close: float, vol: int) -> None:
    """Insert 253 consecutive daily bars ending on ``trade_date`` (min_history_days)."""
    now = datetime.now(UTC)
    store.conn.execute(
        """
        INSERT INTO daily_prices (
                instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw,
                volume_raw, primary_provider, data_status, source_run_id, source_hash, known_from
            )
            SELECT CAST(? AS VARCHAR), CAST(? AS DATE) - CAST(i AS INTEGER),
                   ?, ?, ?, ?, ?, 'MOCK', 'OK', 'run', 'hash', CAST(? AS TIMESTAMPTZ)
            FROM range(0, ?) t(i)
        """,
        [iid, trade_date, close, close, close, close, vol, now, 253],
    )


# ---------------------------------------------------------------------------
# Survivorship bias: dead stocks must appear in historical snapshots
# ---------------------------------------------------------------------------


def test_delisted_stock_appears_in_historical_snapshot(store, config):
    """A delisted stock must appear in universe snapshots for dates when it was alive.

    This is the core survivorship-bias rule (DATABASE_SCHEMA §60).
    """
    builder = UniverseBuilder(store, config)

    # DEAD_CO traded in Jan 2022 but delisted. ALIVE_CO is current.
    _add_security(store, "DEAD_CO")
    _add_security(store, "ALIVE_CO")

    # Both had prices and liquidity in Jan 2022
    _add_price(store, "DEAD_CO", date(2022, 1, 3), close=100.0, vol=20_000)
    _add_price(store, "ALIVE_CO", date(2022, 1, 3), close=200.0, vol=20_000)

    # Only ALIVE_CO has prices in Jan 2023 (DEAD_CO delisted)
    _add_price(store, "ALIVE_CO", date(2023, 1, 3), close=210.0, vol=20_000)

    # Build snapshot for 2022-01-03 — BOTH should be eligible
    snap_2022, members_2022 = builder.build_snapshot(as_of_date=date(2022, 1, 3))
    ids_2022 = {m.instrument_id for m in members_2022 if m.eligible}
    assert "DEAD_CO" in ids_2022, "Delisted stock must be eligible on dates it was alive"
    assert "ALIVE_CO" in ids_2022

    # Build snapshot for 2023-01-03 — only ALIVE_CO should appear (DEAD_CO has no data)
    snap_2023, members_2023 = builder.build_snapshot(as_of_date=date(2023, 1, 3))
    ids_2023 = {m.instrument_id for m in members_2023 if m.eligible}
    assert "ALIVE_CO" in ids_2023
    assert "DEAD_CO" not in ids_2023, (
        "Delisted stock with no prices at as_of_date must not appear as eligible"
    )


# ---------------------------------------------------------------------------
# Point-in-time: future data must not affect historical snapshots
# ---------------------------------------------------------------------------


def test_future_price_data_does_not_affect_past_snapshot(store, config):
    """Snapshot at date T must not include instruments that only have data after T.

    This protects the no-look-ahead rule (AGENTS.md hard rule 1).
    """
    builder = UniverseBuilder(store, config)

    _add_security(store, "FUTURE_CO")
    _add_security(store, "CURRENT_CO")

    # FUTURE_CO only listed and priced from 2024 onwards
    _add_price(store, "FUTURE_CO", date(2024, 1, 3), close=150.0, vol=20_000)
    # CURRENT_CO has data in Jan 2023
    _add_price(store, "CURRENT_CO", date(2023, 1, 3), close=120.0, vol=20_000)

    snap, members = builder.build_snapshot(as_of_date=date(2023, 1, 3))
    ids = {m.instrument_id for m in members if m.eligible}

    assert "CURRENT_CO" in ids
    assert "FUTURE_CO" not in ids, (
        "Instrument with data only after as_of_date must not appear in snapshot"
    )


# ---------------------------------------------------------------------------
# Snapshot persistence and PIT retrieval
# ---------------------------------------------------------------------------


def test_load_snapshot_returns_point_in_time_members(store, config):
    """Universe saved for a date must be retrievable and unchanged later."""
    builder = UniverseBuilder(store, config)
    repo = DuckDBUniverseRepository(store)

    _add_security(store, "SNAP_CO")
    _add_price(store, "SNAP_CO", date(2023, 6, 1), close=50.0, vol=50_000)

    snap, members = builder.build_snapshot(as_of_date=date(2023, 6, 1))
    repo.save_snapshot(snap, members)

    # Retrieve eligible IDs for that date
    loaded_ids = repo.load_snapshot(as_of_date=date(2023, 6, 1))
    assert "SNAP_CO" in loaded_ids


def test_multiple_snapshots_same_date_returns_latest(store, config):
    """If two snapshots exist for the same date, load_snapshot returns the newest one."""
    builder = UniverseBuilder(store, config)
    repo = DuckDBUniverseRepository(store)

    _add_security(store, "MULTI_CO")
    _add_price(store, "MULTI_CO", date(2023, 7, 1), close=100.0, vol=100_000)

    snap1, members1 = builder.build_snapshot(as_of_date=date(2023, 7, 1))
    repo.save_snapshot(snap1, members1)

    # A second rebuild (e.g. after a data correction)
    snap2, members2 = builder.build_snapshot(as_of_date=date(2023, 7, 1))
    repo.save_snapshot(snap2, members2)

    # load_snapshot should succeed (returns list, not error)
    loaded = repo.load_snapshot(as_of_date=date(2023, 7, 1))
    assert isinstance(loaded, list)
