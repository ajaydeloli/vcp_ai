"""Sector rotation (FRONTEND_SPECIFICATION §67.33): NSE's sector indices against NIFTY 500 on a
relative rotation graph, week by week. Display only.

Per week (the last session of each calendar week; the current week uses the latest session):

    RS          = sector close / NIFTY 500 close
    RS-Ratio    = 100 x RS / its RATIO_WEEKS-week average      (> 100: stronger than its trend)
    RS-Momentum = 100 x RS-Ratio / RS-Ratio MOMENTUM_WEEKS ago  (> 100: the ratio is rising)

Quadrants: Leading (both > 100), Weakening (ratio > 100, momentum <= 100), Lagging (both <= 100),
Improving (ratio <= 100, momentum > 100). Sectors usually move clockwise through them."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from vcp_scanner.api import models as m
from vcp_scanner.api.queries import Cur
from vcp_scanner.market_health.features import NIFTY500, SECTOR_INDEX

RATIO_WEEKS = 10
MOMENTUM_WEEKS = 4
TAIL_WEEKS = 12  # points sent per sector; the card shows 4, 6 or 12 of them
FETCH_DAYS = 7 * (RATIO_WEEKS + MOMENTUM_WEEKS + TAIL_WEEKS + 4)


def quadrant(ratio: float, momentum: float) -> str:
    if ratio > 100:
        return "leading" if momentum > 100 else "weakening"
    return "improving" if momentum > 100 else "lagging"


def weekly(closes: dict[date, float]) -> list[tuple[date, float]]:
    """The last close of each calendar (ISO) week, oldest first."""
    last: dict[tuple[int, int], date] = {}
    for d in closes:
        key = d.isocalendar()[:2]
        if key not in last or d > last[key]:
            last[key] = d
    return [(d, closes[d]) for d in sorted(last.values())]


def rotation_points(
    sector: dict[date, float], bench: dict[date, float]
) -> list[m.HealthRotationPoint]:
    """RS-Ratio and RS-Momentum per week, on the weeks both indices closed on the same day."""
    weeks = [(d, c) for d, c in weekly(bench) if d in sector]
    rs = [sector[d] / c for d, c in weeks]
    ratio: list[float | None] = []
    for i in range(len(rs)):
        if i < RATIO_WEEKS - 1:
            ratio.append(None)
        else:
            avg = sum(rs[i - RATIO_WEEKS + 1 : i + 1]) / RATIO_WEEKS
            ratio.append(100.0 * rs[i] / avg)
    out = []
    for i, (d, _c) in enumerate(weeks):
        now = ratio[i]
        then = ratio[i - MOMENTUM_WEEKS] if i >= MOMENTUM_WEEKS else None
        if now is not None and then:
            out.append(m.HealthRotationPoint(
                day=d, ratio=round(now, 3), momentum=round(100.0 * now / then, 3)
            ))  # fmt: skip
    return out


def sector_rotation(cur: Cur, end: date) -> list[m.HealthRotation]:
    """Every sector index with at least one point, in SECTOR_INDEX order."""
    names = [NIFTY500, *SECTOR_INDEX.values()]
    closes: dict[str, dict[date, float]] = defaultdict(dict)
    for name, d, c in cur.execute(
        "SELECT index_name, trade_date, close FROM index_daily WHERE trade_date BETWEEN ? AND ?"
        " AND close > 0 AND index_name IN (SELECT unnest(?))",
        [date.fromordinal(end.toordinal() - FETCH_DAYS), end, names],
    ).fetchall():
        closes[str(name)][d] = float(c)
    bench = closes.get(NIFTY500)
    if not bench:
        return []
    out = []
    for sector, index_name in SECTOR_INDEX.items():
        pts = rotation_points(closes.get(index_name, {}), bench)
        if pts:
            last = pts[-1]
            out.append(m.HealthRotation(
                sector=sector, index_name=index_name, quadrant=quadrant(last.ratio, last.momentum),
                points=pts[-TAIL_WEEKS:],
            ))  # fmt: skip
    return out
