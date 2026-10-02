"""VCP domain objects (VCP_SPECIFICATION sections 9, 9A, 10, 20, 42; DATABASE_SCHEMA 31-33).

Raw measurements are preserved on every object; classification is derived from measurements,
never gated on a score (AGENTS.md hard rule 5). Values that can be derived from other fields
(final contraction depth, tightening ratios, base depth, pivot distance) are properties, so
they cannot disagree with the fields they come from. Missing measurements are ``None``, never 0.

Constructors check structural invariants only (dates in order, one provisional contraction
at most and only the last, confirmation state matching the contractions, production
classifications only inside the Trend Template and Stage 2 gates). Thresholds are the
detector's business, not the domain's.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from vcp_scanner.domain.enums import (
    ConfirmationState,
    InvalidationReason,
    PivotSource,
    SwingKind,
    VCPClassification,
    VCPStatus,
)

#: Tolerance when checking a stored depth against its prices (float rounding only).
_DEPTH_TOLERANCE_PCT = 1e-6


def depth_pct(peak: float, trough: float) -> float:
    """Decline from ``peak`` to ``trough`` in percent of the peak (VCP_SPECIFICATION 10)."""
    return (peak - trough) / peak * 100.0


@dataclass(frozen=True, slots=True)
class Swing:
    """A swing high or low (VCP_SPECIFICATION 9, 9A, 26).

    ``price`` is the adjusted high (HIGH) or low (LOW) of ``swing_date``. ``confirmation_date``
    is the bar on which the swing became known (``right_bars`` later); ``None`` while it is
    unconfirmed. A historical run as of D may use the swing only if ``known_on(D)``.
    """

    kind: SwingKind
    swing_date: date
    price: float
    confirmation_date: date | None

    def __post_init__(self) -> None:
        if not (math.isfinite(self.price) and self.price > 0):
            raise ValueError(f"swing price must be positive, got {self.price}")
        if self.confirmation_date is not None and self.confirmation_date < self.swing_date:
            raise ValueError(
                f"confirmation_date {self.confirmation_date} is before swing_date {self.swing_date}"
            )

    @property
    def is_confirmed(self) -> bool:
        return self.confirmation_date is not None

    def known_on(self, as_of: date) -> bool:
        """True when the swing was confirmed on or before ``as_of`` (no look-ahead)."""
        return self.confirmation_date is not None and self.confirmation_date <= as_of


@dataclass(frozen=True, slots=True)
class Contraction:
    """One contraction T1, T2, ... (VCP_SPECIFICATION 8.1, 10; DATABASE_SCHEMA 32).

    Peak and trough are adjusted prices: the high at the swing high and the lowest low before
    the next confirmed swing high. ``duration_days`` counts trading bars as defined by the
    segmenter. ``atr_pct``, ``range_pct`` and ``volume_ratio`` are ``None`` when the input was
    missing or suspect. ``confirmation_date`` is ``None`` for the provisional (in-progress)
    contraction (section 9A). The tightening ratio to the prior contraction lives on
    ``VCPPattern.tightening_ratios``.
    """

    sequence_number: int
    peak_date: date
    peak_price: float
    trough_date: date
    trough_price: float
    depth_pct: float
    duration_days: int
    atr_pct: float | None
    range_pct: float | None
    volume_ratio: float | None
    confirmation_date: date | None

    def __post_init__(self) -> None:
        if self.sequence_number < 1:
            raise ValueError(f"sequence_number must be >= 1, got {self.sequence_number}")
        if self.trough_date <= self.peak_date:
            raise ValueError(f"trough_date {self.trough_date} must follow {self.peak_date}")
        if not (0 < self.trough_price < self.peak_price):
            raise ValueError(
                f"need 0 < trough_price ({self.trough_price}) < peak_price ({self.peak_price})"
            )
        expected = depth_pct(self.peak_price, self.trough_price)
        if abs(self.depth_pct - expected) > _DEPTH_TOLERANCE_PCT:
            raise ValueError(f"depth_pct {self.depth_pct} does not match prices ({expected})")
        if self.duration_days < 1:
            raise ValueError(f"duration_days must be >= 1, got {self.duration_days}")
        if self.confirmation_date is not None and self.confirmation_date < self.trough_date:
            raise ValueError(
                f"confirmation_date {self.confirmation_date} is before trough_date "
                f"{self.trough_date}"
            )

    @property
    def is_confirmed(self) -> bool:
        return self.confirmation_date is not None


@dataclass(frozen=True, slots=True)
class PivotCandidate:
    """A candidate pivot (VCP_SPECIFICATION 19-22; DATABASE_SCHEMA 33).

    ``distance_to_close_pct`` = (pivot_price - close) / close * 100 at the as-of date (21).
    ``right_side_tightness_pct`` is the right-side range below the pivot (22); ``None`` when it
    could not be measured.
    """

    pivot_price: float
    pivot_date: date
    source: PivotSource
    distance_to_close_pct: float
    touches: int
    rejection_count: int
    right_side_tightness_pct: float | None

    def __post_init__(self) -> None:
        if not (math.isfinite(self.pivot_price) and self.pivot_price > 0):
            raise ValueError(f"pivot_price must be positive, got {self.pivot_price}")
        if self.touches < 1:
            raise ValueError(f"touches must be >= 1, got {self.touches}")
        if not 0 <= self.rejection_count <= self.touches:
            raise ValueError(
                f"rejection_count ({self.rejection_count}) must be in [0, touches ({self.touches})]"
            )


@dataclass(frozen=True, slots=True)
class VCPPattern:
    """One candidate base and its measurements as of a date (VCP_SPECIFICATION 42, 43, 48;
    DATABASE_SCHEMA 31).

    Boolean criteria (``progressive_tightening``, ``volume_dryup_pass``,
    ``volatility_contraction_pass``, ``tight_pivot_pass``) follow section 18A and are ``None``
    when their inputs are missing (never a pass). ``pivot`` is the selected candidate and must
    be one of ``pivot_candidates``. ``is_primary`` marks the pattern chosen by section 61A.
    Scan id and data snapshot id are attached when the pattern is persisted.
    """

    instrument_id: str
    as_of_date: date

    base_start: date
    base_end: date | None
    base_high: float
    base_low: float
    base_duration_days: int
    prior_advance_return_pct: float | None

    contractions: tuple[Contraction, ...]

    progressive_tightening: bool | None
    final_volume_ratio: float | None
    volume_dryup_pass: bool | None
    atr_contraction_ratio: float | None
    volatility_contraction_pass: bool | None
    right_side_range_pct: float | None
    tight_pivot_pass: bool | None

    tightening_quality: float | None
    volatility_quality: float | None
    volume_quality: float | None
    pivot_quality: float | None
    base_quality: float | None

    pivot: PivotCandidate | None

    classification: VCPClassification
    status: VCPStatus
    confirmation_state: ConfirmationState

    trend_template_pass: bool
    weekly_stage2_pass: bool | None

    algorithm_version: str
    config_hash: str

    pivot_candidates: tuple[PivotCandidate, ...] = ()
    invalidation_reasons: tuple[InvalidationReason, ...] = ()
    is_primary: bool = False

    def __post_init__(self) -> None:
        self._check_base()
        self._check_contractions()
        self._check_gates_and_states()
        if self.pivot is not None and self.pivot not in self.pivot_candidates:
            raise ValueError("pivot must be one of pivot_candidates")

    def _check_base(self) -> None:
        if not 0 < self.base_low <= self.base_high:
            raise ValueError(f"need 0 < base_low ({self.base_low}) <= base_high ({self.base_high})")
        if self.base_start > self.as_of_date:
            raise ValueError(f"base_start {self.base_start} is after as_of {self.as_of_date}")
        if self.base_end is not None and not (self.base_start <= self.base_end <= self.as_of_date):
            raise ValueError(f"base_end {self.base_end} outside [base_start, as_of_date]")
        if self.base_duration_days < 1:
            raise ValueError(f"base_duration_days must be >= 1, got {self.base_duration_days}")

    def _check_contractions(self) -> None:
        numbers = [c.sequence_number for c in self.contractions]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"contraction sequence numbers must be 1..n, got {numbers}")
        for prev, nxt in zip(self.contractions, self.contractions[1:], strict=False):
            if nxt.peak_date < prev.trough_date:
                raise ValueError(
                    f"T{nxt.sequence_number} peak {nxt.peak_date} is before "
                    f"T{prev.sequence_number} trough {prev.trough_date}"
                )
        for c in self.contractions:
            if c.peak_date < self.base_start or c.trough_date > self.as_of_date:
                raise ValueError(f"T{c.sequence_number} lies outside the base")
        # Section 9A: at most one provisional contraction, and only the last one.
        if any(not c.is_confirmed for c in self.contractions[:-1]):
            raise ValueError("only the final contraction may be provisional (section 9A)")
        all_confirmed = all(c.is_confirmed for c in self.contractions)
        expected = ConfirmationState.CONFIRMED if all_confirmed else ConfirmationState.PROVISIONAL
        if self.confirmation_state is not expected:
            raise ValueError(
                f"confirmation_state {self.confirmation_state} but contractions say {expected}"
            )
        if self.classification is not VCPClassification.NONE and not self.contractions:
            raise ValueError(f"{self.classification} needs at least one contraction")

    def _check_gates_and_states(self) -> None:
        # Sections 3, 35: production classifications only inside the Trend Template and
        # weekly Stage 2 gates.
        if self.classification.is_production and not (
            self.trend_template_pass and self.weekly_stage2_pass is True
        ):
            raise ValueError(
                f"{self.classification} requires Trend Template PASS and weekly Stage 2"
            )
        # Data states are never conflated with a found pattern (section 5).
        if self.status.is_data_state and self.classification is not VCPClassification.NONE:
            raise ValueError(f"status {self.status} requires classification NONE")
        if self.status is VCPStatus.INVALIDATED and not self.invalidation_reasons:
            raise ValueError("status INVALIDATED needs at least one invalidation reason")
        if self.invalidation_reasons and self.status not in (
            VCPStatus.INVALIDATED,
            VCPStatus.FAILED,
        ):
            raise ValueError(f"invalidation reasons given but status is {self.status}")

    # Derived measurements (DATABASE_SCHEMA 31, 32). -----------------------------------

    @property
    def contraction_count(self) -> int:
        return len(self.contractions)

    @property
    def first_contraction_pct(self) -> float | None:
        return self.contractions[0].depth_pct if self.contractions else None

    @property
    def final_contraction(self) -> Contraction | None:
        """The latest contraction; it is the provisional one when the pattern is PROVISIONAL."""
        return self.contractions[-1] if self.contractions else None

    @property
    def final_contraction_pct(self) -> float | None:
        return self.contractions[-1].depth_pct if self.contractions else None

    @property
    def tightening_ratios(self) -> tuple[float, ...]:
        """D(n+1) / D(n) for each consecutive pair (section 13); empty below two."""
        return tuple(
            nxt.depth_pct / prev.depth_pct
            for prev, nxt in zip(self.contractions, self.contractions[1:], strict=False)
        )

    @property
    def max_tightening_ratio(self) -> float | None:
        ratios = self.tightening_ratios
        return max(ratios) if ratios else None

    @property
    def base_depth_pct(self) -> float:
        """(base_high - base_low) / base_high * 100 (section 8)."""
        return depth_pct(self.base_high, self.base_low)

    @property
    def pivot_distance_pct(self) -> float | None:
        """(pivot - close) / close * 100 for the selected pivot (section 21)."""
        return self.pivot.distance_to_close_pct if self.pivot is not None else None
