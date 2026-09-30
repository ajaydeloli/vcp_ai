from vcp_scanner.data.features.weekly_aggregation import WeeklyAggregationEngine
from vcp_scanner.data.storage.duckdb_store import DuckDBStore


def test_weekly_aggregation():
    store = DuckDBStore(":memory:")
    store.migrate()

    # Insert daily prices for 7 days crossing a weekend
    # 2023-01-02 was a Monday
    store.conn.execute("""
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied, computed_at
        ) VALUES
        ('TEST', '2023-01-02', 10, 15, 9, 12, 100, 'v1', 1, 1, current_timestamp),
        ('TEST', '2023-01-03', 12, 14, 11, 13, 100, 'v1', 1, 1, current_timestamp),
        ('TEST', '2023-01-04', 13, 16, 12, 15, 100, 'v1', 1, 1, current_timestamp),
        ('TEST', '2023-01-05', 15, 20, 14, 19, 100, 'v1', 1, 1, current_timestamp),
        ('TEST', '2023-01-06', 19, 21, 18, 20, 100, 'v1', 1, 1, current_timestamp),
        ('TEST', '2023-01-09', 20, 22, 19, 21, 100, 'v1', 1, 1, current_timestamp)
    """)

    engine = WeeklyAggregationEngine(store)
    engine.compute_for_instrument("TEST")

    res = store.conn.execute(
        "SELECT week_end, open, high, low, close, volume FROM weekly_prices "
        "WHERE instrument_id='TEST' ORDER BY week_end"
    ).fetchall()

    # Expect 2 rows: one ending 2023-01-06, one ending 2023-01-09
    assert len(res) == 2
    assert str(res[0][0]) == "2023-01-06"
    assert res[0][1] == 10  # open of first day
    assert res[0][2] == 21  # high across week
    assert res[0][3] == 9  # low across week
    assert res[0][4] == 20  # close of last day
    assert res[0][5] == 500  # sum volume
