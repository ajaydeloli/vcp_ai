from vcp_scanner.data.features.daily_features import DailyFeatureEngine
from vcp_scanner.data.storage.duckdb_store import DuckDBStore


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

    res = store.conn.execute(
        "SELECT sma_20, high_20 FROM technical_features_daily "
        "WHERE instrument_id='TEST_INST' ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
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
    assert rows[13][0] is None and rows[14][0] is not None  # bar 14 vs bar 15
    assert rows[19][1] is None and rows[20][1] is not None  # bar 20 vs bar 21


def _volume_ratios(store, instrument_id: str) -> list[tuple[float | None, float | None]]:
    return store.conn.execute(
        "SELECT volume_ratio_20, volume_ratio_50 FROM technical_features_daily "
        "WHERE instrument_id = ? ORDER BY trade_date",
        [instrument_id],
    ).fetchall()


def test_volume_ratio_excludes_the_current_bar():
    """Audit P2-1: volume_ratio_N = volume / mean of the N bars BEFORE today."""
    store = DuckDBStore(":memory:")
    store.migrate()
    _seed(store, "VR_INST", 60)  # volume on bar k (0-based) is 1000 + k

    DailyFeatureEngine(store).compute_for_instrument("VR_INST")
    rows = _volume_ratios(store, "VR_INST")

    # Bar 19 has only 19 prior bars: NULL; bar 20 is the first valid ratio.
    assert rows[19][0] is None
    for k in (20, 35, 59):
        expected = (1000 + k) / (1000 + k - 10.5)  # mean of bars k-20 .. k-1
        assert rows[k][0] is not None
        assert abs(rows[k][0] - expected) < 1e-12
    assert rows[49][1] is None
    assert rows[50][1] is not None
    assert abs(rows[50][1] - 1050 / (1000 + 24.5)) < 1e-12


def test_volume_spike_is_not_diluted_by_itself():
    """A 5x spike after flat volume reads 5.0 (it read 4.17 when today was in the mean)."""
    store = DuckDBStore(":memory:")
    store.migrate()
    _seed(store, "SPIKE_INST", 30)
    store.conn.execute(
        "UPDATE daily_prices_adjusted SET volume_adj = CASE WHEN trade_date = "
        "(SELECT MAX(trade_date) FROM daily_prices_adjusted) THEN 5000 ELSE 1000 END"
    )

    DailyFeatureEngine(store).compute_for_instrument("SPIKE_INST")

    assert _volume_ratios(store, "SPIKE_INST")[-1][0] == 5.0


def test_feature_rows_carry_the_current_definition_version():
    from vcp_scanner.domain.features import FEATURES_CALCULATION_VERSION

    store = DuckDBStore(":memory:")
    store.migrate()
    _seed(store, "VER_INST", 5)

    DailyFeatureEngine(store).compute_for_instrument("VER_INST")

    versions = store.conn.execute(
        "SELECT DISTINCT calculation_version FROM technical_features_daily"
    ).fetchall()
    assert versions == [(FEATURES_CALCULATION_VERSION,)]
    assert FEATURES_CALCULATION_VERSION == "features-1.2.0"
