"""RS ranking tests required by TREND_TEMPLATE_SPECIFICATION section 7 (audit P1-6).

Known-answer ranking, ties, NULL-history exclusion, population invariance with respect to the
Trend Template stage, staleness boundary and the rank range of ``rs-1.0.0``. Synthetic data only.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from vcp_scanner.config.models import RSConfig
from vcp_scanner.data.repositories.duckdb_feature_repository import DuckDBFeatureRepository
from vcp_scanner.data.repositories.duckdb_rs_repository import DuckDBRelativeStrengthRepository
from vcp_scanner.data.repositories.duckdb_trend_repository import DuckDBTrendRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.trend import RSPriceInput, RSRow
from vcp_scanner.features.relative_strength import RelativeStrengthEngine, compute_rs_rows
from vcp_scanner.features.trend_template import TrendTemplateEngine

AS_OF = date(2024, 6, 28)
CFG = RSConfig()


def _inp(iid: str, total_return: float | None, *, stale_days: int = 0) -> RSPriceInput:
    """Every window returns ``total_return`` (so rs_raw == total_return under any weights)."""
    lag = None if total_return is None else 100.0
    last = 100.0 * (1 + (total_return or 0.0))
    return RSPriceInput(iid, AS_OF - timedelta(days=stale_days), last, (lag, lag, lag, lag))


def _by_id(inputs: list[RSPriceInput], cfg: RSConfig = CFG) -> dict[str, RSRow]:
    return {r.instrument_id: r for r in compute_rs_rows(inputs, AS_OF, cfg)}


# ---------------------------------------------------------------------------- known answer


def test_known_answer_ranking() -> None:
    rows = _by_id([_inp("A", 0.4), _inp("B", 0.3), _inp("C", 0.2), _inp("D", 0.1)])
    # pct = (below + 0.5 * equal) / N ; rank = 1 + floor(98 * pct)
    expected = {"A": (0.875, 86), "B": (0.625, 62), "C": (0.375, 37), "D": (0.125, 13)}
    for iid, (pct, rank) in expected.items():
        row = rows[iid]
        assert row.rs_percentile == pytest.approx(pct)
        assert row.rs_rank == rank
        assert row.population_size == 4
        assert row.rs_status == "PASS"


def test_raw_score_uses_the_configured_weights_and_windows() -> None:
    item = RSPriceInput("X", AS_OF, 120.0, (100.0, 80.0, 60.0, 40.0))
    (row,) = compute_rs_rows([item], AS_OF, CFG)
    returns = (0.2, 0.5, 1.0, 2.0)
    assert row.returns == pytest.approx(returns)
    assert row.rs_raw == pytest.approx(0.4 * 0.2 + 0.2 * 0.5 + 0.2 * 1.0 + 0.2 * 2.0)
    equal = RSConfig(weights=[0.25, 0.25, 0.25, 0.25])
    (row_eq,) = compute_rs_rows([item], AS_OF, equal)
    assert row_eq.rs_raw == pytest.approx(sum(returns) / 4)


# ---------------------------------------------------------------------------- ties


def test_ties_share_the_average_position() -> None:
    rows = _by_id([_inp("A", 0.4), _inp("B", 0.2), _inp("C", 0.2), _inp("D", 0.1)])
    assert rows["B"].rs_percentile == rows["C"].rs_percentile == pytest.approx(0.5)
    assert rows["B"].rs_rank == rows["C"].rs_rank == 50


def test_all_equal_scores_rank_in_the_middle() -> None:
    rows = compute_rs_rows([_inp(f"S{i}", 0.1) for i in range(7)], AS_OF, CFG)
    assert {r.rs_rank for r in rows} == {50}


# ---------------------------------------------------------------------------- NULL history


def test_null_history_is_excluded_not_scored_zero() -> None:
    base = [_inp("A", 0.4), _inp("B", 0.3), _inp("C", 0.2), _inp("D", 0.1)]
    with_short = _by_id([*base, _inp("SHORT", None)])
    short = with_short["SHORT"]
    assert (short.rs_raw, short.rs_rank, short.rs_percentile) == (None, None, None)
    assert short.rs_status == "INSUFFICIENT_DATA"
    without = _by_id(base)
    for iid in "ABCD":  # the ranked population and every rank are unchanged
        assert with_short[iid].rs_rank == without[iid].rs_rank
        assert with_short[iid].population_size == 4


def test_one_missing_window_is_enough_to_exclude() -> None:
    item = RSPriceInput("P", AS_OF, 110.0, (100.0, 100.0, 100.0, None))
    (row,) = compute_rs_rows([item], AS_OF, CFG)
    assert row.rs_status == "INSUFFICIENT_DATA"
    assert row.returns[:3] == pytest.approx((0.1, 0.1, 0.1)) and row.returns[3] is None


def test_zero_lagged_close_is_missing_not_infinite() -> None:
    item = RSPriceInput("Z", AS_OF, 110.0, (100.0, 0.0, 100.0, 100.0))
    (row,) = compute_rs_rows([item], AS_OF, CFG)
    assert row.returns[1] is None and row.rs_status == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------------------- staleness


def test_staleness_boundary_is_inclusive_of_max_days() -> None:
    limit = CFG.max_staleness_days
    rows = _by_id([_inp("OK", 0.1, stale_days=limit), _inp("OLD", 0.2, stale_days=limit + 1)])
    assert rows["OK"].rs_status == "PASS"
    old = rows["OLD"]
    assert old.rs_status == "STALE_DATA" and old.rs_raw is None
    assert old.returns == pytest.approx((0.2,) * 4)  # measurements kept
    assert rows["OK"].population_size == 1


# ---------------------------------------------------------------------------- rank range


def test_rank_range_is_1_to_98_under_rs_1_0_0_and_monotonic() -> None:
    """Spec quirk (TREND_TEMPLATE_SPECIFICATION section 3): with pct counting the instrument's
    own half-weight, the top pct is (N - 0.5) / N < 1, so rank 99 is unreachable in rs-1.0.0."""
    for n in (1, 2, 10, 100, 1000, 3000):
        rows = compute_rs_rows([_inp(f"S{i}", i / n) for i in range(n)], AS_OF, CFG)
        ranks = [r.rs_rank for r in sorted(rows, key=lambda r: r.rs_raw or 0.0)]
        assert all(r is not None and 1 <= r <= 98 for r in ranks)
        assert ranks == sorted(ranks)
        if n >= 49:
            assert max(ranks) == 98
            assert min(ranks) == 1
    assert 1 + math.floor(98 * (1000 - 0.5) / 1000) == 98


# ---------------------------------------------------------------------------- engine + population


def _seed(store: DuckDBStore, returns: dict[str, float], members: list[str]) -> None:
    """300 daily closes per instrument, ending AS_OF, growing to hit ``returns`` over 63 bars."""
    for iid, r in returns.items():
        closes = [100.0 * (1 + r) ** (i / 63) for i in range(300)][::-1]  # newest first
        store.conn.executemany(
            """
            INSERT INTO daily_prices_adjusted (
                instrument_id, trade_date, open_adj, high_adj, low_adj, close_adj, volume_adj,
                adjustment_version, price_factor_applied, volume_factor_applied, computed_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1000, 'v1', 1, 1, TIMESTAMPTZ '2024-06-30 00:00:00+00')
            """,
            [(iid, AS_OF - timedelta(days=k), c, c, c, c) for k, c in enumerate(closes)],
        )
    store.conn.execute(
        "INSERT INTO universe_snapshots VALUES ('u', 'test', ?, "
        "TIMESTAMPTZ '2024-06-30 00:00:00+00', 'h', '1.1', 'BIASED')",
        [AS_OF],
    )
    store.conn.executemany(
        "INSERT INTO universe_memberships (universe_snapshot_id, instrument_id, eligible) "
        "VALUES ('u', ?, TRUE)",
        [(m,) for m in members],
    )


def _rs_table(store: DuckDBStore) -> list[tuple]:
    return store.conn.execute(
        "SELECT instrument_id, rs_raw, rs_rank, population_size FROM relative_strength_snapshots "
        "ORDER BY instrument_id"
    ).fetchall()


def test_rs_is_unchanged_when_a_stock_leaves_the_trend_template_stage_not_the_population() -> None:
    """TREND_TEMPLATE_SPECIFICATION section 7 / PROJECT_DESIGN section 16: RS is ranked over
    the full eligible universe before the Trend Template gate; evaluating the gate on a subset
    (here without the weakest stock D) must not change any rank."""
    store = DuckDBStore(":memory:")
    store.migrate()
    returns = {"A": 0.4, "B": 0.3, "C": 0.2, "D": 0.1}
    _seed(store, returns, list(returns))
    engine = RelativeStrengthEngine(DuckDBRelativeStrengthRepository(store), config=CFG)
    assert engine.compute_for_date(AS_OF, "u") == 4
    before = _rs_table(store)

    trend = TrendTemplateEngine(DuckDBFeatureRepository(store), DuckDBTrendRepository(store))
    trend.evaluate_many(["A", "B", "C"], AS_OF)  # D is not evaluated by the gate
    assert _rs_table(store) == before
    ranks = {row[0]: row[2] for row in before}
    loaded = DuckDBTrendRepository(store).load_relative_strength("A", AS_OF, CFG.version)
    assert loaded is not None and loaded.rs_rank == ranks["A"] and loaded.population_size == 4

    # Contrast: removing D from the *population* (the universe) does change A's rank.
    only_abc = compute_rs_rows(
        [replace(_inp(i, returns[i]), instrument_id=i) for i in "ABC"], AS_OF, CFG
    )
    assert {r.instrument_id: r.rs_rank for r in only_abc}["A"] != ranks["A"]
