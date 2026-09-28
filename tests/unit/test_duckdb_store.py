"""Unit tests for DuckDBStore — schema creation and migration (DATABASE_SCHEMA §3, §8, §11–14).

All tests use an in-memory DuckDB database (':memory:') — no filesystem I/O.
"""

from __future__ import annotations

import pytest

from vcp_scanner.data.storage.duckdb_store import DuckDBStore


@pytest.fixture()
def store() -> DuckDBStore:
    """In-memory DuckDB store, migrated and ready."""
    s = DuckDBStore(":memory:")
    s.migrate()
    return s


# ---------------------------------------------------------------------------
# Migration idempotency
# ---------------------------------------------------------------------------

def test_migrate_creates_all_tables(store: DuckDBStore) -> None:
    """migrate() must create all five Phase 1 tables."""
    expected_tables = {
        "instruments",
        "ingestion_runs",
        "raw_ohlcv",
        "daily_prices",
        "daily_prices_adjusted",
    }
    result = store.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    actual = {row[0] for row in result}
    assert expected_tables.issubset(actual), (
        f"Missing tables: {expected_tables - actual}"
    )


def test_migrate_is_idempotent(store: DuckDBStore) -> None:
    """Calling migrate() twice must not raise and must leave tables intact."""
    store.migrate()  # second call
    store.migrate()  # third call

    row_count = store.conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchone()
    assert row_count is not None
    assert row_count[0] >= 5


# ---------------------------------------------------------------------------
# Table column spot-checks
# ---------------------------------------------------------------------------

def test_daily_prices_has_bitemporal_columns(store: DuckDBStore) -> None:
    """daily_prices must have known_from and known_to for bitemporal reads."""
    cols = store.conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'daily_prices' AND table_schema = 'main'"
    ).fetchall()
    col_names = {row[0] for row in cols}
    assert "known_from" in col_names
    assert "known_to" in col_names


def test_ingestion_runs_has_status_column(store: DuckDBStore) -> None:
    """ingestion_runs must have a status column (RUNNING|SUCCESS|PARTIAL|FAILED)."""
    cols = store.conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ingestion_runs' AND table_schema = 'main'"
    ).fetchall()
    col_names = {row[0] for row in cols}
    assert "status" in col_names
    assert "ingestion_run_id" in col_names


def test_raw_ohlcv_has_no_primary_key(store: DuckDBStore) -> None:
    """raw_ohlcv intentionally has no PK — provenance rows may share logical identity."""
    # DuckDB: if there's no PK, inserting two identical rows must succeed.
    store.conn.execute(
        """
        INSERT INTO raw_ohlcv VALUES (
            'KITE', 'RELIANCE', 'RELIANCE',
            '2024-01-02 00:00:00+00', '1d',
            100, 105, 98, 102, 1000000, NULL,
            '2024-01-03 09:00:00+00', 'run-1', 'hash-abc'
        )
        """
    )
    store.conn.execute(
        """
        INSERT INTO raw_ohlcv VALUES (
            'KITE', 'RELIANCE', 'RELIANCE',
            '2024-01-02 00:00:00+00', '1d',
            100, 105, 98, 102, 1000000, NULL,
            '2024-01-04 09:00:00+00', 'run-2', 'hash-abc'
        )
        """
    )
    count = store.conn.execute("SELECT COUNT(*) FROM raw_ohlcv").fetchone()
    assert count is not None and count[0] == 2


# ---------------------------------------------------------------------------
# Context-manager lifecycle
# ---------------------------------------------------------------------------

def test_store_context_manager() -> None:
    """DuckDBStore can be used as a context manager without errors."""
    with DuckDBStore(":memory:") as s:
        s.migrate()
        count = s.conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()
        assert count is not None and count[0] == 0
