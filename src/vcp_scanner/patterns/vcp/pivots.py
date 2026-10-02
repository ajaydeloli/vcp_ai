"""Pivot candidates and deterministic selection (VCP_SPECIFICATION 19-22, 19A).

Section 19 lists four candidate sources but no selection rule, and section 20 names ``touches``
and ``rejection_count`` without definitions. Filled in (owner allowed improvements with
documentation, 2026-10-02; recorded in VCP_SPECIFICATION 19A):

Candidates (adjusted prices; bars from the base start to the as-of bar):

* ``BASE_HIGH``: the base high at the base start.
* ``SWING_HIGH``: the last kept peak, i.e. the final contraction's peak (the latest meaningful
  confirmed swing high; noise peaks were merged in segmentation).
* ``RIGHT_SIDE_HIGH``: the highest high of the last ``pivot.right_side_window_days`` bars.
* ``REPEATED_RESISTANCE``: two or more confirmed swing highs in the base (merged noise peaks
  included) within ``pivot.level_tolerance_pct`` of the cluster's highest one; the level is that
  highest price, dated at the cluster's latest swing.

Candidates at the same date and price are one candidate, labelled with the first of
BASE_HIGH, SWING_HIGH, RIGHT_SIDE_HIGH, REPEATED_RESISTANCE (the most structural meaning).

For each candidate at price P, with zone = highs >= P x (1 - tolerance):

* ``touches``: visits to the zone (runs of consecutive bars whose high reaches it).
* ``rejection_count``: finished visits (price left the zone before the as-of bar) with no close
  above P. A visit still running at the as-of bar is not a rejection.
* ``distance_to_close_pct`` = (P - close) / close x 100 at the as-of bar (negative: above it).
* ``right_side_tightness_pct``: (max high - min low) / max high over the bars after the pivot
  within the last ``right_side_window_days`` bars; ``None`` when no such bar exists.

Primary pivot (first rule that applies):

1. the right side is tight (``tight_pivot_pass``) and its top is at or above the close: the
   right-side high (the top of the tight area, section 19 item 3);
2. a contraction exists: the final contraction's peak;
3. otherwise the base high.
The primary is the candidate at that bar, whatever label it carries after de-duplication.

These are hypotheses for the golden dataset and backtests; every candidate is kept (DATABASE_SCHEMA
33) so other rules can be compared later without re-detection.
"""

from __future__ import annotations

from dataclasses import dataclass

from vcp_scanner.config.models import VCPThresholdsConfig
from vcp_scanner.domain.enums import PivotSource
from vcp_scanner.domain.vcp import PivotCandidate
from vcp_scanner.patterns.vcp.measurements import PriceSeries, VCPMeasurements
from vcp_scanner.patterns.vcp.segmentation import SegmentationResult

_SOURCE_ORDER = (
    PivotSource.BASE_HIGH,
    PivotSource.SWING_HIGH,
    PivotSource.RIGHT_SIDE_HIGH,
    PivotSource.REPEATED_RESISTANCE,
)


@dataclass(frozen=True, slots=True)
class PivotSelection:
    """All candidates (ordered by source priority, then date) and the primary one."""

    candidates: tuple[PivotCandidate, ...]
    primary: PivotCandidate | None


def _visits(series: PriceSeries, start: int, n: int, price: float, tol: float) -> tuple[int, int]:
    """(touches, rejections) of the zone at ``price`` over bars [start, n)."""
    floor = price * (1 - tol)
    touches = rejections = 0
    i = start
    while i < n:
        if series.high[i] < floor:
            i += 1
            continue
        touches += 1
        closed_above = False
        while i < n and series.high[i] >= floor:
            closed_above = closed_above or series.close[i] > price
            i += 1
        if i < n and not closed_above:  # the visit finished before the as-of bar
            rejections += 1
    return touches, rejections


def _clusters(prices: list[tuple[int, float]], tol: float) -> list[tuple[int, float]]:
    """Levels with >= 2 swing highs within ``tol`` of the cluster's highest: (latest index, price).

    Greedy from the highest swing down, deterministic (ties by earlier index first).
    """
    remaining = sorted(prices, key=lambda x: (-x[1], x[0]))
    levels: list[tuple[int, float]] = []
    while remaining:
        top_i, top_p = remaining[0]
        members = [x for x in remaining if x[1] >= top_p * (1 - tol)]
        remaining = [x for x in remaining if x[1] < top_p * (1 - tol)]
        if len(members) >= 2:
            levels.append((max(i for i, _ in members), top_p))
    return levels


def select_pivots(
    series: PriceSeries,
    result: SegmentationResult,
    measures: VCPMeasurements,
    config: VCPThresholdsConfig,
) -> PivotSelection:
    """Candidates and the primary pivot for a segmented, measured base (module docstring)."""
    base = result.base
    if base is None:
        return PivotSelection((), None)
    if series.dates[base.base_start_index] != base.base_start:
        raise ValueError("series does not match the segmentation (base start index differs)")
    n = 0
    while n < len(series.dates) and series.dates[n] <= base.as_of:
        n += 1
    b = base.base_start_index
    tol = config.pivot.level_tolerance_pct / 100
    w = config.pivot.right_side_window_days
    close = series.close[n - 1]

    raw: list[tuple[PivotSource, int, float]] = [(PivotSource.BASE_HIGH, b, base.base_high)]
    if base.contractions:
        last = base.contractions[-1]
        raw.append((PivotSource.SWING_HIGH, last.peak_index, last.peak_price))
    rs_start = max(n - w, b)
    rs_i = max(range(rs_start, n), key=lambda i: (series.high[i], i))  # latest on a tie
    raw.append((PivotSource.RIGHT_SIDE_HIGH, rs_i, series.high[rs_i]))
    index_of = {d: i for i, d in enumerate(series.dates[:n])}
    swing_highs = [
        (index_of[s.swing_date], s.price)
        for s in result.swings.highs()
        if s.swing_date >= base.base_start and s.swing_date in index_of
    ]
    raw += [(PivotSource.REPEATED_RESISTANCE, i, p) for i, p in _clusters(swing_highs, tol)]

    seen: dict[tuple[int, float], PivotCandidate] = {}
    for source in _SOURCE_ORDER:
        for src, i, price in sorted((r for r in raw if r[0] is source), key=lambda r: r[1]):
            if (i, price) in seen:
                continue
            touches, rejections = _visits(series, b, n, price, tol)
            assert touches >= 1  # the candidate's own bar reaches its zone
            after = max(i + 1, n - w)
            tight = None
            if after < n:
                hi = max(series.high[after:n])
                tight = (hi - min(series.low[after:n])) / hi * 100
            seen[(i, price)] = PivotCandidate(
                pivot_price=price,
                pivot_date=series.dates[i],
                source=src,
                distance_to_close_pct=(price - close) / close * 100,
                touches=touches,
                rejection_count=rejections,
                right_side_tightness_pct=tight,
            )
    candidates = tuple(seen.values())

    rs_tight = measures.tight_pivot_pass and series.high[rs_i] >= close
    if rs_tight:
        key = (rs_i, series.high[rs_i])  # rule 1
    elif base.contractions:
        key = (base.contractions[-1].peak_index, base.contractions[-1].peak_price)  # rule 2
    else:
        key = (b, base.base_high)  # rule 3
    return PivotSelection(candidates, seen[key])
