"""F7: the shadow score (SCORING_SPECIFICATION §7 as written) and the study's statistics
(FUNDAMENTALS_SPECIFICATION §12). Known answers, point in time, no lookahead."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.api.fixture import put
from vcp_scanner.config.models import FundamentalScoreComponents
from vcp_scanner.data.storage.duckdb_store import DuckDBStore
from vcp_scanner.fundamentals.research import (
    score_version,
    scores_as_of,
    shadow_score,
    store_scores,
)
from vcp_scanner.research.fundamental_study import (
    StudyRow,
    build_report,
    flag_within_date,
    information_coefficient,
    key_of,
    one_per_stock_month,
    quintiles,
    top_minus_bottom,
)

COMPS = FundamentalScoreComponents()
FULL = {"eps_yoy": 50.0, "eps_qoq": 25.0, "sales_yoy": 15.0, "eps_acceleration": 15.0,
        "margin_expansion": 0.0, "roe": 25.0, "debt": 0.5}  # fmt: skip


def test_shadow_score_known_answer() -> None:
    s = shadow_score(FULL, COMPS, 0.5)
    # 30×100 + 20×50 + 20×50 + 10×100 + 10×0 + 5×100 + 5×50 = 6750 over 100 weight
    assert s.score == pytest.approx(67.5) and s.availability == 1.0
    assert s.parts["debt"] == pytest.approx(50.0)  # 1.0 -> 0 bounds: 0.5 is half way


def test_missing_metrics_renormalise_and_too_few_give_none() -> None:
    s = shadow_score({"eps_yoy": 50.0, "eps_qoq": 0.0, "sales_yoy": 30.0, "roe": 10.0}, COMPS, 0.5)
    # (30×100 + 20×0 + 20×100 + 5×0) / 75
    assert s.score == pytest.approx(5000 / 75) and s.availability == pytest.approx(4 / 7)
    thin = shadow_score({"eps_yoy": 50.0, "roe": 20.0, "debt": 0.1}, COMPS, 0.5)
    assert thin.score is None and thin.availability == pytest.approx(3 / 7)
    assert shadow_score({}, COMPS, 0.5).score is None


def test_values_are_clipped_to_the_bounds() -> None:
    hi = shadow_score({**FULL, "eps_yoy": 900.0, "debt": -1.0}, COMPS, 0.5)
    lo = shadow_score({**FULL, "eps_yoy": -80.0}, COMPS, 0.5)
    assert hi.parts["eps_yoy"] == 100.0 and hi.parts["debt"] == 100.0
    assert lo.parts["eps_yoy"] == 0.0


def test_version_changes_with_a_weight_or_the_floor() -> None:
    v = score_version(COMPS, 0.5)
    assert v.startswith("shadow-1-") and v == score_version(FundamentalScoreComponents(), 0.5)
    assert v != score_version(COMPS, 0.6)


@pytest.fixture
def conn() -> Any:
    store = DuckDBStore(":memory:")
    store.migrate()
    c = store.conn
    for day, eps_yoy in ((date(2024, 1, 20), 50.0), (date(2024, 4, 20), 10.0)):
        put(c, "fundamental_metrics", instrument_id="X", as_of_date=day, period_end=day,
            eps_yoy=eps_yoy, eps_qoq=25.0, revenue_yoy=15.0, eps_acceleration=15.0,
            margin_expansion=0.0, roe=25.0, debt_to_equity=0.5)  # fmt: skip
        put(c, "fundamental_data_quality", instrument_id="X", as_of_date=day,
            fundamental_hard_gate_pass=True)  # fmt: skip
    return c


def test_scores_use_only_the_view_stored_by_that_day(conn: Any) -> None:
    pairs = [("X", date(2024, 1, 19)), ("X", date(2024, 1, 20)), ("X", date(2024, 4, 19)),
             ("X", date(2024, 4, 20)), ("Y", date(2024, 5, 1))]  # fmt: skip
    got = {(p.instrument_id, p.day): p for p in scores_as_of(conn, pairs, COMPS, 0.5)}
    assert got[("X", date(2024, 1, 19))].view_date is None  # nothing filed yet
    assert got[("X", date(2024, 1, 19))].shadow.score is None
    assert got[("X", date(2024, 1, 20))].shadow.score == pytest.approx(67.5)
    assert got[("X", date(2024, 4, 19))].metrics["eps_yoy"] == 50.0  # not the April view yet
    assert got[("X", date(2024, 4, 20))].metrics["eps_yoy"] == 10.0
    assert got[("X", date(2024, 4, 20))].gate_pass is True
    assert got[("Y", date(2024, 5, 1))].view_date is None


def test_store_replaces_the_same_version(conn: Any) -> None:
    rows = scores_as_of(conn, [("X", date(2024, 2, 1))], COMPS, 0.5)
    assert store_scores(conn, rows, "v") == 1 and store_scores(conn, rows, "v") == 1
    got = conn.execute("SELECT count(*), max(score) FROM fundamental_research_scores").fetchone()
    assert got[0] == 1 and got[1] == pytest.approx(67.5)


def row(day: date, iid: str, key: float | None, ret: float, strategy: str = "vcp") -> StudyRow:
    return StudyRow(strategy, iid, day, {h: ret for h in (5, 10, 20, 40, 60)}, ret > 0,
                    ret < 0, {"k": key})  # fmt: skip


def test_quintiles_rank_within_each_date() -> None:
    d1, d2 = date(2024, 1, 5), date(2024, 1, 12)
    # on d1 every return is high, on d2 low: the bucket order still follows the key per date
    rows = [row(d1, f"A{i}", float(i), 10.0 + i) for i in range(10)]
    rows += [row(d2, f"B{i}", float(i), -10.0 + i) for i in range(10)]
    qs = quintiles(rows, key_of("k"))
    assert [b.n for b in qs] == [4, 4, 4, 4, 4]
    assert [b.mean[60] for b in qs] == [0.5, 2.5, 4.5, 6.5, 8.5]


def test_information_coefficient_and_dates_with_too_few_setups() -> None:
    d1, d2, d3 = date(2024, 1, 5), date(2024, 1, 12), date(2024, 1, 19)
    rows = [row(d1, f"A{i}", float(i), float(i)) for i in range(10)]  # rho = +1
    rows += [row(d2, f"B{i}", float(i), -float(i)) for i in range(10)]  # rho = -1
    rows += [row(d3, f"C{i}", float(i), float(i)) for i in range(5)]  # too few: skipped
    ic = information_coefficient(rows, key_of("k"), 60)
    assert ic.dates == 2 and ic.mean == pytest.approx(0.0)
    up = information_coefficient(rows[:10], key_of("k"), 60)
    assert up.mean == pytest.approx(1.0) and up.dates == 1 and up.t_stat is None


def test_missing_keys_are_left_out_not_zero() -> None:
    d = date(2024, 1, 5)
    rows = [row(d, f"A{i}", float(i), float(i)) for i in range(10)]
    rows += [row(d, f"N{i}", None, -50.0) for i in range(10)]
    assert information_coefficient(rows, key_of("k"), 60).mean == pytest.approx(1.0)
    assert sum(b.n for b in quintiles(rows, key_of("k"))) == 10


def test_one_setup_per_stock_and_month() -> None:
    rows = [row(date(2024, 1, d), "A", 1.0, 1.0) for d in (5, 12, 19)]
    rows += [row(date(2024, 2, 2), "A", 1.0, 1.0), row(date(2024, 1, 5), "A", 1.0, 1.0, "flat")]
    assert [(r.strategy, r.day) for r in one_per_stock_month(rows)] == [
        ("vcp", date(2024, 1, 5)), ("flat", date(2024, 1, 5)), ("vcp", date(2024, 2, 2)),
    ]  # fmt: skip


def test_report_renders() -> None:
    d = date(2024, 1, 5)
    rows = [row(d, f"A{i}", float(i), float(i)) for i in range(12)]
    for r in rows:
        r.keys.update(shadow_score=r.keys["k"], availability=1.0, gate_pass=1.0)
    text = build_report(rows, "development", date(2022, 2, 1), date(2024, 6, 30), "shadow-1-x",
                        {"vcp": "abcdef0123"})  # fmt: skip
    assert "# F7 fundamentals study: development period" in text and "vcp abcdef01" in text
    assert "| Q5 |" in text and "Coverage: 100 % of 12 setups." in text


def test_top_minus_bottom_and_flag_spreads_are_within_date() -> None:
    d1, d2 = date(2024, 1, 5), date(2024, 1, 12)
    # every return on d1 is 100 higher than on d2: a pooled comparison would be swamped by it
    rows = [row(d1, f"A{i}", float(i), 100.0 + i) for i in range(10)]
    rows += [row(d2, f"B{i}", float(i), float(i)) for i in range(10)]
    sp = top_minus_bottom(rows, key_of("k"), 60)
    assert sp.dates == 2 and sp.mean == pytest.approx(8.0)  # top 2 (8, 9) minus bottom 2 (0, 1)
    for r in rows:
        r.keys["g"] = 1.0 if r.keys["k"] >= 5 else 0.0
    fl = flag_within_date(rows, "g", 60)
    assert fl.dates == 2 and fl.mean == pytest.approx(5.0)  # (5..9) mean 7 minus (0..4) mean 2
