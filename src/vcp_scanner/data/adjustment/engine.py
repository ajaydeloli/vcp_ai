"""Price Adjustment Engine (DATA_SPECIFICATION §19-21).

Consumes CorporateActionResolution records and computes cumulative price
and volume factors.
Applies these factors to daily_prices to yield daily_prices_adjusted.
"""

from __future__ import annotations

import logging

from vcp_scanner.domain.corporate_actions import (
    CorporateActionAdjustment,
    CorporateActionResolution,
)
from vcp_scanner.domain.enums import CorporateActionType
from vcp_scanner.domain.market import Candle

logger = logging.getLogger(__name__)

CALCULATION_VERSION = "1.0"


class AdjustmentEngine:
    """Computes derived adjustment factors from resolved corporate actions."""

    def compute_factors(
        self,
        resolutions: list[CorporateActionResolution],
    ) -> list[CorporateActionAdjustment]:
        """Compute cumulative adjustment factors from a time-series of resolutions.

        Args:
            resolutions: All current resolutions for an instrument, in
                any order (they will be sorted by ex_date internally).

        Returns:
            List of CorporateActionAdjustment rows ordered by effective_date.
        """
        if not resolutions:
            return []

        # Sort resolutions descending by ex_date so we can build backward cumulative factors.
        # Note: In standard price adjustment, today's price is 1.0.
        # An action in the past divides historical prices to match today's scale.

        valid = [
            r
            for r in resolutions
            if r.ex_date is not None and self._is_price_affecting(r.action_type)
        ]
        valid.sort(key=lambda r: r.ex_date, reverse=True)

        adjustments: list[CorporateActionAdjustment] = []
        cum_pf = 1.0
        cum_vf = 1.0

        # We walk backwards from most recent to oldest
        for r in valid:
            pf, vf = self._compute_single_factor(r)

            # If the action has no mathematical effect, we skip making a record
            if pf == 1.0 and vf == 1.0:
                continue

            cum_pf *= pf
            cum_vf *= vf

            adj = CorporateActionAdjustment(
                resolution_id=r.resolution_id,
                instrument_id=r.instrument_id,
                effective_date=r.ex_date,
                price_factor=pf,
                volume_factor=vf,
                cumulative_price_factor=cum_pf,
                cumulative_volume_factor=cum_vf,
                source="INTERNAL",
                calculation_version=CALCULATION_VERSION,
            )
            adjustments.append(adj)

        # Return them sorted ascending by effective date for storage
        adjustments.reverse()
        return adjustments

    def apply_factors(
        self,
        candles: list[Candle],
        adjustments: list[CorporateActionAdjustment],
    ) -> list[Candle]:
        """Apply cumulative adjustment factors to raw candles.

        Args:
            candles: Raw daily candles sorted ascending by date.
            adjustments: Cumulative adjustments sorted ascending by effective_date.

        Returns:
            New list of adjusted Candle objects.
        """
        if not candles:
            return []
        if not adjustments:
            return candles

        # We need to map each candle date to the correct cumulative factor.
        # A candle at time T is adjusted by the cumulative product of all actions
        # with ex_date > T.
        # Because `adjustments` stores `cumulative_price_factor` calculated by walking
        # backwards, the adjustment record for ex_date E holds the multiplier to apply
        # to all candles prior to E.

        adj_idx = 0

        adjusted_candles = []
        for candle in candles:
            # Advance the adjustment pointer if we have passed the ex_date
            while (
                adj_idx < len(adjustments)
                and candle.timestamp.date() >= adjustments[adj_idx].effective_date
            ):
                adj_idx += 1

            if adj_idx < len(adjustments):
                # We are before this ex_date, so we apply its cumulative factor
                # (which inherently includes all subsequent ex_dates because of the backward walk)
                active = adjustments[adj_idx]
                pf = active.cumulative_price_factor
                vf = active.cumulative_volume_factor
            else:
                # We are at or after the last ex_date, so no historical adjustment applies
                pf = 1.0
                vf = 1.0

            if pf == 1.0 and vf == 1.0:
                adjusted_candles.append(candle)
            else:
                ac = Candle(
                    instrument_id=candle.instrument_id,
                    timestamp=candle.timestamp,
                    open=candle.open * pf,
                    high=candle.high * pf,
                    low=candle.low * pf,
                    close=candle.close * pf,
                    volume=int(candle.volume * vf) if candle.volume is not None else None,
                    timeframe=candle.timeframe,
                    provider=candle.provider,
                )
                adjusted_candles.append(ac)

        return adjusted_candles

    def _is_price_affecting(self, action_type: CorporateActionType) -> bool:
        return action_type in (
            CorporateActionType.SPLIT,
            CorporateActionType.BONUS,
            CorporateActionType.DIVIDEND,
        )

    def _compute_single_factor(self, resolution: CorporateActionResolution) -> tuple[float, float]:
        """Compute (price_factor, volume_factor) for a single action.

        Returns (1.0, 1.0) if no change.
        """
        if resolution.action_type == CorporateActionType.SPLIT:
            num = resolution.ratio_numerator
            den = resolution.ratio_denominator
            if num and den and num > 0 and den > 0:
                return (den / num, num / den)

        elif resolution.action_type == CorporateActionType.BONUS:
            # N bonus shares for every D shares held -> total shares = N + D
            num = resolution.ratio_numerator
            den = resolution.ratio_denominator
            if num and den and num > 0 and den > 0:
                total = num + den
                return (den / total, total / den)

        # Dividend logic would go here if we were adjusting for all dividends,
        # but typically we don't adjust volume for dividends, and price only if
        # it's a special dividend.
        # We will return 1.0, 1.0 for now unless cash_amount logic is specified.

        return 1.0, 1.0
