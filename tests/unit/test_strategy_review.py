"""Chart-review sampling for the new strategies (decision C1; synthetic rows)."""

from __future__ import annotations

from datetime import date

from vcp_scanner.research.strategy_review import QUOTAS, select, stratum


def test_stratum_by_grade_and_near_miss() -> None:
    assert stratum(3, {}, "FLAT_BASE") == "GRADE_3"
    assert stratum(2, {}, "FLAT_BASE") == "GRADE_2"
    assert stratum(1, {"FLAT_BASE": ["max_depth_pct"]}, "FLAT_BASE") == "NEAR_MISS"
    assert stratum(1, {"FLAT_BASE": ["max_depth_pct", "trend_template_and_stage2"]},
                   "FLAT_BASE") == "NEAR_MISS"  # fmt: skip
    assert stratum(1, {"FLAT_BASE": ["a", "b"]}, "FLAT_BASE") == "GRADE_1"


def test_select_meets_quotas_spreads_years_one_window_per_stock() -> None:
    rows = []
    for y in (2022, 2023, 2024):
        for k in range(30):
            grade = 1 + k % 3
            rows.append({"instrument_id": f"I{y}{k}", "as_of_date": date(y, 3, 1), "grade": grade,
                         "unmet_rules": {"G2": ["x"]} if grade == 1 and k % 2 else {}})  # fmt: skip
    rows.append({**rows[0], "as_of_date": date(2023, 5, 1)})  # same stock again
    chosen = select(rows, "G2", seed=1)
    assert select(rows, "G2", seed=1) == chosen  # deterministic
    counts = {s: sum(1 for c in chosen if c["stratum"] == s) for s in QUOTAS}
    assert counts == QUOTAS
    assert len({c["instrument_id"] for c in chosen}) == len(chosen)
    years = {c["as_of_date"].year for c in chosen if c["stratum"] == "GRADE_2"}
    assert years == {2022, 2023, 2024}


class _FakeStore:
    """Stands in for DuckDBStore: ``conn.execute(sql, params).fetchall()`` returns the bars."""

    def __init__(self, bars: list[tuple]) -> None:
        self.conn = self
        self._bars = bars

    def execute(self, sql: str, params: list) -> _FakeStore:
        self._p = params
        return self

    def fetchall(self) -> list[tuple]:
        _, _, end, start = self._p
        return [b for b in self._bars if start < b[0] <= end]


def test_long_base_chart_and_point_marks() -> None:
    from datetime import timedelta

    from vcp_scanner.research.strategy_review import CHART_BARS, LEAD_BARS, build_windows

    days = [date(2021, 1, 4) + timedelta(days=i) for i in range(900)]
    days = [d for d in days if d.weekday() < 5]
    bars = [(d, 100.0 + i, 90.0 + i, 95.0 + i, 1000.0) for i, d in enumerate(days)]
    as_of, lip = days[-1], days[-1 - 330]  # a cup about 66 weeks long
    setup = {"instrument_id": "X", "as_of_date": as_of, "base_start_date": lip,
             "base_end_date": None, "pivot_price": 500.0, "stop_reference_price": 480.0,
             "classification": "CUP_HANDLE", "grade": 2, "status": "FORMING",
             "base_depth_pct": 20.0, "pivot_distance_pct": 1.0, "unmet_rules": {},
             "symbol": "X", "stratum": "GRADE_2",
             "details": {"left_lip_date": lip.isoformat(), "bottom_date": days[-100].isoformat(),
                         "right_lip_date": days[-10].isoformat(),
                         "handle_low_date": days[-4].isoformat(), "handle_depth_pct": 5.0,
                         "cup_weeks": 64, "handle_sessions": 10, "bottom_share": 0.2}}  # fmt: skip
    (w,) = build_windows(_FakeStore(bars), [setup], "CUP_HANDLE", "cup_handle")  # type: ignore[arg-type]
    assert len(w["d"]) == 331 + LEAD_BARS > CHART_BARS  # the whole cup and its lead-in
    m = w["m"]
    assert m["bs"] == LEAD_BARS
    assert [p["k"] for p in m["pts"]] == ["L", "B", "R", "H"]
    left = m["pts"][0]
    assert left["i"] == LEAD_BARS and left["y"] == w["h"][LEAD_BARS] and left["up"] is True
    assert m["pts"][3]["y"] == w["l"][-4]
    assert "handle 5.0% deep" in m["txt"]
