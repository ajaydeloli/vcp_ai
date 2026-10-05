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
