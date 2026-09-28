"""Pattern detection and confirmation interfaces (PROJECT_DESIGN sections 4.2, 21)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from vcp_scanner.domain.market import Candle
from vcp_scanner.domain.trend import WeeklyContext
from vcp_scanner.domain.vcp import VCPPattern


@dataclass(frozen=True, slots=True)
class PatternContext:
    """Input context provided to deterministic pattern detectors and ML confirmers."""

    instrument_id: str
    as_of_date: date
    candles: tuple[Candle, ...]
    weekly_context: WeeklyContext | None = None


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    """Outcome of ML or secondary confirmation (PROJECT_DESIGN section 4.2).

    Confirmers may adjust rank or confidence, but cannot override technical gates.
    """

    confirmed: bool
    confidence: float
    notes: str = ""


@runtime_checkable
class PatternDetector(Protocol):
    """Deterministic pattern detector interface."""

    def detect(self, context: PatternContext) -> VCPPattern | None:
        """Evaluate technical structure and return detected pattern or None."""
        ...


@runtime_checkable
class PatternConfirmer(Protocol):
    """Optional pattern confirmer (e.g. future ML model, PROJECT_DESIGN section 4.2)."""

    def confirm(self, context: PatternContext) -> ConfirmationResult:
        """Assess setup quality or historical analogy without overriding technical gates."""
        ...
