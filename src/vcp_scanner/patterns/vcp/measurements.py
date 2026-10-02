"""VCP measurements (VCP_SPECIFICATION 12-18A, 22): tightening, volatility, volume, range.

All values are raw measurements; the boolean criteria follow section 18A and are ``None`` when an
input is missing (never a pass). Scoring and classification read these; nothing here decides
whether a VCP exists.

Definitions (spec gaps filled 2026-10-02, recorded in VCP_SPECIFICATION 18B):

* **Contraction window** = its decline bars: the bar after the peak through the low, so its
  length equals ``duration_days``. The peak bar belongs to the advance before it.
* **ATR%** = the feature engine's ``atr_pct_14`` (simple 14-bar mean of true range / close x 100,
  owner decision 4). A contraction's ``atr_pct`` is its mean over the window.
* **TR%** = true range / close x 100 of each bar, unlagged. A contraction's ``tr_pct`` is its
  mean over the window. ATR14 lags 14 bars, so for a short final contraction (39 % of finals are
  6 bars or fewer) it mostly measures the bars before it. ``volatility.measure`` picks which
  ratio decides ``volatility_contraction_pass``: ``true_range`` (default) or ``atr`` (the
  original 18A rule). Both ratios are always reported.
* **Volume ratio** of a contraction = mean volume over its window / mean volume of the
  ``volume.long_period`` (50) bars before its peak (prior bars, as ``volume_ratio_50`` in
  features-1.2.0). Any missing volume in either window gives ``None``.
* ``range_pct`` of a window = (max high - min low) / max high x 100.
* ``right_side_*`` use the last ``pivot.right_side_window_days`` (10) bars up to the as-of bar;
  the right-side volume baseline is the 50 bars before that window.
* ``last_N_range_pct`` / ``last_N_atr_pct`` (section 16): range over, and mean ATR% of, the last N
  bars. ``volume_avg_N`` (section 17): mean volume of the last N bars including the as-of bar.
* Selling pressure (section 18, supporting only): over the base (base start to as-of), volume on
  up-close days / volume on down-close days, and counts of up/down days whose volume is at least
  ``HIGH_VOLUME_MULTIPLE`` x the mean of the 50 bars before them.
* ``tightening_consistency`` (section 13) = share of consecutive pairs where the depth shrinks.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from vcp_scanner.config.models import VCPThresholdsConfig
from vcp_scanner.patterns.vcp.segmentation import BaseSegmentation

#: A day is "high volume" at this multiple of its prior 50-bar mean (section 18, supporting).
HIGH_VOLUME_MULTIPLE = 1.5
#: Windows for section 16 range compression and section 17 volume averages.
RANGE_WINDOWS = (20, 10, 5)
ATR_WINDOWS = (20, 10)
VOLUME_WINDOWS = (5, 10, 20, 50)


@dataclass(frozen=True, slots=True)
class PriceSeries:
    """Bars for one instrument up to (at least) the as-of date, oldest first.

    ``volume`` is ``None`` where missing or suspect (never 0-filled); ``atr_pct_14`` is the
    feature engine's value (``None`` until its window is full).
    """

    dates: Sequence[date]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    volume: Sequence[float | None]
    atr_pct_14: Sequence[float | None]

    def __post_init__(self) -> None:
        n = len(self.dates)
        if not all(len(x) == n for x in (self.high, self.low, self.close, self.volume,
                                          self.atr_pct_14)):  # fmt: skip
            raise ValueError("PriceSeries columns differ in length")


@dataclass(frozen=True, slots=True)
class ContractionMeasures:
    """Per-contraction measurements (DATABASE_SCHEMA 32), aligned with the base's contractions."""

    sequence_number: int
    atr_pct: float | None
    range_pct: float
    volume_ratio: float | None
    tr_pct: float | None  # research: mean unlagged true range %


@dataclass(frozen=True, slots=True)
class VCPMeasurements:
    """Base-level measurements and section 18A criteria (DATABASE_SCHEMA 31)."""

    contractions: tuple[ContractionMeasures, ...]

    # Tightening (sections 12, 13).
    tightening_ratios: tuple[float, ...]
    max_tightening_ratio: float | None
    tightening_consistency: float | None
    progressive_tightening: bool | None

    # Volatility (sections 15, 16, 18A).
    atr_contraction_ratio: float | None
    volatility_contraction_pass: bool | None
    tr_contraction_ratio: float | None  # research
    last_range_pct: dict[int, float | None]
    last_atr_pct: dict[int, float | None]

    # Volume (sections 17, 18A).
    final_volume_ratio: float | None
    volume_dryup_pass: bool | None
    volume_avg: dict[int, float | None]
    volume_ratio_5_20: float | None

    # Right side (sections 18A, 22).
    right_side_range_pct: float | None
    right_side_atr_pct: float | None
    right_side_volume_ratio: float | None
    tight_pivot_pass: bool | None

    # Selling pressure over the base (section 18, supporting only).
    up_down_volume_ratio: float | None
    high_volume_up_days: int | None
    high_volume_down_days: int | None


def _mean(values: Sequence[float | None]) -> float | None:
    """Mean, or ``None`` when empty or any value is missing (missing is never ignored)."""
    if not values or any(v is None for v in values):
        return None
    vals = [v for v in values if v is not None]
    return math.fsum(vals) / len(vals)


def _range_pct(s: PriceSeries, start: int, end: int) -> float | None:
    """(max high - min low) / max high x 100 over bars [start, end)."""
    if start < 0 or end <= start:
        return None
    hi = max(s.high[start:end])
    return (hi - min(s.low[start:end])) / hi * 100.0


def _tr_pct(s: PriceSeries, i: int) -> float | None:
    if i < 1:
        return None
    prev = s.close[i - 1]
    tr = max(s.high[i] - s.low[i], abs(s.high[i] - prev), abs(s.low[i] - prev))
    return tr / s.close[i] * 100.0


def _volume_ratio(s: PriceSeries, start: int, end: int, baseline_bars: int) -> float | None:
    """Mean volume over [start, end) / mean volume of the ``baseline_bars`` bars before start."""
    if start - baseline_bars < 0 or end <= start:
        return None
    window = _mean(s.volume[start:end])
    base = _mean(s.volume[start - baseline_bars : start])
    if window is None or base is None or base <= 0:
        return None
    return window / base


def _ratio(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b <= 0 else a / b


def measure(
    series: PriceSeries, base: BaseSegmentation, config: VCPThresholdsConfig
) -> VCPMeasurements:
    """Measure a segmented base. ``series`` must be the bars ``segment_base`` was given (the
    base's bar indices point into it); bars after the as-of date are ignored."""
    if series.dates[base.base_start_index] != base.base_start:
        raise ValueError("series does not match the segmentation (base start index differs)")
    n = 0
    while n < len(series.dates) and series.dates[n] <= base.as_of:
        n += 1
    long_p = config.volume.long_period

    per: list[ContractionMeasures] = []
    for c in base.contractions:
        lo, hi = c.peak_index + 1, c.trough_index + 1  # decline bars
        span = _range_pct(series, c.peak_index, hi)  # peak bar to low: never empty
        assert span is not None
        per.append(
            ContractionMeasures(
                sequence_number=c.sequence_number,
                atr_pct=_mean(series.atr_pct_14[lo:hi]),
                range_pct=span,
                volume_ratio=_volume_ratio(series, lo, hi, long_p),
                tr_pct=_mean([_tr_pct(series, i) for i in range(lo, hi)]),
            )
        )

    depths = [c.depth_pct for c in base.contractions]
    ratios = tuple(b / a for a, b in zip(depths, depths[1:], strict=False))
    two = len(depths) >= 2
    tol = 1 + config.progressive_tolerance_pct / 100
    progressive = (all(r <= tol for r in ratios) and depths[-1] < depths[0]) if two else None
    consistency = sum(1 for r in ratios if r < 1) / len(ratios) if ratios else None

    first, final = (per[0], per[-1]) if two else (None, None)
    atr_ratio = _ratio(final.atr_pct, first.atr_pct) if final and first else None
    tr_ratio = _ratio(final.tr_pct, first.tr_pct) if final and first else None
    decisive = tr_ratio if config.volatility.measure == "true_range" else atr_ratio
    vol_pass = None if decisive is None else decisive <= config.volatility.contraction_ratio_max

    final_vol = per[-1].volume_ratio if per else None
    dryup: bool | None = None
    if two and final_vol is not None and per[0].volume_ratio is not None:
        dryup = final_vol <= config.volume.dryup_ratio and final_vol < per[0].volume_ratio

    w = config.pivot.right_side_window_days
    rs_start = n - w
    rs_range = _range_pct(series, rs_start, n) if rs_start >= 0 else None
    rs_atr = _mean(series.atr_pct_14[rs_start:n]) if rs_start >= 0 else None
    rs_vol = _volume_ratio(series, rs_start, n, long_p) if rs_start >= 0 else None
    tight = None if rs_range is None else rs_range <= config.pivot.max_right_side_range_pct

    vol_avg = {k: (_mean(series.volume[n - k : n]) if n - k >= 0 else None) for k in VOLUME_WINDOWS}
    up_down, hv_up, hv_down = _selling_pressure(series, base.base_start_index, n, long_p)

    return VCPMeasurements(
        contractions=tuple(per),
        tightening_ratios=ratios,
        max_tightening_ratio=max(ratios) if ratios else None,
        tightening_consistency=consistency,
        progressive_tightening=progressive,
        atr_contraction_ratio=atr_ratio,
        volatility_contraction_pass=vol_pass,
        tr_contraction_ratio=tr_ratio,
        last_range_pct={k: _range_pct(series, n - k, n) for k in RANGE_WINDOWS},
        last_atr_pct={
            k: (_mean(series.atr_pct_14[n - k : n]) if n - k >= 0 else None) for k in ATR_WINDOWS
        },
        final_volume_ratio=final_vol,
        volume_dryup_pass=dryup,
        volume_avg=vol_avg,
        volume_ratio_5_20=_ratio(vol_avg[5], vol_avg[20]),
        right_side_range_pct=rs_range,
        right_side_atr_pct=rs_atr,
        right_side_volume_ratio=rs_vol,
        tight_pivot_pass=tight,
        up_down_volume_ratio=up_down,
        high_volume_up_days=hv_up,
        high_volume_down_days=hv_down,
    )


def _selling_pressure(
    s: PriceSeries, start: int, n: int, baseline: int
) -> tuple[float | None, int | None, int | None]:
    """Up/down volume ratio and high-volume up/down day counts over bars [start, n)."""
    first = max(start, 1)
    if any(s.volume[i] is None for i in range(first, n)):
        return None, None, None
    up = down = 0.0
    hv_up = hv_down = 0
    for i in range(first, n):
        v = s.volume[i]
        assert v is not None
        move = s.close[i] - s.close[i - 1]
        if move > 0:
            up += v
        elif move < 0:
            down += v
        prior = _mean(s.volume[i - baseline : i]) if i - baseline >= 0 else None
        if prior is not None and prior > 0 and v >= HIGH_VOLUME_MULTIPLE * prior:
            hv_up += move > 0
            hv_down += move < 0
    return (up / down if down > 0 else None), hv_up, hv_down
