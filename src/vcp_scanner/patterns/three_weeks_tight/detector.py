"""Three Weeks Tight detector, ``three_weeks_tight-1.0.0`` (STRATEGY_SPECIFICATION 14).

Three weekly closes in a row, each within about 1-1.5 % of the week before, after an advance.
Steps (section 14.1), on adjusted daily bars ending at the as-of bar ``t``:

1. Weekly bars (shared ``weekly_bars``); only completed weeks are used: the as-of week is left
   out Monday-Thursday and counts on a Friday (decision T1).
2. Candidates: the 3 completed weeks ending ``k`` weeks ago, k = 0 .. ``max_age_weeks``, most
   recent first; the first whose largest week-to-week close change meets the loosest tier is
   the pattern (decision T2). None: NOT_TIGHT.
3. Pattern high / low of its daily bars; pivot = high x (1 + buffer), stop = low.
4. Breakout: a recorded one for this pattern, else the first close above the pivot after week
   3 on breakout volume. Without one, a close below the pattern low since week 3 breaks the
   pattern: no setup (BELOW_PATTERN; older candidates are not tried).
5. Prior advance into the highest high from ``prior_high_window_days`` before week 1 to the end
   of week 3 >= the minimum.
6. Tiers per section 14.2; grade >= 2 also needs Trend Template PASS and weekly Stage 2.
"""

from __future__ import annotations

from vcp_scanner.config.models import ThreeWeeksTightSettings
from vcp_scanner.domain.strategy import Breakout, Setup, StrategyResult
from vcp_scanner.features.base_measures import (
    base_extremes,
    max_weekly_close_change_pct,
    prior_advance,
    volume_dryup_ratio,
    weekly_bars,
    weekly_close_range_pct,
)
from vcp_scanner.patterns.strategy_base import StrategyContext, find_breakout, setup_status

ALGORITHM_VERSION = "three_weeks_tight-1.0.0"
STRATEGY_ID = "three_weeks_tight"
SCORE_SUBS = ("tightness", "pattern_depth", "prior_advance", "tight_weeks", "near_high")
HIGH_52W_BARS = 252


class ThreeWeeksTightDetector:
    strategy_id = STRATEGY_ID
    algorithm_version = ALGORITHM_VERSION

    def __init__(self, settings: ThreeWeeksTightSettings) -> None:
        self._s = settings

    def lookback_bars(self) -> int:
        d = self._s.detector
        pattern = (3 + d.max_age_weeks + 1) * 5 + d.prior_high_window_days
        return max(HIGH_52W_BARS, pattern + d.prior_advance_lookback_days) + 10

    def detect(self, ctx: StrategyContext) -> StrategyResult:
        d = self._s.detector
        b = ctx.bars
        n = len(b.dates)
        iid, as_of = ctx.instrument_id, ctx.as_of_date
        if n == 0 or b.dates[-1] != as_of:
            return StrategyResult(iid, as_of, None, None, "STALE_DATA")
        weeks = weekly_bars(b.dates, b.high, b.low, b.close)
        completed = weeks[:-1] if weeks and weeks[-1].partial else weeks
        loosest = max(t.max_close_change_pct for t in self._s.classification.values())
        if len(completed) < 4:  # the first week may be cut by the bar window
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")

        chosen = None
        for k in range(d.max_age_weeks + 1):
            if len(completed) - 3 - k < 1:
                break
            w = completed[len(completed) - 3 - k : len(completed) - k]
            change = max_weekly_close_change_pct([x.close for x in w], 2)
            if change is not None and change <= loosest:
                chosen = (k, w, change)
                break
        if chosen is None:
            return StrategyResult(iid, as_of, None, "NOT_TIGHT")
        age, w, change = chosen

        index = {dt: i for i, dt in enumerate(b.dates)}
        i0, i1 = index[w[0].first_day], index[w[-1].week_end]
        ext = base_extremes(b.dates, b.high, b.low, i0, i1)
        if ext is None:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        pivot = ext.high * (1.0 + d.pivot_buffer_pct / 100.0)
        t = n - 1
        breakout: Breakout | None = ctx.breakouts.get(b.dates[i0]) or find_breakout(
            b, pivot, i1 + 1, ctx.breakout_volume_ratio
        )
        if breakout is None and any(b.close[j] < ext.low for j in range(i1 + 1, n)):
            return StrategyResult(iid, as_of, None, "BELOW_PATTERN")

        h0 = max(0, i0 - d.prior_high_window_days)
        top = min(range(h0, i1 + 1), key=lambda i: (-b.high[i], i))
        pa = prior_advance(b.high, b.low, top, d.prior_advance_lookback_days)
        if pa is None:
            return StrategyResult(iid, as_of, None, "INSUFFICIENT_HISTORY")
        if pa.pct < d.min_prior_advance_pct:
            reason = "NO_PRIOR_ADVANCE" if pa.window_complete else "INSUFFICIENT_HISTORY"
            return StrategyResult(iid, as_of, None, reason)

        # Consecutive weeks up to the pattern's last week meeting the grade-2 change rule.
        standard = next((t.max_close_change_pct for t in self._s.classification.values()
                         if t.grade == 2), loosest)  # fmt: skip
        end_w = len(completed) - 1 - age
        tight_weeks = 1
        while end_w - tight_weeks >= 0:
            a, c = completed[end_w - tight_weeks].close, completed[end_w - tight_weeks + 1].close
            if a <= 0 or abs(c / a - 1.0) * 100.0 > standard:
                break
            tight_weeks += 1
        depth = ext.depth_pct
        dryup = volume_dryup_ratio(b.volume, i1 - i0 + 1, d.dryup_base_days, i1)
        high_52 = max(b.high[max(0, n - HIGH_52W_BARS) :])
        near_high = (high_52 - pivot) / high_52 * 100.0

        grade, cls = 0, "NONE"
        unmet: dict[str, list[str]] = {}
        trend_ok = ctx.trend_template_pass and ctx.weekly_stage2_pass is True
        for name, tier in self._s.classification.items():
            fails: list[str] = []
            if change > tier.max_close_change_pct:
                fails.append("max_close_change_pct")
            if tier.max_depth_pct is not None and depth > tier.max_depth_pct:
                fails.append("max_depth_pct")
            if tier.max_dryup_ratio is not None and (
                    dryup is None or dryup > tier.max_dryup_ratio):  # fmt: skip
                fails.append("max_dryup_ratio")
            if tier.grade >= 2 and not trend_ok:
                fails.append("trend_template_and_stage2")
            if fails:
                unmet[name.upper()] = fails
            elif tier.grade > grade:
                grade, cls = tier.grade, name.upper()
        if grade == 0:  # pragma: no cover - the loosest tier's change rule held above
            return StrategyResult(iid, as_of, None, "NOT_TIGHT")

        close = b.close[t]
        closes = [x.close for x in w]
        status = setup_status(close, pivot, grade, breakout,
                              self._s.ranking.pivot_ready_max_distance_pct)  # fmt: skip
        setup = Setup(
            instrument_id=iid, as_of_date=as_of,
            base_start=b.dates[i0], base_end=breakout.breakout_date if breakout else None,
            base_high=ext.high, base_low=ext.low, base_depth_pct=depth,
            base_duration_days=ext.sessions, prior_advance_pct=pa.pct,
            pivot_price=pivot, pivot_date=b.dates[ext.high_index], pivot_source="PATTERN_HIGH",
            pivot_distance_pct=(pivot - close) / close * 100.0,
            stop_reference_price=ext.low, dryup_volume_ratio=dryup,
            classification=cls, grade=grade, status=status, confirmation_state="CONFIRMED",
            trend_gate="PASS" if ctx.trend_template_pass else "FAIL",
            weekly_stage2_pass=ctx.weekly_stage2_pass, breakout=breakout, unmet_rules=unmet,
            details={
                "weekly_closes": closes, "max_close_change_pct": change,
                "close_range_pct": weekly_close_range_pct(closes, 3),
                "tight_weeks": tight_weeks, "age_weeks": age,
                "week_ends": [x.week_end.isoformat() for x in w],
                "high_52w": high_52,
            },
            measures={"tightness": change, "pattern_depth": depth, "prior_advance": pa.pct,
                      "tight_weeks": float(tight_weeks), "near_high": near_high},
        )  # fmt: skip
        return StrategyResult(iid, as_of, setup)
