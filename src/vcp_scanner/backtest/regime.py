"""Market regime from our own universe (STRATEGY_SPECIFICATION 20.3; Multi-Strategy step 7b).

No index data: for each trading day, over the universe members that day (eligible members of
the latest Trend Template scan's universe on or before the day) with an adjusted close and
features, the repository supplies the member count, how many close above their 50-day average
(``technical_features_daily.sma_50``, today included) and the mean daily return. From that:

* ``breadth50``: on when at least 40 % of the members with a 50-day average close above it;
* ``ew50``: an equal-weight index of the members (1.0, times 1 + the day's mean return each
  day) is on when it closes above its own 50-day average (that day included).

The regime gates new entries only (``EngineConfig.regime``). Thresholds are fixed by the spec,
not tuned. Days with too few members, or before the index has 50 values, are "off".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

REGIMES = ("none", "breadth50", "ew50")
BREADTH_MIN_SHARE = 0.40
EW_WINDOW = 50
MIN_MEMBERS = 50


@dataclass(frozen=True, slots=True)
class DayBreadth:
    day: date
    with_average: int  # members with a 50-day average that day
    above: int  # of those, closing above it
    mean_return: float | None  # mean daily return of the members (fraction)


def breadth_regime(
    rows: Sequence[DayBreadth], min_share: float = BREADTH_MIN_SHARE,
    min_members: int = MIN_MEMBERS,
) -> dict[date, bool]:  # fmt: skip
    return {r.day: r.with_average >= min_members and r.above / r.with_average >= min_share
            for r in rows}  # fmt: skip


def ew_regime(rows: Sequence[DayBreadth], window: int = EW_WINDOW) -> dict[date, bool]:
    """Rows oldest first. A day without a mean return keeps the index unchanged."""
    out: dict[date, bool] = {}
    index = 1.0
    values: list[float] = []
    for r in sorted(rows, key=lambda r: r.day):
        if r.mean_return is not None:
            index *= 1.0 + r.mean_return
        values.append(index)
        if len(values) < window:
            out[r.day] = False
            continue
        out[r.day] = index > sum(values[-window:]) / window
    return out


def regime_by_day(kind: str, rows: Sequence[DayBreadth]) -> dict[date, bool] | None:
    """The regime series for ``kind`` (``none``: no filter, None)."""
    if kind == "none":
        return None
    if kind == "breadth50":
        return breadth_regime(rows)
    if kind == "ew50":
        return ew_regime(rows)
    raise ValueError(f"unknown regime {kind!r}; regimes: {REGIMES}")
