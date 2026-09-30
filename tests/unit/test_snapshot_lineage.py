"""Stage 2 of audit finding P0-1: derived tables carry the data snapshot they came from.

Features, weekly bars, RS, weekly context and Trend results computed under different data
snapshots must coexist instead of overwriting each other, and each reader must see only its
own snapshot. RS is also keyed by universe snapshot. All data is synthetic (AGENTS.md rule 10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.data.repositories.duckdb_feature_repository import DuckDBFeatureRepository
from vcp_scanner.data.repositories.duckdb_trend_repository import DuckDBTrendRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import TrendTemplateStatus, WeeklyStage
from vcp_scanner.domain.snapshot import LIVE_SNAPSHOT_ID
from vcp_scanner.domain.trend import TrendConditionResult, TrendTemplateResult, WeeklyContext
from vcp_scanner.features.daily_features import DailyFeatureEngine
from vcp_scanner.features.relative_strength import RelativeStrengthEngine
from vcp_scanner.features.weekly_aggregation import WeeklyAggregationEngine

SNAP_A = "snap-20240201T180000Z"
SNAP_B = "snap-20240206T120000Z"
IID = "NSE_EQ|LINEAGE"
AS_OF = date(2024, 3, 29)
DAYS = [d for d in (date(2024, 1, 1) + timedelta(days=i) for i in range(90)) if d.weekday() < 5]
COMPUTED = datetime(2024, 4, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def store() -> DuckDBStore:
    s = DuckDBStore(":memory:")
    s.migrate()
    return s


def _seed_adjusted(store: DuckDBStore, snapshot_id: str, close: float, iid: str = IID) -> None:
    """Seed one flat adjusted series for ``iid`` under ``snapshot_id``."""
    for d in DAYS:
        store.conn.execute(
            """
            INSERT INTO daily_prices_adjusted (
                instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
                adjustment_version, price_factor_applied, volume_factor_applied,
                computed_from_snapshot_id, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1000, 'v1', 1, 1, ?, ?)
            """,
            [iid, d, close, close, close, close, snapshot_id, COMPUTED],
        )


def _count(store: DuckDBStore, table: str) -> int:
    row = store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


# ---------------------------------------------------------------------------
# Daily features and weekly bars
# ---------------------------------------------------------------------------


def test_features_from_two_snapshots_coexist_and_reads_are_scoped(store: DuckDBStore) -> None:
    _seed_adjusted(store, SNAP_A, 100.0)
    _seed_adjusted(store, SNAP_B, 110.0)

    a = DailyFeatureEngine(store, data_snapshot_id=SNAP_A).compute_for_instrument(IID)
    b = DailyFeatureEngine(store, data_snapshot_id=SNAP_B).compute_for_instrument(IID)
    assert a == b == len(DAYS)
    assert _count(store, "technical_features_daily") == 2 * len(DAYS)

    feat_a = DuckDBFeatureRepository(store, SNAP_A).load_daily_features(IID, AS_OF)
    feat_b = DuckDBFeatureRepository(store, SNAP_B).load_daily_features(IID, AS_OF)
    assert feat_a is not None and feat_b is not None
    assert feat_a.sma_20 == pytest.approx(100.0)
    assert feat_b.sma_20 == pytest.approx(110.0)
    # LIVE never sees either frozen snapshot.
    assert DuckDBFeatureRepository(store).load_daily_features(IID, AS_OF) is None


def test_recomputing_one_snapshot_does_not_touch_another(store: DuckDBStore) -> None:
    _seed_adjusted(store, SNAP_A, 100.0)
    engine_a = DailyFeatureEngine(store, data_snapshot_id=SNAP_A)
    engine_a.compute_for_instrument(IID)
    before = DuckDBFeatureRepository(store, SNAP_A).load_daily_features(IID, AS_OF)

    _seed_adjusted(store, SNAP_B, 250.0)
    DailyFeatureEngine(store, data_snapshot_id=SNAP_B).compute_for_instrument(IID)
    engine_a.compute_for_instrument(IID)  # idempotent re-run

    assert DuckDBFeatureRepository(store, SNAP_A).load_daily_features(IID, AS_OF) == before
    assert _count(store, "technical_features_daily") == 2 * len(DAYS)


def test_feature_history_is_scoped_to_snapshot(store: DuckDBStore) -> None:
    _seed_adjusted(store, SNAP_A, 100.0)
    _seed_adjusted(store, SNAP_B, 110.0)
    DailyFeatureEngine(store, data_snapshot_id=SNAP_A).compute_for_instrument(IID)
    DailyFeatureEngine(store, data_snapshot_id=SNAP_B).compute_for_instrument(IID)

    history = DuckDBFeatureRepository(store, SNAP_B).load_daily_feature_history(IID, AS_OF, 500)
    assert len(history) == len(DAYS)
    assert all(h.sma_20 in (None, pytest.approx(110.0)) for h in history)


def test_weekly_bars_from_two_snapshots_coexist(store: DuckDBStore) -> None:
    _seed_adjusted(store, SNAP_A, 100.0)
    _seed_adjusted(store, SNAP_B, 110.0)
    wa = WeeklyAggregationEngine(store, data_snapshot_id=SNAP_A).compute_for_instrument(IID)
    wb = WeeklyAggregationEngine(store, data_snapshot_id=SNAP_B).compute_for_instrument(IID)
    assert wa == wb > 0
    assert _count(store, "weekly_prices") == wa + wb

    start, end = DAYS[0], DAYS[-1] + timedelta(days=7)
    bars_a = DuckDBFeatureRepository(store, SNAP_A).load_weekly_prices(IID, start, end)
    bars_b = DuckDBFeatureRepository(store, SNAP_B).load_weekly_prices(IID, start, end)
    assert {b.close for b in bars_a} == {100.0}
    assert {b.close for b in bars_b} == {110.0}
    # Re-running snapshot A after B exists must not delete or alter B's weeks.
    WeeklyAggregationEngine(store, data_snapshot_id=SNAP_A).compute_for_instrument(IID)
    assert _count(store, "weekly_prices") == wa + wb


# ---------------------------------------------------------------------------
# Relative strength: data snapshot AND universe snapshot are both in the key
# ---------------------------------------------------------------------------


def _seed_universe(store: DuckDBStore, universe_id: str, created: str, members: list[str]) -> None:
    store.conn.execute(
        "INSERT INTO universe_snapshots VALUES (?, 'test_uni', ?, ?, 'hash', 'v1', 'COMPLETE')",
        [universe_id, AS_OF, created],
    )
    for m in members:
        store.conn.execute(
            "INSERT INTO universe_memberships VALUES "
            "(?, ?, TRUE, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            [universe_id, m],
        )


def _seed_rising(store: DuckDBStore, snapshot_id: str, iid: str, slope: float) -> None:
    store.conn.execute(
        """
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied,
            computed_from_snapshot_id, computed_at
        )
        SELECT ?, ?::DATE - INTERVAL (i) DAY, 100, 100, 100, 100 + (260 - i) * ?, 100,
               'v1', 1, 1, ?, current_timestamp
        FROM range(0, 260) t(i)
        """,
        [iid, AS_OF, slope, snapshot_id],
    )


def test_rs_same_date_two_universes_do_not_overwrite(store: DuckDBStore) -> None:
    for iid, slope in (("T1", 1.0), ("T2", 2.0), ("T3", 3.0)):
        _seed_rising(store, SNAP_A, iid, slope)
    _seed_universe(store, "uni-old", "2024-03-30T00:00:00Z", ["T1", "T2"])
    _seed_universe(store, "uni-new", "2024-03-31T00:00:00Z", ["T1", "T2", "T3"])

    engine = RelativeStrengthEngine(store, data_snapshot_id=SNAP_A)
    engine.compute_for_date(AS_OF, "uni-old")
    engine.compute_for_date(AS_OF, "uni-new")

    assert _count(store, "relative_strength_snapshots") == 2 + 3  # both universes kept

    repo = DuckDBTrendRepository(store, SNAP_A)
    version = engine.calculation_version
    old = repo.load_relative_strength("T1", AS_OF, version, universe_snapshot_id="uni-old")
    new = repo.load_relative_strength("T1", AS_OF, version, universe_snapshot_id="uni-new")
    assert old is not None and new is not None
    assert old.population_size == 2 and new.population_size == 3
    # Without a universe, the most recently created universe snapshot wins.
    default = repo.load_relative_strength("T1", AS_OF, version)
    assert default == new


def test_rs_is_scoped_to_data_snapshot(store: DuckDBStore) -> None:
    for iid, slope in (("T1", 1.0), ("T2", 2.0)):
        _seed_rising(store, SNAP_A, iid, slope)
        _seed_rising(store, SNAP_B, iid, 3.0 - slope)  # reversed ranking under B
    _seed_universe(store, "uni", "2024-03-30T00:00:00Z", ["T1", "T2"])

    ea = RelativeStrengthEngine(store, data_snapshot_id=SNAP_A)
    eb = RelativeStrengthEngine(store, data_snapshot_id=SNAP_B)
    ea.compute_for_date(AS_OF, "uni")
    eb.compute_for_date(AS_OF, "uni")

    ra = DuckDBTrendRepository(store, SNAP_A).load_relative_strength(
        "T1", AS_OF, ea.calculation_version
    )
    rb = DuckDBTrendRepository(store, SNAP_B).load_relative_strength(
        "T1", AS_OF, eb.calculation_version
    )
    assert ra is not None and rb is not None
    assert ra.rs_rank != rb.rs_rank  # T1 is weakest under A, strongest under B
    assert (
        DuckDBTrendRepository(store).load_relative_strength("T1", AS_OF, ea.calculation_version)
        is None
    )


# ---------------------------------------------------------------------------
# Trend repository: weekly context, conditions and result summaries
# ---------------------------------------------------------------------------


def _context(stage: WeeklyStage) -> WeeklyContext:
    return WeeklyContext(IID, AS_OF, stage, 100.0, 1.5, 0.5, False, "weekly-stage-1")


def _result(passed: bool, context: WeeklyContext) -> TrendTemplateResult:
    return TrendTemplateResult(
        instrument_id=IID,
        as_of_date=AS_OF,
        status=TrendTemplateStatus.PASS if passed else TrendTemplateStatus.FAIL,
        conditions=(TrendConditionResult(1, "close_above_sma150", 5.0, 0.0, passed),),
        algorithm_version="trend-1",
        weekly_context=context,
    )


def test_weekly_context_is_snapshot_scoped(store: DuckDBStore) -> None:
    a, b = DuckDBTrendRepository(store, SNAP_A), DuckDBTrendRepository(store, SNAP_B)
    a.save_weekly_context([_context(WeeklyStage.STAGE_2)])
    b.save_weekly_context([_context(WeeklyStage.STAGE_4)])

    assert _count(store, "weekly_context") == 2
    loaded_a = a.load_weekly_context(IID, AS_OF, "weekly-stage-1")
    loaded_b = b.load_weekly_context(IID, AS_OF, "weekly-stage-1")
    assert loaded_a is not None and loaded_a.weekly_stage is WeeklyStage.STAGE_2
    assert loaded_b is not None and loaded_b.weekly_stage is WeeklyStage.STAGE_4
    assert DuckDBTrendRepository(store).load_weekly_context(IID, AS_OF, "weekly-stage-1") is None


def test_trend_conditions_and_results_record_their_snapshot(store: DuckDBStore) -> None:
    a, b = DuckDBTrendRepository(store, SNAP_A), DuckDBTrendRepository(store, SNAP_B)
    a.save_trend_template_results("scan-a", "cfg", [_result(True, _context(WeeklyStage.STAGE_2))])
    b.save_trend_template_results("scan-b", "cfg", [_result(False, _context(WeeklyStage.STAGE_4))])

    # Same date/version/config, different snapshot: two condition rows, never one overwrite.
    assert _count(store, "trend_template_conditions") == 2
    cond_a = a.load_trend_conditions(IID, AS_OF, "trend-1", "cfg")
    cond_b = b.load_trend_conditions(IID, AS_OF, "trend-1", "cfg")
    assert [c.passed for c in cond_a] == [True]
    assert [c.passed for c in cond_b] == [False]

    stored = dict(
        store.conn.execute(
            "SELECT scan_id, data_snapshot_id FROM trend_template_results"
        ).fetchall()
    )
    assert stored == {"scan-a": SNAP_A, "scan-b": SNAP_B}


def test_default_repositories_use_live(store: DuckDBStore) -> None:
    DuckDBTrendRepository(store).save_weekly_context([_context(WeeklyStage.STAGE_2)])
    row = store.conn.execute("SELECT data_snapshot_id FROM weekly_context").fetchone()
    assert row == (LIVE_SNAPSHOT_ID,)


# ---------------------------------------------------------------------------
# Migration of pre-lineage databases
# ---------------------------------------------------------------------------


def test_migration_keeps_legacy_derived_rows_under_live() -> None:
    store = DuckDBStore(":memory:")
    store.conn.execute(
        """
        CREATE TABLE weekly_prices (
            instrument_id VARCHAR NOT NULL, week_end DATE NOT NULL,
            open DOUBLE NOT NULL, high DOUBLE NOT NULL, low DOUBLE NOT NULL,
            close DOUBLE NOT NULL, volume DOUBLE, source_daily_version VARCHAR NOT NULL,
            PRIMARY KEY (instrument_id, week_end, source_daily_version)
        )
        """
    )
    store.conn.execute(
        """
        CREATE TABLE weekly_context (
            instrument_id VARCHAR NOT NULL, as_of_date DATE NOT NULL,
            weekly_stage VARCHAR NOT NULL, sma_w DOUBLE, slope_pct DOUBLE, prior_pct DOUBLE,
            is_partial_week BOOLEAN NOT NULL, algorithm_version VARCHAR NOT NULL,
            PRIMARY KEY (instrument_id, as_of_date, algorithm_version)
        )
        """
    )
    store.conn.execute(
        "INSERT INTO weekly_prices VALUES ('X', '2024-01-05', 1, 2, 0.5, 1.5, 9, 'v1')"
    )
    store.conn.execute(
        "INSERT INTO weekly_context VALUES ('X', '2024-01-05', 'STAGE_2', 1, 1, 1, FALSE, 'w1')"
    )

    store.migrate()

    assert store.conn.execute("SELECT close, data_snapshot_id FROM weekly_prices").fetchall() == [
        (1.5, LIVE_SNAPSHOT_ID)
    ]
    assert store.conn.execute("SELECT data_snapshot_id FROM weekly_context").fetchall() == [
        (LIVE_SNAPSHOT_ID,)
    ]
    # New key allows the same week under another snapshot; migrating again changes nothing.
    store.conn.execute(
        "INSERT INTO weekly_prices VALUES ('X', '2024-01-05', 1, 2, 0.5, 9.9, 9, 'v1', 'snap-z')"
    )
    store.migrate()
    assert _count(store, "weekly_prices") == 2
