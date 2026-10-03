"""Score every Trend Template passer of a scan (SCORING_SPECIFICATION 1-6, 11; Phase 7 step 3).

Inputs per instrument (``SetupInputs``) come from the Trend Template scan (gate, RS rank), the
primary VCP pattern of the VCP scan of the same date and config (measurements, class, status),
the feature table (close, 52-week high, SMA50/200, SMA200 21 sessions earlier) and the adjusted
bars (volume measurements). Scoring itself is pure: ``score_setup`` and ``score_scan``.

**Scope** (owner decision 2026-10-03): every passer is scored and stored, so research can test
the components on their own; only **eligible** setups are ranked (section 1): classification
VCP_LIKE or better, and status not FAILED / INVALIDATED or a data state (an invalid or broken
pattern is not an actionable setup; fixed 2026-10-03). Fundamentals are NULL until Phase 8.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from vcp_scanner.config.models import ScoringConfig
from vcp_scanner.scoring.components import (
    ComponentScore,
    measure_distribution,
    measure_up_down_volume,
    rs_score,
    trend_score,
    vcp_score,
    volume_score,
)
from vcp_scanner.scoring.final import FinalScore, final_score, ranking_percentiles

RANKED_CLASSES = frozenset({"VCP_LIKE", "VCP", "A_PLUS_VCP"})
RANKED_STATUSES = frozenset({"FORMING", "PIVOT_READY", "BREAKOUT"})
BARS_NEEDED = 76  # 25 distribution days + their 50-session averages + one previous close


@dataclass(frozen=True, slots=True)
class PatternInputs:
    classification: str
    status: str
    confirmation_state: str | None
    contraction_count: int | None
    max_tightening_ratio: float | None
    final_contraction_pct: float | None
    volatility_ratio: float | None  # of the configured vcp.volatility.measure
    right_side_range_pct: float | None
    base_depth_pct: float | None
    final_volume_ratio: float | None


@dataclass(frozen=True, slots=True)
class SetupInputs:
    instrument_id: str
    as_of: date
    rs_rank: float | None
    close: float | None
    high_252: float | None
    sma50: float | None
    sma200: float | None
    sma200_lagged: float | None
    closes: Sequence[float]  # adjusted closes up to the as-of bar
    volumes: Sequence[float | None]
    pattern: PatternInputs | None  # the primary VCP pattern, None without one


@dataclass(frozen=True, slots=True)
class ScoredSetup:
    instrument_id: str
    as_of: date
    classification: str | None
    status: str | None
    confirmation_state: str | None
    eligible: bool
    components: tuple[ComponentScore, ...]  # TREND, VCP, VOLUME, RS
    final: FinalScore
    ranking_percentile: float | None = None


def is_eligible(p: PatternInputs | None) -> bool:
    return p is not None and p.classification in RANKED_CLASSES and p.status in RANKED_STATUSES


def score_setup(x: SetupInputs, cfg: ScoringConfig, min_rs_rank: float) -> ScoredSetup:
    c = cfg.components
    p = x.pattern
    trend = trend_score(x.close, x.high_252, x.sma50, x.sma200, x.sma200_lagged,
                        c.trend.weights, c.trend.bounds)  # fmt: skip
    vcp = vcp_score(
        p.contraction_count if p else None, p.max_tightening_ratio if p else None,
        p.final_contraction_pct if p else None, p.volatility_ratio if p else None,
        p.right_side_range_pct if p else None, p.base_depth_pct if p else None,
        c.vcp.weights, c.vcp.bounds,
    )  # fmt: skip
    volume = volume_score(
        p.final_volume_ratio if p else None,
        measure_up_down_volume(x.closes, x.volumes),
        measure_distribution(x.closes, x.volumes, cfg.high_volume_multiple),
        c.volume.weights, c.volume.bounds,
    )  # fmt: skip
    rs = rs_score(x.rs_rank, min_rs_rank)
    comps = (trend, vcp, volume, rs)
    return ScoredSetup(
        x.instrument_id, x.as_of, p.classification if p else None, p.status if p else None,
        p.confirmation_state if p else None, is_eligible(p), comps,
        final_score(comps, cfg.weights),
    )  # fmt: skip


def score_scan(
    inputs: Sequence[SetupInputs], cfg: ScoringConfig, min_rs_rank: float
) -> list[ScoredSetup]:
    """Score every setup, then rank the eligible ones per confirmation state."""
    from dataclasses import replace

    scored = [score_setup(x, cfg, min_rs_rank) for x in inputs]
    pct = ranking_percentiles(
        [(s.instrument_id, s.confirmation_state or "", s.final.final)
         for s in scored if s.eligible]
    )  # fmt: skip
    return [replace(s, ranking_percentile=pct.get(s.instrument_id)) if s.eligible else s
            for s in scored]  # fmt: skip
