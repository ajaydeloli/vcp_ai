"""Base and contraction segmentation (VCP_SPECIFICATION 7, 8.1, 9A, 23)."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

import pytest

from vcp_scanner.config.models import VCPThresholdsConfig
from vcp_scanner.domain.vcp import Contraction
from vcp_scanner.patterns.vcp.segmentation import NoBaseReason, segment_base

D0 = date(2026, 1, 1)


def _cfg(**over: Any) -> VCPThresholdsConfig:
    """Small windows so synthetic series stay short: 2/2 swings, 60-bar base, 20-bar advance."""
    base: dict[str, Any] = {
        "swing": {"left_bars": 2, "right_bars": 2, "min_depth_pct": 2.0, "min_duration_days": 3},
        "base": {"max_duration_days": 60},
        "prior_advance": {"lookback_days": 20, "min_return_pct": 20.0},
    }
    for k, v in over.items():
        base[k] = {**base.get(k, {}), **v}
    return VCPThresholdsConfig(**base)


def _path(start: float, legs: list[tuple[int, float]]) -> list[float]:
    """Closes moving linearly through (bars, target) legs."""
    closes = [start]
    for bars, target in legs:
        a = closes[-1]
        closes += [a + (target - a) * (i + 1) / bars for i in range(bars)]
    return closes


def _run(closes: list[float], cfg: VCPThresholdsConfig | None = None, cut: int | None = None):  # type: ignore[no-untyped-def]
    """Bars with high = close x 1.002 and low = close x 0.998; as of the last (or ``cut``) bar."""
    d = [D0 + timedelta(days=i) for i in range(len(closes))]
    h = [c * 1.002 for c in closes]
    lo = [c * 0.998 for c in closes]
    as_of = d[cut if cut is not None else -1]
    return segment_base(d, h, lo, as_of=as_of, config=cfg or _cfg()), d


# Advance 100 -> 150 (+50 %), then T1 150 -> 120, T2 145 -> 130.5, T3 142 -> 135, recovery.
CLASSIC = [(20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (5, 135), (5, 140)]


def test_classic_three_contractions() -> None:
    res, d = _run(_path(100, CLASSIC))
    base = res.base
    assert base is not None and res.no_base_reason is None
    assert base.base_start == d[20] and base.base_high == pytest.approx(150 * 1.002)
    assert base.base_low == pytest.approx(120 * 0.998)
    # The 20 bars ending at the high are bars 1..20, so the window's low is bar 1 (close 102.5).
    assert base.prior_advance_return_pct == pytest.approx(
        (150 * 1.002) / (102.5 * 0.998) * 100 - 100
    )
    assert base.prior_advance_low_date == d[1]
    peaks = [c.peak_date for c in base.contractions]
    assert peaks == [d[20], d[40], d[54]]
    troughs = [c.trough_date for c in base.contractions]
    assert troughs == [d[30], d[46], d[59]]
    depths = [c.depth_pct for c in base.contractions]
    expected = [
        (1 - (lo * 0.998) / (hi * 1.002)) * 100 for hi, lo in ((150, 120), (145, 130.5), (142, 135))
    ]
    assert depths == pytest.approx(expected)
    # Closed contractions confirm on the next peak's confirmation date (peak + right_bars).
    assert base.contractions[0].confirmation_date == d[42]
    assert base.contractions[1].confirmation_date == d[56]
    # The final low has 5 bars of recovery after it: a confirmed swing low (low + 2 bars).
    assert base.contractions[2].confirmation_date == d[61]
    assert base.is_confirmed
    assert base.base_duration_days == len(d) - 20
    assert base.merged_peak_dates == ()


def test_final_contraction_is_provisional_until_its_low_is_a_swing_low() -> None:
    closes = _path(100, CLASSIC)
    res, d = _run(closes, cut=60)  # one bar after the T3 low
    final = res.base.contractions[-1]  # type: ignore[union-attr]
    assert final.trough_date == d[59] and final.confirmation_date is None
    assert not res.base.is_confirmed  # type: ignore[union-attr]
    res2, _ = _run(
        closes, _cfg(confirmation={"allow_provisional_final_contraction": False}), cut=60
    )
    assert len(res2.base.contractions) == 2  # type: ignore[union-attr]


def test_final_low_confirms_on_its_right_side_only() -> None:
    # T3 falls only 3 bars after its peak; the rally into that peak had lower lows, so the low
    # is not a formal swing low, but it is final once 2 bars pass without a lower low.
    legs = [(20, 150), (10, 120), (10, 145), (6, 130.5), (2, 136), (3, 142), (3, 136.5),
            (4, 140)]  # fmt: skip
    closes = _path(100, legs)
    res, d = _run(closes)
    final = res.base.contractions[-1]  # type: ignore[union-attr]
    assert final.peak_date == d[51] and final.trough_date == d[54]
    assert final.confirmation_date == d[56]
    res2, _ = _run(closes, cut=55)
    assert res2.base.contractions[-1].confirmation_date is None  # type: ignore[union-attr]


def test_no_final_contraction_while_pressing_the_high() -> None:
    # After T2 the stock makes a new peak at 142 and dips only 1 %: no final contraction yet.
    legs = [(20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (3, 140.6), (3, 141.5)]
    res, d = _run(_path(100, legs))
    base = res.base
    assert base is not None
    assert [c.peak_date for c in base.contractions] == [d[20], d[40]]
    assert all(c.is_confirmed for c in base.contractions)


def test_small_bounce_inside_a_decline_is_merged() -> None:
    # T1 falls 150 -> 135, bounces 1 % to 136.4, then falls on to 120: one contraction, not two.
    legs = [(20, 150), (5, 135), (3, 136.4), (5, 120), (10, 145), (6, 130.5), (8, 142),
            (5, 135), (5, 140)]  # fmt: skip
    res, d = _run(_path(100, legs))
    base = res.base
    assert base is not None
    assert [round(c.depth_pct) for c in base.contractions] == [20, 10, 5]
    assert base.merged_peak_dates == (d[28],)


def test_shallow_decline_is_merged_into_the_previous_contraction() -> None:
    # Between T1 and T2 a 1 % dip after a lower peak: that peak is removed, T1 runs on.
    legs = [(20, 150), (10, 120), (6, 140), (3, 138.8), (4, 145), (6, 130.5), (8, 142),
            (5, 135), (5, 140)]  # fmt: skip
    res, d = _run(_path(100, legs))
    base = res.base
    assert base is not None
    assert [round(c.depth_pct) for c in base.contractions] == [20, 10, 5]
    assert d[36] in base.merged_peak_dates


def test_equal_highs_take_the_earliest_and_merge_the_twin() -> None:
    closes = _path(100, [(20, 150)]) + [150.0] + _path(150, CLASSIC[1:])[1:]
    res, d = _run(closes)
    base = res.base
    assert base is not None
    assert base.base_start == d[20]
    assert d[21] in base.merged_peak_dates
    assert len(base.contractions) == 3


def test_no_prior_advance() -> None:
    # Only +10 % before the high.
    legs = [(20, 110), (10, 90), (10, 106), (6, 97), (8, 104), (5, 100), (5, 103)]
    res, _ = _run(_path(100, legs))
    assert res.base is None and res.no_base_reason is NoBaseReason.NO_PRIOR_ADVANCE
    res2, _ = _run(_path(100, legs), _cfg(prior_advance={"enabled": False}))
    assert res2.base is not None and res2.base.prior_advance_return_pct is None


def test_short_history_fail_is_insufficient_but_a_pass_counts() -> None:
    weak = _path(100, [(5, 110), (10, 90), (10, 106), (6, 97), (8, 104), (5, 100), (5, 103)])
    res, _ = _run(weak)  # 6 bars before the high, window wants 20
    assert res.no_base_reason is NoBaseReason.INSUFFICIENT_HISTORY
    strong = _path(100, [(5, 150), *CLASSIC[1:]])
    res2, _ = _run(strong)
    assert res2.base is not None


def test_no_confirmed_swing_high_in_window() -> None:
    res, _ = _run(_path(100, [(40, 150)]))  # straight up: no swing high confirmed
    assert res.base is None and res.no_base_reason is NoBaseReason.NO_CONFIRMED_SWING_HIGH


def test_old_higher_high_outside_the_window_is_ignored() -> None:
    # A 160 high 80+ bars ago, then a lower run-up and base inside the 60-bar window.
    legs = [(20, 160), (30, 100), (20, 150), *CLASSIC[1:]]
    res, d = _run(_path(100, legs))
    assert res.base is not None and res.base.base_high == pytest.approx(150 * 1.002)


def test_segments_build_valid_domain_contractions() -> None:
    res, _ = _run(_path(100, CLASSIC))
    for c in res.base.contractions:  # type: ignore[union-attr]
        Contraction(
            sequence_number=c.sequence_number, peak_date=c.peak_date, peak_price=c.peak_price,
            trough_date=c.trough_date, trough_price=c.trough_price, depth_pct=c.depth_pct,
            duration_days=c.duration_days, atr_pct=None, range_pct=None, volume_ratio=None,
            confirmation_date=c.confirmation_date,
        )  # fmt: skip


@pytest.mark.parametrize("seed", range(15))
def test_reads_nothing_after_as_of_and_keeps_invariants(seed: int) -> None:
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(199):
        closes.append(closes[-1] * (1 + rng.gauss(0.002, 0.025)))
    d = [D0 + timedelta(days=i) for i in range(200)]
    h = [c * (1 + abs(rng.gauss(0, 0.01))) for c in closes]
    lo = [c * (1 - abs(rng.gauss(0, 0.01))) for c in closes]
    cfg = _cfg()
    for k in range(40, 200, 7):
        full = segment_base(d, h, lo, as_of=d[k], config=cfg)
        cut = segment_base(d[: k + 1], h[: k + 1], lo[: k + 1], as_of=d[k], config=cfg)
        assert full == cut
        if full.base is None:
            continue
        cs = full.base.contractions
        assert all(c.depth_pct >= 2.0 for c in cs)
        assert all(c.duration_days >= 3 or c.depth_pct >= 4.0 for c in cs)
        assert all(a.trough_index < b.peak_index for a, b in zip(cs, cs[1:], strict=False))
        assert all(c.is_confirmed for c in cs[:-1])
        assert all(c.peak_price <= full.base.base_high for c in cs)
        assert all(c.trough_price >= full.base.base_low for c in cs)


def test_sharp_two_bar_rally_is_a_real_swing() -> None:
    # T2's low is followed by a 9 % rally in 2 bars to a new peak, then T3: the peak is kept.
    legs = [(20, 150), (10, 120), (10, 145), (6, 130.5), (2, 142.5), (5, 135), (5, 140)]
    res, d = _run(_path(100, legs))
    base = res.base
    assert base is not None
    assert [c.peak_date for c in base.contractions] == [d[20], d[40], d[48]]
    assert base.merged_peak_dates == ()
    # The plain "< 3 bars" rule (short_swing_max_depth_pct = 100) merges that peak away.
    old, _ = _run(_path(100, legs), _cfg(swing={"short_swing_max_depth_pct": 100.0}))
    assert d[48] in old.base.merged_peak_dates  # type: ignore[union-attr]


def test_small_two_bar_dip_is_still_noise() -> None:
    # After T1 a lower peak at 140 dips 3 % in 2 bars before rising to 145: merged.
    legs = [(20, 150), (10, 120), (6, 140), (2, 135.8), (4, 145), (6, 130.5), (8, 142),
            (5, 135), (5, 140)]  # fmt: skip
    res, d = _run(_path(100, legs))
    base = res.base
    assert base is not None
    assert d[36] in base.merged_peak_dates
    assert [round(c.depth_pct) for c in base.contractions] == [20, 10, 5]


def test_short_swing_threshold_validation() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="short_swing_max_depth_pct"):
        _cfg(swing={"short_swing_max_depth_pct": 1.0})


def test_equal_high_merge_joins_a_shallow_dip_to_a_deeper_drop() -> None:
    # T1 dips 7 % from 150, the stock returns to 149.9 and then drops 17 %: one contraction.
    legs = [(20, 150), (6, 139.5), (6, 149.9), (10, 124.5), (8, 140), (5, 127.4), (5, 135)]
    off, d = _run(_path(100, legs))
    assert off.base is not None
    assert [round(c.depth_pct) for c in off.base.contractions] == [7, 17, 9]  # default: off
    on, _ = _run(_path(100, legs), _cfg(swing={"merge_equal_highs": True}))
    assert on.base is not None
    assert [c.peak_date for c in on.base.contractions] == [d[20], d[50]]
    assert [round(c.depth_pct) for c in on.base.contractions] == [17, 9]
    assert on.base.contractions[0].trough_date == d[42]
    assert d[32] in on.base.merged_peak_dates


def test_equal_high_merge_keeps_a_tightening_pair() -> None:
    # Equal highs but the second pullback is shallower (14 % then 6 %): two contractions.
    legs = [(20, 150), (10, 130), (10, 149.8), (6, 142), (5, 146)]
    on, _ = _run(_path(100, legs), _cfg(swing={"merge_equal_highs": True}))
    assert on.base is not None
    assert [round(c.depth_pct) for c in on.base.contractions] == [14, 6]
    # A second high outside the tolerance is never merged, however deep its pullback.
    legs2 = [(20, 150), (6, 139.5), (6, 146), (10, 124.5), (8, 140)]
    far, _ = _run(_path(100, legs2), _cfg(swing={"merge_equal_highs": True}))
    assert far.base is not None and far.base.merged_peak_dates == ()
    assert len(far.base.contractions) == 2  # 150 -> 139.5 and 146 -> 124.5, kept apart
