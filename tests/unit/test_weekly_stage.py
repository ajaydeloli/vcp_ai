"""Weekly Stage classifier tests (TREND_TEMPLATE_SPECIFICATION sections 4 and 7).

Weekly series are synthetic: each week has five trading days (Mon-Fri) that all close at that
week's target close, so the derived weekly close is exactly the target. Tests run against an
in-memory DuckDB store using the real ``WeeklyAggregationEngine`` and DuckDB repositories.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vcp_scanner.config.models import StageConfig
from vcp_scanner.data.features.weekly_aggregation import WeeklyAggregationEngine
from vcp_scanner.data.repositories.duckdb_feature_repository import DuckDBFeatureRepository
from vcp_scanner.data.repositories.duckdb_trend_repository import DuckDBTrendRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import WeeklyStage
from vcp_scanner.domain.trend import WeeklyContext
from vcp_scanner.features.weekly_stage import (
    STAGE_ALGORITHM_VERSION,
    WeeklyStageEngine,
    classify_weekly_stage,
)

FIRST_MONDAY = date(2022, 1, 3)
INSTRUMENT = "SYN_W"


# --------------------------------------------------------------------------- series builders


def _rising(n: int) -> list[float]:
    return [100.0 + 1.0 * w for w in range(n)]


def _falling(n: int) -> list[float]:
    return [300.0 - 1.0 * w for w in range(n)]


def _flat(n: int) -> list[float]:
    return [100.0] * n


def _top(n: int = 70) -> list[float]:
    """Big advance, then flat: prior_pct ~ +18%, slope 0."""
    return [100.0] * 30 + [130.0] * (n - 30)


def _transition() -> list[float]:
    """SMA still rising steeply but the latest close is under it."""
    return [100.0 + 3.0 * w for w in range(69)] + [240.0]


class Env:
    def __init__(self, config: StageConfig | None = None) -> None:
        self.store = DuckDBStore(":memory:")
        self.store.migrate()
        self.features = DuckDBFeatureRepository(self.store)
        self.trend = DuckDBTrendRepository(self.store)
        self.engine = WeeklyStageEngine(self.features, config)

    def seed_weeks(
        self,
        weekly_closes: list[float],
        *,
        instrument_id: str = INSTRUMENT,
        last_week_days: int = 5,
        tail_close: float | None = None,
    ) -> date:
        """Insert Mon-Fri bars per week and aggregate.

        ``last_week_days`` < 5 truncates the final week (as-of mid-week). ``tail_close`` sets the
        close of the days *after* the as-of day within the final week (used to prove no leak).
        Returns the as-of date: the last day of the truncated final week.
        """
        rows = []
        as_of = FIRST_MONDAY
        for w, close in enumerate(weekly_closes):
            monday = FIRST_MONDAY + timedelta(weeks=w)
            is_last = w == len(weekly_closes) - 1
            for d in range(5):
                day = monday + timedelta(days=d)
                if is_last and d == last_week_days - 1:
                    as_of = day
                use = close if not (is_last and d >= last_week_days and tail_close) else tail_close
                rows.append((instrument_id, day, use, use, use, use, 1000.0, "adj-test", 1, 1))
        self.store.conn.executemany(
            """
            INSERT INTO daily_prices_adjusted (
                instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
                adjustment_version, price_factor_applied, volume_factor_applied, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TIMESTAMPTZ '2024-01-01 00:00:00+00')
            """,
            rows,
        )
        WeeklyAggregationEngine(self.store).compute_for_instrument(instrument_id)
        return as_of


@pytest.fixture
def env() -> Env:
    return Env()


# --------------------------------------------------------------------------- one per stage


@pytest.mark.parametrize(
    ("series", "expected"),
    [
        (_rising(70), WeeklyStage.STAGE_2),
        (_falling(70), WeeklyStage.STAGE_4),
        (_top(), WeeklyStage.STAGE_3),
        (_flat(70), WeeklyStage.STAGE_1),
        (_transition(), WeeklyStage.TRANSITION),
    ],
    ids=["stage2", "stage4", "stage3", "stage1", "transition"],
)
def test_each_stage_end_to_end(env: Env, series: list[float], expected: WeeklyStage) -> None:
    as_of = env.seed_weeks(series)
    ctx = env.engine.classify(INSTRUMENT, as_of)

    assert ctx.weekly_stage is expected
    assert ctx.algorithm_version == STAGE_ALGORITHM_VERSION == "stage-1.0.0"
    assert ctx.sma_w is not None and ctx.slope_pct is not None and ctx.prior_pct is not None
    assert ctx.is_partial_week is False  # as-of is a Friday
    assert ctx.weekly_stage2_pass is (expected is WeeklyStage.STAGE_2)


def test_stage_measurements_match_the_formulas(env: Env) -> None:
    series = _rising(70)
    as_of = env.seed_weeks(series)
    ctx = env.engine.classify(INSTRUMENT, as_of)

    sma_t = sum(series[-30:]) / 30
    sma_4 = sum(series[-34:-4]) / 30
    sma_30 = sum(series[-60:-30]) / 30
    assert ctx.sma_w == pytest.approx(sma_t)
    assert ctx.slope_pct == pytest.approx((sma_t / sma_4 - 1) * 100)
    assert ctx.prior_pct == pytest.approx((sma_4 / sma_30 - 1) * 100)


def test_transition_is_not_stage_2(env: Env) -> None:
    as_of = env.seed_weeks(_transition())
    ctx = env.engine.classify(INSTRUMENT, as_of)
    assert ctx.weekly_stage is WeeklyStage.TRANSITION
    assert ctx.weekly_stage2_pass is False
    assert ctx.slope_pct is not None and ctx.slope_pct > 0.5  # SMA rising, close on wrong side


# --------------------------------------------------------------------------- missing data


def test_too_few_weeks_is_insufficient_with_null_measurements(env: Env) -> None:
    as_of = env.seed_weeks(_rising(33))  # need sma_weeks + slope_lookback = 34
    ctx = env.engine.classify(INSTRUMENT, as_of)
    assert ctx.weekly_stage is WeeklyStage.INSUFFICIENT_DATA
    assert ctx.slope_pct is None and ctx.prior_pct is None
    assert ctx.weekly_stage2_pass is False


def test_29_weeks_has_no_sma_at_all(env: Env) -> None:
    as_of = env.seed_weeks(_rising(29))
    ctx = env.engine.classify(INSTRUMENT, as_of)
    assert ctx.weekly_stage is WeeklyStage.INSUFFICIENT_DATA
    assert ctx.sma_w is None


def test_stage_2_needs_only_34_weeks_prior_stays_null(env: Env) -> None:
    as_of = env.seed_weeks(_rising(40))
    ctx = env.engine.classify(INSTRUMENT, as_of)
    assert ctx.weekly_stage is WeeklyStage.STAGE_2
    assert ctx.prior_pct is None  # not enough history for sma_w(t-30w); never 0


def test_flat_sma_without_prior_history_is_insufficient_not_a_guess(env: Env) -> None:
    as_of = env.seed_weeks(_flat(40))  # Stage 1 vs 3 needs prior_pct; only 40 weeks
    ctx = env.engine.classify(INSTRUMENT, as_of)
    assert ctx.weekly_stage is WeeklyStage.INSUFFICIENT_DATA
    assert ctx.slope_pct == pytest.approx(0.0)
    assert ctx.prior_pct is None


def test_exactly_60_weeks_is_enough_for_stage_1_or_3(env: Env) -> None:
    as_of = env.seed_weeks(_flat(60))
    assert env.engine.classify(INSTRUMENT, as_of).weekly_stage is WeeklyStage.STAGE_1


def test_no_bar_on_as_of_date_is_insufficient(env: Env) -> None:
    as_of = env.seed_weeks(_rising(70))
    ctx = env.engine.classify(INSTRUMENT, as_of + timedelta(days=10))
    assert ctx.weekly_stage is WeeklyStage.INSUFFICIENT_DATA
    assert ctx.sma_w is None


def test_unknown_instrument_is_insufficient(env: Env) -> None:
    ctx = env.engine.classify("NOPE", FIRST_MONDAY)
    assert ctx.weekly_stage is WeeklyStage.INSUFFICIENT_DATA


# --------------------------------------------------------------------------- boundaries


def _series_with_slope() -> list[float]:
    return [100.0] * 60 + [100.0, 100.0, 100.0, 100.0, 101.0]


def test_slope_exactly_on_flat_band_is_flat_not_stage_2() -> None:
    closes = _series_with_slope()
    probe = classify_weekly_stage(closes, StageConfig())
    assert probe.slope_pct is not None and probe.slope_pct > 0
    band = probe.slope_pct

    on_band = classify_weekly_stage(closes, StageConfig(flat_band_pct=band))
    just_inside = classify_weekly_stage(closes, StageConfig(flat_band_pct=band * 0.999999))
    assert on_band.stage in (WeeklyStage.STAGE_1, WeeklyStage.STAGE_3)  # |slope| <= band
    assert just_inside.stage is WeeklyStage.STAGE_2  # slope > band


def test_prior_advance_exactly_on_minimum_is_stage_3() -> None:
    closes = _top()
    probe = classify_weekly_stage(closes, StageConfig())
    assert probe.prior_pct is not None
    prior = probe.prior_pct
    assert classify_weekly_stage(closes, StageConfig(prior_advance_min_pct=prior)).stage is (
        WeeklyStage.STAGE_3
    )
    assert classify_weekly_stage(
        closes, StageConfig(prior_advance_min_pct=prior * 1.000001)
    ).stage is (WeeklyStage.STAGE_1)


def test_close_equal_to_sma_in_downslope_is_transition_not_stage_4() -> None:
    # Stage 4 needs close < sma_w strictly. Solve close == sma_t for a falling series:
    # sma_t = (sum(previous 29) + c) / 30 = c  ->  c = sum(previous 29) / 29
    falling = _falling(69)
    c = sum(falling[-29:]) / 29
    out = classify_weekly_stage([*falling, c], StageConfig())
    assert out.sma_w == pytest.approx(c)
    assert out.slope_pct is not None and out.slope_pct < -0.5
    assert out.stage is WeeklyStage.TRANSITION


def test_config_windows_are_honoured() -> None:
    cfg = StageConfig(sma_weeks=10, slope_lookback_weeks=2)
    assert classify_weekly_stage(_rising(11), cfg).stage is WeeklyStage.INSUFFICIENT_DATA
    twelve = classify_weekly_stage(_rising(12), cfg)  # 10 + 2 bars: slope known, prior not
    assert twelve.stage is WeeklyStage.STAGE_2 and twelve.prior_pct is None
    twenty = classify_weekly_stage(_rising(20), cfg)  # 10 + 10 bars: prior known
    assert twenty.stage is WeeklyStage.STAGE_2 and twenty.prior_pct is not None


# --------------------------------------------------------------------------- partial week


@pytest.mark.parametrize(
    ("days", "partial"), [(1, True), (2, True), (3, True), (4, True), (5, False)]
)
def test_is_partial_week_flag(env: Env, days: int, partial: bool) -> None:
    as_of = env.seed_weeks(_rising(70), last_week_days=days)
    ctx = env.engine.classify(INSTRUMENT, as_of)
    assert ctx.is_partial_week is partial
    assert ctx.weekly_stage is WeeklyStage.STAGE_2


def test_partial_week_uses_only_days_up_to_as_of(env: Env) -> None:
    # Wednesday as-of; Thursday/Friday crash to 1.0 and the stored weekly bar (ending Friday)
    # includes that crash. The classifier must ignore the stored current-week bar.
    as_of = env.seed_weeks(_rising(70), last_week_days=3, tail_close=1.0)
    assert as_of.weekday() == 2
    leaked_close = env.store.conn.execute(
        "SELECT close FROM weekly_prices WHERE week_end > ? ORDER BY week_end LIMIT 1", [as_of]
    ).fetchone()
    assert leaked_close == (1.0,), "precondition: stored bar contains post-as-of data"

    full = env.engine.classify(INSTRUMENT, as_of)
    assert full.is_partial_week is True
    assert full.weekly_stage is WeeklyStage.STAGE_2

    # Look-ahead check: delete everything after as-of, rebuild weekly bars, same answer.
    env.store.conn.execute("DELETE FROM daily_prices_adjusted WHERE trade_date > ?", [as_of])
    env.store.conn.execute("DELETE FROM weekly_prices WHERE week_end >= ?", [as_of])
    WeeklyAggregationEngine(env.store).compute_for_instrument(INSTRUMENT)
    assert env.engine.classify(INSTRUMENT, as_of) == full


def test_result_at_earlier_friday_unchanged_by_later_weeks(env: Env) -> None:
    as_of = env.seed_weeks(_rising(70))
    earlier = as_of - timedelta(weeks=5)  # a Friday, 65 weeks in
    with_future = env.engine.classify(INSTRUMENT, earlier)

    env.store.conn.execute("DELETE FROM daily_prices_adjusted WHERE trade_date > ?", [earlier])
    env.store.conn.execute("DELETE FROM weekly_prices WHERE week_end > ?", [earlier])
    assert env.engine.classify(INSTRUMENT, earlier) == with_future


def test_duplicate_weekly_versions_do_not_double_count(env: Env) -> None:
    as_of = env.seed_weeks(_rising(70))
    baseline = env.engine.classify(INSTRUMENT, as_of)
    env.store.conn.execute(
        "INSERT INTO weekly_prices (instrument_id, week_end, open, high, low, close, volume,"
        " source_daily_version) SELECT instrument_id, week_end, open, high, low, close,"
        " volume, 'other-version' FROM weekly_prices"
    )
    assert env.engine.classify(INSTRUMENT, as_of) == baseline


# --------------------------------------------------------------------------- determinism, storage


def test_determinism_and_classify_many_order(env: Env) -> None:
    as_of = env.seed_weeks(_rising(70), instrument_id="UP")
    env.seed_weeks(_falling(70), instrument_id="DOWN")

    assert env.engine.classify("UP", as_of) == env.engine.classify("UP", as_of)
    out = env.engine.classify_many(["DOWN", "UP", "MISSING"], as_of)
    assert [c.instrument_id for c in out] == ["DOWN", "UP", "MISSING"]
    assert [c.weekly_stage for c in out] == [
        WeeklyStage.STAGE_4,
        WeeklyStage.STAGE_2,
        WeeklyStage.INSUFFICIENT_DATA,
    ]


def test_weekly_context_round_trip_and_idempotent_upsert(env: Env) -> None:
    as_of = env.seed_weeks(_rising(40), instrument_id="W40")  # prior_pct is NULL here
    ctx = env.engine.classify("W40", as_of)
    assert ctx.prior_pct is None

    env.trend.save_weekly_context([ctx])
    env.trend.save_weekly_context([ctx])

    loaded = env.trend.load_weekly_context("W40", as_of, STAGE_ALGORITHM_VERSION)
    assert loaded == ctx
    assert loaded is not None and loaded.prior_pct is None  # NULL survives, not 0
    count = env.store.conn.execute("SELECT COUNT(*) FROM weekly_context").fetchone()
    assert count == (1,)
    assert env.trend.load_weekly_context("W40", as_of, "stage-9.9.9") is None
    assert (
        env.trend.load_weekly_context("W40", as_of - timedelta(days=7), STAGE_ALGORITHM_VERSION)
        is None
    )


def test_insufficient_context_is_stored_as_insufficient(env: Env) -> None:
    ctx = env.engine.classify("MISSING", FIRST_MONDAY)
    env.trend.save_weekly_context([ctx])
    row = env.store.conn.execute(
        "SELECT weekly_stage, sma_w, slope_pct, prior_pct FROM weekly_context"
    ).fetchone()
    assert row == ("INSUFFICIENT_DATA", None, None, None)


def test_weekly_context_type_is_domain_object(env: Env) -> None:
    as_of = env.seed_weeks(_rising(70))
    assert isinstance(env.engine.classify(INSTRUMENT, as_of), WeeklyContext)
