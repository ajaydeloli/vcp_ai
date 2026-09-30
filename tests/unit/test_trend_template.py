"""Trend Template engine tests (TREND_TEMPLATE_SPECIFICATION sections 1, 2 and 7).

All price series are synthetic and generated in-test (AGENTS.md hard rule 10). Each test
uses an in-memory DuckDB store, the real Phase 4 ``DailyFeatureEngine`` and the real
DuckDB repositories, so the engine is exercised end to end.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from vcp_scanner.config.models import TrendTemplateConfig
from vcp_scanner.data.repositories.duckdb_feature_repository import DuckDBFeatureRepository
from vcp_scanner.data.repositories.duckdb_trend_repository import DuckDBTrendRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import TrendTemplateStatus, WeeklyStage
from vcp_scanner.domain.trend import TREND_CONDITION_NAMES, TrendConditionResult, WeeklyContext
from vcp_scanner.features.daily_features import DailyFeatureEngine
from vcp_scanner.features.trend_template import (
    TREND_ALGORITHM_VERSION,
    TrendInputs,
    TrendTemplateEngine,
    derive_status,
    evaluate_conditions,
)

INSTRUMENT = "SYN_UP"
START = date(2023, 1, 2)  # a Monday
LOOKBACK = 21
NEEDED = 252 + LOOKBACK  # 273 sessions of warm-up


# --------------------------------------------------------------------------- helpers


def _trading_days(n: int, start: date = START) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _uptrend(n: int) -> list[float]:
    return [100.0 + 0.5 * i for i in range(n)]


def _downtrend(n: int) -> list[float]:
    return [400.0 - 0.5 * i for i in range(n)]


def _insert_prices(store: DuckDBStore, instrument_id: str, closes: list[float]) -> list[date]:
    """Insert synthetic adjusted bars: open == close, high/low +-1%."""
    days = _trading_days(len(closes))
    rows = [
        (instrument_id, d, c, c * 1.01, c * 0.99, c, 1000.0, "adj-test", 1, 1)
        for d, c in zip(days, closes, strict=True)
    ]
    store.conn.executemany(
        """
        INSERT INTO daily_prices_adjusted (
            instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
            adjustment_version, price_factor_applied, volume_factor_applied, computed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TIMESTAMPTZ '2024-01-01 00:00:00+00')
        """,
        rows,
    )
    return days


def _insert_rs(store: DuckDBStore, instrument_id: str, as_of: date, rank: int | None) -> None:
    store.conn.execute(
        """
        INSERT INTO relative_strength_snapshots (
            as_of_date, instrument_id, ret_63, ret_126, ret_189, ret_252, rs_raw, rs_rank,
            rs_percentile, population_size, rs_status, universe_snapshot_id, calculation_version
        ) VALUES (?, ?, NULL, NULL, NULL, NULL, ?, ?, NULL, 100, ?, 'U1', 'rs-1.0.0')
        """,
        [
            as_of,
            instrument_id,
            0.5 if rank is not None else None,
            rank,
            "PASS" if rank is not None else "INSUFFICIENT_DATA",
        ],
    )


class Env:
    def __init__(self, config: TrendTemplateConfig | None = None) -> None:
        self.store = DuckDBStore(":memory:")
        self.store.migrate()
        self.features = DuckDBFeatureRepository(self.store)
        self.trend = DuckDBTrendRepository(self.store)
        self.engine = TrendTemplateEngine(self.features, self.trend, config)

    def seed(
        self,
        closes: list[float],
        rs_rank: int | None = 85,
        *,
        instrument_id: str = INSTRUMENT,
        with_rs_row: bool = True,
        compute_features: bool = True,
    ) -> date:
        days = _insert_prices(self.store, instrument_id, closes)
        if compute_features:
            DailyFeatureEngine(self.store).compute_for_instrument(instrument_id)
        if with_rs_row:
            _insert_rs(self.store, instrument_id, days[-1], rs_rank)
        return days[-1]


@pytest.fixture
def env() -> Env:
    return Env()


# --------------------------------------------------------------------------- structure


def test_always_ten_conditions_in_spec_order(env: Env) -> None:
    as_of = env.seed(_uptrend(300))
    result = env.engine.evaluate(INSTRUMENT, as_of)
    assert [c.condition_id for c in result.conditions] == list(range(1, 11))
    assert tuple(c.name for c in result.conditions) == TREND_CONDITION_NAMES
    assert result.algorithm_version == TREND_ALGORITHM_VERSION == "trend-1.0.0"


# --------------------------------------------------------------------------- pass / fail


def test_uptrend_passes_and_stores_every_measurement(env: Env) -> None:
    as_of = env.seed(_uptrend(300), rs_rank=85)
    result = env.engine.evaluate(INSTRUMENT, as_of)

    assert result.status is TrendTemplateStatus.PASS
    assert result.passed is True
    assert result.trend_template_pass is True
    assert result.rs_rank == 85
    for c in result.conditions:
        assert c.measurement is not None, c.name
        assert c.threshold is not None, c.name
        assert c.passed is True, c.name

    by_name = {c.name: c for c in result.conditions}
    assert by_name["close_above_sma150"].measurement == pytest.approx(249.5)
    assert by_name["rs_rank_min"].measurement == 85.0
    assert by_name["rs_rank_min"].threshold == 70.0
    # sma200 rising: the measurement is today's SMA200, the threshold SMA200 21 sessions ago
    rising = by_name["sma200_rising"]
    assert rising.measurement is not None and rising.threshold is not None
    assert rising.measurement > rising.threshold


def test_downtrend_fails_with_all_rows_stored(env: Env) -> None:
    as_of = env.seed(_downtrend(300), rs_rank=85)
    result = env.engine.evaluate(INSTRUMENT, as_of)

    assert result.status is TrendTemplateStatus.FAIL
    assert result.trend_template_pass is False
    assert result.passed is False
    assert len(result.conditions) == 10
    assert all(c.measurement is not None and c.threshold is not None for c in result.conditions)
    failed = {c.name for c in result.conditions if c.passed is False}
    assert {"close_above_sma150", "close_above_sma200", "sma200_rising"} <= failed
    assert "rs_rank_min" not in failed  # no short-circuit, and RS itself is fine here


def test_low_rs_rank_is_the_only_failure(env: Env) -> None:
    as_of = env.seed(_uptrend(300), rs_rank=60)
    result = env.engine.evaluate(INSTRUMENT, as_of)
    assert result.status is TrendTemplateStatus.FAIL
    failed = [c.name for c in result.conditions if c.passed is False]
    assert failed == ["rs_rank_min"]


def test_rs_rank_exactly_at_threshold_passes(env: Env) -> None:
    as_of = env.seed(_uptrend(300), rs_rank=70)
    assert env.engine.evaluate(INSTRUMENT, as_of).status is TrendTemplateStatus.PASS


def test_stricter_rs_threshold_from_config() -> None:
    e = Env(TrendTemplateConfig(min_rs_rank=80))
    as_of = e.seed(_uptrend(300), rs_rank=75)
    result = e.engine.evaluate(INSTRUMENT, as_of)
    assert result.status is TrendTemplateStatus.FAIL
    rs_cond = result.conditions[9]
    assert rs_cond.threshold == 80.0 and rs_cond.passed is False


def test_meets_stricter_rs_flag_is_research_only() -> None:
    e = Env(TrendTemplateConfig(min_rs_rank=70, stricter_rs_rank=80))
    as_of = e.seed(_uptrend(300), rs_rank=75)
    result = e.engine.evaluate(INSTRUMENT, as_of)
    assert result.status is TrendTemplateStatus.PASS  # 75 >= min_rs_rank 70
    assert result.meets_stricter_rs is False  # 75 < stricter_rs_rank 80
    e2 = Env(TrendTemplateConfig(min_rs_rank=70, stricter_rs_rank=80))
    as_of2 = e2.seed(_uptrend(300), rs_rank=80)
    assert e2.engine.evaluate(INSTRUMENT, as_of2).meets_stricter_rs is True


def test_meets_stricter_rs_is_none_without_rank(env: Env) -> None:
    as_of = env.seed(_uptrend(300), rs_rank=None)
    assert env.engine.evaluate(INSTRUMENT, as_of).meets_stricter_rs is None


# --------------------------------------------------------------------------- missing data


def test_short_history_is_insufficient_not_fail(env: Env) -> None:
    # Phase 4 fills SMA columns from partial windows, so this proves the engine does not
    # trust non-NULL features when the warm-up is not met.
    as_of = env.seed(_uptrend(100), rs_rank=None)
    result = env.engine.evaluate(INSTRUMENT, as_of)

    assert result.status is TrendTemplateStatus.INSUFFICIENT_DATA
    assert result.trend_template_pass is None
    assert result.passed is False
    assert len(result.conditions) == 10
    assert all(c.passed is None for c in result.conditions)  # never False, never 0
    assert all(c.name in TREND_CONDITION_NAMES for c in result.conditions)
    assert result.rs_rank is None
    assert result.conditions[9].threshold == 70.0
    assert result.conditions[9].measurement is None


def test_warmup_boundary_272_insufficient_273_evaluated(env: Env) -> None:
    as_of_short = env.seed(_uptrend(NEEDED - 1), instrument_id="SYN_272")
    short = env.engine.evaluate("SYN_272", as_of_short)
    assert short.status is TrendTemplateStatus.INSUFFICIENT_DATA

    as_of_ok = env.seed(_uptrend(NEEDED), instrument_id="SYN_273")
    ok = env.engine.evaluate("SYN_273", as_of_ok)
    assert ok.status is TrendTemplateStatus.PASS
    assert all(c.passed is True for c in ok.conditions)


def test_rs_snapshot_with_null_rank_is_insufficient(env: Env) -> None:
    as_of = env.seed(_uptrend(300), rs_rank=None)
    result = env.engine.evaluate(INSTRUMENT, as_of)

    assert result.status is TrendTemplateStatus.INSUFFICIENT_DATA
    assert result.rs_rank is None
    assert result.conditions[9].passed is None
    assert all(c.passed is True for c in result.conditions[:9])  # price conditions still stored


def test_missing_rs_snapshot_is_data_not_ready(env: Env) -> None:
    as_of = env.seed(_uptrend(300), with_rs_row=False)
    result = env.engine.evaluate(INSTRUMENT, as_of)
    assert result.status is TrendTemplateStatus.DATA_NOT_READY
    assert result.trend_template_pass is None
    assert result.conditions[9].passed is None
    assert all(c.passed is True for c in result.conditions[:9])


def test_no_bar_on_as_of_date_is_data_not_ready(env: Env) -> None:
    last = env.seed(_uptrend(300))
    result = env.engine.evaluate(INSTRUMENT, last + timedelta(days=3))  # later, no bar
    assert result.status is TrendTemplateStatus.DATA_NOT_READY
    assert all(c.passed is None for c in result.conditions)


def test_unknown_instrument_is_data_not_ready(env: Env) -> None:
    result = env.engine.evaluate("NOPE", START)
    assert result.status is TrendTemplateStatus.DATA_NOT_READY
    assert len(result.conditions) == 10
    assert all(c.passed is None and c.measurement is None for c in result.conditions)


def test_features_not_computed_is_data_not_ready(env: Env) -> None:
    as_of = env.seed(_uptrend(300), compute_features=False)
    result = env.engine.evaluate(INSTRUMENT, as_of)
    assert result.status is TrendTemplateStatus.DATA_NOT_READY
    assert all(c.passed is None for c in result.conditions)


# --------------------------------------------------------------------------- config variants


def test_longer_sma200_lookback_raises_the_warmup() -> None:
    e = Env(TrendTemplateConfig(sma200_slope_lookback_days=63))
    as_of = e.seed(_uptrend(300))  # 300 < 252 + 63
    assert e.engine.evaluate(INSTRUMENT, as_of).status is TrendTemplateStatus.INSUFFICIENT_DATA

    e2 = Env(TrendTemplateConfig(sma200_slope_lookback_days=63))
    as_of2 = e2.seed(_uptrend(315))
    assert e2.engine.evaluate(INSTRUMENT, as_of2).status is TrendTemplateStatus.PASS


def test_close_extreme_basis_uses_closes_not_high_low() -> None:
    e = Env(TrendTemplateConfig(extreme_basis="close"))
    closes = _uptrend(300)
    as_of = e.seed(closes)
    result = e.engine.evaluate(INSTRUMENT, as_of)
    window = closes[-252:]
    by_name = {c.name: c for c in result.conditions}
    assert by_name["above_52w_low"].threshold == pytest.approx(min(window) * 1.25)
    assert by_name["near_52w_high"].threshold == pytest.approx(max(window) * 0.75)

    default = Env()
    as_of2 = default.seed(closes)
    hl = {c.name: c for c in default.engine.evaluate(INSTRUMENT, as_of2).conditions}
    assert hl["near_52w_high"].threshold == pytest.approx(max(window) * 1.01 * 0.75)


# --------------------------------------------------------------------------- boundaries

_BASE = TrendInputs(
    close=200.0,
    sma_50=180.0,
    sma_150=170.0,
    sma_200=160.0,
    sma_200_prior=150.0,
    high_252=210.0,
    low_252=100.0,
    rs_rank=80,
)


def _cond(inputs: TrendInputs, condition_id: int) -> TrendConditionResult:
    return evaluate_conditions(inputs, TrendTemplateConfig())[condition_id - 1]


# (condition_id, field overrides that fail, overrides that sit exactly on the boundary)
_BOUNDARY_CASES = [
    (1, {"close": 169.0}, {"close": 170.0}, False),  # strict >
    (2, {"close": 159.0}, {"close": 160.0}, False),
    (3, {"sma_150": 159.0}, {"sma_150": 160.0}, False),
    (4, {"sma_200_prior": 161.0}, {"sma_200_prior": 160.0}, False),
    (5, {"sma_50": 169.0}, {"sma_50": 170.0}, False),
    (6, {"sma_50": 159.0, "sma_150": 150.0}, {"sma_50": 160.0}, False),
    (7, {"close": 179.0}, {"close": 180.0}, False),
    (8, {"close": 124.9}, {"close": 125.0}, True),  # >= low * 1.25
    (9, {"close": 157.0}, {"close": 157.5}, True),  # >= high * 0.75
    (10, {"rs_rank": 69}, {"rs_rank": 70}, True),  # >= min_rs_rank
]


@pytest.mark.parametrize(("cid", "fail", "boundary", "inclusive"), _BOUNDARY_CASES)
def test_condition_pass_fail_and_exact_boundary(
    cid: int, fail: dict[str, float], boundary: dict[str, float], inclusive: bool
) -> None:
    assert _cond(_BASE, cid).passed is True, "baseline must pass"
    assert _cond(replace(_BASE, **fail), cid).passed is False
    assert _cond(replace(_BASE, **boundary), cid).passed is inclusive


@pytest.mark.parametrize("cid", range(1, 11))
def test_none_input_gives_none_verdict_never_false(cid: int) -> None:
    empty = evaluate_conditions(TrendInputs(), TrendTemplateConfig())[cid - 1]
    assert empty.measurement is None and empty.passed is None


def test_derive_status_precedence() -> None:
    cfg = TrendTemplateConfig()
    assert derive_status(evaluate_conditions(_BASE, cfg)) is TrendTemplateStatus.PASS
    assert (
        derive_status(evaluate_conditions(replace(_BASE, rs_rank=1), cfg))
        is TrendTemplateStatus.FAIL
    )
    # a failing condition plus a missing one is still not a definitive FAIL
    mixed = replace(_BASE, rs_rank=None, close=1.0)
    assert derive_status(evaluate_conditions(mixed, cfg)) is TrendTemplateStatus.INSUFFICIENT_DATA


# --------------------------------------------------------------------------- look-ahead


def test_result_at_t_unchanged_when_later_data_is_deleted(env: Env) -> None:
    closes = _uptrend(320)
    days = _trading_days(320)
    env.seed(closes)
    t = days[299]
    _insert_rs(env.store, INSTRUMENT, t, 85)

    before = env.engine.evaluate(INSTRUMENT, t)
    assert before.status is TrendTemplateStatus.PASS

    for table, col in (
        ("daily_prices_adjusted", "trade_date"),
        ("technical_features_daily", "trade_date"),
        ("relative_strength_snapshots", "as_of_date"),
    ):
        env.store.conn.execute(f"DELETE FROM {table} WHERE {col} > ?", [t])
    after = env.engine.evaluate(INSTRUMENT, t)

    assert after == before


def test_determinism_same_inputs_same_rows(env: Env) -> None:
    as_of = env.seed(_uptrend(300))
    a = env.engine.evaluate(INSTRUMENT, as_of)
    b = env.engine.evaluate(INSTRUMENT, as_of)
    assert a == b
    assert a.conditions == b.conditions


def test_evaluate_many_preserves_order_and_attaches_weekly_context(env: Env) -> None:
    as_of_a = env.seed(_uptrend(300), instrument_id="AAA")
    env.seed(_downtrend(300), instrument_id="BBB")
    ctx = WeeklyContext("AAA", as_of_a, WeeklyStage.STAGE_2, 1.0, 2.0, None, False, "stage-1.0.0")

    results = env.engine.evaluate_many(["BBB", "AAA"], as_of_a, weekly_contexts={"AAA": ctx})

    assert [r.instrument_id for r in results] == ["BBB", "AAA"]
    assert results[0].weekly_context is None
    assert results[1].weekly_context == ctx
    assert results[0].status is TrendTemplateStatus.FAIL
    assert results[1].status is TrendTemplateStatus.PASS


# --------------------------------------------------------------------------- persistence


def test_persist_results_and_conditions_round_trip(env: Env) -> None:
    as_of = env.seed(_uptrend(300), instrument_id="P_PASS")
    env.seed(_uptrend(100), instrument_id="P_SHORT", rs_rank=None)
    ctx = WeeklyContext(
        "P_PASS", as_of, WeeklyStage.STAGE_2, 150.5, 2.5, 12.0, False, "stage-1.0.0"
    )
    passed = env.engine.evaluate("P_PASS", as_of, weekly_context=ctx)
    short = env.engine.evaluate("P_SHORT", _trading_days(100)[-1])

    env.trend.save_trend_template_results("scan-1", "hash-abc", [passed, short])

    row = env.store.conn.execute(
        "SELECT status, trend_template_pass, weekly_stage, weekly_stage2_pass, sma_w, slope_pct,"
        " is_partial_week, rs_rank, trend_score, calculation_version, config_hash"
        " FROM trend_template_results WHERE scan_id='scan-1' AND instrument_id='P_PASS'"
    ).fetchone()
    assert row == (
        "PASS", True, "STAGE_2", True, 150.5, 2.5, False, 85, None, "trend-1.0.0", "hash-abc",
    )  # fmt: skip

    short_row = env.store.conn.execute(
        "SELECT status, trend_template_pass, weekly_stage, rs_rank FROM trend_template_results"
        " WHERE instrument_id='P_SHORT'"
    ).fetchone()
    assert short_row == ("INSUFFICIENT_DATA", None, None, None)  # NULL, not FALSE

    assert env.trend.load_trend_conditions("P_PASS", as_of, "trend-1.0.0", "hash-abc") == list(
        passed.conditions
    )
    short_conds = env.trend.load_trend_conditions(
        "P_SHORT", short.as_of_date, "trend-1.0.0", "hash-abc"
    )
    assert len(short_conds) == 10
    assert all(c.passed is None for c in short_conds)
    null_verdicts = env.store.conn.execute(
        "SELECT COUNT(*) FROM trend_template_conditions WHERE instrument_id='P_SHORT'"
        " AND passed IS NULL"
    ).fetchone()
    assert null_verdicts == (10,)


def test_save_is_idempotent(env: Env) -> None:
    as_of = env.seed(_uptrend(300))
    result = env.engine.evaluate(INSTRUMENT, as_of)
    env.trend.save_trend_template_results("s", "h", [result])
    env.trend.save_trend_template_results("s", "h", [result])
    counts = env.store.conn.execute(
        "SELECT (SELECT COUNT(*) FROM trend_template_results),"
        " (SELECT COUNT(*) FROM trend_template_conditions)"
    ).fetchone()
    assert counts == (1, 10)


def test_different_config_hashes_do_not_overwrite_each_other() -> None:
    loose = Env(TrendTemplateConfig(min_rs_rank=70))
    as_of = loose.seed(_uptrend(300), rs_rank=75)
    strict_engine = TrendTemplateEngine(
        loose.features, loose.trend, TrendTemplateConfig(min_rs_rank=80)
    )
    loose_res = loose.engine.evaluate(INSTRUMENT, as_of)
    strict_res = strict_engine.evaluate(INSTRUMENT, as_of)
    loose.trend.save_trend_template_results("s1", "hash-loose", [loose_res])
    loose.trend.save_trend_template_results("s2", "hash-strict", [strict_res])

    loose_rows = loose.trend.load_trend_conditions(INSTRUMENT, as_of, "trend-1.0.0", "hash-loose")
    strict_rows = loose.trend.load_trend_conditions(INSTRUMENT, as_of, "trend-1.0.0", "hash-strict")
    assert loose_rows == list(loose_res.conditions) and strict_rows == list(strict_res.conditions)
    assert loose_rows[9].threshold == 70.0 and loose_rows[9].passed is True
    assert strict_rows[9].threshold == 80.0 and strict_rows[9].passed is False
    count = loose.store.conn.execute("SELECT COUNT(*) FROM trend_template_conditions").fetchone()
    assert count == (20,)


def test_load_relative_strength_reads_snapshot(env: Env) -> None:
    as_of = env.seed(_uptrend(300), rs_rank=91)
    rs = env.trend.load_relative_strength(INSTRUMENT, as_of, "rs-1.0.0")
    assert rs is not None and rs.rs_rank == 91 and rs.population_size == 100
    assert env.trend.load_relative_strength(INSTRUMENT, as_of, "rs-9.9.9") is None
    assert (
        env.trend.load_relative_strength(INSTRUMENT, as_of - timedelta(days=1), "rs-1.0.0") is None
    )


# --------------------------------------------------------------------------- boundaries of layers


def test_engine_module_does_not_import_storage_engines() -> None:
    src = Path(__file__).resolve().parents[2] / "src/vcp_scanner/features/trend_template.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert not roots & {"duckdb", "pyarrow", "kiteconnect", "dhanhq"}
