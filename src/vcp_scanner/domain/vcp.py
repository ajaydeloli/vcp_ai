"""VCP domain objects (PROJECT_DESIGN section 21, VCP_SPECIFICATION section 42).

Raw measurements are preserved on every object. Classification is derived from
measurements; it is never gated on a score (AGENTS.md hard rule 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from vcp_scanner.domain.enums import ConfirmationState, VCPClassification, VCPStatus


@dataclass(frozen=True, slots=True)
class Contraction:
    index: int
    start_date: date
    end_date: date
    peak_price: float
    trough_price: float
    depth_pct: float
    duration_days: int
    atr_pct: float
    volume_ratio: float | None  # None when volume is missing or suspect


@dataclass(frozen=True, slots=True)
class PivotCandidate:
    """Actionable resistance for the current base (PROJECT_DESIGN section 24)."""

    pivot_price: float
    pivot_date: date
    kind: str  # STRUCTURAL | PROVISIONAL | CONFIRMED (kept as text until Phase 6 fixes the set)
    pivot_distance_pct: float | None = None
    right_side_range_pct: float | None = None


@dataclass(frozen=True, slots=True)
class VCPPattern:
    instrument_id: str
    as_of_date: date

    base_start: date
    base_end: date | None
    base_high: float
    base_low: float

    contractions: tuple[Contraction, ...]

    progressive_tightening: bool | None
    tightening_quality: float | None
    volatility_quality: float | None
    volume_quality: float | None
    pivot_quality: float | None

    pivot: PivotCandidate | None
    final_contraction_pct: float | None
    pivot_distance_pct: float | None

    classification: VCPClassification
    status: VCPStatus
    confirmation_state: ConfirmationState
    algorithm_version: str
