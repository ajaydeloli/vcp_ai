"""Bugs found by the first real-data run (audit Fix 5b). Synthetic data only."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from vcp_scanner.cli import _resolve_instrument_args
from vcp_scanner.cli_pipeline import _matches
from vcp_scanner.data.adjustment.builder import AdjustedPriceBuilder
from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.data.providers.fake import make_candle
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import DuckDBMarketDataRepository
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import (
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType as T

IID = "NSE_EQ|COMBO"
EX = date(2025, 6, 16)
T0 = datetime(2025, 7, 1, tzinfo=UTC)


def _res(rid: str, kind: T, num: float, den: float, ex: date = EX) -> CorporateActionResolution:
    return CorporateActionResolution(
        rid, IID, kind, CorporateActionStatus.SINGLE_SOURCE, ex_date=ex,
        ratio_numerator=num, ratio_denominator=den,
    )  # fmt: skip


# ---------------------------------------------------------------------------- same-day actions


def test_split_and_bonus_on_one_ex_date_combine_into_one_factor() -> None:
    """Like BAJFINANCE 2025-06-16: 1:2 split (0.5) and 4:1 bonus (0.2) -> 0.1 overall."""
    (adj,) = AdjustmentEngine().compute_factors(
        [_res("S", T.SPLIT, 2, 1), _res("B", T.BONUS, 4, 1)]
    )
    assert adj.effective_date == EX
    assert adj.price_factor == pytest.approx(0.1)
    assert adj.volume_factor == pytest.approx(10.0)
    assert adj.cumulative_price_factor == pytest.approx(0.1)
    assert set(adj.resolution_id.split("+")) == {"S", "B"}


def test_combined_factor_survives_storage_and_a_later_action_compounds() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBCorporateActionRepository(store)
    later = _res("L", T.SPLIT, 5, 1, ex=EX + timedelta(days=200))
    factors = AdjustmentEngine().compute_factors(
        [_res("S", T.SPLIT, 2, 1), _res("B", T.BONUS, 4, 1), later]
    )
    repo.replace_adjustments(IID, factors, known_from=T0)
    stored = repo.load_adjustments(IID)
    assert [(a.effective_date, round(a.cumulative_price_factor, 6)) for a in stored] == [
        (EX, 0.02),
        (later.ex_date, 0.2),
    ]

    # end to end: a raw 1000 bar before the double action lands at 1000 x 0.02 = 20
    market = DuckDBMarketDataRepository(store, clock=lambda: T0)
    market.save_daily([make_candle(IID, EX - timedelta(days=3), 1000, 1001, 999, 1000)])
    AdjustedPriceBuilder(market, repo).build_for_instrument(IID, computed_at=T0)
    (row,) = market.load_adjusted_daily(IID, EX - timedelta(days=10), EX)
    assert row.close_adj == pytest.approx(20.0)


# ---------------------------------------------------------------------------- --instrument


@pytest.mark.parametrize(
    ("iid", "wanted", "expected"),
    [
        ("NSE_EQ|RELIANCE", ["RELIANCE"], True),
        ("NSE_EQ|RELIANCE", ["reliance"], True),
        ("NSE_EQ|RELIANCE", ["NSE_EQ|RELIANCE"], True),
        ("NSE_EQ|RELIANCE", ["TCS"], False),
        ("NSE_EQ|RELIANCE", None, True),
        ("NSE_EQ|RELIANCE", [], True),
    ],
)
def test_instrument_filter_accepts_ids_or_symbols(
    iid: str, wanted: list[str] | None, expected: bool
) -> None:
    assert _matches(iid, wanted) is expected


def test_instrument_args_resolve_to_ids_and_keep_unknowns() -> None:
    known = ["NSE_EQ|RELIANCE", "NSE_EQ|TCS"]
    assert _resolve_instrument_args(["reliance", "NSE_EQ|TCS", "GHOST"], known) == [
        "NSE_EQ|RELIANCE",
        "NSE_EQ|TCS",
        "GHOST",
    ]
    assert _resolve_instrument_args(None, known) is None


def test_worker_repairs_a_stale_factor_set_even_when_no_resolution_changed() -> None:
    """A DB written before the same-day fix holds 0.5 instead of 0.1; a rerun repairs it."""
    from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
    from vcp_scanner.data.reconciliation.engine import ReconciliationEngine
    from vcp_scanner.domain.corporate_actions import CorporateAction

    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBCorporateActionRepository(store)

    class _Provider:
        def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
            return [
                CorporateAction(f"N-{k}", IID, kind, "NSE", T0, ex_date=EX,
                                ratio_numerator=n, ratio_denominator=d)
                for k, (kind, n, d) in enumerate([(T.SPLIT, 2, 1), (T.BONUS, 4, 1)])
            ]  # fmt: skip

    class _Nothing:
        def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
            return []

    worker = CorporateActionIngestionWorker(
        _Provider(), _Nothing(), repo, ReconciliationEngine(), AdjustmentEngine()
    )
    worker.run(date(2025, 1, 1), date(2025, 12, 31), known_at=T0)
    assert [round(a.price_factor, 6) for a in repo.load_adjustments(IID)] == [0.1]

    # simulate the old bug's stored state, then rerun with unchanged resolutions
    (good,) = repo.load_adjustments(IID)
    from dataclasses import replace as dc_replace

    stale = dc_replace(good, price_factor=0.5, volume_factor=2.0,
                       cumulative_price_factor=0.5, cumulative_volume_factor=2.0)  # fmt: skip
    repo.replace_adjustments(IID, [stale], known_from=T0 + timedelta(hours=1))
    worker.run(date(2025, 1, 1), date(2025, 12, 31), known_at=T0 + timedelta(hours=2))
    assert [round(a.price_factor, 6) for a in repo.load_adjustments(IID)] == [0.1]
