"""Forward labels (PROJECT_DESIGN 39; VCP_SPECIFICATION 53-54; Phase 9 step 2).

A label describes what happened *after* an observation (one scanned Trend Template passer on
one as-of date). It uses only bars after the as-of bar, so it can never feed back into the scan
(VCP_SPECIFICATION 54), and it never rewrites the scan (section 56).

Definitions (label version ``LABEL_VERSION``; entry = the as-of close):

* ``ret_{5,10,20,40,60}``: close N sessions later / entry - 1, in %; NULL until N bars exist.
* ``mfe_60`` / ``mae_60``: highest high / lowest low over the next 60 sessions vs entry, in %
  (NULL until 60 bars exist).
* ``breakout_within_20``: a close above the observation's primary pivot within 20 sessions on
  volume >= ``min_volume_ratio`` x the mean of the 50 bars before that day (the detector's
  breakout rule, VCP_SPECIFICATION 61B). ``breakout_day`` = sessions after the as-of date.
  NULL without a pivot, or while fewer than 20 sessions have passed and no breakout yet.
* ``failed_breakout``: after that breakout, a close back below the pivot within
  ``FAIL_WINDOW`` sessions (the FAILED status). NULL without a breakout or while the window is
  open.
* ``complete``: every label is final (60 sessions, and the failure window closed).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

LABEL_VERSION = "labels-1.0.0"
HORIZONS = (5, 10, 20, 40, 60)
BREAKOUT_WINDOW = 20
FAIL_WINDOW = 10
VOLUME_BASE = 50


@dataclass(frozen=True, slots=True)
class ForwardLabels:
    ret: dict[int, float | None]
    mfe_60: float | None
    mae_60: float | None
    breakout_within_20: bool | None
    breakout_day: int | None
    failed_breakout: bool | None
    bars_after: int
    complete: bool


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0


def forward_labels(
    entry: float,
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[float | None],
    prior_volume: Sequence[float | None],
    pivot: float | None,
    min_volume_ratio: float,
) -> ForwardLabels:
    """Labels from the bars *after* the as-of date (``high``/``low``/``close``/``volume``,
    oldest first) and the ``VOLUME_BASE`` volumes up to and including the as-of bar."""
    n = len(close)
    ret = {h: (_pct(close[h - 1], entry) if n >= h else None) for h in HORIZONS}
    mfe = _pct(max(high[:60]), entry) if n >= 60 else None
    mae = _pct(min(low[:60]), entry) if n >= 60 else None

    breakout_day: int | None = None
    if pivot is not None:
        vols = list(prior_volume) + list(volume)
        off = len(prior_volume)
        for i in range(min(BREAKOUT_WINDOW, n)):
            if close[i] <= pivot:
                continue
            base = vols[off + i - VOLUME_BASE : off + i]
            v = volume[i]
            if len(base) < VOLUME_BASE or v is None or any(x is None for x in base):
                continue
            mean = sum(x for x in base if x is not None) / VOLUME_BASE
            if mean > 0 and v >= min_volume_ratio * mean:
                breakout_day = i + 1
                break
    if pivot is None:
        breakout: bool | None = None
    elif breakout_day is not None:
        breakout = True
    else:
        breakout = False if n >= BREAKOUT_WINDOW else None

    failed: bool | None = None
    fail_done = True
    if breakout_day is not None:
        assert pivot is not None
        after = close[breakout_day : breakout_day + FAIL_WINDOW]
        if any(c < pivot for c in after):
            failed = True
        elif len(after) == FAIL_WINDOW:
            failed = False
        else:
            fail_done = False
    complete = n >= 60 and (pivot is None or breakout is not None) and fail_done
    return ForwardLabels(ret, mfe, mae, breakout, breakout_day, failed, n, complete)
