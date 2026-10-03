"""Score outcome study (research/score_study.py; Phase 7 step 5)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vcp_scanner.research.outcomes import Outcome, WindowRow
from vcp_scanner.research.score_study import (
    format_study,
    information_coefficient,
    quintiles,
    spearman,
    standard_keys,
)

D0 = date(2024, 1, 31)


def _row(d: date, k: int, score: float | None, ret: float, trade: float | None = None) -> WindowRow:
    o = Outcome(ret / 3, ret, max(ret, 0.0), min(ret, 0.0), "WIN" if ret > 10 else "LOSS", None,
                trade)  # fmt: skip
    return WindowRow(d, f"I{k}", "S", "VCP", "FORMING", o, score, True,
                     {"TREND": score, "VCP": None})  # fmt: skip


def test_spearman() -> None:
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2], [1, 2]) is None  # too few
    assert spearman([1, 1, 1], [1, 2, 3]) is None  # no spread


def test_quintiles_are_within_each_date() -> None:
    # Two dates of ten windows: in a good month every return is high, in a bad one low; within
    # each date the return rises with the score.
    rows = []
    for j, base in enumerate((50.0, -20.0)):
        d = D0 + timedelta(days=30 * j)
        rows += [_row(d, 10 * j + k, float(k), base + k, trade=float(k)) for k in range(10)]
    qs = quintiles(rows, lambda r: r.score)
    assert [q.bucket for q in qs] == [1, 2, 3, 4, 5]
    assert all(q.n == 4 for q in qs)  # 2 per date x 2 dates: the regime is balanced
    assert qs[0].median_ret_60 < qs[-1].median_ret_60  # type: ignore[operator]
    assert qs[-1].trade_avg == pytest.approx((8 + 9 + 8 + 9) / 4)
    ic = information_coefficient(rows, lambda r: r.score)
    assert ic.mean == pytest.approx(1.0) and ic.dates == 2


def test_missing_keys_and_censored_windows_are_left_out() -> None:
    rows = [_row(D0, k, float(k), float(k)) for k in range(12)]
    rows.append(_row(D0, 99, None, 5.0))
    rows.append(WindowRow(D0, "C", "S", "VCP", None, None, 50.0))  # censored
    assert sum(q.n for q in quintiles(rows, lambda r: r.score)) == 12
    assert information_coefficient(rows, lambda r: r.components.get("VCP")).dates == 0
    few = [_row(D0, k, float(k), float(k)) for k in range(5)]  # under MIN_PER_DATE
    assert information_coefficient(few, lambda r: r.score).mean is None
    text = format_study(rows, standard_keys(), "t")
    assert "final score: IC" in text and "vcp: IC -" in text and "Q5" in text
