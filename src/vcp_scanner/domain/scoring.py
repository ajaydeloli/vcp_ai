"""Scoring result objects (SCORING_SPECIFICATION).

The Final Setup Score is a ranking score, not a probability (PROJECT_DESIGN section 4.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from vcp_scanner.domain.enums import ConfirmationState, VCPClassification


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """One scored sub-component. Raw measurement is always kept (PROJECT_DESIGN section 4.5)."""

    component: str  # trend | vcp | volume | rs | fundamentals
    sub_component: str
    measurement: float | None
    normalized: float | None  # 0..100, None when the measurement is missing
    weight: float  # weight within its component
    points: float | None
    max_points: float


@dataclass(frozen=True, slots=True)
class SetupScore:
    instrument_id: str
    as_of_date: date
    trend_score: float | None
    vcp_score: float | None
    volume_score: float | None
    rs_score: float | None
    fundamental_score: float | None
    final_score: float | None
    weights_renormalized: bool
    classification: VCPClassification
    confirmation_state: ConfirmationState
    components: tuple[ScoreComponent, ...]
    scoring_version: str
    ranking_percentile: float | None = None
