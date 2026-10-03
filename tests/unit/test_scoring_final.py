"""Final setup score and ranking percentile (SCORING_SPECIFICATION 1, 10; Phase 7 step 2)."""

from __future__ import annotations

import pytest

from vcp_scanner.config.models import ScoringWeights
from vcp_scanner.scoring.components import ComponentScore
from vcp_scanner.scoring.final import final_score, ranking_percentiles

W = ScoringWeights()


def _c(name: str, score: float | None) -> ComponentScore:
    return ComponentScore(name, score, ())


def test_all_components_weighted_average() -> None:
    f = final_score([_c("TREND", 80), _c("VCP", 60), _c("VOLUME", 40), _c("RS", 100),
                     _c("FUNDAMENTAL", 50)], W)  # fmt: skip
    assert f.final == pytest.approx((25 * 80 + 35 * 60 + 15 * 40 + 15 * 100 + 10 * 50) / 100)
    assert f.fundamental_available and not f.weights_renormalized and f.flags == ()
    assert f.effective_weights["VCP"] == pytest.approx(35.0)


def test_null_fundamentals_renormalize_and_are_flagged() -> None:
    comps = [_c("TREND", 80), _c("VCP", 60), _c("VOLUME", 40), _c("RS", 100)]
    f = final_score([*comps, _c("FUNDAMENTAL", None)], W)
    expected = (25 * 80 + 35 * 60 + 15 * 40 + 15 * 100) / 90
    assert f.final == pytest.approx(expected)
    assert f.weights_renormalized and not f.fundamental_available
    assert f.flags == ("FUNDAMENTALS_UNAVAILABLE",)
    assert f.effective_weights["FUNDAMENTAL"] == 0.0
    assert sum(f.effective_weights.values()) == pytest.approx(100.0)
    assert f.effective_weights["VCP"] == pytest.approx(35 / 90 * 100)
    # A component that is simply absent counts as NULL too; never as 0.
    assert final_score(comps, W).final == pytest.approx(expected)
    # Fundamentals of 0 are a real score, not a missing one.
    zero = final_score([*comps, _c("FUNDAMENTAL", 0.0)], W)
    assert zero.final == pytest.approx((25 * 80 + 35 * 60 + 15 * 40 + 15 * 100) / 100)


def test_other_null_components_and_nothing_available() -> None:
    f = final_score([_c("TREND", 50), _c("VCP", None), _c("VOLUME", None), _c("RS", 70)], W)
    assert f.final == pytest.approx((25 * 50 + 15 * 70) / 40)
    assert set(f.flags) == {"VCP_UNAVAILABLE", "VOLUME_UNAVAILABLE", "FUNDAMENTALS_UNAVAILABLE"}
    none = final_score([], W)
    assert none.final is None and none.weights_renormalized
    assert all(w == 0.0 for w in none.effective_weights.values())


def test_ranking_percentile_per_confirmation_state() -> None:
    items = [
        ("a", "CONFIRMED", 90.0), ("b", "CONFIRMED", 70.0), ("c", "CONFIRMED", 70.0),
        ("d", "CONFIRMED", 50.0), ("e", "PROVISIONAL", 10.0), ("f", "CONFIRMED", None),
    ]  # fmt: skip
    p = ranking_percentiles(items)
    assert p["a"] == 100.0 and p["d"] == 0.0
    assert p["b"] == p["c"] == pytest.approx(100 * (1 + 0.5) / 3)  # tie shares a value
    assert p["e"] == 100.0  # its own state, a group of one
    assert p["f"] is None
    assert ranking_percentiles([]) == {}
