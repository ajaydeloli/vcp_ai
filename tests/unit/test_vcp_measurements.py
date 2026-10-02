"""VCP measurements (VCP_SPECIFICATION 12-18A, 18B, 22)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest

from vcp_scanner.config.models import VCPThresholdsConfig
from vcp_scanner.patterns.vcp.measurements import PriceSeries, measure
from vcp_scanner.patterns.vcp.segmentation import segment_base

D0 = date(2026, 1, 1)
# 60 flat bars (volume baseline), advance 100 -> 150, then T1 20 %, T2 10 %, T3 5 %, recovery.
LEGS = [(60, 100), (20, 150), (10, 120), (10, 145), (6, 130.5), (8, 142), (5, 135), (5, 140)]


def _cfg(**over: Any) -> VCPThresholdsConfig:
    base: dict[str, Any] = {
        "swing": {"left_bars": 2, "right_bars": 2, "min_depth_pct": 2.0, "min_duration_days": 3},
        "base": {"max_duration_days": 60},
        "prior_advance": {"lookback_days": 20, "min_return_pct": 20.0},
    }
    for k, v in over.items():
        base[k] = {**base.get(k, {}), **v} if isinstance(v, dict) else v
    return VCPThresholdsConfig(**base)


def _closes() -> list[float]:
    closes = [100.0]
    for bars, target in LEGS:
        a = closes[-1]
        closes += [a + (target - a) * (i + 1) / bars for i in range(bars)]
    return closes


def _atr_pct(high: list[float], low: list[float], close: list[float]) -> list[float | None]:
    """Same definition as the feature engine: simple 14-bar mean of true range / close."""
    tr: list[float | None] = [None]
    for i in range(1, len(close)):
        p = close[i - 1]
        tr.append(max(high[i] - low[i], abs(high[i] - p), abs(low[i] - p)))
    out: list[float | None] = []
    for i in range(len(close)):
        w = tr[i - 13 : i + 1] if i >= 13 else []
        out.append(None if len(w) < 14 or None in w else sum(w) / 14 / close[i] * 100)  # type: ignore[arg-type]
    return out


def _series(volume_fn: Any = None, wiggle: float = 0.002) -> PriceSeries:
    closes = _closes()
    high = [c * (1 + wiggle) for c in closes]
    low = [c * (1 - wiggle) for c in closes]
    vol = [volume_fn(i) if volume_fn else 1000.0 for i in range(len(closes))]
    d = [D0 + timedelta(days=i) for i in range(len(closes))]
    return PriceSeries(d, high, low, closes, vol, _atr_pct(high, low, closes))


def _measure(s: PriceSeries, cfg: VCPThresholdsConfig | None = None):  # type: ignore[no-untyped-def]
    cfg = cfg or _cfg()
    seg = segment_base(s.dates, s.high, s.low, as_of=s.dates[-1], config=cfg).base
    assert seg is not None and len(seg.contractions) == 3
    return measure(s, seg, cfg), seg


def test_tightening_measures() -> None:
    m, seg = _measure(_series())
    d = [c.depth_pct for c in seg.contractions]
    assert m.tightening_ratios == pytest.approx((d[1] / d[0], d[2] / d[1]))
    assert m.max_tightening_ratio == pytest.approx(max(d[1] / d[0], d[2] / d[1]))
    assert m.tightening_consistency == 1.0
    assert m.progressive_tightening is True


def _with_depths(seg: Any, depths: list[float]) -> Any:
    """The same bars, with contraction lows moved so the depths are exactly ``depths``."""
    cs = tuple(
        replace(c, trough_price=c.peak_price * (1 - d / 100))
        for c, d in zip(seg.contractions, depths, strict=True)
    )
    return replace(seg, contractions=cs)


@pytest.mark.parametrize(
    ("depths", "tol", "expected"),
    [
        ([20, 10, 5], 10, True),
        ([20, 22, 5], 10, True),  # 22/20 = 1.10: exactly the relative tolerance
        ([20, 22.2, 5], 10, False),  # 1.11 > 1.10
        ([20, 22, 5], 0, False),
        ([10, 10.5, 10.9], 10, False),  # each step within tolerance, but last >= first
    ],
)
def test_progressive_tightening_rule(depths: list[float], tol: float, expected: bool) -> None:
    s = _series()
    _, seg = _measure(s)
    cfg = _cfg()
    cfg = cfg.model_copy(update={"progressive_tolerance_pct": tol})
    m = measure(s, _with_depths(seg, depths), cfg)
    assert m.progressive_tightening is expected


# Bar layout of _closes(): T1 peak 80, decline 81-90; T2 peak 100, decline 101-106;
# T3 peak 114, decline 115-119; recovery 120-124 (125 bars).
def _vol(t1: float, t3: float) -> Any:
    def f(i: int) -> float:
        if 81 <= i <= 90:
            return t1
        if 115 <= i <= 119:
            return t3
        return 1000.0

    return f


def test_layout_assumption() -> None:
    _, seg = _measure(_series())
    assert [(c.peak_index, c.trough_index) for c in seg.contractions] == [
        (80, 90), (100, 106), (114, 119)
    ]  # fmt: skip


def test_volume_dryup_through_the_sequence() -> None:
    m, _ = _measure(_series(_vol(1500, 400)))
    assert m.contractions[0].volume_ratio == pytest.approx(1.5)  # baseline bars 31-80 = 1000
    # T3 baseline = bars 65-114: 40 x 1000 + 10 x 1500 -> mean 1100.
    assert m.final_volume_ratio == pytest.approx(400 / 1100)
    assert m.volume_dryup_pass is True
    m2, _ = _measure(_series(_vol(1500, 900)))
    assert m2.final_volume_ratio == pytest.approx(900 / 1100)
    assert m2.volume_dryup_pass is False  # above dryup_ratio 0.70
    m3, _ = _measure(_series(_vol(500, 650)))
    assert m3.volume_dryup_pass is False  # 650/... below 0.70 but not below T1's ratio


def test_missing_volume_is_never_a_pass() -> None:
    def f(i: int) -> float | None:
        return None if i == 117 else _vol(1500, 400)(i)

    m, _ = _measure(_series(f))
    assert m.final_volume_ratio is None and m.volume_dryup_pass is None
    assert m.contractions[0].volume_ratio == pytest.approx(1.5)
    assert m.volume_avg[5] == 1000.0  # bars 120-124 are complete
    assert m.volume_avg[20] is None and m.volume_avg[50] is None  # include bar 117


def test_volatility_contraction_uses_feature_atr() -> None:
    s = _series()
    m, _ = _measure(s)
    atr = s.atr_pct_14
    t1 = sum(atr[81:91]) / 10  # type: ignore[arg-type]
    t3 = sum(atr[115:120]) / 5  # type: ignore[arg-type]
    assert m.contractions[0].atr_pct == pytest.approx(t1)
    assert m.atr_contraction_ratio == pytest.approx(t3 / t1)
    # The bars themselves contracted more than the lagging ATR14 says.
    assert m.tr_contraction_ratio is not None and m.tr_contraction_ratio < m.atr_contraction_ratio  # type: ignore[operator]


def _tr(s: PriceSeries, i: int) -> float:
    p = s.close[i - 1]
    return max(s.high[i] - s.low[i], abs(s.high[i] - p), abs(s.low[i] - p)) / s.close[i] * 100


@pytest.mark.parametrize("cap", [0.30, 0.50, 0.70, 0.95])
def test_volatility_rule_switch(cap: float) -> None:
    s = _series()
    tr1 = sum(_tr(s, i) for i in range(81, 91)) / 10
    tr3 = sum(_tr(s, i) for i in range(115, 120)) / 5
    m, _ = _measure(s, _cfg(volatility={"contraction_ratio_max": cap}))
    assert m.tr_contraction_ratio == pytest.approx(tr3 / tr1)
    assert m.volatility_contraction_pass is (tr3 / tr1 <= cap)  # default: true range decides
    a, _ = _measure(s, _cfg(volatility={"contraction_ratio_max": cap, "measure": "atr"}))
    assert a.volatility_contraction_pass is (a.atr_contraction_ratio <= cap)  # type: ignore[operator]
    assert a.tr_contraction_ratio == m.tr_contraction_ratio  # both always measured


def test_missing_atr_gives_none() -> None:
    s = _series()
    atr = list(s.atr_pct_14)
    atr[116] = None
    m, _ = _measure(replace(s, atr_pct_14=atr))
    assert m.contractions[-1].atr_pct is None and m.atr_contraction_ratio is None
    assert m.volatility_contraction_pass is not None  # true range (default) does not need ATR
    a, _ = _measure(replace(s, atr_pct_14=atr), _cfg(volatility={"measure": "atr"}))
    assert a.volatility_contraction_pass is None  # the ATR rule cannot decide: never a pass


def test_right_side_and_range_compression() -> None:
    s = _series()
    m, _ = _measure(s)
    hi, lo = max(s.high[115:125]), min(s.low[115:125])
    assert m.right_side_range_pct == pytest.approx((hi - lo) / hi * 100)
    assert m.tight_pivot_pass is (m.right_side_range_pct <= 5.0)  # type: ignore[operator]
    hi5, lo5 = max(s.high[120:125]), min(s.low[120:125])
    assert m.last_range_pct[5] == pytest.approx((hi5 - lo5) / hi5 * 100)
    assert m.last_atr_pct[10] == pytest.approx(sum(s.atr_pct_14[115:125]) / 10)  # type: ignore[arg-type]
    loose, _ = _measure(s, _cfg(pivot={"max_right_side_range_pct": 1.0}))
    assert loose.tight_pivot_pass is False


def test_selling_pressure_counts() -> None:
    def f(i: int) -> float:
        return 2000.0 if i in (85, 86) else (1600.0 if i == 95 else 1000.0)

    m, _ = _measure(_series(f))
    assert m.high_volume_down_days == 2  # 85, 86 fall on 2x volume
    assert m.high_volume_up_days == 1  # 95 rises on 1.6x
    assert m.up_down_volume_ratio is not None and m.up_down_volume_ratio > 0


def test_one_contraction_has_no_sequence_criteria() -> None:
    s = _series()
    _, seg = _measure(s)
    one = replace(seg, contractions=seg.contractions[:1])
    m = measure(s, one, _cfg())
    assert m.progressive_tightening is None and m.tightening_ratios == ()
    assert m.atr_contraction_ratio is None and m.volume_dryup_pass is None
    assert m.final_volume_ratio == m.contractions[0].volume_ratio


def test_bars_after_as_of_are_ignored_and_mismatch_is_refused() -> None:
    s = _series()
    cfg = _cfg()
    as_of = s.dates[121]
    seg = segment_base(s.dates, s.high, s.low, as_of=as_of, config=cfg).base
    assert seg is not None
    cut = PriceSeries(
        *(col[:122] for col in (s.dates, s.high, s.low, s.close, s.volume, s.atr_pct_14))
    )
    assert measure(s, seg, cfg) == measure(cut, seg, cfg)
    shifted = PriceSeries(
        *(col[1:] for col in (s.dates, s.high, s.low, s.close, s.volume, s.atr_pct_14))
    )
    with pytest.raises(ValueError, match="does not match"):
        measure(shifted, seg, cfg)
