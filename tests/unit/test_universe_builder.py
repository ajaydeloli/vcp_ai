from datetime import UTC, date, datetime

import pytest

from vcp_scanner.config.models import StalenessConfig, UniverseConfig
from vcp_scanner.data.repositories.duckdb_universe_repository import DuckDBUniverseRepository
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
        min_avg_traded_value_50d_inr=5_000_000.0,
        eligible_series=["EQ"],
        exclude_asm_gsm=True,
    )
    builder = UniverseBuilder(DuckDBUniverseRepository(store), config)

    # Insert mock data
    now = datetime.now(UTC)

    def insert_price(iid, price, vol, bars=253):
        # 253 consecutive bars ending 2023-01-01 satisfies UniverseConfig.min_history_days.
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
            [iid, date(2023, 1, 1), price, price, price, price, vol, now, bars],
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


def _seed(store, iid, bars, last=date(2023, 1, 1), price=100.0, vol=100000):
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
        [iid, last, price, price, price, price, vol, now, bars],
    )
    store.conn.execute(
        "INSERT INTO security_master_history "
        "(instrument_id, series, exchange, valid_from, known_from)"
        " VALUES (?, 'EQ', 'NSE', '2000-01-01', ?)",
        [iid, now],
    )


def _config(**kw):
    return UniverseConfig(
        exchange="NSE",
        min_close_price=10.0,
        min_daily_turnover_inr=5_000_000.0,
        min_avg_traded_value_50d_inr=5_000_000.0,
        eligible_series=["EQ"],
        **kw,
    )


def test_min_history_days_is_enforced(store):
    _seed(store, "LONG", 253)
    _seed(store, "SHORT", 100)
    _, members = UniverseBuilder(DuckDBUniverseRepository(store), _config()).build_snapshot(
        date(2023, 1, 1)
    )
    by_id = {m.instrument_id: m for m in members}
    assert by_id["LONG"].eligible is True
    assert by_id["SHORT"].eligible is False
    assert "History" in by_id["SHORT"].exclusion_reason


def test_staleness_limit_comes_from_config(store):
    """Audit P2-2: staleness counts missed sessions (here the calendar falls back to the
    stored bar dates: no bhavcopy is loaded) against data.quality.staleness."""
    _seed(store, "OLD", 253, last=date(2022, 12, 1))  # 31 sessions before as-of
    _seed(store, "FRESH", 253)  # trades every day up to the as-of date
    _, m1 = UniverseBuilder(DuckDBUniverseRepository(store), _config()).build_snapshot(
        date(2023, 1, 1)
    )
    _, m2 = UniverseBuilder(
        DuckDBUniverseRepository(store),
        _config(),
        staleness=StalenessConfig(universe_max_missed_sessions=31),
    ).build_snapshot(date(2023, 1, 1))
    old1 = next(m for m in m1 if m.instrument_id == "OLD")
    old2 = next(m for m in m2 if m.instrument_id == "OLD")
    assert old1.eligible is False and "31 NSE sessions" in old1.exclusion_reason
    assert old2.eligible is True


def _add_delisting(store, iid="GONE"):
    store.conn.execute(
        "INSERT INTO security_master_history (instrument_id, series, exchange, valid_from,"
        " delisting_date, known_from) VALUES (?, 'EQ', 'NSE', '2000-01-01', '2020-01-01', ?)",
        [iid, datetime.now(UTC)],
    )


def test_survivorship_biased_without_delisting_data(store):
    _seed(store, "LIVE", 253)
    snap, _ = UniverseBuilder(DuckDBUniverseRepository(store), _config()).build_snapshot(
        date(2023, 1, 1)
    )
    assert snap.survivorship_status.value == "BIASED"


def test_survivorship_partial_with_delistings_but_no_bhavcopy(store):
    _seed(store, "LIVE", 253)
    _add_delisting(store)
    snap, _ = UniverseBuilder(DuckDBUniverseRepository(store), _config()).build_snapshot(
        date(2023, 1, 1)
    )
    assert snap.survivorship_status.value == "PARTIAL"


def test_known_at_rebuilds_snapshot_as_it_was_known(store):
    _seed(store, "LATE", 253)  # every row has known_from = now
    builder = UniverseBuilder(DuckDBUniverseRepository(store), _config())

    now_snap, now_members = builder.build_snapshot(date(2023, 1, 1))
    assert [m.instrument_id for m in now_members] == ["LATE"]

    past = datetime(2023, 1, 2, tzinfo=UTC)  # before any of the data was known
    past_snap, past_members = builder.build_snapshot(date(2023, 1, 1), known_at=past)
    assert past_members == []
    assert past_snap.created_at == past


def test_known_at_must_be_timezone_aware(store):
    with pytest.raises(ValueError, match="timezone-aware"):
        UniverseBuilder(DuckDBUniverseRepository(store), _config()).build_snapshot(
            date(2023, 1, 1),
            known_at=datetime(2023, 1, 2),  # noqa: DTZ001 - naive on purpose
        )


def test_defaults_match_project_design_section_14():
    """PROJECT_DESIGN §14: EQ only (no BE/T2T), min price 20, 1 crore on 20d and 50d."""
    cfg = UniverseConfig()
    assert cfg.eligible_series == ["EQ"]
    assert cfg.min_close_price == 20.0
    assert cfg.min_daily_turnover_inr == 10_000_000.0
    assert cfg.min_avg_traded_value_50d_inr == 10_000_000.0


def test_shipped_universe_yaml_matches_model_defaults():
    """config/universe.yaml must not silently diverge from the governing design defaults."""
    import pathlib

    import yaml

    raw = yaml.safe_load(
        (pathlib.Path(__file__).parents[2] / "config" / "universe.yaml").read_text()
    )
    assert UniverseConfig(**raw) == UniverseConfig()


def test_fifty_day_liquidity_gate(store):
    """A stock liquid over 20 days but not over 50 days is excluded (§14 dual window)."""
    now = datetime.now(UTC)
    store.conn.execute(
        """
        INSERT INTO daily_prices (
            instrument_id, trade_date, open_raw, high_raw, low_raw, close_raw,
            volume_raw, primary_provider, data_status, source_run_id, source_hash, known_from
        )
        SELECT 'SPIKE', CAST(? AS DATE) - CAST(i AS INTEGER),
               100, 100, 100, 100,
               CASE WHEN i < 20 THEN 200000 ELSE 1 END,  -- 20d avg 20M, 50d avg 8M
               'MOCK', 'OK', 'run', 'hash', CAST(? AS TIMESTAMPTZ)
        FROM range(0, 253) t(i)
        """,
        [date(2023, 1, 1), now],
    )
    store.conn.execute(
        "INSERT INTO security_master_history "
        "(instrument_id, series, exchange, valid_from, known_from)"
        " VALUES ('SPIKE', 'EQ', 'NSE', '2000-01-01', ?)",
        [now],
    )
    _, members = UniverseBuilder(DuckDBUniverseRepository(store), UniverseConfig()).build_snapshot(
        date(2023, 1, 1)
    )
    m = members[0]
    assert m.eligible is False
    assert "50d" in m.exclusion_reason
