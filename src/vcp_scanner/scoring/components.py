"""Component scores: trend, VCP, volume, RS (SCORING_SPECIFICATION 2-6; Phase 7 step 1).

Scores *rank* setups the detector already qualified; they are not probabilities and never
change a gate or a class. Every number here is a pure function of measurements, so a score can
be recomputed and explained from its stored sub-components (DATABASE_SCHEMA 35).

Conventions (details fixed in the implementation, 2026-10-03):

* ``linear(x, worst, best)`` maps ``worst`` to 0 and ``best`` to 100 and clips (section 2); it
  works when ``best < worst`` (smaller is better).
* A sub-component whose measurement is missing (None or not finite) is **NULL**: it is left out,
  never scored as 0 or as neutral. Its stored row keeps ``normalized = None`` and ``points = 0``.
* Component score = sum of points / sum of the max points of the *available* sub-components x
  100 (the same renormalization the spec sets for fundamentals, section 7). A component with no
  available sub-component is NULL, and the final score renormalizes over the rest (section 1).
* points = normalized x weight / 100; max points = weight.

Measurements from bars (``measure_*`` helpers) use only bars up to the as-of date.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from vcp_scanner.config.models import NormalizationBounds

TREND_SUBS = ("high_proximity", "sma200_slope", "ma_stack_margin")
VCP_SUBS = (
    "contraction_sequence", "tightening", "final_contraction", "volatility", "pivot",
    "base_structure",
)  # fmt: skip
VOLUME_SUBS = ("dryup_quality", "up_down_volume", "distribution")
SMA200_SLOPE_LAG = 21
UP_DOWN_WINDOW = 50
DISTRIBUTION_WINDOW = 25
VOLUME_AVG_WINDOW = 50


@dataclass(frozen=True, slots=True)
class SubScore:
    name: str
    raw: float | None
    normalized: float | None  # 0-100; None = NULL (missing input)
    weight: float
    points: float
    max_points: float


@dataclass(frozen=True, slots=True)
class ComponentScore:
    component: str  # TREND | VCP | VOLUME | RS
    score: float | None  # 0-100; None = no available sub-component
    subs: tuple[SubScore, ...]


def _usable(x: float | None) -> bool:
    return x is not None and math.isfinite(x)


def linear(x: float, worst: float, best: float) -> float:
    """clip((x - worst) / (best - worst) x 100, 0, 100) (section 2)."""
    return min(100.0, max(0.0, (x - worst) / (best - worst) * 100.0))


def score_component(
    component: str,
    raw: Mapping[str, float | None],
    weights: Mapping[str, float],
    bounds: Mapping[str, NormalizationBounds],
    names: Sequence[str],
) -> ComponentScore:
    """Score one component from raw measurements keyed by sub-component name."""
    if set(weights) != set(names) or set(bounds) != set(names):
        raise ValueError(f"{component} sub-components must be exactly {sorted(names)}")
    subs: list[SubScore] = []
    for name in names:
        x, w, b = raw.get(name), weights[name], bounds[name]
        if _usable(x):
            assert x is not None
            norm = linear(x, b.worst, b.best)
            subs.append(SubScore(name, x, norm, w, norm * w / 100.0, w))
        else:
            subs.append(SubScore(name, None, None, w, 0.0, w))
    avail = [s for s in subs if s.normalized is not None]
    total = sum(s.max_points for s in avail)
    score = sum(s.points for s in avail) / total * 100.0 if avail and total > 0 else None
    return ComponentScore(component, score, tuple(subs))


# -- measurements from bars --------------------------------------------------------------------


def _pct(a: float | None, b: float | None) -> float | None:
    """(a / b - 1) x 100, None when an input is missing or b <= 0."""
    if not (_usable(a) and _usable(b)) or b is None or a is None or b <= 0:
        return None
    return (a / b - 1.0) * 100.0


def _volumes_ok(volume: Sequence[float | None], start: int, end: int) -> bool:
    """Every volume in ``[start, end)`` is present and positive (zero = suspect, section 5)."""
    if start < 0:
        return False
    return all(v is not None and math.isfinite(v) and v > 0 for v in volume[start:end])


def measure_up_down_volume(
    close: Sequence[float], volume: Sequence[float | None], window: int = UP_DOWN_WINDOW
) -> float | None:
    """Up-day volume / down-day volume over the last ``window`` sessions (a session is up or
    down against the previous close; unchanged sessions are ignored). None when a volume is
    missing or zero or there are too few bars; 10.0 (a cap) when there was no down volume."""
    n = len(close)
    if n < window + 1 or not _volumes_ok(volume, n - window, n):
        return None
    up = down = 0.0
    for i in range(n - window, n):
        v = float(volume[i] or 0.0)  # present and positive: checked above
        if close[i] > close[i - 1]:
            up += v
        elif close[i] < close[i - 1]:
            down += v
    if down == 0:
        return 10.0 if up > 0 else None
    return up / down


def measure_distribution(
    close: Sequence[float],
    volume: Sequence[float | None],
    multiple: float,
    window: int = DISTRIBUTION_WINDOW,
    avg_window: int = VOLUME_AVG_WINDOW,
) -> float | None:
    """Count of high-volume down days in the last ``window`` sessions: close below the previous
    close on volume > ``multiple`` x the mean volume of the ``avg_window`` sessions *before*
    that day (the day itself excluded). None when a needed volume is missing or zero."""
    n = len(close)
    first = n - window
    if first < 1 or not _volumes_ok(volume, first - avg_window, n):
        return None
    vol = [0.0 if v is None else float(v) for v in volume]  # all present: checked above
    count = 0
    for i in range(first, n):
        avg = sum(vol[i - avg_window : i]) / avg_window
        if close[i] < close[i - 1] and vol[i] > multiple * avg:
            count += 1
    return float(count)


# -- components ---------------------------------------------------------------------------------


def trend_score(
    close: float | None,
    high_252: float | None,
    sma50: float | None,
    sma200: float | None,
    sma200_lagged: float | None,
    weights: Mapping[str, float],
    bounds: Mapping[str, NormalizationBounds],
) -> ComponentScore:
    """Section 3. ``sma200_lagged`` is SMA200 ``SMA200_SLOPE_LAG`` sessions earlier."""
    below = None
    if _usable(close) and _usable(high_252) and high_252 is not None and high_252 > 0:
        assert close is not None
        below = (high_252 - close) / high_252 * 100.0
    raw = {
        "high_proximity": below,
        "sma200_slope": _pct(sma200, sma200_lagged),
        "ma_stack_margin": _pct(sma50, sma200),
    }
    return score_component("TREND", raw, weights, bounds, TREND_SUBS)


def vcp_score(
    contraction_count: int | None,
    max_tightening_ratio: float | None,
    final_contraction_pct: float | None,
    volatility_ratio: float | None,
    right_side_range_pct: float | None,
    base_depth_pct: float | None,
    weights: Mapping[str, float],
    bounds: Mapping[str, NormalizationBounds],
) -> ComponentScore:
    """Section 4. Volume is deliberately not an input. ``volatility_ratio`` is the contraction
    ratio of the configured ``vcp.volatility.measure`` (true range by default, the measure the
    detector's volatility test uses; the spec's ``atr_contraction_ratio`` when measure = atr)."""
    raw = {
        "contraction_sequence": None if contraction_count is None else float(contraction_count),
        "tightening": max_tightening_ratio,
        "final_contraction": final_contraction_pct,
        "volatility": volatility_ratio,
        "pivot": right_side_range_pct,
        "base_structure": base_depth_pct,
    }
    return score_component("VCP", raw, weights, bounds, VCP_SUBS)


def volume_score(
    final_volume_ratio: float | None,
    up_down_volume: float | None,
    distribution_days: float | None,
    weights: Mapping[str, float],
    bounds: Mapping[str, NormalizationBounds],
) -> ComponentScore:
    """Section 5. Inputs come from the pattern (dry-up) and ``measure_*`` (bars)."""
    raw = {
        "dryup_quality": final_volume_ratio,
        "up_down_volume": up_down_volume,
        "distribution": distribution_days,
    }
    return score_component("VOLUME", raw, weights, bounds, VOLUME_SUBS)


def rs_score(rs_rank: float | None, min_rs_rank: float) -> ComponentScore:
    """Section 6: linear(rs_rank, worst = trend_template.min_rs_rank, best = 99)."""
    # min_rs_rank may be 99 (allowed by config): keep worst below best so the bound is valid.
    bounds = {"rs_rank": NormalizationBounds(worst=min(float(min_rs_rank), 98.0), best=99.0)}
    return score_component("RS", {"rs_rank": rs_rank}, {"rs_rank": 100.0}, bounds, ("rs_rank",))
