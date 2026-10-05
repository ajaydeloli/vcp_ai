"""Flat / tight base detector, ``flat_base-1.0.0`` (STRATEGY_SPECIFICATION 13).

A Stage 2 stock that, after an advance, moves sideways for at least four to five weeks within
a shallow range. Steps (section 13.1), on adjusted daily bars ending at the as-of bar ``t``:

1. Base start ``s`` = the highest high among bars ``t - max_duration + 1`` .. ``t -
   min_duration + 1`` (earliest on a tie). Pivot = high[s] x (1 + buffer); it never moves.
2. Prior advance into ``s`` >= the minimum (shared ``prior_advance``); a fail with a short
   window is INSUFFICIENT_HISTORY.
3. Breakout: a recorded one for this base, else the first close above the pivot after ``s`` on
   breakout volume (``find_breakout``). After a breakout the base is measured up to the day
   before it (its shape is frozen; ``base_end`` = the breakout date).
4. Without a breakout, a close more than ``max_overshoot_pct`` above the pivot means the stock
   has left the range: no base (MOVED_ABOVE_BASE). A smaller close above the pivot without
   breakout volume leaves the base intact (decision F2).
5. Measurements: depth, weekly close range of the base's weeks, right-side range and low,
   length, volume dry-up. Tiers per section 13.2; grade >= 2 also needs Trend Template PASS
   and weekly Stage 2. No tier met (too deep): no base (TOO_DEEP).
"""

from __future__ import annotations

from vcp_scanner.config.models import FlatBaseSettings
from vcp_scanner.domain.strategy import Breakout, Setup, StrategyResult
from vcp_scanner.features.base_measures import (
    base_extremes,
    prior_advance,
    volume_dryup_ratio,
    weekly_bars,
    weekly_close_range_pct,
)
from vcp_scanner.patterns.strategy_base import StrategyContext, find_breakout, setup_status

ALGORITHM_VERSION = "flat_base-1.0.0"
STRATEGY_ID = "flat_base"
SCORE_SUBS = ("depth", "weekly_tightness", "right_side", "length", "prior_advance")


class FlatBaseDetector:
    strategy_id = STRATEGY_ID
    algorithm_version = ALGORITHM_VERSION

    def __init__(self, settings: FlatBaseSettings) -> None:
        self._s = settings

    def lookback_bars(self) -> int:
        d = self._s.detector
        return d.max_duration_days + d.prior_advance_lookback_days + 15

    def detect(self, ctx: StrategyContext) -> StrategyResult:
        d = self._s.detector
        b = ctx.bars
        n = len(b.dates)
        iid, as_of = ctx.instrument_id, ctx.as_of_date
        if n == 0 or b.dates[-1] != as_of:
            return StrategyResult(iid, as_of, None, None, "STALE_DATA")
        if n < d.max_duration_days + 1:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        t = n - 1
        lo, hi = t - d.max_duration_days + 1, t - d.min_duration_days + 1
        s = min(range(lo, hi + 1), key=lambda i: (-b.high[i], i))
        base_high = b.high[s]
        pivot = base_high * (1.0 + d.pivot_buffer_pct / 100.0)

        pa = prior_advance(b.high, b.low, s, d.prior_advance_lookback_days)
        if pa is None:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        if pa.pct < d.min_prior_advance_pct:
            reason = "NO_PRIOR_ADVANCE" if pa.window_complete else "INSUFFICIENT_HISTORY"
            return StrategyResult(iid, as_of, None, reason)

        breakout: Breakout | None = ctx.breakouts.get(b.dates[s]) or find_breakout(
            b, pivot, s + 1, ctx.breakout_volume_ratio
        )
        end = t
        if breakout is None:
            limit = pivot * (1.0 + d.max_overshoot_pct / 100.0)
            if any(b.close[k] > limit for k in range(s + 1, n)):
                return StrategyResult(iid, as_of, None, "MOVED_ABOVE_BASE")
        elif breakout.breakout_date in b.dates:
            end = max(s, list(b.dates).index(breakout.breakout_date) - 1)

        ext = base_extremes(b.dates, b.high, b.low, s, end)
        if ext is None:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        depth = (base_high - ext.low) / base_high * 100.0
        weeks = weekly_bars(b.dates[s : end + 1], b.high[s : end + 1], b.low[s : end + 1],
                            b.close[s : end + 1])  # fmt: skip
        wcr = weekly_close_range_pct([w.close for w in weeks], len(weeks))
        r0 = max(s, end - d.right_side_days + 1)
        rs_high = max(b.high[r0 : end + 1])
        rs_low = min(b.low[r0 : end + 1])
        rs_range = (rs_high - rs_low) / rs_high * 100.0
        dryup = volume_dryup_ratio(b.volume, d.dryup_recent_days, d.dryup_base_days, end)
        length = end - s + 1

        grade, cls = 0, "NONE"
        unmet: dict[str, list[str]] = {}
        trend_ok = ctx.trend_template_pass and ctx.weekly_stage2_pass is True
        for name, tier in self._s.classification.items():
            fails: list[str] = []
            if depth > tier.max_depth_pct:
                fails.append("max_depth_pct")
            if tier.min_duration_days is not None and length < tier.min_duration_days:
                fails.append("min_duration_days")
            if tier.max_weekly_close_range_pct is not None and (
                    wcr is None or wcr > tier.max_weekly_close_range_pct):  # fmt: skip
                fails.append("max_weekly_close_range_pct")
            if tier.max_right_side_range_pct is not None and (
                    rs_range > tier.max_right_side_range_pct):  # fmt: skip
                fails.append("max_right_side_range_pct")
            if tier.max_dryup_ratio is not None and (
                    dryup is None or dryup > tier.max_dryup_ratio):  # fmt: skip
                fails.append("max_dryup_ratio")
            if tier.grade >= 2 and not trend_ok:
                fails.append("trend_template_and_stage2")
            if fails:
                unmet[name.upper()] = fails
            elif tier.grade > grade:
                grade, cls = tier.grade, name.upper()
        if grade == 0:
            return StrategyResult(iid, as_of, None, "TOO_DEEP")

        close = b.close[t]
        status = setup_status(close, pivot, grade, breakout,
                              self._s.ranking.pivot_ready_max_distance_pct)  # fmt: skip
        setup = Setup(
            instrument_id=iid, as_of_date=as_of,
            base_start=b.dates[s], base_end=breakout.breakout_date if breakout else None,
            base_high=base_high, base_low=ext.low, base_depth_pct=depth,
            base_duration_days=length, prior_advance_pct=pa.pct,
            pivot_price=pivot, pivot_date=b.dates[s], pivot_source="BASE_HIGH",
            pivot_distance_pct=(pivot - close) / close * 100.0,
            stop_reference_price=rs_low, dryup_volume_ratio=dryup,
            classification=cls, grade=grade, status=status, confirmation_state="CONFIRMED",
            trend_gate="PASS" if ctx.trend_template_pass else "FAIL",
            weekly_stage2_pass=ctx.weekly_stage2_pass, breakout=breakout, unmet_rules=unmet,
            details={
                "weekly_close_range_pct": wcr, "weeks": ext.weeks,
                "right_side_range_pct": rs_range, "right_side_low": rs_low,
                "prior_advance_low_date": b.dates[pa.low_index].isoformat(),
                "closes_above_pivot": sum(1 for k in range(s + 1, end + 1)
                                          if b.close[k] > pivot),
                "pivot_buffer_pct": d.pivot_buffer_pct,
            },
            measures={"depth": depth, "weekly_tightness": wcr, "right_side": rs_range,
                      "length": float(ext.weeks), "prior_advance": pa.pct},
        )  # fmt: skip
        return StrategyResult(iid, as_of, setup)
