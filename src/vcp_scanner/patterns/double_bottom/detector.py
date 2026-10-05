"""Double bottom detector, ``double_bottom-1.0.0`` (STRATEGY_SPECIFICATION 15.2).

A W: a decline, a rally to a middle peak, a second decline that slightly undercuts the first low,
then a rally back. Steps, on adjusted daily bars ending at the as-of bar ``t``:

1. Left high ``L`` = the highest high among bars ``t - max_base`` .. ``t - min_base`` (earliest
   on a tie).
2. Second low ``B2`` = the lowest low after ``L`` up to the base's end (the latest on a tie, so
   two equal lows make a W with a 0 % undercut).
3. Middle peak ``M``: among bars ``m`` between ``L`` and ``B2`` whose first low (the lowest low
   between ``L`` and ``m``, earliest on a tie) leaves at least ``min_leg_days`` on each leg and
   which bounced at least the loosest tier's minimum above it, the one with the highest high
   (latest on a tie); ``B1`` = its first low. At least ``min_days_after_low`` sessions after
   ``B2``. Otherwise NO_W.
4. Pivot = high[M] x (1 + buffer). Breakout: a recorded one for this base (same pivot), else the
   first close above the pivot after ``B2`` on breakout volume; the base is then measured up to
   the day before it (frozen shape). Without one, a close more than ``max_overshoot_pct`` above
   the pivot is MOVED_ABOVE_BASE (decision F2). More than ``max_days_after_breakout`` sessions
   after its breakout the base is no longer a setup (OLD_BREAKOUT; step 5b, owner option a),
   so a long-gone breakout is not ranked or traded as a fresh one.
5. Prior advance into ``L``, measurements and tiers per section 15.2; grade >= 2 also needs
   Trend Template PASS and weekly Stage 2. No tier met: TOO_DEEP.
   Stop = the right-side low (last ``right_side_days`` bars, decision D2).
"""

from __future__ import annotations

from dataclasses import dataclass

from vcp_scanner.config.models import DoubleBottomSettings
from vcp_scanner.domain.strategy import Breakout, Setup, StrategyResult
from vcp_scanner.features.base_measures import prior_advance, volume_dryup_ratio
from vcp_scanner.patterns.strategy_base import (
    DailyBars,
    StrategyContext,
    find_breakout,
    highest,
    lowest,
    setup_status,
)

ALGORITHM_VERSION = "double_bottom-1.0.0"
STRATEGY_ID = "double_bottom"
SCORE_SUBS = ("right_side", "depth", "undercut", "middle_peak", "prior_advance")


@dataclass(frozen=True, slots=True)
class _W:
    first_low: int
    middle: int
    second_low: int


class DoubleBottomDetector:
    strategy_id = STRATEGY_ID
    algorithm_version = ALGORITHM_VERSION

    def __init__(self, settings: DoubleBottomSettings) -> None:
        self._s = settings
        self._min_bounce = min(t.min_middle_bounce_pct for t in settings.classification.values())

    def lookback_bars(self) -> int:
        d = self._s.detector
        return d.max_base_days + d.prior_advance_lookback_days + 10

    def _w(self, b: DailyBars, lip: int, end: int) -> _W | None:
        """The W's points after the left high ``lip``, up to bar ``end``."""
        d = self._s.detector
        if end - lip < 3 * d.min_leg_days + d.min_days_after_low:
            return None
        b2 = lowest(b.low, lip + 1, end, latest=True)
        if end - b2 < d.min_days_after_low:
            return None
        best: tuple[int, int] | None = None
        first_low: int | None = None
        for m in range(lip + 2, b2):
            k = m - 1  # the first low runs over lip + 1 .. m - 1
            if first_low is None or b.low[k] < b.low[first_low]:
                first_low = k
            b1 = first_low
            if b1 - lip < d.min_leg_days or m - b1 < d.min_leg_days or b2 - m < d.min_leg_days:
                continue
            if b.low[b1] <= 0 or (b.high[m] / b.low[b1] - 1.0) * 100.0 < self._min_bounce:
                continue
            if best is None or b.high[m] >= b.high[best[0]]:
                best = (m, b1)
        return None if best is None else _W(best[1], best[0], b2)

    def _pivot(self, b: DailyBars, w: _W) -> float:
        return b.high[w.middle] * (1.0 + self._s.detector.pivot_buffer_pct / 100.0)

    def detect(self, ctx: StrategyContext) -> StrategyResult:  # noqa: C901, PLR0911, PLR0912, PLR0915
        d = self._s.detector
        b = ctx.bars
        n = len(b.dates)
        iid, as_of = ctx.instrument_id, ctx.as_of_date
        if n == 0 or b.dates[-1] != as_of:
            return StrategyResult(iid, as_of, None, None, "STALE_DATA")
        if n < d.min_base_days + 1:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        t = n - 1
        lip = highest(b.high, max(0, t - d.max_base_days), t - d.min_base_days)
        index = {dt: i for i, dt in enumerate(b.dates)}

        # Points, pivot and breakout: a recorded breakout fixes the base's end first.
        breakout: Breakout | None = None
        w: _W | None = None
        recorded = ctx.breakouts.get(b.dates[lip])
        if recorded is not None and recorded.breakout_date in index:
            wr = self._w(b, lip, index[recorded.breakout_date] - 1)
            if wr is not None and abs(self._pivot(b, wr) - recorded.pivot_price) <= (
                    1e-9 * recorded.pivot_price):  # fmt: skip
                w, breakout = wr, recorded
        if w is None:
            w = self._w(b, lip, t)
            if w is None:
                return StrategyResult(iid, as_of, None, "NO_W")
            found = find_breakout(b, self._pivot(b, w), w.second_low + 1,
                                  ctx.breakout_volume_ratio)  # fmt: skip
            if found is not None:
                wf = self._w(b, lip, index[found.breakout_date] - 1)
                if wf is not None and wf.middle == w.middle:
                    w, breakout = wf, found
        pivot = self._pivot(b, w)
        end = t if breakout is None else index[breakout.breakout_date] - 1
        if breakout is not None and t - (end + 1) > d.max_days_after_breakout:
            return StrategyResult(iid, as_of, None, "OLD_BREAKOUT")
        if breakout is None:
            limit = pivot * (1.0 + d.max_overshoot_pct / 100.0)
            if any(b.close[k] > limit for k in range(w.second_low + 1, n)):
                return StrategyResult(iid, as_of, None, "MOVED_ABOVE_BASE")

        pa = prior_advance(b.high, b.low, lip, d.prior_advance_lookback_days)
        if pa is None:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        if pa.pct < d.min_prior_advance_pct:
            reason = "NO_PRIOR_ADVANCE" if pa.window_complete else "INSUFFICIENT_HISTORY"
            return StrategyResult(iid, as_of, None, reason)

        left, low1, peak, low2 = (b.high[lip], b.low[w.first_low], b.high[w.middle],
                                  b.low[w.second_low])  # fmt: skip
        if left <= low2:  # pragma: no cover - the left high is above every later low
            return StrategyResult(iid, as_of, None, "NO_W")
        depth = (left - low2) / left * 100.0
        undercut = (low1 - low2) / low1 * 100.0
        bounce = (peak / low1 - 1.0) * 100.0
        peak_pos = (peak - low2) / (left - low2)
        r0 = max(lip, end - d.right_side_days + 1)
        rs_high = max(b.high[r0 : end + 1])
        rs_low = min(b.low[r0 : end + 1])
        rs_range = (rs_high - rs_low) / rs_high * 100.0
        dryup = volume_dryup_ratio(b.volume, d.dryup_recent_days, d.dryup_base_days, end)
        length = end - lip + 1

        grade, cls = 0, "NONE"
        unmet: dict[str, list[str]] = {}
        trend_ok = ctx.trend_template_pass and ctx.weekly_stage2_pass is True
        for name, tier in self._s.classification.items():
            fails: list[str] = []
            if depth > tier.max_depth_pct:
                fails.append("max_depth_pct")
            if tier.min_depth_pct is not None and depth < tier.min_depth_pct:
                fails.append("min_depth_pct")
            if undercut > tier.max_undercut_pct:
                fails.append("max_undercut_pct")
            if tier.require_undercut and undercut <= 0:
                fails.append("require_undercut")
            if bounce < tier.min_middle_bounce_pct:
                fails.append("min_middle_bounce_pct")
            if tier.middle_below_left_high and peak >= left:
                fails.append("middle_below_left_high")
            if tier.min_base_days is not None and length < tier.min_base_days:
                fails.append("min_base_days")
            if tier.min_prior_advance_pct is not None and pa.pct < tier.min_prior_advance_pct:
                fails.append("min_prior_advance_pct")
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
            base_start=b.dates[lip], base_end=breakout.breakout_date if breakout else None,
            base_high=left, base_low=low2, base_depth_pct=depth,
            base_duration_days=length, prior_advance_pct=pa.pct,
            pivot_price=pivot, pivot_date=b.dates[w.middle], pivot_source="MIDDLE_PEAK",
            pivot_distance_pct=(pivot - close) / close * 100.0,
            stop_reference_price=rs_low, dryup_volume_ratio=dryup,
            classification=cls, grade=grade, status=status, confirmation_state="CONFIRMED",
            trend_gate="PASS" if ctx.trend_template_pass else "FAIL",
            weekly_stage2_pass=ctx.weekly_stage2_pass, breakout=breakout, unmet_rules=unmet,
            details={
                "left_high_date": b.dates[lip].isoformat(),
                "first_low_date": b.dates[w.first_low].isoformat(),
                "middle_peak_date": b.dates[w.middle].isoformat(),
                "second_low_date": b.dates[w.second_low].isoformat(),
                "first_low": low1, "middle_peak": peak, "second_low": low2,
                "undercut_pct": undercut, "middle_bounce_pct": bounce,
                "middle_peak_position": peak_pos,
                "right_side_range_pct": rs_range, "right_side_low": rs_low,
                "base_sessions": length,
                "prior_advance_low_date": b.dates[pa.low_index].isoformat(),
                "pivot_buffer_pct": d.pivot_buffer_pct,
            },
            measures={"right_side": rs_range, "depth": depth, "undercut": undercut,
                      "middle_peak": peak_pos, "prior_advance": pa.pct},
        )  # fmt: skip
        return StrategyResult(iid, as_of, setup)
