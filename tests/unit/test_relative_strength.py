from datetime import date, timedelta
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.features.relative_strength import RelativeStrengthEngine

def test_relative_strength():
    store = DuckDBStore(":memory:")
    store.migrate()
    
    # Insert universe
    store.conn.execute("INSERT INTO universe_snapshots VALUES ('snap_1', 'test_uni', '2023-12-31', current_timestamp, 'hash', 'v1', 'COMPLETE')")
    store.conn.execute("INSERT INTO universe_memberships VALUES ('snap_1', 'T1', TRUE, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)")
    store.conn.execute("INSERT INTO universe_memberships VALUES ('snap_1', 'T2', TRUE, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)")
    
    # We need 253 days of history for T1 and T2. We can just insert the key rows using explicit dates.
    dates = [
        date(2023, 12, 31),
        date(2023, 12, 31) - timedelta(days=63),
        date(2023, 12, 31) - timedelta(days=126),
        date(2023, 12, 31) - timedelta(days=189),
        date(2023, 12, 31) - timedelta(days=252),
    ]
    
    # Ensure they exist with exactly these counts.
    # To make ROW_NUMBER work correctly, we just insert the 253 rows directly.
    import duckdb
    
    # insert 253 rows for T1, T2
    store.conn.execute("""
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied, computed_at
        )
        SELECT 
            'T1',
            '2023-12-31'::DATE - INTERVAL (i) DAY,
            100, 100, 100, 100 + i, 100, 'v1', 1, 1, current_timestamp
        FROM range(0, 260) t(i)
    """)
    store.conn.execute("""
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied, computed_at
        )
        SELECT 
            'T2',
            '2023-12-31'::DATE - INTERVAL (i) DAY,
            100, 100, 100, 100 + i * 2, 100, 'v1', 1, 1, current_timestamp
        FROM range(0, 260) t(i)
    """)
    
    engine = RelativeStrengthEngine(store)
    engine.compute_for_date(date(2023, 12, 31), "snap_1")
    
    res = store.conn.execute("SELECT instrument_id, rs_raw, rs_rank, population_size FROM relative_strength_snapshots ORDER BY rs_rank DESC").fetchall()
    print(res)
    assert len(res) == 2

