"""Breakout events across days (VCP_SPECIFICATION 47, 61B)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from vcp_scanner.config.models import ClassificationConfig, VCPThresholdsConfig
from vcp_scanner.domain.enums import VCPStatus
from vcp_scanner.patterns.vcp.detector import VCPDetector
from vcp_scanner.patterns.vcp.measurements import PriceSeries
from vcp_scanner.patterns.vcp.monitor import (
    PRIOR_DAY_PIVOT,
    STRUCTURAL,
    BreakoutEvent,
    PriorPattern,
    event_id,
    track_breakouts,
)

D0 = date(2026, 1, 1)
CLASSIC = [(60, 100), (20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (5, 135), (5, 140)]
VCP_CFG = VCPThresholdsConfig(
    swing={"left_bars": 2, "right_bars": 2},  # type: ignore[arg-type]
    base={"max_duration_days": 80},  # type: ignore[arg-type]
    prior_advance={"lookback_days": 20},  # type: ignore[arg-type]
)


def _series(extra: list[tuple[float, float]] | None = None) -> PriceSeries:
    closes = [100.0]
    for bars, target in CLASSIC:
        a = closes[-1]
        closes += [a + (target - a) * (i + 1) / bars for i in range(bars)]
    volume = [1000.0] * len(closes)
    for c, v in extra or []:
        closes.append(c)
        volume.append(v)
    d = [D0 + timedelta(days=i) for i in range(len(closes))]
    return PriceSeries(d, [c * 1.002 for c in closes], [c * 0.998 for c in closes], closes,
                       volume, [2.0] * len(closes))  # fmt: skip


def _run(s: PriceSeries, **kw: Any):  # type: ignore[no-untyped-def]
    det = VCPDetector(VCP_CFG, ClassificationConfig(), config_hash="h").detect(
        "I", s, s.dates[-1], trend_template_pass=True, weekly_stage2_pass=True
    )
    return track_breakouts(det, s, config_hash="h", vcp=VCP_CFG, **kw), det


def _event(base_start: date, pivot: float, when: date, detected: date) -> BreakoutEvent:
    return BreakoutEvent(event_id("I", base_start, "h"), "I", base_start, when, pivot, when,
                         "SWING_HIGH", 2.0, detected, STRUCTURAL)  # fmt: skip


def test_no_history_no_change() -> None:
    (det, new), raw = _run(_series(), prior=None, event=None)
    assert new is None and det == raw
    assert det.pattern.status is VCPStatus.PIVOT_READY  # type: ignore[union-attr]


def test_structural_breakout_becomes_an_event() -> None:
    (det, new), _ = _run(_series([(143.0, 2000.0)]), prior=None, event=None)
    assert new is not None and new.method == STRUCTURAL
    assert new.pivot_price == det.pivots.structural.pivot_price  # type: ignore[union-attr]
    assert new.breakout_date == det.as_of == new.detected_as_of
    assert det.pattern.status is VCPStatus.BREAKOUT  # type: ignore[union-attr]


def test_existing_event_decides_later_days() -> None:
    s = _series([(141.5, 1000.0)])
    base_start = s.dates[80]
    ev = _event(base_start, 141.0, s.dates[124], s.dates[124])
    (det, new), _ = _run(s, prior=None, event=ev)
    assert new is None
    assert det.pattern.status is VCPStatus.BREAKOUT  # type: ignore[union-attr]  # 141.5 >= 141
    assert det.pattern.base_end == s.dates[124]  # type: ignore[union-attr]
    (det2, _), _ = _run(_series([(139.0, 1000.0)]), prior=None, event=ev)
    assert det2.pattern.status is VCPStatus.FAILED  # type: ignore[union-attr]


def test_events_of_another_base_or_from_the_future_are_ignored() -> None:
    s = _series([(139.0, 1000.0)])
    other = _event(s.dates[50], 141.0, s.dates[124], s.dates[124])
    (det, _), raw = _run(s, prior=None, event=other)
    assert det == raw
    future = _event(s.dates[80], 141.0, s.dates[124], s.dates[-1] + timedelta(days=1))
    (det2, _), raw2 = _run(s, prior=None, event=future)
    assert det2 == raw2


def test_prior_day_pivot_breakout() -> None:
    # Yesterday's primary pivot was the top of a tight right side (140.88); today closes above
    # it on 2x volume, below the structural pivot (142.28): only the stored pivot sees it.
    s = _series([(141.3, 2000.0)])
    prior = PriorPattern(s.dates[124], s.dates[80], 140.6 * 1.002, s.dates[115], "RIGHT_SIDE_HIGH",
                         "A_PLUS_VCP", "PIVOT_READY")  # fmt: skip
    (det, new), raw = _run(s, prior=prior, event=None)
    assert raw.breakout is None  # the stateless detector cannot see it
    assert new is not None and new.method == PRIOR_DAY_PIVOT
    assert new.pivot_price == prior.pivot_price and new.breakout_date == s.dates[-1]
    assert det.pattern.status is VCPStatus.BREAKOUT and det.pattern.base_end == s.dates[-1]  # type: ignore[union-attr]
    quiet = _series([(141.3, 1000.0)])
    (det_q, new_q), _ = _run(quiet, prior=prior, event=None)
    assert new_q is None and det_q.pattern.status is not VCPStatus.BREAKOUT  # type: ignore[union-attr]
    other_base = replace(prior, base_start=s.dates[50])
    assert _run(s, prior=other_base, event=None)[0][1] is None


def test_invalidated_and_data_states_pass_through() -> None:
    s = _series([(143.0, 2000.0)])
    det = VCPDetector(VCP_CFG, ClassificationConfig(), config_hash="h").detect(
        "I", s, s.dates[-1], trend_template_pass=False, weekly_stage2_pass=True
    )
    assert det.pattern.status is VCPStatus.INVALIDATED  # type: ignore[union-attr]
    out, new = track_breakouts(det, s, prior=None, event=None, config_hash="h", vcp=VCP_CFG)
    assert out == det and new is None


def test_event_id_is_deterministic() -> None:
    assert event_id("I", D0, "h") == event_id("I", D0, "h") != event_id("I", D0, "g")
