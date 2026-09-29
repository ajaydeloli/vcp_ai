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

