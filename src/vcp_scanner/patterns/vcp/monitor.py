"""Breakout tracking across days (VCP_SPECIFICATION 45, 47, 55, 56, 61B).

The detector is stateless: it sees one date. Two things need the previous days:

1. **A breakout is an immutable event** (section 47). Once a base has broken out, later days of
   that base (same instrument, same base start) are judged against the event's pivot: BREAKOUT
   while the close stays at or above it, FAILED once it closes back below. The event is never
   rewritten, and earlier observations are never changed (section 56).
2. **Right-side pivots move** with every new bar (the breakout bar joins the window), so their
   breakouts are detected against the pivot *stored for the previous scan date*: today's close
   above yesterday's primary pivot, same base, on volume >= ``breakout.min_volume_ratio`` x the
   mean of the 50 bars before today.

Order of evaluation for a date with a pattern (data states and INVALIDATED keep their status
and create no event):

* an event for this base known on or before the date -> status from that event;
* else the detector's structural breakout -> new event ``STRUCTURAL``;
* else a breakout of the previous scan's primary pivot -> new event ``PRIOR_DAY_PIVOT``.

Dates must therefore be processed in order; a rerun of a date ignores events detected after it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import date

from vcp_scanner.config.models import VCPThresholdsConfig
from vcp_scanner.domain.enums import VCPStatus
from vcp_scanner.patterns.vcp.detector import VCPDetection
from vcp_scanner.patterns.vcp.measurements import PriceSeries, _mean

STRUCTURAL = "STRUCTURAL"
PRIOR_DAY_PIVOT = "PRIOR_DAY_PIVOT"


@dataclass(frozen=True, slots=True)
class BreakoutEvent:
    breakout_event_id: str
    instrument_id: str
    base_start: date
    breakout_date: date
    pivot_price: float
    pivot_date: date
    pivot_source: str
    volume_ratio: float
    detected_as_of: date
    method: str


@dataclass(frozen=True, slots=True)
class PriorPattern:
    """The instrument's primary pattern from its previous scan date (same config)."""

    as_of: date
    base_start: date
    pivot_price: float | None
    pivot_date: date | None
    pivot_source: str | None
    classification: str
    status: str


def event_id(instrument_id: str, base_start: date, config_hash: str) -> str:
    raw = f"{instrument_id}|{base_start.isoformat()}|{config_hash}"
    return "bo-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _last_volume_ratio(series: PriceSeries, n: int, long_period: int) -> float | None:
    """Volume of bar ``n - 1`` over the mean of the ``long_period`` bars before it."""
    i = n - 1
    if i - long_period < 0:
        return None
    v = series.volume[i]
    base = _mean(series.volume[i - long_period : i])
    if v is None or base is None or base <= 0:
        return None
    return v / base


def track_breakouts(
    detection: VCPDetection,
    series: PriceSeries,
    *,
    prior: PriorPattern | None,
    event: BreakoutEvent | None,
    config_hash: str,
    vcp: VCPThresholdsConfig,
) -> tuple[VCPDetection, BreakoutEvent | None]:
    """Apply section 47 events to one day's detection; returns it and any new event."""
    pattern = detection.pattern
    if pattern is None or pattern.status.is_data_state or pattern.status is VCPStatus.INVALIDATED:
        return detection, None
    as_of = detection.as_of
    n = 0
    while n < len(series.dates) and series.dates[n] <= as_of:
        n += 1
    close = series.close[n - 1]

    if (
        event is not None
        and event.base_start == pattern.base_start
        and event.detected_as_of <= as_of
        and event.breakout_date <= as_of
    ):
        status = VCPStatus.BREAKOUT if close >= event.pivot_price else VCPStatus.FAILED
        new_pattern = replace(pattern, status=status, base_end=event.breakout_date)
        return replace(detection, pattern=new_pattern, status=status), None

    eid = event_id(detection.instrument_id, pattern.base_start, config_hash)
    structural = detection.pivots.structural if detection.pivots else None
    if detection.breakout is not None and structural is not None:
        new = BreakoutEvent(
            eid, detection.instrument_id, pattern.base_start, detection.breakout.breakout_date,
            structural.pivot_price, structural.pivot_date, structural.source.value,
            detection.breakout.volume_ratio, as_of, STRUCTURAL,
        )  # fmt: skip
        return detection, new

    if (
        prior is not None
        and prior.as_of < as_of
        and prior.base_start == pattern.base_start
        and prior.pivot_price is not None
        and prior.pivot_date is not None
        and close > prior.pivot_price
    ):
        ratio = _last_volume_ratio(series, n, vcp.volume.long_period)
        if ratio is not None and ratio >= vcp.breakout.min_volume_ratio:
            new = BreakoutEvent(
                eid, detection.instrument_id, pattern.base_start, as_of, prior.pivot_price,
                prior.pivot_date, prior.pivot_source or "", ratio, as_of, PRIOR_DAY_PIVOT,
            )  # fmt: skip
            new_pattern = replace(pattern, status=VCPStatus.BREAKOUT, base_end=as_of)
            return replace(detection, pattern=new_pattern, status=VCPStatus.BREAKOUT), new
    return detection, None
