"""VCP detector v2: find the current base and measure it (claude/vcp_detector_v2_spec_draft.md).

Job 1 only: find each stock's current base, its contractions and pivot, and record every
measurement as a number. Deciding which bases to trade is the research plan's job (rules
tested one at a time), never this module's. Pure: arrays in, a result out; no database.

As-of safety: the caller passes the bars up to and including the scan day; nothing later is
ever read. A swing high counts only once ``right_bars`` bars have followed it, so a later run
never changes an earlier day's result.

**Base start (3.1).** The highest confirmed swing high of the last ``window_bars`` sessions (the
earliest on a tie). If it lies in the window's oldest ``left_edge_share``, the window grows by
``extend_bars`` and the older high is taken only if it is higher; this repeats while the high
keeps landing at the left edge, up to ``max_window_bars``. No close since may be above it, and the
run-up into it must be at least ``prior_advance.min_pct``.

**Contractions (3.2).** Swing highs after the base start split it into segments; each segment's
low is the lowest low before the next swing high. Levels are compared with a tolerance *t*
(``level_tolerance_pct``):

1. A contraction is a whole falling leg: while the low after a swing high is lower than the
   leg's low by more than *t*, that swing high is a lower high inside the leg (a staircase).
2. A low not lower by more than *t* (a higher or equal low) means the leg ended; the next leg
   starts at that swing high.
3. The final leg has *ended* once a close (or a high, ``break_on``) is above its last lower high
   by more than *t*; with no lower high, once its low is a confirmed swing low.
4. Undercut: a low below the base's earlier lows by more than *t* merges everything since the
   base start back into one first contraction and restarts the count (rule 4), unless the
   shakeout setting accepts it (rule 5: no deeper than ``shakeout_tolerance_pct`` and a close back
   above the old low within ``shakeout_recovery_bars``).
5. A leg shallower than the minimum swing (``swing.min_depth_pct``, or short and shallow) is noise
   and is merged into its neighbour, not counted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from vcp_scanner.patterns.vcp2.config import VCP2Config

NO_SWING_HIGH = "NO_SWING_HIGH"
BROKEN_OUT = "BROKEN_OUT"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NO_RUNUP = "NO_RUNUP"
VOLUME_BASE = 50


@dataclass(frozen=True, slots=True)
class Bars:
    """One stock's adjusted bars up to and including the scan day, oldest first."""

    dates: Sequence[date]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    volume: Sequence[float | None]

    def __post_init__(self) -> None:
        n = len(self.dates)
        if not all(len(x) == n for x in (self.high, self.low, self.close, self.volume)):
            raise ValueError("Bars columns differ in length")


@dataclass(slots=True)
class Leg:
    """One contraction: a falling leg from ``peak`` to its lowest low."""

    peak: int
    peak_price: float
    low: int
    low_price: float
    lower_highs: int = 0
    last_lower_high: float | None = None

    @property
    def depth_pct(self) -> float:
        return (self.peak_price - self.low_price) / self.peak_price * 100.0

    @property
    def bars(self) -> int:
        return self.low - self.peak


@dataclass(frozen=True, slots=True)
class Contraction:
    peak_date: date
    peak_price: float
    low_date: date
    low_price: float
    depth_pct: float
    bars: int
    lower_highs: int
    volume_ratio: float | None  # mean volume in the leg / mean of the 50 bars before its peak
    depth_ratio: float | None  # depth / previous contraction's depth


@dataclass(frozen=True, slots=True)
class Candidate:
    """Every v2 measurement of one stock's current base on the scan day (spec 3.4)."""

    as_of: date
    # base (3.1)
    base_start_date: date
    base_high: float
    base_low: float
    base_low_date: date
    base_bars: int
    base_depth_pct: float
    window_bars: int
    window_extensions: int
    prior_advance_pct: float | None
    prior_advance_long_pct: float | None
    close_in_base_pct: float | None  # today's close within the base range, 0 = low, 100 = high
    # contractions (3.2)
    contractions: tuple[Contraction, ...]
    contraction_count: int
    tightening_share: float | None  # share of contractions shallower than the one before
    final_depth_pct: float
    final_bars: int
    final_ended: bool
    undercuts: int
    deepest_undercut_pct: float | None
    undercut_recovery_bars: int | None
    shakeouts: int
    # pivot (3.3)
    pivot: float
    pivot_alt: float  # highest high of the last right_side_window sessions
    distance_to_pivot_pct: float
    right_side_range_pct: float
    atr_ratio: float | None  # mean true range % of the right side / of the whole base
    days_since_final_low: int
    # volume (3.4)
    final_volume_ratio: float | None  # dry-up: final leg volume / 50 bars before its peak
    up_down_volume: float | None  # volume on up days / down days inside the base
    last5_volume_ratio: float | None  # last 5 sessions / the 50 before them
    # loose minimum (3.1)
    is_candidate: bool
    unmet: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Detection:
    as_of: date
    candidate: Candidate | None
    reason: str | None  # why there is no base today, when there is none


# ---- swings -------------------------------------------------------------------------------


def swing_highs(high: Sequence[float], left: int, right: int) -> list[int]:
    """Confirmed swing highs: at least as high as the ``left`` bars before and ``right`` after."""
    n = len(high)
    out = []
    for i in range(left, n - right):
        h = high[i]
        if all(high[j] <= h for j in range(i - left, i + right + 1) if j != i):
            out.append(i)
    return out


def _highest(swings: Sequence[int], high: Sequence[float], lo: int) -> int | None:
    """The highest swing high at index >= lo; the earliest on a tie."""
    best = None
    for i in swings:
        if i >= lo and (best is None or high[i] > high[best]):
            best = i
    return best


# ---- base start (3.1) ---------------------------------------------------------------------


def base_start(
    high: Sequence[float], swings: Sequence[int], cfg: VCP2Config
) -> tuple[int, int, int] | None:
    """(index, window length used, extensions) of the base start, or None without a swing high."""
    bs = cfg.base_start
    n = len(high)
    w = bs.window_bars
    lo = max(0, n - w)
    best = _highest(swings, high, lo)
    if best is None:
        return None
    extensions = 0
    while True:
        in_left_edge = best < lo + bs.left_edge_share * w
        if not in_left_edge or w >= bs.max_window_bars or lo == 0:
            break
        w_new = min(w + bs.extend_bars, bs.max_window_bars)
        lo_new = max(0, n - w_new)
        older = _highest(swings, high, lo_new)
        if older is None or high[older] <= high[best]:
            break  # an older high only wins if it is higher
        best, w, lo = older, w_new, lo_new
        extensions += 1
    return best, w, extensions


# ---- contractions (3.2) -------------------------------------------------------------------


def _argmin(low: Sequence[float], a: int, b: int) -> int:
    """Index of the lowest low in [a, b); the earliest on a tie."""
    best = a
    for i in range(a + 1, b):
        if low[i] < low[best]:
            best = i
    return best


def _noise(leg: Leg, cfg: VCP2Config) -> bool:
    sw = cfg.swing
    d = leg.depth_pct
    return d < sw.min_depth_pct or (leg.bars < sw.min_duration_bars
                                    and d < sw.short_swing_max_depth_pct)  # fmt: skip


@dataclass(slots=True)
class LegResult:
    legs: list[Leg]
    undercuts: int = 0
    deepest_undercut_pct: float | None = None
    undercut_recovery_bars: int | None = None
    shakeouts: int = 0


def contraction_legs(
    bars: Bars, start: int, swings: Sequence[int], cfg: VCP2Config
) -> LegResult:
    """The base's contractions (rules 1-5 in the module docstring), oldest first."""
    hi, lo_, cl = bars.high, bars.low, bars.close
    n = len(hi)
    t = cfg.contraction.level_tolerance_pct / 100.0
    peaks = [i for i in swings if i > start]
    # segment k runs from peak k (start, then each swing high) to the next swing high
    bounds = [start, *peaks, n]
    segs = []
    for k in range(len(bounds) - 1):
        a, b = bounds[k], bounds[k + 1]
        if b - a < 2:
            continue  # two adjacent swing highs: no bar between them
        low_i = _argmin(lo_, a + 1, b)
        segs.append((a, low_i))
    res = LegResult(legs=[])
    if not segs:
        return res
    done: list[Leg] = []
    a0, l0 = segs[0]
    cur = Leg(a0, hi[a0], l0, lo_[l0])
    brk = cl if cfg.contraction.break_on == "close" else hi
    for a, low_i in segs[1:]:
        h, low_p = hi[a], lo_[low_i]
        # rule 3: did the leg end, by a break of its last lower high, before this segment?
        ended = cur.last_lower_high is not None and any(
            brk[j] > cur.last_lower_high * (1 + t) for j in range(cur.low + 1, a + 1))
        # rule 4: after an earlier leg (or once the leg in progress has ended), a low below the
        # base's lowest low so far by more than t merges everything back into one first
        # contraction
        if done or ended:
            base_low = min([x.low_price for x in done] + [cur.low_price])
            if low_p < base_low * (1 - t):
                depth = (base_low - low_p) / base_low * 100.0
                rec = next((j - low_i for j in range(low_i + 1, n) if cl[j] > base_low), None)
                so = cfg.contraction
                if (so.shakeout_tolerance_pct > 0 and depth <= so.shakeout_tolerance_pct
                        and rec is not None and rec <= so.shakeout_recovery_bars):  # fmt: skip
                    res.shakeouts += 1  # rule 5: accepted as a shakeout, no reset
                else:
                    res.undercuts += 1
                    res.deepest_undercut_pct = max(res.deepest_undercut_pct or 0.0, depth)
                    res.undercut_recovery_bars = rec
                    first = done[0] if done else cur
                    inside = sum(x.lower_highs + 1 for x in done) + cur.lower_highs + 1
                    cur = Leg(first.peak, first.peak_price, low_i, low_p, inside, h)
                    done = []
                    continue
        # a swing high above the leg's peak (by more than t) always ends the leg
        higher_high = h > cur.peak_price * (1 + t)
        if not higher_high and not ended and low_p < cur.low_price * (1 - t):
            # rule 1: a lower low, so this swing high was a lower high inside the leg
            cur.low, cur.low_price = low_i, low_p
            cur.lower_highs += 1
            cur.last_lower_high = h
            continue
        # rule 2: a higher or equal low (or a higher high): the leg ended; the next one
        # starts at this swing high
        if _noise(cur, cfg):
            # a shallow leg is noise: merged into its neighbour, not counted
            if h >= cur.peak_price:
                cur = Leg(a, h, low_i, low_p)
            elif low_p < cur.low_price:
                cur.low, cur.low_price = low_i, low_p
            continue
        done.append(cur)
        cur = Leg(a, h, low_i, low_p)
    res.legs = [*done, cur]  # the final leg is kept even if shallow: measured, not counted
    return res


def final_leg_ended(bars: Bars, leg: Leg, cfg: VCP2Config) -> bool:
    """Rule 3: the final leg is over once price breaks its last lower high (by more than t);
    with no lower high, once its low is a confirmed swing low."""
    n = len(bars.high)
    t = cfg.contraction.level_tolerance_pct / 100.0
    after = range(leg.low + 1, n)
    if leg.last_lower_high is not None:
        level = leg.last_lower_high * (1 + t)
        prices = bars.close if cfg.contraction.break_on == "close" else bars.high
        return any(prices[j] > level for j in after)
    r = cfg.swing.right_bars
    return leg.low + r <= n - 1 and all(bars.low[j] >= leg.low_price for j in after)


# ---- measurements -------------------------------------------------------------------------


def _mean(xs: Sequence[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def _vol_ratio(vol: Sequence[float | None], a: int, b: int) -> float | None:
    """Mean volume of bars [a, b] over the mean of the 50 bars before a."""
    return _ratio(_mean(vol[a : b + 1]), _mean(vol[max(0, a - VOLUME_BASE) : a]))


def _tr_pct(bars: Bars, i: int) -> float:
    prev = bars.close[i - 1] if i > 0 else bars.close[i]
    tr = max(bars.high[i], prev) - min(bars.low[i], prev)
    return tr / bars.close[i] * 100.0


# ---- the detector -------------------------------------------------------------------------


def detect(bars: Bars, cfg: VCP2Config) -> Detection:
    """The current base of one stock on its last bar (the scan day), measured."""
    n = len(bars.dates)
    as_of = bars.dates[-1]
    sw = cfg.swing
    keep = cfg.base_start.max_window_bars + cfg.prior_advance.long_lookback_bars + sw.left_bars + 5
    if n > keep:  # only the recent past can matter; trims the work, never the result
        cut = n - keep
        bars = Bars(bars.dates[cut:], bars.high[cut:], bars.low[cut:], bars.close[cut:],
                    bars.volume[cut:])  # fmt: skip
        n = len(bars.dates)
    hi, lo_, cl, vol = bars.high, bars.low, bars.close, bars.volume
    swings = swing_highs(hi, sw.left_bars, sw.right_bars)
    found = base_start(hi, swings, cfg)
    if found is None:
        return Detection(as_of, None, NO_SWING_HIGH)
    s, window, extensions = found
    base_high = hi[s]
    if any(cl[j] > base_high for j in range(s + 1, n)):
        return Detection(as_of, None, BROKEN_OUT)
    pa = cfg.prior_advance
    before = lo_[max(0, s - pa.lookback_bars) : s]
    if not before:
        return Detection(as_of, None, INSUFFICIENT_HISTORY)
    prior = (base_high / min(before) - 1) * 100.0
    if prior < pa.min_pct:
        short = s < pa.lookback_bars
        return Detection(as_of, None, INSUFFICIENT_HISTORY if short else NO_RUNUP)
    long_before = lo_[max(0, s - pa.long_lookback_bars) : s]
    prior_long = (base_high / min(long_before) - 1) * 100.0 if long_before else None

    res = contraction_legs(bars, s, swings, cfg)
    legs = res.legs
    counted = [x for x in legs if not _noise(x, cfg)]
    # the final contraction is the last real one; a shallow wiggle after it is not measured as
    # one, but its peak still counts for the pivot when it is higher
    last = legs[-1] if legs else Leg(s, base_high, s, base_high)
    final = counted[-1] if counted else last
    final_ended = final is not last or final_leg_ended(bars, final, cfg)
    base_low_i = _argmin(lo_, s, n)
    base_low = lo_[base_low_i]
    depth = (base_high - base_low) / base_high * 100.0
    contractions = []
    prev_depth = None
    for x in counted:
        contractions.append(Contraction(
            bars.dates[x.peak], x.peak_price, bars.dates[x.low], x.low_price, x.depth_pct,
            x.bars, x.lower_highs, _vol_ratio(vol, x.peak, x.low),
            x.depth_pct / prev_depth if prev_depth else None,
        ))  # fmt: skip
        prev_depth = x.depth_pct
    pairs = list(zip(counted, counted[1:], strict=False))
    tightening = (sum(1 for a, b in pairs if b.depth_pct < a.depth_pct) / len(pairs)
                  if pairs else None)  # fmt: skip
    rw = cfg.pivot.right_side_window
    right = range(max(s, n - rw), n)
    r_hi = max(hi[j] for j in right)
    r_lo = min(lo_[j] for j in right)
    base_tr = _mean([_tr_pct(bars, j) for j in range(s + 1, n)])
    right_tr = _mean([_tr_pct(bars, j) for j in right])
    pivot = max(final.peak_price, last.peak_price)
    up = sum(vol[j] or 0.0 for j in range(s + 1, n) if cl[j] > cl[j - 1])
    down = sum(vol[j] or 0.0 for j in range(s + 1, n) if cl[j] < cl[j - 1])
    span = base_high - base_low
    c = cfg.candidate
    unmet = []
    if n - s < c.min_base_bars:
        unmet.append("base_bars")
    if not c.min_depth_pct <= depth <= c.max_depth_pct:
        unmet.append("base_depth")
    if len(counted) < c.min_contractions:
        unmet.append("contractions")
    cand = Candidate(
        as_of=as_of,
        base_start_date=bars.dates[s],
        base_high=base_high,
        base_low=base_low,
        base_low_date=bars.dates[base_low_i],
        base_bars=n - s,
        base_depth_pct=depth,
        window_bars=window,
        window_extensions=extensions,
        prior_advance_pct=prior,
        prior_advance_long_pct=prior_long,
        close_in_base_pct=(cl[-1] - base_low) / span * 100.0 if span > 0 else None,
        contractions=tuple(contractions),
        contraction_count=len(counted),
        tightening_share=tightening,
        final_depth_pct=final.depth_pct,
        final_bars=final.bars,
        final_ended=final_ended,
        undercuts=res.undercuts,
        deepest_undercut_pct=res.deepest_undercut_pct,
        undercut_recovery_bars=res.undercut_recovery_bars,
        shakeouts=res.shakeouts,
        pivot=pivot,
        pivot_alt=r_hi,
        distance_to_pivot_pct=(cl[-1] / pivot - 1) * 100.0,
        right_side_range_pct=(r_hi - r_lo) / r_hi * 100.0,
        atr_ratio=_ratio(right_tr, base_tr),
        days_since_final_low=n - 1 - final.low,
        # dry-up inside the final contraction: peak to its low once it has ended, else to today
        final_volume_ratio=(_vol_ratio(vol, final.peak, final.low if final_ended else n - 1)
                            if final.peak < n - 1 else None),  # fmt: skip
        up_down_volume=up / down if down else None,
        last5_volume_ratio=_vol_ratio(vol, max(0, n - 5), n - 1),
        is_candidate=not unmet,
        unmet=tuple(unmet),
    )
    return Detection(as_of, cand, None)
