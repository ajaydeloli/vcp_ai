"""Gap Safety Net (DATA_SPECIFICATION §18A, 22).

Catches massive price gaps that likely represent missing corporate actions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from vcp_scanner.config.models import UnexplainedGapConfig
from vcp_scanner.domain.corporate_actions import CorporateActionResolution
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity
from vcp_scanner.domain.market import Candle
from vcp_scanner.infrastructure.clock import Clock, utc_now

logger = logging.getLogger(__name__)


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

        # Index known ex-dates for fast lookup
        known_ex_dates = {r.ex_date for r in resolutions if r.ex_date is not None}

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
                ex_date = curr.timestamp.date()
                if ex_date not in known_ex_dates:
                    # We have a gap with no corporate action to explain it.
                    event = self._build_event(prev, curr, gap_ratio, detected_at)
                    events.append(event)

        return events

    def _build_event(
        self, prev: Candle, curr: Candle, gap_ratio: float, detected_at: datetime
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

        if is_split_like and expected_ratio:
            context["suspected_ratio"] = expected_ratio
            desc = (
                f"Unexplained gap of {gap_ratio * 100:.1f}%. "
                f"Looks like a {expected_ratio} split/bonus."
            )
        else:
            desc = f"Unexplained gap of {gap_ratio * 100:.1f}%."

        return DataQualityEvent(
            event_id=str(uuid.uuid4()),
            instrument_id=curr.instrument_id,
            flag=DataQualityFlag.UNEXPLAINED_GAP,
            severity=EventSeverity.HIGH,
            detected_at=detected_at,
            description=desc,
            context=context,
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
