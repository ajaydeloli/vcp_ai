"""Shared base-pattern measurements (STRATEGY_SPECIFICATION 12A; Multi-Strategy step 3).

Pure functions over the daily bars a detector already holds, oldest first, ending at the as-of
bar. Nothing is stored (owner decision 2026-10-05, option A): window lengths are per-strategy
thresholds, and base measures depend on where each detector puts its base.

Conventions (as elsewhere in the project):

* Nothing after the as-of bar is read; callers pass bars up to and including it.
* Missing is ``None``, never 0 (AGENTS.md rule 4): a window that does not fit, a missing or
  non-finite input, or a zero denominator gives ``None``.
* Percentages are in percent (x 100).

VCP keeps its own copies of these formulas (``patterns/vcp/segmentation.py``,
``patterns/vcp/measurements.py``) so its stored results cannot move; tests pin that the shared
prior advance equals VCP's.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


def _finite(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


# -- weekly bars -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeeklyBar:
    """One ISO week (Monday-Sunday) of daily bars, as ``weekly_prices`` stores it: the week ends
    on its last trading day, open = first day's open, close = last day's close, high / low the
    extremes. ``volume`` is the sum, or ``None`` if any day's volume is missing (stricter than
    the SQL sum, which skips NULLs). ``partial`` marks the as-of week when it may not be over."""

    week_end: date
    first_day: date
    sessions: int
    open: float | None
    high: float
    low: float
    close: float
    volume: float | None
    partial: bool


def weekly_bars(
    dates: Sequence[date],
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[float | None] | None = None,
    open_: Sequence[float] | None = None,
) -> list[WeeklyBar]:
    """Weekly bars from daily bars ending at the as-of bar (the last one).

    The as-of week is built only from days up to the as-of bar, so this never looks ahead (the
    weekly Stage uses the same rule, TREND_TEMPLATE_SPECIFICATION 4). It is ``partial`` when the
    as-of date is Monday-Thursday (no exchange calendar here; errs on the side of partial, as
    the weekly Stage does).
    """
    n = len(dates)
    if not all(len(x) == n for x in (high, low, close)):
        raise ValueError("daily columns differ in length")
    if volume is not None and len(volume) != n:
        raise ValueError("volume differs in length")
    if open_ is not None and len(open_) != n:
        raise ValueError("open differs in length")
    out: list[WeeklyBar] = []
    i = 0
    while i < n:
        week = dates[i].isocalendar()[:2]
        j = i
        while j + 1 < n and dates[j + 1].isocalendar()[:2] == week:
            j += 1
        vols = None if volume is None else volume[i : j + 1]
        vol = (None if vols is None or any(not _finite(v) for v in vols)
               else float(sum(v for v in vols if v is not None)))  # fmt: skip
        out.append(WeeklyBar(
            week_end=dates[j], first_day=dates[i], sessions=j - i + 1,
            open=None if open_ is None else open_[i],
            high=max(high[i : j + 1]), low=min(low[i : j + 1]), close=close[j],
            volume=vol, partial=False,
        ))  # fmt: skip
        i = j + 1
    if out and dates[-1].weekday() < 4:
        last = out[-1]
        out[-1] = WeeklyBar(last.week_end, last.first_day, last.sessions, last.open, last.high,
                            last.low, last.close, last.volume, True)  # fmt: skip
    return out


def weekly_close_range_pct(closes: Sequence[float], weeks: int) -> float | None:
    """(max - min) / min x 100 over the last ``weeks`` weekly closes (Three Weeks Tight, flat
    base). ``None`` with fewer closes or a non-positive / non-finite one."""
    if weeks < 1 or len(closes) < weeks:
        return None
    window = closes[-weeks:]
    if not all(_finite(c) and c > 0 for c in window):
        return None
    lo = min(window)
    return (max(window) - lo) / lo * 100.0


def max_weekly_close_change_pct(closes: Sequence[float], weeks: int) -> float | None:
    """Largest |close / previous close - 1| x 100 among the last ``weeks`` weekly closes, each
    against the week before it (``weeks`` changes, so ``weeks + 1`` closes are needed).
    The classic Three Weeks Tight test: every week closes within about 1-1.5 % of the last."""
    if weeks < 1 or len(closes) < weeks + 1:
        return None
    window = closes[-(weeks + 1) :]
    if not all(_finite(c) and c > 0 for c in window):
        return None
    return max(abs(b / a - 1.0) * 100.0 for a, b in zip(window, window[1:], strict=False))


# -- prior advance and base extremes -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriorAdvance:
    pct: float  # high[end] / lowest low of the window - 1, in %
    low_index: int
    window_complete: bool  # False when fewer than ``lookback`` bars exist before ``end``


def prior_advance(
    high: Sequence[float], low: Sequence[float], end: int, lookback: int
) -> PriorAdvance | None:
    """The run-up into a base whose high is bar ``end``: ``high[end]`` over the lowest low of
    the ``lookback`` bars ending at ``end`` (``end`` included), minus 1, in %. Earliest bar on a
    tie. VCP's formula (VCP_SPECIFICATION 8.1 item 1). With fewer bars than ``lookback`` the
    window starts at bar 0 and ``window_complete`` is False: a pass still counts (the true low
    could only be lower), a fail may be missing history."""
    if lookback < 1 or not 0 <= end < len(high) or len(low) != len(high):
        return None
    first = end - lookback + 1
    start = max(first, 0)
    window = low[start : end + 1]
    if not all(_finite(x) for x in window) or not _finite(high[end]):
        return None
    k = min(range(len(window)), key=lambda t: (window[t], t))
    if window[k] <= 0:
        return None
    return PriorAdvance((high[end] / window[k] - 1.0) * 100.0, start + k, first >= 0)


@dataclass(frozen=True, slots=True)
class BaseExtremes:
    high: float
    high_index: int
    low: float
    low_index: int
    depth_pct: float  # (high - low) / high x 100
    sessions: int  # bars from start to end, both included
    weeks: int  # ISO weeks touched from start to end, both included


def base_extremes(
    dates: Sequence[date], high: Sequence[float], low: Sequence[float], start: int,
    end: int | None = None,
) -> BaseExtremes | None:  # fmt: skip
    """Highest high and lowest low of bars ``start`` .. ``end`` (default: the last bar), both
    included; earliest bar on a tie. Depth and length as the setup record defines them
    (STRATEGY_SPECIFICATION 6.1). VCP's base low is the lowest low from the base start to the
    as-of bar, which is this with ``end`` = the last bar. VCP's base *high* is the base-start
    bar's high instead, so after a breakout above it the two differ (about 10 % of stored VCP
    patterns); a detector that wants the start bar's high reads ``high[start]``."""
    n = len(dates)
    last = n - 1 if end is None else end
    if not 0 <= start <= last < n or len(high) != n or len(low) != n:
        return None
    hs, ls = high[start : last + 1], low[start : last + 1]
    if not all(_finite(x) for x in (*hs, *ls)):
        return None
    hi = min(range(len(hs)), key=lambda t: (-hs[t], t))
    lo = min(range(len(ls)), key=lambda t: (ls[t], t))
    if hs[hi] <= 0:
        return None
    weeks = len({d.isocalendar()[:2] for d in dates[start : last + 1]})
    return BaseExtremes(hs[hi], start + hi, ls[lo], start + lo,
                        (hs[hi] - ls[lo]) / hs[hi] * 100.0, last - start + 1, weeks)  # fmt: skip


# -- volume ------------------------------------------------------------------------------------


def volume_dryup_ratio(
    volume: Sequence[float | None], recent: int, base: int = 50, end: int | None = None
) -> float | None:
    """Mean volume of the ``recent`` bars ending at ``end`` (default: the last bar) over the
    mean of the ``base`` bars before them. ``None`` when either window does not fit, any volume
    in them is missing, or the base mean is 0. The dry-up measure strategies hand to the shared
    volume score (STRATEGY_SPECIFICATION 9.2); VCP's ``final_volume_ratio`` is the same idea
    over its final contraction."""
    last = len(volume) - 1 if end is None else end
    if recent < 1 or base < 1 or last >= len(volume) or last - recent - base + 1 < 0:
        return None
    rec = volume[last - recent + 1 : last + 1]
    prior = volume[last - recent - base + 1 : last - recent + 1]
    if not all(_finite(v) for v in (*rec, *prior)):
        return None
    base_mean = sum(v for v in prior if v is not None) / base
    if base_mean <= 0:
        return None
    return (sum(v for v in rec if v is not None) / recent) / base_mean
