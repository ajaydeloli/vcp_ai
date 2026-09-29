# Phase 4 Plan - Indicators & Technical Features

## 1. Domain Models
Create `src/vcp_scanner/domain/features.py` containing:
- `DailyFeatures`: SMA, EMA, ATR, 52-week highs/lows, volume metrics.
- `WeeklyPrice`: Derived weekly open, high, low, close, volume.

## 2. DuckDB Schema
Update `src/vcp_scanner/data/storage/duckdb_store.py` with:
- `technical_features_daily` table
- `weekly_prices` table
- `relative_strength_snapshots` table

## 3. Interfaces / Repositories
Update `src/vcp_scanner/data/repositories/base.py` with:
- `FeatureRepository`: save/load for `technical_features_daily`, `weekly_prices`.

## 4. Calculation Engines
Create `src/vcp_scanner/features/daily_features.py` (or similar location)
Create `src/vcp_scanner/features/weekly_aggregation.py`
Create `src/vcp_scanner/features/relative_strength.py`

## 5. Tests
- `tests/unit/test_daily_features.py`
- `tests/unit/test_weekly_aggregation.py`
- `tests/unit/test_relative_strength.py`
- `tests/unit/test_duckdb_store.py` (add coverage for new tables)
