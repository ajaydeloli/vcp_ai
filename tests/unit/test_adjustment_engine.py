"""Golden corporate-action fixtures (DATA_SPECIFICATION §18A).

Tests independent correctness of the Price Adjustment Engine against known
historical reality for Indian equities.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.domain.corporate_actions import (
    CorporateActionResolution,
    CorporateActionStatus,
)
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.market import Candle


def _make_res(
    action_type: CorporateActionType,
    ex: date,
    num: float,
    den: float,
) -> CorporateActionResolution:
    return CorporateActionResolution(
        resolution_id=f"{action_type.value}-{ex}",
        instrument_id="TEST",
        action_type=action_type,
        status=CorporateActionStatus.CONFIRMED,
        ex_date=ex,
        ratio_numerator=num,
        ratio_denominator=den,
    )


def _make_candle(d: date, close: float, volume: int = 1000) -> Candle:
    return Candle(
        instrument_id="TEST",
        timestamp=datetime(d.year, d.month, d.day, tzinfo=UTC),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        timeframe="D",
        provider="INTERNAL",
    )


class TestPriceAdjustmentEngine:
    def test_single_split(self) -> None:
        """10-for-1 split (e.g. IRCTC in 2021). Price drops to 1/10th, volume 10x."""
        engine = AdjustmentEngine()

        resolutions = [_make_res(CorporateActionType.SPLIT, date(2021, 10, 28), 10.0, 1.0)]
        factors = engine.compute_factors(resolutions)

        assert len(factors) == 1
        f = factors[0]
        assert f.price_factor == 0.1
        assert f.volume_factor == 10.0
        assert f.cumulative_price_factor == 0.1

        candles = [
            _make_candle(date(2021, 10, 27), 4000.0, 1000),  # Pre-split
            _make_candle(date(2021, 10, 28), 400.0, 10000),  # Post-split
        ]

        adj_candles = engine.apply_factors(candles, factors)

        assert adj_candles[0].close == 400.0  # Pre-split price adjusted down
        assert adj_candles[0].volume == 10000  # Pre-split volume adjusted up
        assert adj_candles[1].close == 400.0  # Post-split unchanged
        assert adj_candles[1].volume == 10000  # Post-split unchanged

    def test_single_bonus(self) -> None:
        """1-for-1 bonus (e.g. RELIANCE in 2017). Gives 1 bonus for every 1 held -> total 2.
        Price drops to 1/2, volume 2x."""
        engine = AdjustmentEngine()

        resolutions = [_make_res(CorporateActionType.BONUS, date(2017, 9, 7), 1.0, 1.0)]
        factors = engine.compute_factors(resolutions)

        f = factors[0]
        assert f.price_factor == 0.5
        assert f.volume_factor == 2.0

        candles = [
            _make_candle(date(2017, 9, 6), 1600.0, 1000),
            _make_candle(date(2017, 9, 7), 800.0, 2000),
        ]

        adj = engine.apply_factors(candles, factors)

        assert adj[0].close == 800.0
        assert adj[0].volume == 2000
        assert adj[1].close == 800.0
        assert adj[1].volume == 2000

    def test_multiple_actions_cumulative(self) -> None:
        """Sequential bonus and split.
        e.g., ITC had multiple over the years.
        Action 1 (older): 1:2 Bonus (gives 1 for every 2 held) -> Price * 2/3
        Action 2 (newer): 10:1 Split -> Price * 1/10
        """
        engine = AdjustmentEngine()

        resolutions = [
            _make_res(CorporateActionType.BONUS, date(2010, 8, 3), 1.0, 2.0),  # 1 for 2 bonus
            _make_res(CorporateActionType.SPLIT, date(2016, 7, 1), 10.0, 1.0),  # 10 for 1 split
        ]
        factors = engine.compute_factors(resolutions)

        assert len(factors) == 2
        # Engine sorts factors by effective_date ascending for storage
        f_bonus = factors[0]  # The older bonus
        f_split = factors[1]  # The newer split

        # Backward accumulation:
        # Split: pf = 1/10
        # Bonus: pf = 2/3 * 1/10 = 2/30 = 1/15

        assert f_split.price_factor == pytest.approx(0.1)
        assert f_split.cumulative_price_factor == pytest.approx(0.1)

        assert f_bonus.price_factor == pytest.approx(2.0 / 3.0)
        assert f_bonus.cumulative_price_factor == pytest.approx((2.0 / 3.0) * 0.1)

        candles = [
            _make_candle(date(2010, 8, 1), 300.0),  # Pre-bonus, pre-split
            _make_candle(date(2011, 1, 1), 200.0),  # Post-bonus, pre-split
            _make_candle(date(2016, 7, 2), 20.0),  # Post-bonus, post-split
        ]

        adj = engine.apply_factors(candles, factors)

        assert adj[0].close == pytest.approx(20.0)
        assert adj[1].close == pytest.approx(20.0)
        assert adj[2].close == pytest.approx(20.0)
