"""Gap Safety Net (DATA_SPECIFICATION §18A, 22).

Catches massive price gaps that likely represent missing corporate actions.
"""

from __future__ import annotations

import logging
from datetime import datetime

from vcp_scanner.config.models import UnexplainedGapConfig
from vcp_scanner.data.adjustment.engine import AdjustmentEngine
from vcp_scanner.domain.corporate_actions import (
    PRICE_SCALING_ACTIONS,
    CorporateActionResolution,
    ExDatePrices,
    derived_factor,
    explains_price_gap,
)
from vcp_scanner.domain.enums import CorporateActionType, DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity, make_event_id
from vcp_scanner.domain.market import Candle
from vcp_scanner.infrastructure.clock import Clock, utc_now

logger = logging.getLogger(__name__)

# Stateless; used only for its split/bonus factor formula, so the detector and the adjusted
# prices agree on what an action does.
_ENGINE = AdjustmentEngine()


class GapDetector:
    """Detects unexplained gaps between consecutive daily candles."""

    def __init__(
        self,
        config: UnexplainedGapConfig | None = None,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self._config = config or UnexplainedGapConfig()
        self._clock = clock

    def detect(
        self,
        candles: list[Candle],
        resolutions: list[CorporateActionResolution],
        detected_at: datetime | None = None,
    ) -> list[DataQualityEvent]:
        """Scan ordered candles for unexplained price gaps.

        Args:
            candles: Raw daily candles for a single instrument, sorted by date.
            resolutions: Reconciled corporate actions for this instrument.
            detected_at: The timestamp to stamp on generated events. Defaults to the injected clock.

        Returns:
            List of DataQualityEvent for detected anomalies.
        """
        if len(candles) < 2:
            return []

        if detected_at is None:
            detected_at = self._clock()

        # Only actions that actually adjust prices explain a gap (audit P0-3). A dividend, a
        # PROVIDER_CONFLICT or a split with an unreadable ratio leaves the raw gap unadjusted,
        # so it must not silence the safety net.
        adjusting = [r for r in resolutions if r.ex_date is not None and explains_price_gap(r)]

        events: list[DataQualityEvent] = []
        gap_threshold = self._config.gap_pct / 100.0

        for i in range(1, len(candles)):
            prev = candles[i - 1]
            curr = candles[i]

            if prev.close <= 0 or curr.open <= 0:
                continue  # Avoid division by zero or invalid prices

            gap_ratio = (curr.open / prev.close) - 1.0
            abs_gap = abs(gap_ratio)

            if abs_gap >= gap_threshold:
                residual = self._residual_gap(prev, curr, adjusting)
                if residual is not None and abs(residual) < gap_threshold:
                    continue
                # We have a gap with no corporate action to explain it.
                event = self._build_event(prev, curr, gap_ratio, detected_at, residual)
                events.append(event)

        return events

    @staticmethod
    def _residual_gap(
        prev: Candle, curr: Candle, adjusting: list[CorporateActionResolution]
    ) -> float | None:
        """The gap left after the actions whose ex-date falls in ``(prev, curr]`` (audit 2.7c).

        An illiquid stock may not trade on the ex-date, so the action's jump shows on the first
        bar after it. Any ex-date after the previous bar and up to this bar can explain the gap,
        but only by its size: the prior close times the combined price factor must land within
        ``gap_pct`` of the open. A long absence with an unrelated action in it therefore stays
        flagged.

        * SPLIT/BONUS: the ratio's factor (``AdjustmentEngine`` convention).
        * RIGHTS with a usable ratio and issue price: the TERP factor from the prior close.
        * A rights issue or demerger whose factor cannot be derived from these two bars (a
          demerger's factor *is* the ex-date open over the prior close; a rights issue with no
          issue price) explains the gap as before; a separate ``factor_unknown`` event blocks
          the symbol when the adjustment engine cannot derive it either.

        Returns ``None`` when no adjusting action falls in the window, ``0.0`` when one is
        deferred as above, else the residual gap ratio.
        """
        lo, hi = prev.timestamp.date(), curr.timestamp.date()
        window = [r for r in adjusting if r.ex_date is not None and lo < r.ex_date <= hi]
        if not window:
            return None
        factor = 1.0
        bars = ExDatePrices(prior_close=prev.close, ex_open=curr.open, raw=True)
        for r in window:
            if r.action_type in PRICE_SCALING_ACTIONS:
                factor *= _ENGINE.single_factor(r)[0]
                continue
            if r.action_type is CorporateActionType.RIGHTS:
                derived = derived_factor(r, bars)
                if derived is not None:
                    factor *= derived[0]
                    continue
            return 0.0  # demerger, or rights without a derivable factor: see docstring
        return curr.open / (prev.close * factor) - 1.0

    def _build_event(
        self,
        prev: Candle,
        curr: Candle,
        gap_ratio: float,
        detected_at: datetime,
        residual: float | None = None,
    ) -> DataQualityEvent:
        is_split_like, expected_ratio = self._is_split_like(prev.close, curr.open)

        context: dict[str, str | float | int | None] = {
            "prev_date": prev.timestamp.date().isoformat(),
            "curr_date": curr.timestamp.date().isoformat(),
            "prev_close": prev.close,
            "curr_open": curr.open,
            "gap_pct": round(gap_ratio * 100, 2),
            "is_split_like": is_split_like,
        }
        if residual is not None:
            # An action fell in the window but did not account for the gap's size.
            context["residual_gap_pct_after_actions"] = round(residual * 100, 2)

        if is_split_like and expected_ratio:
            context["suspected_ratio"] = expected_ratio
            desc = (
                f"Unexplained gap of {gap_ratio * 100:.1f}%. "
                f"Looks like a {expected_ratio} split/bonus."
            )
        else:
            desc = f"Unexplained gap of {gap_ratio * 100:.1f}%."

        # DATA_SPECIFICATION 18A: a gap alone may be genuine (earnings, circuit moves), so it
        # only raises a warning. A gap that also looks like a small-integer split/bonus is a
        # suspected missed corporate action and blocks signals until an action is added or a
        # human marks the gap genuine.
        return DataQualityEvent(
            event_id=make_event_id(
                DataQualityFlag.UNEXPLAINED_GAP,
                curr.instrument_id,
                curr.timestamp.date().isoformat(),
            ),
            instrument_id=curr.instrument_id,
            flag=DataQualityFlag.UNEXPLAINED_GAP,
            severity=EventSeverity.HIGH,
            detected_at=detected_at,
            description=desc,
            context=context,
            trade_date=curr.timestamp.date(),
            blocks_signal=is_split_like,
            dataset="daily_prices",
        )

    def _is_split_like(self, prev_close: float, curr_open: float) -> tuple[bool, str | None]:
        """Check if the price drop resembles a small-integer fraction."""
        if curr_open >= prev_close:
            return False, None  # Splits/bonuses drop the price

        ratio = curr_open / prev_close
        max_int = self._config.split_like_max_integer
        tolerance = self._config.split_like_tolerance_pct / 100.0

        # Try to find a fraction p/q where p, q <= max_int
        # that is within the tolerance of the actual ratio.
        # Since it's a price drop, q > p.
        for q in range(2, max_int + 1):
            for p in range(1, q):
                target = p / q
                if abs(ratio - target) <= tolerance:
                    # For a fraction p/q, it corresponds to a split of q for p.
                    return True, f"{q}:{p}"

        return False, None
