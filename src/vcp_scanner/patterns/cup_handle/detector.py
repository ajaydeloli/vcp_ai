"""Cup and handle detector, ``cup_handle-1.1.0`` (STRATEGY_SPECIFICATION 15.1).

A rounded, U-shaped correction after an advance; the right side climbs back near the old high,
then a short, shallow pullback (the handle) forms in the upper half on light volume. Steps, on
adjusted daily bars ending at the as-of bar ``t`` (earliest bar on a tie throughout):

1. Right lip ``R`` (the handle's start) = the highest high among bars ``t - max_handle + 1`` ..
   ``t - min_handle + 1``. Pivot = high[R] x (1 + buffer); it never moves.
2. Left lip ``L`` = the highest high among bars ``R - max_cup`` .. ``R - min_cup``. No high
   between them may exceed the higher rim x (1 + tolerance) (step 5b reading A), and the right
   lip may be at most ``max_right_lip_above_pct`` above the left (1.1.0: a stock that already
   rallied past its old high is not in a cup): else NO_CUP.
3. Breakout: a recorded one for this base (same left lip and pivot), else the first close above
   the pivot after ``R`` on breakout volume. After a breakout the handle ends the day before it
   (the shape is frozen; ``base_end`` = the breakout date).
4. The handle stays below its start: without a breakout, a close more than ``max_overshoot_pct``
   above the pivot is MOVED_ABOVE_BASE; any other high above high[R] up to the handle's end is
   NO_HANDLE (reading B). A handle shorter than ``min_handle`` (a breakout right after ``R``)
   is NO_HANDLE too.
5. Cup bottom ``B`` = the lowest low of ``L`` .. ``R``; handle low ``H`` = the lowest low of
   ``R`` .. the handle's end. Measurements, the rounded-vs-V rule (decision C1) and tiers per
   section 15.1; grade >= 2 also needs Trend Template PASS and weekly Stage 2. No tier met:
   TOO_DEEP.

Points are found from the as-of bar, so a cup stays visible for only a few sessions after its
breakout (reading C); trades come from the scans before it.
"""

from __future__ import annotations

from vcp_scanner.config.models import CupHandleSettings
from vcp_scanner.domain.strategy import Breakout, Setup, StrategyResult
from vcp_scanner.features.base_measures import base_extremes, prior_advance, volume_dryup_ratio
from vcp_scanner.patterns.strategy_base import (
    StrategyContext,
    find_breakout,
    highest,
    lowest,
    setup_status,
)

ALGORITHM_VERSION = "cup_handle-1.1.0"  # 1.1.0: right lip at most 3 % above the left
STRATEGY_ID = "cup_handle"
SCORE_SUBS = ("roundness", "handle_depth", "handle_position", "cup_depth", "prior_advance")


class CupHandleDetector:
    strategy_id = STRATEGY_ID
    algorithm_version = ALGORITHM_VERSION

    def __init__(self, settings: CupHandleSettings) -> None:
        self._s = settings

    def lookback_bars(self) -> int:
        """325 + 25 + 120 + 10 = 480 with the default config (section 15.1)."""
        d = self._s.detector
        return d.max_cup_days + d.max_handle_days + d.prior_advance_lookback_days + 10

    def detect(self, ctx: StrategyContext) -> StrategyResult:  # noqa: C901, PLR0911, PLR0912, PLR0915
        d = self._s.detector
        b = ctx.bars
        n = len(b.dates)
        iid, as_of = ctx.instrument_id, ctx.as_of_date
        if n == 0 or b.dates[-1] != as_of:
            return StrategyResult(iid, as_of, None, None, "STALE_DATA")
        if n < d.min_cup_days + d.min_handle_days + 1:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        t = n - 1

        # 1. Right lip (handle start) and pivot.
        r = highest(b.high, max(0, t - d.max_handle_days + 1), t - d.min_handle_days + 1)
        if r - d.min_cup_days < 0:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        pivot = b.high[r] * (1.0 + d.pivot_buffer_pct / 100.0)

        # 2. Left lip and the rim check.
        lip = highest(b.high, max(0, r - d.max_cup_days), r - d.min_cup_days)
        rim = max(b.high[lip], b.high[r]) * (1.0 + d.lip_tolerance_pct / 100.0)
        above = b.high[lip] * (1.0 + d.max_right_lip_above_pct / 100.0)
        if b.high[r] > above or any(b.high[k] > rim for k in range(lip + 1, r)):
            return StrategyResult(iid, as_of, None, "NO_CUP")

        # 3. Breakout (recorded for this base, else searched) freezes the handle.
        recorded = ctx.breakouts.get(b.dates[lip])
        breakout: Breakout | None
        if (recorded is not None and recorded.breakout_date > b.dates[r]
                and abs(recorded.pivot_price - pivot) <= 1e-9 * pivot):  # fmt: skip
            breakout = recorded
        else:
            breakout = find_breakout(b, pivot, r + 1, ctx.breakout_volume_ratio)
        end = t
        if breakout is not None and breakout.breakout_date in b.dates:
            end = list(b.dates).index(breakout.breakout_date) - 1

        # 4. The handle stays below its start.
        if breakout is None:
            limit = pivot * (1.0 + d.max_overshoot_pct / 100.0)
            if any(b.close[k] > limit for k in range(r + 1, n)):
                return StrategyResult(iid, as_of, None, "MOVED_ABOVE_BASE")
        if end - r + 1 < d.min_handle_days or any(
                b.high[k] > b.high[r] for k in range(r + 1, end + 1)):  # fmt: skip
            return StrategyResult(iid, as_of, None, "NO_HANDLE")

        pa = prior_advance(b.high, b.low, lip, d.prior_advance_lookback_days)
        if pa is None:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        if pa.pct < d.min_prior_advance_pct:
            reason = "NO_PRIOR_ADVANCE" if pa.window_complete else "INSUFFICIENT_HISTORY"
            return StrategyResult(iid, as_of, None, reason)

        # 5. Cup bottom, handle low and the measurements.
        bot = lowest(b.low, lip, r)
        hl = lowest(b.low, r, end)
        left, low_b, right = b.high[lip], b.low[bot], b.high[r]
        cup_range = left - low_b
        if left <= 0 or right <= 0 or cup_range <= 0:  # pragma: no cover - degenerate bars
            return StrategyResult(iid, as_of, None, "NO_CUP")
        cup_depth = cup_range / left * 100.0
        gap = (left - right) / left * 100.0
        zone = low_b + d.bottom_zone_pct / 100.0 * cup_range
        cup_bars = r - lip + 1
        bottom_sessions = sum(1 for k in range(lip, r + 1) if b.low[k] <= zone)
        bottom_share = bottom_sessions / cup_bars
        bottom_pos = (bot - lip) / (r - lip)
        rounded = (bottom_share >= d.min_bottom_share
                   and bottom_sessions >= d.min_bottom_sessions
                   and d.min_bottom_position <= bottom_pos <= d.max_bottom_position)  # fmt: skip
        handle_low = b.low[hl]
        handle_depth = (right - handle_low) / right * 100.0
        handle_pos = (handle_low - low_b) / cup_range
        handle_sessions = end - r + 1
        dryup = volume_dryup_ratio(b.volume, handle_sessions, d.dryup_base_days, end)
        sma = (sum(b.close[end - d.sma_days + 1 : end + 1]) / d.sma_days
               if end - d.sma_days + 1 >= 0 else None)  # fmt: skip
        ext = base_extremes(b.dates, b.high, b.low, lip, r)
        cup_weeks = ext.weeks if ext is not None else None

        grade, cls = 0, "NONE"
        unmet: dict[str, list[str]] = {}
        trend_ok = ctx.trend_template_pass and ctx.weekly_stage2_pass is True
        for name, tier in self._s.classification.items():
            fails: list[str] = []
            if cup_depth > tier.max_cup_depth_pct:
                fails.append("max_cup_depth_pct")
            if tier.min_cup_depth_pct is not None and cup_depth < tier.min_cup_depth_pct:
                fails.append("min_cup_depth_pct")
            if tier.require_rounded and not rounded:
                fails.append("rounded")
            if handle_depth > tier.max_handle_depth_pct:
                fails.append("max_handle_depth_pct")
            if tier.min_handle_position is not None and handle_pos < tier.min_handle_position:
                fails.append("min_handle_position")
            if gap > tier.max_right_lip_gap_pct:
                fails.append("max_right_lip_gap_pct")
            if tier.min_prior_advance_pct is not None and pa.pct < tier.min_prior_advance_pct:
                fails.append("min_prior_advance_pct")
            if tier.max_handle_dryup_ratio is not None and (
                    dryup is None or dryup > tier.max_handle_dryup_ratio):  # fmt: skip
                fails.append("max_handle_dryup_ratio")
            if tier.handle_low_above_sma and (sma is None or handle_low <= sma):
                fails.append("handle_low_above_sma")
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
            base_high=left, base_low=low_b, base_depth_pct=cup_depth,
            base_duration_days=end - lip + 1, prior_advance_pct=pa.pct,
            pivot_price=pivot, pivot_date=b.dates[r], pivot_source="HANDLE_HIGH",
            pivot_distance_pct=(pivot - close) / close * 100.0,
            stop_reference_price=handle_low, dryup_volume_ratio=dryup,
            classification=cls, grade=grade, status=status, confirmation_state="CONFIRMED",
            trend_gate="PASS" if ctx.trend_template_pass else "FAIL",
            weekly_stage2_pass=ctx.weekly_stage2_pass, breakout=breakout, unmet_rules=unmet,
            details={
                "left_lip_date": b.dates[lip].isoformat(),
                "bottom_date": b.dates[bot].isoformat(),
                "right_lip_date": b.dates[r].isoformat(),
                "handle_low_date": b.dates[hl].isoformat(),
                "cup_depth_pct": cup_depth, "handle_depth_pct": handle_depth,
                "cup_sessions": cup_bars - 1, "cup_weeks": cup_weeks,
                "handle_sessions": handle_sessions, "right_lip_gap_pct": gap,
                "bottom_share": bottom_share, "bottom_sessions": bottom_sessions,
                "bottom_position": bottom_pos, "rounded": rounded,
                "handle_position": handle_pos, "handle_dryup_ratio": dryup,
                "handle_low": handle_low, "sma_close": sma,
                "prior_advance_low_date": b.dates[pa.low_index].isoformat(),
                "pivot_buffer_pct": d.pivot_buffer_pct,
            },
            measures={"roundness": bottom_share, "handle_depth": handle_depth,
                      "handle_position": handle_pos, "cup_depth": cup_depth,
                      "prior_advance": pa.pct},
        )  # fmt: skip
        return StrategyResult(iid, as_of, setup)
