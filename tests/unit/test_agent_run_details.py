"""Chartable figures of the lab's runs for the AI Agent page (owner, 2026-10-22)."""

from __future__ import annotations

import json
from datetime import date

from vcp_scanner.api.agent import _details, review

WHAT_IF = {
    "baseline": {"days_ranked": 125, "setups_with_outcome": 6685, "ic_mean": -0.002, "ic_t": -0.14,
                 "top_minus_bottom_fifth_pct": 0.28, "top_5_per_day_mean_pct": 9.01,
                 "all_mean_pct": 9.84},
    "what_if": {"days_ranked": 125, "setups_with_outcome": 6685, "ic_mean": -0.017, "ic_t": -1.14,
                "top_minus_bottom_fifth_pct": -0.76, "top_5_per_day_mean_pct": 7.66,
                "all_mean_pct": 9.84},
    "ranking_change": {"score_rank_correlation": 0.818, "top_5_kept_share": 0.563},
}  # fmt: skip


def test_a_what_if_run_gives_both_scores_and_its_change_in_words() -> None:
    params = {
        "weights": {"pattern": 60.0, "rs": 0.0},
        "sub_weights": None,
        "horizon": 60,
        "top_n": 5,
    }
    got = _details("what_if", json.dumps(params), json.dumps(WHAT_IF))
    assert got["change"] == "pattern 60, rs 0"
    w = got["what_if"]
    assert (w.ic, w.baseline_ic, w.top_per_day_pct, w.baseline_top_per_day_pct) == (
        -0.017,
        -0.002,
        7.66,
        9.01,
    )
    assert (w.all_mean_pct, w.top_kept_share, w.horizon, w.top_n, w.days) == (
        9.84,
        0.563,
        60,
        5,
        125,
    )


def test_a_study_run_gives_its_groups() -> None:
    res = {"overall": {"n": 100, "mean_ret_pct": 4.0, "win_rate": 0.55},
           "groups": [{"group": "A_PLUS_VCP", "n": 8, "mean_ret_pct": 7.5, "win_rate": 0.6,
                       "small_sample": True}]}  # fmt: skip
    got = _details("study", "{}", json.dumps(res))
    assert got["study_overall"].group == "All" and got["study_overall"].n == 100
    assert [(g.group, g.mean_ret_pct, g.small_sample) for g in got["study_groups"]] == [
        ("A_PLUS_VCP", 7.5, True)
    ]


def test_a_backtest_run_links_its_backtest_and_odd_results_give_nothing() -> None:
    assert _details("backtest", None, json.dumps({"backtest_id": "bt-1", "lines": []})) == {
        "backtest_id": "bt-1"
    }
    assert _details("what_if", None, "not json") == {}
    assert _details("study", None, None) == {}


def test_the_review_countdown() -> None:
    r = review(date(2026, 10, 22))
    assert (r.cycle, r.review_from, r.days_to_review) == ("2027-04", date(2027, 4, 1), 161)
    assert (r.max_per_strategy, r.promotion_min_trades, r.promotion_min_months) == (3, 30, 3)
    assert review(date(2027, 5, 1)).days_to_review == 0
