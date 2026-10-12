"""Sector rotation (relative rotation graph): weeks, RS-Ratio, RS-Momentum, quadrants."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from vcp_scanner.api.rotation import (
    MOMENTUM_WEEKS,
    RATIO_WEEKS,
    TAIL_WEEKS,
    quadrant,
    rotation_points,
    sector_rotation,
    weekly,
)
from vcp_scanner.data.storage.duckdb_store import DuckDBStore

MONDAY = date(2026, 1, 5)


def _days(weeks: int) -> list[date]:
    """Monday to Friday sessions for ``weeks`` weeks."""
    return [MONDAY + timedelta(days=7 * w + k) for w in range(weeks) for k in range(5)]


def test_quadrants() -> None:
    assert quadrant(101, 101) == "leading"
    assert quadrant(101, 99) == "weakening"
    assert quadrant(99, 99) == "lagging"
    assert quadrant(99, 101) == "improving"
    assert quadrant(100, 100) == "lagging"  # 100 is not above


def test_weekly_takes_the_last_session_of_each_week() -> None:
    closes = {d: float(i) for i, d in enumerate(_days(3)[:-1])}  # the third week ends Thursday
    got = weekly(closes)
    assert [d.weekday() for d, _ in got] == [4, 4, 3]
    assert [c for _, c in got] == [4.0, 9.0, 13.0]


def test_a_sector_keeping_pace_sits_at_100() -> None:
    days = _days(20)
    bench = {d: 100.0 * 1.01**i for i, d in enumerate(days)}
    pts = rotation_points({d: 2 * c for d, c in bench.items()}, bench)
    assert len(pts) == 20 - (RATIO_WEEKS - 1) - MOMENTUM_WEEKS
    assert all(p.ratio == pytest.approx(100) and p.momentum == pytest.approx(100) for p in pts)


def test_a_cycle_turns_clockwise() -> None:
    days = _days(60)
    bench = {d: 100.0 for d in days}
    # relative strength rising and falling in a 16-week cycle
    sector = {d: 100.0 * (1 + 0.05 * math.sin(2 * math.pi * (i // 5) / 16)) for i, d in
              enumerate(days)}  # fmt: skip
    pts = rotation_points(sector, bench)
    order = [quadrant(p.ratio, p.momentum) for p in pts]
    turns = {("improving", "leading"), ("leading", "weakening"), ("weakening", "lagging"),
             ("lagging", "improving")}  # fmt: skip
    changes = [(a, b) for a, b in zip(order, order[1:], strict=False) if a != b]
    assert changes and all(c in turns for c in changes)
    assert set(order) == {"leading", "weakening", "lagging", "improving"}


def test_sector_rotation_reads_index_daily() -> None:
    store = DuckDBStore(":memory:")
    store.migrate()
    days = _days(30)
    rows = []
    for i, d in enumerate(days):
        rows.append(("Nifty 500", d, 100.0))
        rows.append(("Nifty IT", d, 100.0 + i))  # outperforming, ever more slowly
        if i >= 100:
            rows.append(("Nifty Power", d, 50.0))  # too short a history: no point
    store.conn.executemany(
        "INSERT INTO index_daily (index_name, trade_date, close) VALUES (?, ?, ?)", rows
    )
    got = sector_rotation(store.conn, days[-1])
    assert [r.sector for r in got] == ["Information Technology"]
    it = got[0]
    assert len(it.points) == TAIL_WEEKS and it.points[-1].day == days[-1]
    assert it.points[-1].ratio > 100 and it.quadrant in ("leading", "weakening")
