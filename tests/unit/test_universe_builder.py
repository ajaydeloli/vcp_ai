from datetime import UTC, date, datetime

import pytest

from vcp_scanner.config.models import UniverseConfig
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.universe.builder import UniverseBuilder


@pytest.fixture
def store():
    with DuckDBStore(":memory:") as s:
        s.migrate()
        yield s


def test_universe_builder_filters_correctly(store):
    config = UniverseConfig(
        exchange="NSE",
        min_close_price=10.0,
        min_daily_turnover_inr=5_000_000.0,
        eligible_series=["EQ"],
        exclude_asm_gsm=True,
    )
    builder = UniverseBuilder(store, config)

    # Insert mock data
    now = datetime.now(UTC)

    def insert_price(iid, price, vol):
        store.conn.execute(
            """
            INSERT INTO daily_prices (
                instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw,
                volume_raw, primary_provider, data_status, source_run_id, source_hash, known_from
            )
            VALUES (?, '2023-01-01', ?, ?, ?, ?, ?, 'MOCK', 'OK', 'run', 'hash', ?)
            """,
            [iid, price, price, price, price, vol, now],
        )

    def insert_sm(iid, series):
        store.conn.execute(
            """
            INSERT INTO security_master_history (
                instrument_id, series, exchange, valid_from, known_from
            )
            VALUES (?, ?, 'NSE', '2000-01-01', ?)
            """,
            [iid, series, now],
        )

    def insert_asm(iid):
        store.conn.execute(
            """
            INSERT INTO surveillance_flags_history (
                instrument_id, flag_type, valid_from, known_from
            )
            VALUES (?, 'ASM', '2020-01-01', ?)
            """,
            [iid, now],
        )

    # 1. Valid instrument (10M traded value)
    insert_sm("valid_1", "EQ")
    insert_price("valid_1", 100, 100000)

    # 2. Invalid price
    insert_sm("low_price", "EQ")
    insert_price("low_price", 5, 2000000)

    # 3. Invalid series
    insert_sm("bad_series", "BE")
    insert_price("bad_series", 100, 100000)

    # 4. ASM flagged
    insert_sm("asm_stock", "EQ")
    insert_price("asm_stock", 100, 100000)
    insert_asm("asm_stock")

    snapshot, memberships = builder.build_snapshot(as_of_date=date(2023, 1, 1))

    assert len(memberships) == 4

    valid_members = [m for m in memberships if m.instrument_id == "valid_1"]
    assert len(valid_members) == 1
    assert valid_members[0].eligible is True

    low_price = [m for m in memberships if m.instrument_id == "low_price"][0]
    assert low_price.eligible is False
    assert "Price" in low_price.exclusion_reason

    bad_series = [m for m in memberships if m.instrument_id == "bad_series"][0]
    assert bad_series.eligible is False
    assert "Series" in bad_series.exclusion_reason

    asm_stock = [m for m in memberships if m.instrument_id == "asm_stock"][0]
    assert asm_stock.eligible is False
    assert "ASM" in asm_stock.exclusion_reason
