from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.data.repositories.duckdb_feature_repository import DuckDBFeatureRepository
from vcp_scanner.features.daily_features import DailyFeatureEngine
from vcp_scanner.features.weekly_aggregation import WeeklyAggregationEngine
from vcp_scanner.features.relative_strength import RelativeStrengthEngine

def main():
    store = DuckDBStore(":memory:")
    store.migrate()
    print("Migrated successfully!")
    
if __name__ == "__main__":
    main()
