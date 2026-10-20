"""Agent step A2b: what_if_score recomputes stored scores exactly and compares rankings
(SCORING_SPECIFICATION 1-6)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vcp_scanner.agent import whatif as wi

BASE = {"TREND": 25.0, "PATTERN": 35.0, "VOLUME": 15.0, "RS": 15.0}
DAY = date(2023, 3, 1)


def _setup(trend: float, pattern: float, volume: float | None, rs: float, ret: float,
           day: date = DAY, iid: str = "A") -> wi.Scored:  # fmt: skip
    s = wi.Scored(iid, day, None, ret)
    s.subs["TREND"]["high_proximity"] = (5.0, trend, 40.0)
    s.subs["TREND"]["sma200_slope"] = (4.0, trend, 30.0)
    s.subs["PATTERN"]["tightening"] = (0.8, pattern, 20.0)
    s.subs["VOLUME"]["dryup_quality"] = (None if volume is None else 0.5, volume, 50.0)
    s.subs["RS"]["rs_rank"] = (90.0, rs, 100.0)
    s.stored_final = wi.recompute(s, BASE, None)
    return s


def test_parse_change_validates_names_and_values() -> None:
    ch = wi.parse_change({"RS": 30}, {"volume.dryup_quality": 60},
                         {"trend.high_proximity": {"worst": 30, "best": 0}})  # fmt: skip
    assert ch.weights == {"RS": 30.0} and ch.sub_weights == {("VOLUME", "dryup_quality"): 60.0}
    assert ch.bounds == {("TREND", "high_proximity"): (30.0, 0.0)}
    assert wi.parse_change(None, {"vcp.tightening": 5}, None).sub_weights == {
        ("PATTERN", "tightening"): 5.0}  # fmt: skip
    for args, msg in (((None, None, None), "no change"), (({"speed": 1}, None, None), "keys"),
                      ((None, {"tightening": 1}, None), "component.sub_component"),
                      ((None, None, {"trend.x": {"worst": 1}}), "worst"),
                      ((None, None, {"trend.x": {"worst": 1, "best": 1}}), "differ"),
                      (({"rs": -1}, None, None), ">= 0")):  # fmt: skip
        with pytest.raises(ValueError, match=msg):
            wi.parse_change(*args)  # type: ignore[arg-type]


def test_recompute_matches_the_scorer_and_drops_null_components() -> None:
    s = _setup(80.0, 60.0, None, 50.0, 1.0)
    # volume is NULL: final over trend, pattern, rs = (25x80 + 35x60 + 15x50) / 75
    assert s.stored_final == pytest.approx((25 * 80 + 35 * 60 + 15 * 50) / 75)
    more_rs = wi.recompute(s, BASE, wi.parse_change({"rs": 75}, None, None))
    assert more_rs == pytest.approx((25 * 80 + 35 * 60 + 75 * 50) / 135)
    # new bounds re-normalize from the raw value: high_proximity raw 5, worst 10 best 0 -> 50
    nb = wi.recompute(s, BASE, wi.parse_change(None, None,
                                               {"trend.high_proximity": {"worst": 10,
                                                                         "best": 0}}))  # fmt: skip
    trend = (50 * 40 + 80 * 30) / 70
    assert nb == pytest.approx((25 * trend + 35 * 60 + 15 * 50) / 75)


def _days(n_days: int = 6) -> list[wi.Scored]:
    rows = []
    for d in range(n_days):
        day = DAY + timedelta(days=d)
        for k in range(10):  # RS orders outcomes perfectly, trend inversely
            rows.append(_setup(trend=100 - 10 * k, pattern=50.0, volume=50.0, rs=10.0 * k,
                               ret=float(k), day=day, iid=f"S{k}"))  # fmt: skip
    return rows


def test_compare_reports_a_better_ranking_and_how_much_changed() -> None:
    res = wi.compare(_days(), BASE, wi.parse_change({"rs": 100, "trend": 0}, None, None), 3)
    assert res["baseline_check"]["share"] == 1.0
    assert res["what_if"]["ic_mean"] == pytest.approx(1.0)
    assert res["baseline"]["ic_mean"] < res["what_if"]["ic_mean"]
    assert res["what_if"]["top_3_per_day_mean_pct"] == pytest.approx(8.0)  # S9, S8, S7
    assert res["ranking_change"]["top_3_kept_share"] < 1.0


def test_a_baseline_that_does_not_reproduce_is_refused() -> None:
    rows = _days()
    for s in rows:
        s.stored_final = (s.stored_final or 0) + 1.0  # stored scores from another scoring
    with pytest.raises(ValueError, match="baseline check failed"):
        wi.compare(rows, BASE, wi.parse_change({"rs": 30}, None, None))
