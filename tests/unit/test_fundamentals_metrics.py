"""Fundamentals F3: metrics, point-in-time selection, data quality and hard-gate flags."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from vcp_scanner.fundamentals.metrics import (
    AVAILABLE,
    BASIS_CHANGE,
    IST,
    MISSING,
    NEGATIVE_BASE,
    NOT_APPLICABLE,
    ShareAction,
    SnapshotData,
    compute_view,
    cutoff_for,
    month_end_shift,
    share_factor,
    usable,
)


def q(
    end: date,
    eps: float,
    revenue: float = 1000.0,
    *,
    basis: str = "CONSOLIDATED",
    revision: int = 0,
    broadcast: datetime | None = None,
    status: str = "OK",
    extra: dict[str, float] | None = None,
    instant: dict[str, float] | None = None,
    kind: str = "QUARTER",
) -> SnapshotData:
    """A quarter broadcast 20 days after its end at 18:00 IST (evening, after the close)."""
    when = broadcast or datetime.combine(end + timedelta(days=20), datetime.min.time(), IST)
    when = when.replace(hour=18) if broadcast is None else when
    items = {"eps": eps, "revenue": revenue, "pbt": 150.0, "finance_costs": 20.0,
             "depreciation": 30.0, "other_income": 0.0, "profit_owners": eps * 10}  # fmt: skip
    items |= extra or {}
    return SnapshotData(
        f"{end}-{basis}-{revision}-{kind}", end, kind, basis, revision, when, status,
        {"QUARTER": items, "INSTANT": instant or {}},
    )  # fmt: skip


ENDS = [month_end_shift(date(2023, 6, 30), 3 * i) for i in range(6)]  # Jun23 .. Sep24


def series(eps: list[float], revenue: list[float] | None = None, **kw: Any) -> list[SnapshotData]:
    revenue = revenue or [1000.0] * len(eps)
    return [q(e, x, r, **kw) for e, x, r in zip(ENDS, eps, revenue, strict=False)]


# --- helpers ------------------------------------------------------------------------------------


def test_month_end_shift() -> None:
    assert month_end_shift(date(2024, 3, 31), -3) == date(2023, 12, 31)
    assert month_end_shift(date(2024, 6, 30), -12) == date(2023, 6, 30)
    assert month_end_shift(date(2023, 12, 31), 2) == date(2024, 2, 29)


def test_cutoff_is_the_close_in_ist() -> None:
    assert cutoff_for(date(2025, 1, 16)).isoformat() == "2025-01-16T15:30:00+05:30"


def test_evening_filing_is_first_used_the_next_day() -> None:
    snaps = series([10, 11, 12, 13, 14, 15])
    filed = snaps[-1].available_at.date()  # Sep 2024 quarter, filed in the evening
    assert compute_view(snaps, filed).period_end == ENDS[-2]
    assert compute_view(snaps, filed + timedelta(days=1)).period_end == ENDS[-1]


# --- metrics ------------------------------------------------------------------------------------


def test_growth_metrics_known_answers() -> None:
    # EPS 10, 11, 12, 13 | 15, 18 ; revenue 100 .. 150
    snaps = series([10, 11, 12, 13, 15, 18], [100, 110, 120, 130, 140, 150])
    v = compute_view(snaps, date(2024, 12, 31))
    assert v.period_end == date(2024, 9, 30)
    assert v.value("eps_yoy") == pytest.approx((18 - 11) / 11 * 100)
    assert v.value("eps_qoq") == pytest.approx((18 - 15) / 15 * 100)
    assert v.value("sales_yoy") == pytest.approx((150 - 110) / 110 * 100)
    prev_yoy = (15 - 10) / 10 * 100
    assert v.value("eps_acceleration") == pytest.approx(v.value("eps_yoy") - prev_yoy)
    # Operating margin (150 + 20 + 30 - 0) / revenue: 200/150 now, 200/110 a year ago.
    assert v.value("margin_expansion") == pytest.approx(200 / 150 * 100 - 200 / 110 * 100)
    assert v.ttm_eps == pytest.approx(12 + 13 + 15 + 18)
    assert v.quarters_available == 6


def test_negative_base_and_missing() -> None:
    snaps = series([10, -2, 12, 13, 15, 18])
    v = compute_view(snaps, date(2024, 12, 31))
    assert v.metrics["eps_yoy"].status == NEGATIVE_BASE and v.value("eps_yoy") is None
    few = series([10, 11])
    w = compute_view(few, date(2023, 12, 31))
    assert w.metrics["eps_yoy"].status == MISSING and w.value("eps_qoq") is not None


def test_bonus_puts_earlier_eps_on_the_new_share_basis() -> None:
    # A 1:1 bonus goes ex between the Jun 2024 and Sep 2024 filings: EPS halves as filed.
    snaps = series([20, 22, 24, 26, 28, 12])
    bonus = [ShareAction(date(2024, 10, 1), 0.5)]
    v = compute_view(snaps, date(2024, 12, 31), bonus)
    assert v.value("eps_qoq") == pytest.approx((12 - 14) / 14 * 100)
    assert v.value("eps_yoy") == pytest.approx((12 - 11) / 11 * 100)
    assert v.ttm_eps == pytest.approx(12 + 13 + 14 + 12)
    without = compute_view(snaps, date(2024, 12, 31))
    assert without.value("eps_qoq") == pytest.approx((12 - 28) / 28 * 100)


def test_share_factor_uses_broadcast_dates() -> None:
    a = [ShareAction(date(2024, 10, 28), 0.5)]
    early = datetime(2024, 10, 14, 18, tzinfo=IST)
    late = datetime(2025, 1, 16, 20, tzinfo=IST)
    assert share_factor(a, early, late) == 0.5
    assert share_factor(a, late, late) == 1.0


def test_basis_policy_and_basis_change() -> None:
    con = series([10, 11, 12, 13, 15, 18])
    v = compute_view(con + series([1, 1, 1, 1, 1, 1], basis="STANDALONE"), date(2024, 12, 31))
    assert v.basis == "CONSOLIDATED" and v.eps == 18
    # The latest quarter only in standalone; a year earlier only in consolidated.
    mixed = con[:2] + [q(ENDS[5], 5.0, basis="STANDALONE")]
    w = compute_view(mixed, date(2024, 12, 31))
    assert w.basis == "STANDALONE"
    assert w.metrics["eps_yoy"].status == BASIS_CHANGE


def test_financial_company_has_no_operating_margin() -> None:
    snaps = series([10, 11, 12, 13, 15, 18], instant={"is_financial": 1.0})
    v = compute_view(snaps, date(2024, 12, 31))
    assert v.metrics["margin_expansion"].status == NOT_APPLICABLE
    assert v.operating_margin is None


def test_roe_and_debt_to_equity_from_balance_sheets() -> None:
    snaps = series([10, 11, 12, 13, 15, 18])
    # Balance sheets at Mar 2024 and Sep 2024 (in the quarter snapshots of those dates).
    snaps[3] = q(ENDS[3], 13, instant={"equity": 1000.0, "borrowings": 400.0})
    snaps[5] = q(ENDS[5], 18, instant={"equity": 1200.0, "borrowings": 300.0})
    v = compute_view(snaps, date(2024, 12, 31))
    profits = (12 + 13 + 15 + 18) * 10
    assert v.value("roe") == pytest.approx(profits / 1100 * 100)
    assert v.value("debt_to_equity") == pytest.approx(300 / 1200)
    filed_only = series([10, 11, 12, 13, 15, 18], instant={"debt_equity_ratio": 0.7})
    assert compute_view(filed_only, date(2024, 12, 31)).value("debt_to_equity") == 0.7


# --- point in time, revisions, quality ----------------------------------------------------------


def test_restatement_is_used_only_after_it_was_filed() -> None:
    snaps = series([10, 11, 12, 13, 15, 18])
    later = datetime(2025, 2, 1, 18, tzinfo=IST)
    restated = q(ENDS[5], 9.0, revision=1, broadcast=later)
    before = compute_view(snaps, date(2025, 1, 31))
    after = compute_view([*snaps, restated], date(2025, 2, 2))
    assert before.eps == 18 and after.eps == 9 and after.restated
    # A recompute of the past date with the later filing present gives the same answer.
    again = compute_view([*snaps, restated], date(2025, 1, 31))
    assert again == before


def test_invalid_snapshots_are_ignored() -> None:
    snaps = series([10, 11, 12, 13, 15, 18])
    bad = q(ENDS[5], 999.0, revision=1, status="INVALID",
            broadcast=datetime(2024, 10, 25, 9, tzinfo=IST))  # fmt: skip
    assert compute_view([*snaps, bad], date(2024, 12, 31)).eps == 18
    assert len(usable([*snaps, bad], cutoff_for(date(2024, 12, 31)))) == 6


def test_availability_staleness_and_gates() -> None:
    v = compute_view(series([10, 11, 12, 13, 15, 18]), date(2024, 12, 31))
    assert v.availability_score == pytest.approx(5 / 7)  # no balance sheet: roe, d/e missing
    assert v.hard_gate_pass and not v.stale
    stale = compute_view(series([10, 11, 12, 13, 15, 18]), date(2025, 3, 1))
    assert stale.stale  # Sep 2024 is the latest quarter, more than 120 days back
    neg = compute_view(series([10, 11, -12, -13, -15, -18]), date(2024, 12, 31))
    assert not neg.hard_gate_pass and "NEGATIVE_TTM_EPS" in (neg.gate_reason or "")
    thin = compute_view(series([10, 11]), date(2023, 12, 31))
    assert not thin.hard_gate_pass and thin.gate_reason == "INSUFFICIENT_DATA"
    none = compute_view([], date(2024, 12, 31))
    assert none.gate_reason == "NO_DATA" and none.metrics["eps_yoy"].status == MISSING


def test_missing_is_never_zero() -> None:
    v = compute_view(series([10, 11]), date(2023, 12, 31))
    for name, m in v.metrics.items():
        assert (m.value is None) == (m.status != AVAILABLE), name


# --- real filings end to end ------------------------------------------------------------------


def test_reliance_q3_fy25_eps_growth_across_the_bonus(tmp_path: Path) -> None:
    """Known answer: EPS 13.70 against 25.52 a year earlier, 1:1 bonus (ex 2024-10-28) in
    between, so growth is 13.70 / 12.76 - 1 = +7.37 %."""
    from tests.unit.test_fundamentals_parse import load, old_ref, store_filings
    from vcp_scanner.data.repositories.duckdb_fundamental_repository import (
        DuckDBFundamentalRepository,
    )
    from vcp_scanner.data.storage.duckdb_store import DuckDBStore
    from vcp_scanner.fundamentals.snapshots import parse_filings

    store = DuckDBStore(":memory:")
    store.migrate()
    repo = DuckDBFundamentalRepository(store)
    store_filings(repo, tmp_path, [
        (old_ref("1", "31-Dec-2023", "Quarterly", "Consolidated", "19-Jan-2024 19:17:54"),
         load("reliance_q3fy24_con")),
        (old_ref("2", "31-Dec-2024", "Quarterly", "Consolidated", "16-Jan-2025 20:15:20"),
         load("reliance_q3fy25_con")),
    ])  # fmt: skip
    assert parse_filings(repo, tmp_path).parsed == 2
    snaps = repo.snapshots_for("INS1")
    bonus = [ShareAction(date(2024, 10, 28), 0.5)]
    v = compute_view(snaps, date(2025, 1, 17), bonus)
    assert v.eps == 13.70
    assert v.value("eps_yoy") == pytest.approx((13.70 - 12.76) / 12.76 * 100)
    assert round(v.value("eps_yoy") or 0, 2) == 7.37
    assert compute_view(snaps, date(2025, 1, 16), bonus).period_end == date(2023, 12, 31)
