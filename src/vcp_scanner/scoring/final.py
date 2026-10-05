"""Final setup score and ranking percentile (SCORING_SPECIFICATION 1; Phase 7 step 2).

``final = sum(w_i x score_i) / sum(w_i)`` over the components whose score is not NULL. A NULL
component is never scored as 0 or as neutral: its weight is dropped and the rest renormalized
(``weights_renormalized = True``), and it is flagged ``<COMPONENT>_UNAVAILABLE``. Fundamentals
are NULL until Phase 8, so every score is currently flagged ``FUNDAMENTALS_UNAVAILABLE``.

Stored weights are the *effective* ones: the configured weights of the available components
rescaled to sum to 100, and 0 for an unavailable component (DATABASE_SCHEMA 35).

Ranking percentile (details fixed 2026-10-03): within one scan and one confirmation state,
``100 x (others strictly lower + 0.5 x others equal) / (n - 1)``; the best gets 100, the worst
0, ties share a value, and a group of one gets 100. Setups without a final score get none.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass

from vcp_scanner.config.models import ScoringWeights
from vcp_scanner.scoring.components import ComponentScore

COMPONENTS = ("TREND", "VCP", "VOLUME", "RS", "FUNDAMENTAL")


@dataclass(frozen=True, slots=True)
class FinalScore:
    final: float | None
    effective_weights: dict[str, float]  # component -> weight used (sums to 100, or all 0)
    fundamental_available: bool
    weights_renormalized: bool
    flags: tuple[str, ...]


def _configured(weights: ScoringWeights, pattern: str = "VCP") -> dict[str, float]:
    return {
        "TREND": weights.trend,
        pattern: weights.vcp,  # the pattern weight (STRATEGY_SPECIFICATION 9)
        "VOLUME": weights.volume,
        "RS": weights.rs,
        "FUNDAMENTAL": weights.fundamentals,
    }


def final_score(
    components: Sequence[ComponentScore | None], weights: ScoringWeights, pattern: str = "VCP"
) -> FinalScore:
    """Combine component scores (any order; a missing component counts as NULL).

    ``pattern`` names the strategy's pattern component (``VCP`` for VCP, ``PATTERN`` for the
    others); it takes the configured pattern weight (``weights.vcp``)."""
    by = {c.component: c.score for c in components if c is not None}
    scores = {k: v for k, v in by.items() if v is not None}
    configured = _configured(weights, pattern)
    names = tuple(configured)  # TREND, <pattern>, VOLUME, RS, FUNDAMENTAL
    available = {k: w for k, w in configured.items() if k in scores and w > 0}
    total = sum(available.values())
    flags = tuple(f"{k}S_UNAVAILABLE" if k == "FUNDAMENTAL" else f"{k}_UNAVAILABLE"
                  for k in names if by.get(k) is None)  # fmt: skip
    renormalized = any(configured[k] > 0 and by.get(k) is None for k in names)
    if total <= 0:
        return FinalScore(None, dict.fromkeys(names, 0.0), by.get("FUNDAMENTAL") is not None,
                          renormalized, flags)  # fmt: skip
    final = sum(w * scores[k] for k, w in available.items()) / total
    effective = {k: (available[k] / total * 100.0 if k in available else 0.0) for k in names}
    return FinalScore(final, effective, by.get("FUNDAMENTAL") is not None, renormalized, flags)


def ranking_percentiles(
    items: Sequence[tuple[Hashable, str, float | None]],
) -> dict[Hashable, float | None]:
    """Percentile per key among items of the same group (the confirmation state), as in the
    module docstring. ``items`` = (key, group, final score)."""
    groups: dict[str, list[float]] = defaultdict(list)
    for _, group, score in items:
        if score is not None:
            groups[group].append(score)
    out: dict[Hashable, float | None] = {}
    for key, group, score in items:
        if score is None:
            out[key] = None
            continue
        values = groups[group]
        n = len(values)
        if n == 1:
            out[key] = 100.0
            continue
        lower = sum(1 for v in values if v < score)
        equal = sum(1 for v in values if v == score) - 1  # others only
        out[key] = 100.0 * (lower + 0.5 * equal) / (n - 1)
    return out
