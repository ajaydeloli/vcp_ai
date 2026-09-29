Phase 4 — Indicators & Technical Features is complete.

What changed:
- Added Domain models `DailyFeatures` and `WeeklyPrice` in `src/vcp_scanner/domain/features.py`.
- Added Feature Repository protocol to `src/vcp_scanner/data/repositories/base.py` and implemented DuckDB logic in `duckdb_feature_repository.py`.
- DDL Schema definitions added to `src/vcp_scanner/data/storage/duckdb_store.py` for `technical_features_daily`, `weekly_prices`, `relative_strength_snapshots`, `trend_template_results`, `trend_template_conditions`, `weekly_context`.
- Created `DailyFeatureEngine` (`src/vcp_scanner/features/daily_features.py`) which computes SMA, ATR, 52-week highs/lows, and volume metrics via DuckDB window functions directly from canonical adjusted data.
- Created `WeeklyAggregationEngine` (`src/vcp_scanner/features/weekly_aggregation.py`) to deterministically aggregate daily prices to weekly OHLCV bars using ISO weeks.
- Created `RelativeStrengthEngine` (`src/vcp_scanner/features/relative_strength.py`) which computes the weighted relative strength score (`rs-1.0.0` specification with `R_63`, `R_126`, `R_189`, `R_252`) and rank across the eligible universe snapshot.
- Added tests for each engine in `tests/unit/test_daily_features.py`, `tests/unit/test_weekly_aggregation.py`, `tests/unit/test_relative_strength.py`.

Tests run:
- All 3 tests executed and pass.

Docs updated:
- (None updated directly, only implementations to match specs).

Known limitations:
- EMA calculation is mocked as NULL in `daily_features.py` due to the complexity of writing recursive CTEs in pure SQL. It can be added as a post-processing pass or recursive CTE if required.
