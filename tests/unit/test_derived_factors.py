"""Rights issues and demergers: factors derived from raw ex-date prices (audit step 2.4).

Real cases in tests/fixtures/corporate_actions/golden_derived_actions.json: BHARTIARTL rights
2021-09-27 (TERP factor equals Kite's to 1e-5), RELIANCE demerger 2023-07-20 and ITC demerger
2025-01-06 (factor from NSE's special pre-open price on the ex-date).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner.data.adjustment.engine import AdjustmentEngine, ex_date_prices
from vcp_scanner.data.ingestion.ca_worker import CorporateActionIngestionWorker
from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider
from vcp_scanner.data.quality.events import corporate_action_events
from vcp_scanner.data.reconciliation.engine import ReconciliationConfig, ReconciliationEngine
from vcp_scanner.data.repositories.duckdb_corporate_action_repository import (
    DuckDBCorporateActionRepository,
)
from vcp_scanner.data.repositories.duckdb_market_repository import (
    DuckDBMarketDataRepository,
    FinalDailyBar,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.domain.corporate_actions import (
    CorporateAction,
    CorporateActionResolution,
    CorporateActionStatus,
    ExDatePrices,
    derived_factor,
    factor_unknown,
)
from vcp_scanner.domain.enums import CorporateActionType as T
from vcp_scanner.domain.enums import Timeframe
from vcp_scanner.domain.market import Candle, Instrument

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "corporate_actions"
        / "golden_derived_actions.json"
    ).read_text()
)["actions"]
AT = datetime(2026, 9, 30, 12, tzinfo=UTC)


def _golden(symbol: str) -> dict[str, Any]:
    return next(g for g in GOLDEN if g["symbol"] == symbol)


def _resolution(
    action_type: T,
    *,
    num: float | None = None,
    den: float | None = None,
    cash: float | None = None,
    status: CorporateActionStatus = CorporateActionStatus.SINGLE_SOURCE,
    ex: date = date(2023, 7, 20),
) -> CorporateActionResolution:
    return CorporateActionResolution(
        f"r-{action_type}-{ex}", "I", action_type, status, ex_date=ex,
        ratio_numerator=num, ratio_denominator=den, cash_amount=cash,
    )  # fmt: skip


def _parse(record: dict[str, Any]) -> CorporateAction:
    provider = NSECorporateActionProvider()
    provider.unparsed_ratios = []
    provider.unhandled_records = []
    action = provider._parse_nse_action(record)
    assert action is not None
    return action


# --- parsing NSE records ---------------------------------------------------------------


def test_real_rights_record_parses_ratio_and_issue_price() -> None:
    action = _parse(_golden("BHARTIARTL")["nse_record"])
    assert action.action_type is T.RIGHTS
    assert (action.ratio_numerator, action.ratio_denominator) == (1.0, 14.0)
    assert action.cash_amount == 535.0  # face value 5 + premium 530


@pytest.mark.parametrize("symbol", ["RELIANCE", "ITC"])
def test_real_demerger_record_is_a_demerger(symbol: str) -> None:
    action = _parse(_golden(symbol)["nse_record"])
    assert action.action_type is T.DEMERGER
    assert action.ex_date == date.fromisoformat(_golden(symbol)["ex_date"])


@pytest.mark.parametrize(
    ("subject", "face", "ratio", "price"),
    [
        (" Rights 21:20@ Premium Rs 25/-", "10", (21.0, 20.0), 35.0),
        ("Rights 1:19.07 @ Premium Rs 0", "1", (1.0, 19.07), 1.0),
        (" Rights 613:399", "10", (613.0, 399.0), None),  # no premium: price unknown
        ("Rights Issue 4:17@ Premium Rs 390/-", "10", (4.0, 17.0), 400.0),
        ("Rights 7:10 @ Prm Rs 102/-", "10", (7.0, 10.0), 112.0),
    ],
)
def test_rights_subject_variants(
    subject: str, face: str, ratio: tuple[float, float], price: float | None
) -> None:
    record = {"symbol": "X", "isin": "INE000X01010", "exDate": "01-Oct-2021",
              "faceVal": face, "subject": subject, "ndStartDate": "-"}  # fmt: skip
    action = _parse(record)
    assert (action.ratio_numerator, action.ratio_denominator) == ratio
    assert action.cash_amount == price


# --- the factor math on real prices ----------------------------------------------------


def test_bharti_rights_terp_matches_kite() -> None:
    g = _golden("BHARTIARTL")
    r = _resolution(T.RIGHTS, num=1, den=14, cash=g["expected"]["issue_price"])
    pf, vf = derived_factor(r, ExDatePrices(g["prior"]["close"], g["ex"]["open"], raw=True))  # type: ignore[misc]
    assert pf == pytest.approx(g["expected"]["price_factor"], abs=1e-5)
    assert pf == pytest.approx(g["kite"]["price_ratio"], abs=2e-5)
    assert vf == pytest.approx(1 / pf)


@pytest.mark.parametrize("symbol", ["RELIANCE", "ITC"])
def test_demerger_factor_is_ex_date_open_over_prior_close(symbol: str) -> None:
    g = _golden(symbol)
    r = _resolution(T.DEMERGER)
    got = derived_factor(r, ExDatePrices(g["prior"]["close"], g["ex"]["open"], raw=True))
    assert got is not None
    assert got[0] == pytest.approx(g["expected"]["price_factor"], abs=1e-6)
    assert got[1] == 1.0  # a demerger does not change the share count


def test_rights_above_market_have_no_bonus_element() -> None:
    r = _resolution(T.RIGHTS, num=1, den=5, cash=120.0)
    assert derived_factor(r, ExDatePrices(100.0, 99.0, raw=True)) == (1.0, 1.0)


@pytest.mark.parametrize(
    ("resolution", "prices"),
    [
        (_resolution(T.RIGHTS, num=1, den=5), ExDatePrices(100.0, 99.0, True)),  # no price
        (_resolution(T.RIGHTS, cash=50.0), ExDatePrices(100.0, 99.0, True)),  # no ratio
        (_resolution(T.DEMERGER), ExDatePrices(100.0, 100.0, True)),  # open not below close
        (_resolution(T.DEMERGER), ExDatePrices(100.0, None, True)),  # did not trade
        (_resolution(T.DEMERGER), ExDatePrices(100.0, 2.0, True)),  # implausible
    ],
)
def test_underivable_factor_on_raw_prices_is_unknown(
    resolution: CorporateActionResolution, prices: ExDatePrices
) -> None:
    assert derived_factor(resolution, prices) is None
    assert factor_unknown(resolution, prices)


def test_no_factor_and_no_block_on_provider_adjusted_prices() -> None:
    r = _resolution(T.DEMERGER)
    kite = ExDatePrices(100.0, 100.0, raw=False)  # Kite already removed the gap
    assert not factor_unknown(r, kite)
    assert not factor_unknown(r, None)
    assert AdjustmentEngine().compute_factors([r], {r.ex_date: kite}) == []  # type: ignore[dict-item]


# --- prices around ex-dates from candles -----------------------------------------------


def _bar(d: date, o: float, c: float, provider: str = "NSE_BHAVCOPY") -> Candle:
    return Candle("I", datetime(d.year, d.month, d.day, tzinfo=UTC), Timeframe.DAILY,
                  o, max(o, c), min(o, c), c, 100, provider)  # fmt: skip


def test_ex_date_prices_from_candles() -> None:
    bars = [_bar(date(2023, 7, 18), 2800, 2820.45), _bar(date(2023, 7, 19), 2830, 2841.85),
            _bar(date(2023, 7, 20), 2580, 2619.85)]  # fmt: skip
    prices = ex_date_prices(bars, [date(2023, 7, 20), date(2023, 7, 1), date(2023, 7, 22)])
    assert prices[date(2023, 7, 20)] == ExDatePrices(2841.85, 2580.0, True)
    assert date(2023, 7, 1) not in prices  # no earlier bar
    assert prices[date(2023, 7, 22)] == ExDatePrices(2619.85, None, True)  # no bar that day
    kite = ex_date_prices([*bars[:2], _bar(date(2023, 7, 20), 2580, 2619.85, "KITE")],
                          [date(2023, 7, 20)])  # fmt: skip
    assert not kite[date(2023, 7, 20)].raw


# --- engine and events -----------------------------------------------------------------


def test_engine_combines_derived_and_ratio_factors() -> None:
    demerger = _resolution(T.DEMERGER, ex=date(2023, 7, 20))
    bonus = _resolution(T.BONUS, num=1, den=1, ex=date(2024, 10, 28))
    prices = {date(2023, 7, 20): ExDatePrices(2841.85, 2580.0, True)}
    adjustments = AdjustmentEngine().compute_factors([demerger, bonus], prices)
    assert [a.effective_date for a in adjustments] == [date(2023, 7, 20), date(2024, 10, 28)]
    assert adjustments[0].price_factor == pytest.approx(2580.0 / 2841.85)
    assert adjustments[0].volume_factor == 1.0
    assert adjustments[0].cumulative_price_factor == pytest.approx(0.5 * 2580.0 / 2841.85)
    # Without prices the demerger contributes nothing (and the quality layer decides).
    assert [a.effective_date for a in AdjustmentEngine().compute_factors([demerger, bonus])] == [
        date(2024, 10, 28)
    ]


def test_underivable_factor_is_a_blocking_event_only_on_raw_prices() -> None:
    r = _resolution(T.RIGHTS, num=613, den=399)  # issue price unknown
    raw = {r.ex_date: ExDatePrices(100.0, 99.0, True)}
    (event,) = corporate_action_events("I", [r], AT, ex_prices=raw)  # type: ignore[arg-type]
    assert event.blocks_signal and event.trade_date == r.ex_date
    assert event.context is not None and event.context["cause"] == "factor_unknown"
    kite = {r.ex_date: ExDatePrices(100.0, 99.0, False)}
    assert corporate_action_events("I", [r], AT, ex_prices=kite) == []  # type: ignore[arg-type]


# --- end to end through the corporate-action worker ------------------------------------


class _Provider:
    def __init__(self, actions: list[CorporateAction]) -> None:
        self.actions = actions

    def get_actions(self, start, end, instruments=None):  # noqa: ANN001, ANN201
        return list(self.actions)


def test_worker_stores_the_reliance_demerger_factor_from_bhavcopy_bars() -> None:
    g = _golden("RELIANCE")
    store = DuckDBStore(":memory:")
    store.migrate()
    market = DuckDBMarketDataRepository(store)
    iid = "NSE_EQ|RELIANCE"
    market.save_final_daily(
        [
            FinalDailyBar(iid, date(2023, 7, 19), 2830.0, 2851.0, 2820.0, 2841.85, 10, "EQ"),
            FinalDailyBar(iid, date(2023, 7, 20), 2580.0, 2632.0, 2555.0, 2619.85, 10, "EQ"),
        ],
        known_from=AT,
        source_run_id="t",
    )
    demerger = _parse(g["nse_record"])
    repo = DuckDBCorporateActionRepository(store)
    worker = CorporateActionIngestionWorker(
        _Provider([demerger]), _Provider([]), repo,
        ReconciliationEngine(ReconciliationConfig(secondary_grace_days=3)), AdjustmentEngine(),
        market=market,
    )  # fmt: skip
    worker.run(date(2023, 7, 1), date(2023, 7, 31), [Instrument(iid, "RELIANCE")], known_at=AT)
    (factor,) = repo.load_adjustments(iid)
    assert factor.effective_date == date(2023, 7, 20)
    assert float(factor.price_factor) == pytest.approx(g["expected"]["price_factor"], abs=1e-6)


def test_reconciliation_uses_the_most_recently_seen_primary_record() -> None:
    """A re-read NSE record with more detail (a parser upgrade) wins over the older reading."""
    ex = date(2021, 9, 27)
    old = CorporateAction("nse-old", "I", T.RIGHTS, "NSE", AT, ex_date=ex,
                          ingested_at=datetime(2026, 1, 1, tzinfo=UTC))  # fmt: skip
    new = CorporateAction("nse-new", "I", T.RIGHTS, "NSE", AT, ex_date=ex, ratio_numerator=1,
                          ratio_denominator=14, cash_amount=535.0,
                          ingested_at=datetime(2026, 9, 30, tzinfo=UTC))  # fmt: skip
    engine = ReconciliationEngine(ReconciliationConfig(secondary_grace_days=3))
    for actions in ([old, new], [new, old]):
        (result,) = engine.reconcile(
            instrument_id="I", actions=actions, as_of_date=date(2026, 9, 30)
        )
        res = result.resolution
        assert (res.ratio_numerator, res.cash_amount, res.nse_action_id) == (1, 535.0, "nse-new")
