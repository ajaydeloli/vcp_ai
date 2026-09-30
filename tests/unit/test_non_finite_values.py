"""Audit P1-9: NaN / inf / non-positive prices are missing or invalid data, never a verdict.

Synthetic values only.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from vcp_scanner.config.models import StageConfig, TrendTemplateConfig
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.schema import validate_ohlc
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.enums import Timeframe, TrendTemplateStatus, WeeklyStage
from vcp_scanner.domain.market import Candle
from vcp_scanner.features.relative_strength import RelativeStrengthEngine
from vcp_scanner.features.trend_template import TrendInputs, derive_status, evaluate_conditions
from vcp_scanner.features.weekly_stage import classify_weekly_stage

NAN = float("nan")
INF = float("inf")

# ---------------------------------------------------------------------------- validate_ohlc


@pytest.mark.parametrize(
    ("bar", "reason"),
    [
        ((NAN, NAN, NAN, NAN, 100), "not finite"),
        ((10, 12, 9, NAN, 100), "close not finite"),
        ((10, INF, 9, 11, 100), "high not finite"),
        ((0, 0, 0, 0, 0), "<= 0"),
        ((-5, -1, -9, -2, 10), "<= 0"),
        ((10, 12, 0, 11, 100), "low <= 0"),
    ],
)
def test_bad_bars_are_rejected(bar: tuple[float, ...], reason: str) -> None:
    result = validate_ohlc(*bar)  # type: ignore[arg-type]
    assert result.is_valid is False
    assert reason in (result.reason or "")


def test_valid_bar_and_missing_volume_still_pass() -> None:
    assert validate_ohlc(10, 12, 9, 11, 100).is_valid
    assert validate_ohlc(10, 12, 9, 11, None).is_valid
    assert validate_ohlc(10, 10, 10, 10, 0).is_valid  # flat bar, zero volume: legal


def test_nan_bar_never_reaches_canonical_prices() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBMarketDataRepository(store)
    ts = datetime(2024, 1, 2, tzinfo=UTC)
    bad = Candle("X", ts, Timeframe.DAILY, NAN, NAN, NAN, NAN, 10, "TEST")
    assert repo.save_daily([bad]) == 0
    assert repo.load_daily("X", date(2024, 1, 1), date(2024, 1, 31)) == []


# ---------------------------------------------------------------------------- Trend Template

_BASE = TrendInputs(
    close=120, sma_50=110, sma_150=100, sma_200=90, sma_200_prior=85,
    high_252=125, low_252=60, rs_rank=80,
)  # fmt: skip


def test_clean_inputs_still_pass() -> None:
    assert derive_status(evaluate_conditions(_BASE, TrendTemplateConfig())) is (
        TrendTemplateStatus.PASS
    )


@pytest.mark.parametrize(
    "field", ["close", "sma_50", "sma_150", "sma_200", "sma_200_prior", "high_252", "low_252"]
)
@pytest.mark.parametrize("bad", [NAN, INF, -INF])
def test_non_finite_input_is_insufficient_not_fail(field: str, bad: float) -> None:
    conditions = evaluate_conditions(replace(_BASE, **{field: bad}), TrendTemplateConfig())
    assert derive_status(conditions) is TrendTemplateStatus.INSUFFICIENT_DATA
    # the bad value is stored as NULL, never as NaN
    for c in conditions:
        for v in (c.measurement, c.threshold):
            assert v is None or math.isfinite(v)


# ---------------------------------------------------------------------------- weekly Stage


def test_non_finite_weekly_close_is_insufficient() -> None:
    closes = [100.0 + i for i in range(70)]
    assert classify_weekly_stage(closes, StageConfig()).stage is WeeklyStage.STAGE_2
    closes[40] = NAN
    result = classify_weekly_stage(closes, StageConfig())
    assert result.stage is WeeklyStage.INSUFFICIENT_DATA
    assert (result.sma_w, result.slope_pct, result.prior_pct) == (None, None, None)


# ---------------------------------------------------------------------------- RS


def _seed_rs(store: DuckDBStore, closes: dict[str, list[float]]) -> None:
    store.conn.execute(
        "INSERT INTO universe_snapshots VALUES "
        "('u1', 'test', '2023-12-31', current_timestamp, 'h', 'v1', 'BIASED')"
    )
    for iid, series in closes.items():
        store.conn.execute(
            "INSERT INTO universe_memberships VALUES "
            "('u1', ?, TRUE, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)",
            [iid],
        )
        for i, c in enumerate(series):  # i = 0 is the as-of date
            store.conn.execute(
                """
                INSERT INTO daily_prices_adjusted (
                    instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj,
                    volume_adj, adjustment_version, price_factor_applied,
                    volume_factor_applied, computed_at
                ) VALUES (?, DATE '2023-12-31' - ?::INTEGER, ?, ?, ?, ?, 100, 'v1', 1, 1,
                          current_timestamp)
                """,
                [iid, i, c, c, c, c],
            )


def test_nan_close_is_excluded_from_the_rs_population() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    good = {f"G{k}": [200.0 + k - i * 0.1 for i in range(260)] for k in range(4)}
    bad = [200.0 - i * 0.1 for i in range(260)]
    bad[0] = NAN  # corrupt as-of close
    _seed_rs(store, {**good, "BAD": bad})

    RelativeStrengthEngine(store).compute_for_date(date(2023, 12, 31), "u1")
    rows = dict(
        (r[0], r[1:])
        for r in store.conn.execute(
            "SELECT instrument_id, rs_raw, rs_rank, rs_status, population_size, ret_63 "
            "FROM relative_strength_snapshots"
        ).fetchall()
    )
    rs_raw, rs_rank, status, _, ret_63 = rows["BAD"]
    assert (rs_raw, rs_rank, ret_63, status) == (None, None, None, "INSUFFICIENT_DATA")
    for iid in good:
        assert rows[iid][3] == 4  # population excludes the NaN instrument
        assert rows[iid][1] is not None
