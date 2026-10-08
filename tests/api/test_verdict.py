"""The Market health verdict: straight-line scores per reading and the 0-100 total."""

from __future__ import annotations

from vcp_scanner.api import health
from vcp_scanner.api import models as m


def _item(id_: str, status: str, score: float | None) -> m.HealthItem:
    return m.HealthItem(
        id=id_, label=id_, status=status, text="", value=None, score=score, short=""
    )


def _groups(*items: m.HealthItem) -> list[m.HealthGroup]:
    return [m.HealthGroup(id="g", title="g", items=list(items))]


def test_ramp_is_a_straight_line_held_flat_at_both_ends() -> None:
    assert health._ramp(5, 8, 3) == 60.0  # distribution days: 3 or fewer is 100, 8 or more is 0
    assert health._ramp(6, 8, 3) == 40.0 and health._ramp(2, 8, 3) == 100.0
    assert health._ramp(9, 8, 3) == 0.0
    assert health._ramp(40, 20, 60) == 50.0  # breadth: 20% is 0, 60% is 100


def test_verdict_is_the_average_of_the_scored_readings_and_skips_grey() -> None:
    v = health._verdict(
        _groups(_item("a", "green", 90), _item("b", "red", 10), _item("c", "grey", None))
    )
    assert v is not None and v.score == 50 and v.counted == 2
    assert (v.green, v.amber, v.red) == (1, 0, 1)
    assert v.label == "Uptrend under pressure" and v.status == "amber"
    assert v.weakest == ["b"] and v.strongest == ["a"]


def test_verdict_bands() -> None:
    def label(score: float) -> str:
        v = health._verdict(_groups(_item("a", "amber", score)))
        assert v is not None
        return v.label

    assert label(70) == "Confirmed uptrend" and label(69) == "Uptrend under pressure"
    assert label(45) == "Uptrend under pressure" and label(44) == "Correction"
    assert label(25) == "Correction" and label(24) == "Downtrend"


def test_index_below_its_200_day_average_is_a_downtrend_whatever_the_score() -> None:
    v = health._verdict(
        _groups(_item("index_ma", "red", 35), _item("b", "green", 100), _item("c", "green", 100))
    )
    assert v is not None and v.override and v.label == "Downtrend" and v.status == "red"
    assert v.score == 78  # the score is still shown


def test_nothing_scored_gives_no_verdict() -> None:
    assert health._verdict(_groups(_item("a", "grey", None))) is None
