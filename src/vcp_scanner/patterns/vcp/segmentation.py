"""Base and contraction segmentation (VCP_SPECIFICATION 7, 8, 8.1, 9A, 10, 23).

Rules (owner decisions 2026-10-01, details as implemented 2026-10-02):

1. **Base start**: the highest confirmed swing high among the last ``base.max_duration_days`` bars
   (as-of bar included); on a tie, the earliest. It must follow a **prior advance**: base high /
   lowest low of the ``prior_advance.lookback_days`` bars ending at that high - 1 >=
   ``prior_advance.min_return_pct``. If that high fails, there is no base (no fallback to a lower
   high). With fewer bars than the window, a pass still counts (the true low can only be lower);
   a fail is ``INSUFFICIENT_HISTORY``.
2. **Peaks**: the base start, then every confirmed swing high after it.
3. **Closed contraction** k: from peak k to the lowest low strictly after it and before peak k+1
   (earliest bar on a tie). It is confirmed on peak k+1's confirmation date (its low can no
   longer change after that).
4. **Noise** (section 23): a swing shallower than ``swing.min_depth_pct``, or shorter than
   ``swing.min_duration_days`` bars while also shallower than ``swing.short_swing_max_depth_pct``,
   is merged (sharp moves of several percent in one or two bars are real swings). Both swings
   of a closed contraction are tested,
   each as (high - low) / high x 100 and its bar count:
   - the decline from peak k to its low: if noise, peak k is removed, so the previous
     contraction runs on to the next peak (T1's peak is the base start and stays; its next peak
     is removed instead);
   - the rally from that low to peak k+1 (a bounce inside a decline): if noise, peak k+1 is
     removed, so contraction k runs on to the following peak.
   Repeated until no closed contraction has a noise swing.
   **Equal-high merge** (research setting ``swing.merge_equal_highs``, off by default, owner's
   mark check 2026-10-03): then, while peak k+1 is within ``swing.equal_high_tolerance_pct`` of
   peak k and its pullback is deeper than peak k's, peak k+1 is removed (a shallow dip and a
   deeper drop from the same level are one contraction).
5. **Final contraction** (section 9A): from the last peak to the lowest low since. It is confirmed
   once ``swing.right_bars`` bars have followed that low without a lower low (on the date of the
   ``right_bars``-th bar), otherwise provisional. Only the right side is tested: the low is
   already the lowest since the peak, and bars before the peak belong to the previous swing
   (a formal swing low would also need its left bars, which can reach back past the peak).
   If it is noise by the rule above, there is no final contraction yet. With
   ``confirmation.allow_provisional_final_contraction = false`` a provisional one is dropped.
6. ``duration_days`` = bars from peak to low; base low = lowest low from the base start to the
   as-of bar; base duration = bars from the base start to the as-of bar, both included.

Breakouts and invalidation (price above the base high, a broken low) are statuses, decided
later (Phase 6 step 6). Only bars on or before ``as_of`` are read.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum, unique

from vcp_scanner.config.models import VCPSwingConfig, VCPThresholdsConfig
from vcp_scanner.domain.vcp import Swing, depth_pct
from vcp_scanner.patterns.vcp.swings import SwingSet, detect_swings


@unique
class NoBaseReason(StrEnum):
    """Why no base was found as of a date."""

    NO_CONFIRMED_SWING_HIGH = "NO_CONFIRMED_SWING_HIGH"
    NO_PRIOR_ADVANCE = "NO_PRIOR_ADVANCE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


@dataclass(frozen=True, slots=True)
class ContractionSegment:
    """One contraction's structure (measurements come in step 4). Indices are bar positions in
    the series passed to ``segment_base``."""

    sequence_number: int
    peak_index: int
    peak_date: date
    peak_price: float
    trough_index: int
    trough_date: date
    trough_price: float
    confirmation_date: date | None  # None = provisional (section 9A)

    @property
    def depth_pct(self) -> float:
        return depth_pct(self.peak_price, self.trough_price)

    @property
    def duration_days(self) -> int:
        return self.trough_index - self.peak_index

    @property
    def is_confirmed(self) -> bool:
        return self.confirmation_date is not None


@dataclass(frozen=True, slots=True)
class BaseSegmentation:
    """A base and its contractions as of a date."""

    as_of: date
    base_start_index: int
    base_start: date
    base_high: float
    base_low: float
    base_low_date: date
    base_duration_days: int
    prior_advance_return_pct: float | None  # None when the prior-advance test is disabled
    prior_advance_low_date: date | None
    contractions: tuple[ContractionSegment, ...]
    #: Peaks removed as noise (section 23), for explainability.
    merged_peak_dates: tuple[date, ...]

    @property
    def base_depth_pct(self) -> float:
        return depth_pct(self.base_high, self.base_low)

    @property
    def is_confirmed(self) -> bool:
        return all(c.is_confirmed for c in self.contractions)


@dataclass(frozen=True, slots=True)
class SegmentationResult:
    """Either a base or the reason there is none."""

    base: BaseSegmentation | None
    no_base_reason: NoBaseReason | None
    swings: SwingSet


def _lowest(lows: Sequence[float], start: int, end: int) -> int | None:
    """Index of the lowest low in ``lows[start:end]`` (earliest on a tie); None if empty."""
    best: int | None = None
    for j in range(start, end):
        if best is None or lows[j] < lows[best]:
            best = j
    return best


def _small(size_pct: float, bars: int, sw: VCPSwingConfig) -> bool:
    """Rule 4: shallower than ``min_depth_pct``, or shorter than ``min_duration_days`` bars
    while also shallower than ``short_swing_max_depth_pct``."""
    return size_pct < sw.min_depth_pct or (
        bars < sw.min_duration_days and size_pct < sw.short_swing_max_depth_pct
    )


def _is_noise(peak: int, trough: int | None, highs: Sequence[float], lows: Sequence[float],
              sw: VCPSwingConfig) -> bool:  # fmt: skip
    """The decline from a peak to its low."""
    if trough is None or lows[trough] >= highs[peak]:
        return True
    return _small(depth_pct(highs[peak], lows[trough]), trough - peak, sw)


def _is_noise_rally(trough: int | None, next_peak: int, highs: Sequence[float],
                    lows: Sequence[float], sw: VCPSwingConfig) -> bool:  # fmt: skip
    """The rally from a contraction's low to the next peak, measured like a decline:
    (next peak high - low) / next peak high x 100, and bars from low to peak."""
    if trough is None:
        return True
    return _small(depth_pct(highs[next_peak], lows[trough]), next_peak - trough, sw)


def segment_base(
    dates: Sequence[date],
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    as_of: date,
    config: VCPThresholdsConfig,
) -> SegmentationResult:
    """Find the base and its contractions as of ``as_of`` (see the module docstring).

    Pass at least ``config.lookback_bars()`` bars ending at ``as_of`` (more are fine; bars after
    ``as_of`` are ignored).
    """
    sw = config.swing
    swings = detect_swings(
        dates, highs, lows, as_of=as_of, left_bars=sw.left_bars, right_bars=sw.right_bars
    )
    n = 0
    while n < len(dates) and dates[n] <= as_of:
        n += 1
    index_of = {d: i for i, d in enumerate(dates[:n])}
    window_start = max(0, n - config.base.max_duration_days)

    high_swings = {index_of[s.swing_date]: s for s in swings.highs()}
    in_window = [i for i in sorted(high_swings) if i >= window_start]
    if not in_window:
        return SegmentationResult(None, NoBaseReason.NO_CONFIRMED_SWING_HIGH, swings)
    b = min(in_window, key=lambda i: (-highs[i], i))  # highest; earliest on a tie

    advance: float | None = None
    advance_low_date: date | None = None
    pa = config.prior_advance
    if pa.enabled:
        first = b - pa.lookback_days + 1
        low_i = _lowest(lows, max(first, 0), b + 1)
        assert low_i is not None  # the window holds at least bar b
        advance = (highs[b] / lows[low_i] - 1.0) * 100.0
        advance_low_date = dates[low_i]
        if advance < pa.min_return_pct:
            reason = (
                NoBaseReason.INSUFFICIENT_HISTORY if first < 0 else NoBaseReason.NO_PRIOR_ADVANCE
            )
            return SegmentationResult(None, reason, swings)

    peaks, merged = _merge_noise([b, *(i for i in sorted(high_swings) if i > b)], highs, lows, sw)
    if sw.merge_equal_highs:
        peaks, more = _merge_equal_highs(peaks, highs, lows, n, sw.equal_high_tolerance_pct)
        merged = sorted(merged + more)
    contractions = _contractions(peaks, dates, highs, lows, n, high_swings, config)
    base_low_i = _lowest(lows, b, n)
    assert base_low_i is not None
    base = BaseSegmentation(
        as_of=as_of,
        base_start_index=b,
        base_start=dates[b],
        base_high=highs[b],
        base_low=lows[base_low_i],
        base_low_date=dates[base_low_i],
        base_duration_days=n - b,
        prior_advance_return_pct=advance,
        prior_advance_low_date=advance_low_date,
        contractions=contractions,
        merged_peak_dates=tuple(dates[i] for i in merged),
    )
    return SegmentationResult(base, None, swings)


def _merge_noise(
    peaks: list[int],
    highs: Sequence[float],
    lows: Sequence[float],
    sw: VCPSwingConfig,
) -> tuple[list[int], list[int]]:
    """Remove peaks of noise swings (rule 4). Returns (kept, removed) indices.

    Both swings of a closed contraction are tested: the decline (peak k to its low) and the
    rally out of that low (to peak k+1). A noise decline removes peak k (or peak k+1 for T1,
    whose peak is the base start); a noise rally removes peak k+1, so the contraction carries
    on to the next peak.
    """
    kept = list(peaks)
    removed: list[int] = []
    while True:
        for k in range(len(kept) - 1):  # closed contractions only
            trough = _lowest(lows, kept[k] + 1, kept[k + 1])
            if _is_noise(kept[k], trough, highs, lows, sw):
                drop = k + 1 if k == 0 else k
            elif _is_noise_rally(trough, kept[k + 1], highs, lows, sw):
                drop = k + 1
            else:
                continue
            removed.append(kept.pop(drop))
            break
        else:
            return kept, sorted(removed)


def _merge_equal_highs(
    peaks: list[int],
    highs: Sequence[float],
    lows: Sequence[float],
    n: int,
    tolerance_pct: float,
) -> tuple[list[int], list[int]]:
    """Equal-high merge (``swing.merge_equal_highs``): remove peak k+1 when its high is within
    ``tolerance_pct`` of peak k's and its pullback (to the lowest low before the next peak, or
    up to the as-of bar) is deeper than peak k's. Returns (kept, removed) indices."""
    kept = list(peaks)
    removed: list[int] = []
    changed = True
    while changed:
        changed = False
        for k in range(len(kept) - 1):
            a, b = kept[k], kept[k + 1]
            if abs(highs[b] / highs[a] - 1.0) * 100.0 > tolerance_pct:
                continue
            end_b = kept[k + 2] if k + 2 < len(kept) else n
            ta, tb = _lowest(lows, a + 1, b), _lowest(lows, b + 1, end_b)
            if ta is None or tb is None:
                continue
            if depth_pct(highs[b], lows[tb]) > depth_pct(highs[a], lows[ta]):
                removed.append(kept.pop(k + 1))
                changed = True
                break
    return kept, removed


def _contractions(
    peaks: list[int],
    dates: Sequence[date],
    highs: Sequence[float],
    lows: Sequence[float],
    n: int,
    high_swings: dict[int, Swing],
    config: VCPThresholdsConfig,
) -> tuple[ContractionSegment, ...]:
    """Build closed contractions and the final one (rules 3 and 5)."""
    out: list[ContractionSegment] = []
    for k, peak in enumerate(peaks):
        last = k == len(peaks) - 1
        end = n if last else peaks[k + 1]
        trough = _lowest(lows, peak + 1, end)
        if last:
            sw = config.swing
            if _is_noise(peak, trough, highs, lows, sw):
                break  # no final contraction yet
            assert trough is not None
            # The low is the lowest since the peak, so only its right side can still change:
            # it is final once ``right_bars`` bars have passed without a lower low.
            confirm_at = trough + sw.right_bars
            confirmed = dates[confirm_at] if confirm_at <= n - 1 else None
            if confirmed is None and not config.confirmation.allow_provisional_final_contraction:
                break
        else:
            assert trough is not None  # closed contractions are not noise after merging
            confirmed = high_swings[peaks[k + 1]].confirmation_date
        out.append(
            ContractionSegment(
                sequence_number=len(out) + 1,
                peak_index=peak,
                peak_date=dates[peak],
                peak_price=highs[peak],
                trough_index=trough,
                trough_date=dates[trough],
                trough_price=lows[trough],
                confirmation_date=confirmed,
            )
        )
    return tuple(out)
