"""VCP setup score v2 (research; owner approval 2026-11-08, fixed).

Six parts at equal weight, each linear between its worst (0) and best (100) value and clipped
at both ends; the score is the mean of the parts that have a value (none: no score). Bounds are
the 10th / 90th percentiles of each measurement over the 891 backtest trades of the paper rules,
rounded. Parts 1-3 reward a tight pattern, parts 4-6 penalise a stock already stretched above
its averages. Used only by the research ledger ``research-score2-v1`` to choose between
breakouts when slots are short; the frozen score (scoring-1.0.0) and everything that reads it
are unchanged. A changed part, weight or bound is a new version, never an edit of this one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from vcp_scanner.backtest.engine import Bar, Signal

SCORE_V2_VERSION = "score-v2-1"


@dataclass(frozen=True, slots=True)
class Part:
    key: str
    best: float  # value scored 100
    worst: float  # value scored 0


PARTS: tuple[Part, ...] = (
    Part("right_side_range_pct", best=7.0, worst=16.0),  # VCP.pivot raw
    Part("final_contraction_pct", best=6.0, worst=13.0),  # VCP.final_contraction raw
    Part("base_depth_pct", best=11.0, worst=34.0),  # VCP.base_structure raw
    Part("above_sma50_pct", best=1.0, worst=14.0),  # close vs its 50-day average (adjusted)
    Part("stack_margin_pct", best=9.0, worst=39.0),  # TREND.ma_stack_margin raw
    Part("prior_advance_pct", best=50.0, worst=170.0),  # pattern prior_advance_return_pct
)


def part_score(value: float, part: Part) -> float:
    """100 at ``best``, 0 at ``worst``, linear between and clipped (lower is better in every
    part of v2, so ``best`` < ``worst``)."""
    x = (part.worst - value) / (part.worst - part.best) * 100
    return max(0.0, min(100.0, x))


def score_v2(values: Mapping[str, float | None]) -> float | None:
    """The mean of the part scores of the parts with a value; None when there is none."""
    got = [part_score(float(v), p) for p in PARTS if (v := values.get(p.key)) is not None]
    return sum(got) / len(got) if got else None


def above_sma50_pct(bars: Sequence[Bar], day: date) -> float | None:
    """How far ``day``'s close is above the 50-day simple average of the closes up to and
    including it, in %; None without that bar or 50 bars of history."""
    for i, b in enumerate(bars):
        if b.day == day:
            if i < 49:
                return None
            sma = sum(x.close for x in bars[i - 49 : i + 1]) / 50
            return (b.close / sma - 1) * 100 if sma > 0 else None
    return None


def rescore(
    signals: Sequence[Signal], parts: Mapping[tuple[str, date], Mapping[str, float | None]],
    bars: Mapping[str, Sequence[Bar]],
) -> list[Signal]:  # fmt: skip
    """The signals with their score replaced by score v2 (stored parts plus the 50-day distance
    from ``bars``). A signal with no v2 score keeps no score, so it ranks last."""
    out = []
    for s in signals:
        values = dict(parts.get((s.instrument_id, s.scan_date), {}))
        values["above_sma50_pct"] = above_sma50_pct(bars.get(s.instrument_id, []), s.scan_date)
        out.append(replace(s, score=score_v2(values)))
    return out
