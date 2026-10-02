"""Classification, invalidation, breakout and status (VCP_SPECIFICATION 5, 24-31, 45, 46, 61B).

**Classification** (sections 28-31, 18A): the highest tier whose every rule holds, in the order
A_PLUS_VCP, VCP, VCP_LIKE, else NONE. A tier's rules: contraction count within
``[min_contractions, max_contractions or vcp.contractions.max]``; final contraction depth <=
``max_final_contraction_pct``; each required section 18A criterion is ``True`` (``None`` - missing
input - never satisfies it). Production tiers (VCP, A_PLUS_VCP) also need Trend Template PASS
and weekly Stage 2 (sections 3, 35); without them the best possible class is VCP_LIKE. Every
unmet rule is reported per tier for explainability (section 43).

**Invalidation** (sections 24-25, rules chosen 2026-10-02, VCP_SPECIFICATION 61B):

* ``TREND_TEMPLATE_FAIL``: ``invalidation.trend_template_failure`` and the Trend Template did not
  pass.
* ``BASE_STRUCTURE_FAIL``: with two or more contractions, the close is more than
  ``invalidation.base_low_break_pct`` below the lowest low of the base before the final
  contraction (from the base start to the final contraction's peak): the last leg has cut
  through the base.
* ``EXCESS_VOLATILITY``: the mean true range % of the last ``pivot.right_side_window_days`` bars
  is at least ``invalidation.volatility_expansion_multiple`` x T1's (the widest part of the base).

**Breakout** (section 45): the first bar after the structural pivot's date (the final
contraction's peak, else the base high) that closes above it on volume >=
``breakout.min_volume_ratio`` x the mean of the 50 bars before it. A close above the pivot
without that volume is not a breakout. The structural pivot is used because it is fixed; a
right-side pivot moves with every new bar, so its breakouts are recorded by the daily run
against the previous day's stored pivot (VCP_SPECIFICATION 47, 61B).

**Status** (first that applies): a data state given by the caller (DATA_NOT_READY,
STALE_DATA, INSUFFICIENT_DATA); INVALIDATED (any reason); BREAKOUT (a breakout and the close is
still at or above the pivot); FAILED (a breakout, then a close back below the pivot); PIVOT_READY
(production class, pivot 0..``pivot.max_distance_pct`` above the close, section 46); FORMING.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from vcp_scanner.config.models import (
    ClassificationConfig,
    TierClassificationConfig,
    VCPThresholdsConfig,
)
from vcp_scanner.domain.enums import InvalidationReason, VCPClassification, VCPStatus
from vcp_scanner.domain.vcp import PivotCandidate
from vcp_scanner.patterns.vcp.measurements import PriceSeries, VCPMeasurements, _mean, _tr_pct
from vcp_scanner.patterns.vcp.segmentation import BaseSegmentation

_TIERS: tuple[tuple[VCPClassification, str], ...] = (
    (VCPClassification.A_PLUS_VCP, "a_plus"),
    (VCPClassification.VCP, "vcp"),
    (VCPClassification.VCP_LIKE, "vcp_like"),
)
_CRITERIA = {
    "require_progressive_tightening": "progressive_tightening",
    "require_volume_dryup": "volume_dryup_pass",
    "require_volatility_contraction": "volatility_contraction_pass",
    "require_tight_pivot": "tight_pivot_pass",
}


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    classification: VCPClassification
    #: Unmet rules per tier (empty tuple = the tier's rules all hold).
    unmet: Mapping[VCPClassification, tuple[str, ...]]


def _tier_unmet(
    tier: TierClassificationConfig,
    production: bool,
    count: int,
    final_depth: float | None,
    measures: VCPMeasurements,
    vcp: VCPThresholdsConfig,
    trend_template_pass: bool,
    weekly_stage2_pass: bool | None,
) -> tuple[str, ...]:
    unmet: list[str] = []
    if count < tier.min_contractions:
        unmet.append("min_contractions")
    if count > (tier.max_contractions or vcp.contractions.max):
        unmet.append("max_contractions")
    if final_depth is None or final_depth > tier.max_final_contraction_pct:
        unmet.append("max_final_contraction_pct")
    for flag, required in tier.requirements().items():
        if required and getattr(measures, _CRITERIA[flag]) is not True:
            unmet.append(flag)
    if production and not trend_template_pass:
        unmet.append("trend_template")
    if production and weekly_stage2_pass is not True:
        unmet.append("weekly_stage2")
    return tuple(unmet)


def classify(
    base: BaseSegmentation,
    measures: VCPMeasurements,
    classification: ClassificationConfig,
    vcp: VCPThresholdsConfig,
    *,
    trend_template_pass: bool,
    weekly_stage2_pass: bool | None,
) -> ClassificationResult:
    """The highest tier whose rules all hold (module docstring)."""
    count = len(base.contractions)
    final_depth = base.contractions[-1].depth_pct if base.contractions else None
    unmet = {
        cls: _tier_unmet(
            getattr(classification, name),
            cls.is_production,
            count,
            final_depth,
            measures,
            vcp,
            trend_template_pass,
            weekly_stage2_pass,
        )  # fmt: skip
        for cls, name in _TIERS
    }
    best = next((cls for cls, _ in _TIERS if not unmet[cls]), VCPClassification.NONE)
    return ClassificationResult(best, unmet)


def invalidation_reasons(
    series: PriceSeries,
    base: BaseSegmentation,
    measures: VCPMeasurements,
    vcp: VCPThresholdsConfig,
    *,
    trend_template_pass: bool,
) -> tuple[InvalidationReason, ...]:
    """Reasons that invalidate the base as of its date (module docstring), in enum order."""
    n = _as_of_len(series, base.as_of)
    reasons: list[InvalidationReason] = []
    inv = vcp.invalidation
    if inv.trend_template_failure and not trend_template_pass:
        reasons.append(InvalidationReason.TREND_TEMPLATE_FAIL)
    if len(base.contractions) >= 2:
        final_peak = base.contractions[-1].peak_index
        prior_low = min(series.low[base.base_start_index : final_peak + 1])
        if series.close[n - 1] < prior_low * (1 - inv.base_low_break_pct / 100):
            reasons.append(InvalidationReason.BASE_STRUCTURE_FAIL)
    t1 = measures.contractions[0].tr_pct if measures.contractions else None
    w = vcp.pivot.right_side_window_days
    recent = _mean([_tr_pct(series, i) for i in range(max(n - w, 1), n)]) if n > 1 else None
    if (
        t1 is not None
        and t1 > 0
        and recent is not None
        and recent >= inv.volatility_expansion_multiple * t1
    ):
        reasons.append(InvalidationReason.EXCESS_VOLATILITY)
    return tuple(reasons)


def _as_of_len(series: PriceSeries, as_of: date) -> int:
    n = 0
    while n < len(series.dates) and series.dates[n] <= as_of:
        n += 1
    return n


@dataclass(frozen=True, slots=True)
class Breakout:
    """The breakout bar (section 45) and whether the as-of close is still at or above the pivot."""

    breakout_date: date
    volume_ratio: float
    holding: bool


def find_breakout(
    series: PriceSeries, pivot: PivotCandidate, as_of: date, vcp: VCPThresholdsConfig
) -> Breakout | None:
    """First close above ``pivot`` after its date with enough volume (module docstring)."""
    n = _as_of_len(series, as_of)
    long_p = vcp.volume.long_period
    start = series.dates.index(pivot.pivot_date) + 1
    for i in range(start, n):
        if series.close[i] <= pivot.pivot_price or i - long_p < 0:
            continue
        v = series.volume[i]
        base = _mean(series.volume[i - long_p : i])
        if v is None or base is None or base <= 0:
            continue
        ratio = v / base
        if ratio >= vcp.breakout.min_volume_ratio:
            return Breakout(series.dates[i], ratio, series.close[n - 1] >= pivot.pivot_price)
    return None


def decide_status(
    *,
    data_state: VCPStatus | None,
    reasons: tuple[InvalidationReason, ...],
    breakout: Breakout | None,
    classification: VCPClassification,
    pivot: PivotCandidate | None,
    vcp: VCPThresholdsConfig,
) -> VCPStatus:
    """Status by precedence (module docstring)."""
    if data_state is not None:
        if not data_state.is_data_state:
            raise ValueError(f"data_state must be a data status, got {data_state}")
        return data_state
    if reasons:
        return VCPStatus.INVALIDATED
    if breakout is not None:
        return VCPStatus.BREAKOUT if breakout.holding else VCPStatus.FAILED
    if (
        classification.is_production
        and pivot is not None
        and 0 <= pivot.distance_to_close_pct <= vcp.pivot.max_distance_pct
    ):
        return VCPStatus.PIVOT_READY
    return VCPStatus.FORMING
