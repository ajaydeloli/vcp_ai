"""Swing detection with confirmation dates (VCP_SPECIFICATION 9, 9A, 26; method ``pivot_n_bar``).

A bar is a **swing high** when its high is greater than or equal to the high of each of the
``left_bars`` bars before it and the ``right_bars`` bars after it; a **swing low** likewise with
lows (section 9). Bars are the instrument's own trading bars (adjusted prices), so "N bars"
means N of its sessions; missing sessions are never filled.

**Confirmation.** A swing at bar ``i`` becomes known on bar ``i + right_bars``: that bar's date is
its ``confirmation_date``. A bar with fewer than ``left_bars`` bars before it is never a swing
(its left side is unknown).

**As-of safety (section 26).** ``detect_swings`` reads only bars dated on or before ``as_of``.
Because confirmation needs nothing after bar ``i + right_bars``, the confirmed swings as of D
are exactly the full-history swings whose ``confirmation_date <= D``: a later run never adds,
moves or removes a swing that was confirmed earlier (tested).

**Pending swings.** In the newest ``right_bars`` bars a bar can satisfy the rule against every
bar seen so far; it is reported as *pending* (``confirmation_date = None``). A pending swing may
still be cancelled by a later bar. It is never used as a confirmed swing.

**Ties.** Equal highs within a window satisfy ``>=`` on both sides, so a plateau of equal highs
yields one swing high per bar of the plateau (the literal section 9 rule). Merging such
neighbours is part of noise filtering in base and contraction segmentation (section 23,
Phase 6 step 3), not of swing detection.

A bar can be both a swing high and a swing low (an outside bar); both are reported, HIGH first.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from vcp_scanner.config.models import VCPSwingConfig
from vcp_scanner.domain.enums import SwingKind
from vcp_scanner.domain.errors import DataValidationError
from vcp_scanner.domain.vcp import Swing


@dataclass(frozen=True, slots=True)
class SwingSet:
    """Swings as of a date: confirmed ones and the still-pending newest ones.

    Both tuples are ordered by swing date, HIGH before LOW on the same date.
    """

    as_of: date
    confirmed: tuple[Swing, ...]
    pending: tuple[Swing, ...]

    def highs(self) -> tuple[Swing, ...]:
        return tuple(s for s in self.confirmed if s.kind is SwingKind.HIGH)

    def lows(self) -> tuple[Swing, ...]:
        return tuple(s for s in self.confirmed if s.kind is SwingKind.LOW)


def _validate(dates: Sequence[date], highs: Sequence[float], lows: Sequence[float]) -> None:
    for i, (d, h, lo) in enumerate(zip(dates, highs, lows, strict=True)):
        if i and d <= dates[i - 1]:
            raise DataValidationError(f"bar dates must be strictly increasing: {d} at {i}")
        if not (math.isfinite(h) and math.isfinite(lo) and 0 < lo <= h):
            raise DataValidationError(f"bad bar on {d}: high {h}, low {lo}")


def _is_extreme(values: Sequence[float], i: int, lo: int, hi: int, *, is_high: bool) -> bool:
    """True when ``values[i]`` is >= (high) or <= (low) every value in ``values[lo:hi]``."""
    v = values[i]
    if is_high:
        return all(v >= values[j] for j in range(lo, hi) if j != i)
    return all(v <= values[j] for j in range(lo, hi) if j != i)


def detect_swings(
    dates: Sequence[date],
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    as_of: date,
    left_bars: int,
    right_bars: int,
) -> SwingSet:
    """Swings among the bars dated on or before ``as_of`` (see the module docstring).

    ``dates`` must be strictly increasing; ``highs``/``lows`` are adjusted prices with
    ``0 < low <= high``. Bars after ``as_of`` are ignored, never read.
    """
    if left_bars < 1 or right_bars < 1:
        raise ValueError(f"left_bars and right_bars must be >= 1, got {left_bars}, {right_bars}")
    if not len(dates) == len(highs) == len(lows):
        raise DataValidationError(
            f"dates, highs and lows differ in length ({len(dates)}, {len(highs)}, {len(lows)})"
        )
    n = 0
    while n < len(dates) and dates[n] <= as_of:
        n += 1
    _validate(dates[:n], highs[:n], lows[:n])  # bars after as_of are not inspected
    hs, ls = highs[:n], lows[:n]

    confirmed: list[Swing] = []
    pending: list[Swing] = []
    for i in range(left_bars, n):
        right_end = min(i + right_bars, n - 1)  # last bar index known as of as_of
        is_confirmed = i + right_bars <= n - 1
        for kind, values in ((SwingKind.HIGH, hs), (SwingKind.LOW, ls)):
            if not _is_extreme(
                values, i, i - left_bars, right_end + 1, is_high=kind is SwingKind.HIGH
            ):
                continue
            if is_confirmed:
                confirmed.append(Swing(kind, dates[i], values[i], dates[i + right_bars]))
            else:
                pending.append(Swing(kind, dates[i], values[i], None))
    return SwingSet(as_of=as_of, confirmed=tuple(confirmed), pending=tuple(pending))


class SwingDetector:
    """``pivot_n_bar`` swing detector bound to ``vcp.swing`` settings (section 50)."""

    def __init__(self, config: VCPSwingConfig) -> None:
        self._left = config.left_bars
        self._right = config.right_bars

    def detect(
        self,
        dates: Sequence[date],
        highs: Sequence[float],
        lows: Sequence[float],
        *,
        as_of: date,
    ) -> SwingSet:
        return detect_swings(
            dates, highs, lows, as_of=as_of, left_bars=self._left, right_bars=self._right
        )
