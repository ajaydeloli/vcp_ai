from datetime import date, timedelta
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.features.daily_features import DailyFeatureEngine

def test_daily_feature_computation():
    store = DuckDBStore(":memory:")
    store.migrate()
    
    # Insert some dummy daily_prices_adjusted
    store.conn.execute("""
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied, computed_at
        )
        SELECT 
            'TEST_INST', 
            current_date - INTERVAL (20 - i) DAY, 
            100 + i, 105 + i, 95 + i, 102 + i, 1000,
            'v1', 1.0, 1.0, current_timestamp
        FROM range(1, 21) t(i)
    """)
    
    engine = DailyFeatureEngine(store)
    engine.compute_for_instrument("TEST_INST")
    
    res = store.conn.execute("SELECT sma_20, high_20 FROM technical_features_daily WHERE instrument_id='TEST_INST' ORDER BY trade_date DESC LIMIT 1").fetchone()
    assert res is not None
    print(res)



def test_warmup_windows_are_null_until_full():
    """AGENTS.md rule 4: missing is not zero. A 50-bar stock has no 150/200/252 values."""
    store = DuckDBStore(":memory:")
    store.migrate()

    store.conn.execute("""
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied, computed_at
        )
        SELECT
            'WARM_INST',
            DATE '2024-01-01' + CAST(i AS INTEGER),
            100 + i, 105 + i, 95 + i, 102 + i, 1000,
            'v1', 1.0, 1.0, current_timestamp
        FROM range(0, 50) t(i)
    """)

    DailyFeatureEngine(store).compute_for_instrument("WARM_INST")

    last = store.conn.execute(
        """
        SELECT sma_20, sma_50, sma_150, sma_200, high_252, low_252
        FROM technical_features_daily
        WHERE instrument_id = 'WARM_INST'
        ORDER BY trade_date DESC LIMIT 1
        """
    ).fetchone()
    assert last is not None
    sma_20, sma_50, sma_150, sma_200, high_252, low_252 = last
    assert sma_20 is not None
    assert sma_50 is not None  # exactly 50 bars: the 50-window is just full
    assert sma_150 is None
    assert sma_200 is None
    assert high_252 is None
    assert low_252 is None

    # An earlier bar must not have a partial-window SMA either.
    early = store.conn.execute(
        """
        SELECT sma_20, sma_50 FROM technical_features_daily
        WHERE instrument_id = 'WARM_INST'
        ORDER BY trade_date ASC LIMIT 1
        """
    ).fetchone()
    assert early == (None, None)


def _seed(store, instrument_id: str, n: int) -> None:
    store.conn.execute(f"""
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied, computed_at
        )
        SELECT
            '{instrument_id}',
            DATE '2024-01-01' + CAST(i AS INTEGER),
            100 + i, 105 + i, 95 + i, 102 + i, 1000 + i,
            'v1', 1.0, 1.0, current_timestamp
        FROM range(0, {n}) t(i)
    """)


def test_short_history_has_null_windowed_features():
    """A 10-bar stock has no 20/50-bar windows, no ATR and no rolling volatility."""
    store = DuckDBStore(":memory:")
    store.migrate()
    _seed(store, "SHORT_INST", 10)

    DailyFeatureEngine(store).compute_for_instrument("SHORT_INST")

    row = store.conn.execute(
        """
        SELECT sma_20, high_20, low_20, high_50, low_50, atr_14, atr_pct_14,
               volume_avg_20, volume_avg_50, volume_ratio_20, volume_ratio_50,
               rolling_volatility_20, rolling_volatility_50,
               volume_avg_5, volume_avg_10
        FROM technical_features_daily
        WHERE instrument_id = 'SHORT_INST'
        ORDER BY trade_date DESC LIMIT 1
        """
    ).fetchone()
    assert row is not None
    *must_be_null, volume_avg_5, volume_avg_10 = row
    assert all(v is None for v in must_be_null)
    assert volume_avg_5 is not None  # 5- and 10-bar windows are full at bar 10
    assert volume_avg_10 is not None


def test_first_bar_has_no_return_or_true_range():
    """The first bar has no previous close: return must be NULL, not 0."""
    store = DuckDBStore(":memory:")
    store.migrate()
    _seed(store, "FIRST_INST", 30)

    DailyFeatureEngine(store).compute_for_instrument("FIRST_INST")

    first = store.conn.execute(
        "SELECT daily_return FROM technical_features_daily "
        "WHERE instrument_id = 'FIRST_INST' ORDER BY trade_date ASC LIMIT 1"
    ).fetchone()
    assert first == (None,)


def test_atr_and_volatility_first_valid_bars():
    """ATR needs 14 true ranges (first valid on bar 15); volatility_20 needs 20 returns (bar 21)."""
    store = DuckDBStore(":memory:")
    store.migrate()
    _seed(store, "EDGE_INST", 25)

    DailyFeatureEngine(store).compute_for_instrument("EDGE_INST")

    rows = store.conn.execute(
        "SELECT atr_14, rolling_volatility_20 FROM technical_features_daily "
        "WHERE instrument_id = 'EDGE_INST' ORDER BY trade_date"
    ).fetchall()
    assert rows[13][0] is None and rows[14][0] is not None   # bar 14 vs bar 15
    assert rows[19][1] is None and rows[20][1] is not None   # bar 20 vs bar 21
