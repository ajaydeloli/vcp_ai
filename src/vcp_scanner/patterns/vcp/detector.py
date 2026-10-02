"""VCP detector: orchestrates swings, segmentation, measurements, pivots, classification and
status for one instrument as of a date (VCP_SPECIFICATION 26, 49, 50, 61, 61A).

Deterministic and as-of safe: only bars dated on or before ``as_of`` are read, and nothing else
(no clock, no randomness, no I/O). Gates are inputs: the caller passes the Trend Template and
weekly Stage 2 verdicts for the date, and a data state (DATA_NOT_READY, STALE_DATA,
INSUFFICIENT_DATA) when the data gate blocks the instrument.

Outcomes:

* data state given -> no pattern, that status;
* no base (section 8.1) -> no pattern; ``INSUFFICIENT_DATA`` when the history is too short to
  test the prior advance, otherwise no status (``no_pattern_reason`` says why);
* otherwise a ``VCPPattern`` with every measurement, its classification (possibly NONE), status
  and confirmation state. One candidate base exists per date in V1, so it is the primary
  (section 61A ordering is implemented in ``select_primary`` for when several exist).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date

from vcp_scanner.config.models import ClassificationConfig, VCPThresholdsConfig
from vcp_scanner.domain.enums import ConfirmationState, VCPClassification, VCPStatus
from vcp_scanner.domain.vcp import Contraction, VCPPattern
from vcp_scanner.patterns.vcp.classifier import (
    Breakout,
    ClassificationResult,
    classify,
    decide_status,
    find_breakout,
    invalidation_reasons,
)
from vcp_scanner.patterns.vcp.measurements import PriceSeries, VCPMeasurements, measure
from vcp_scanner.patterns.vcp.pivots import PivotSelection, select_pivots
from vcp_scanner.patterns.vcp.segmentation import NoBaseReason, SegmentationResult, segment_base
from vcp_scanner.versioning import VCP_ALGORITHM_VERSION


@dataclass(frozen=True, slots=True)
class VCPDetection:
    """Everything the detector found for one instrument and date (explainability, section 43)."""

    instrument_id: str
    as_of: date
    pattern: VCPPattern | None
    status: VCPStatus | None
    no_pattern_reason: str | None
    segmentation: SegmentationResult | None = None
    measurements: VCPMeasurements | None = None
    pivots: PivotSelection | None = None
    classification: ClassificationResult | None = None
    breakout: Breakout | None = None


class VCPDetector:
    """Section 50 orchestrator bound to the ``vcp`` and ``classification`` settings."""

    def __init__(
        self,
        vcp: VCPThresholdsConfig,
        classification: ClassificationConfig,
        *,
        config_hash: str,
        algorithm_version: str = VCP_ALGORITHM_VERSION,
    ) -> None:
        self._vcp = vcp
        self._cls = classification
        self._config_hash = config_hash
        self._version = algorithm_version

    def detect(
        self,
        instrument_id: str,
        series: PriceSeries,
        as_of: date,
        *,
        trend_template_pass: bool,
        weekly_stage2_pass: bool | None,
        data_state: VCPStatus | None = None,
    ) -> VCPDetection:
        vcp = self._vcp
        if data_state is not None:
            status = decide_status(
                data_state=data_state, reasons=(), breakout=None,
                classification=VCPClassification.NONE, pivot=None, vcp=vcp,
            )  # fmt: skip
            return VCPDetection(instrument_id, as_of, None, status, f"DATA:{status.value}")

        seg = segment_base(series.dates, series.high, series.low, as_of=as_of, config=vcp)
        base = seg.base
        if base is None:
            assert seg.no_base_reason is not None
            status_nb = (
                VCPStatus.INSUFFICIENT_DATA
                if seg.no_base_reason is NoBaseReason.INSUFFICIENT_HISTORY
                else None
            )
            return VCPDetection(
                instrument_id, as_of, None, status_nb, seg.no_base_reason.value, seg
            )

        measures = measure(series, base, vcp)
        pivots = select_pivots(series, seg, measures, vcp)
        cls = classify(
            base, measures, self._cls, vcp,
            trend_template_pass=trend_template_pass, weekly_stage2_pass=weekly_stage2_pass,
        )  # fmt: skip
        reasons = invalidation_reasons(
            series, base, measures, vcp, trend_template_pass=trend_template_pass
        )
        breakout = (
            find_breakout(series, pivots.structural, as_of, vcp) if pivots.structural else None
        )
        status = decide_status(
            data_state=None, reasons=reasons, breakout=breakout,
            classification=cls.classification, pivot=pivots.primary, vcp=vcp,
        )  # fmt: skip

        contractions = tuple(
            Contraction(
                sequence_number=c.sequence_number,
                peak_date=c.peak_date,
                peak_price=c.peak_price,
                trough_date=c.trough_date,
                trough_price=c.trough_price,
                depth_pct=c.depth_pct,
                duration_days=c.duration_days,
                atr_pct=m.atr_pct,
                range_pct=m.range_pct,
                volume_ratio=m.volume_ratio,
                confirmation_date=c.confirmation_date,
            )
            for c, m in zip(base.contractions, measures.contractions, strict=True)
        )
        pattern = VCPPattern(
            instrument_id=instrument_id,
            as_of_date=as_of,
            base_start=base.base_start,
            base_end=breakout.breakout_date if breakout else None,
            base_high=base.base_high,
            base_low=base.base_low,
            base_duration_days=base.base_duration_days,
            prior_advance_return_pct=base.prior_advance_return_pct,
            contractions=contractions,
            progressive_tightening=measures.progressive_tightening,
            final_volume_ratio=measures.final_volume_ratio,
            volume_dryup_pass=measures.volume_dryup_pass,
            atr_contraction_ratio=measures.atr_contraction_ratio,
            volatility_contraction_pass=measures.volatility_contraction_pass,
            right_side_range_pct=measures.right_side_range_pct,
            tight_pivot_pass=measures.tight_pivot_pass,
            tightening_quality=None,  # quality vector: scoring phase (section 32)
            volatility_quality=None,
            volume_quality=None,
            pivot_quality=None,
            base_quality=None,
            pivot=pivots.primary,
            classification=cls.classification,
            status=status,
            confirmation_state=(
                ConfirmationState.CONFIRMED if base.is_confirmed else ConfirmationState.PROVISIONAL
            ),
            trend_template_pass=trend_template_pass,
            weekly_stage2_pass=weekly_stage2_pass,
            algorithm_version=self._version,
            config_hash=self._config_hash,
            pivot_candidates=pivots.candidates,
            invalidation_reasons=reasons,
        )
        (pattern,) = select_primary([pattern])
        return VCPDetection(
            instrument_id, as_of, pattern, status, None, seg, measures, pivots, cls, breakout
        )


def _primary_key(p: VCPPattern) -> tuple[int, int, int, int, int]:
    """Section 61A order: class, CONFIRMED, latest base end (open = as-of), longer, earlier."""
    end = p.base_end or p.as_of_date
    return (
        -p.classification.rank,
        0 if p.confirmation_state is ConfirmationState.CONFIRMED else 1,
        -end.toordinal(),
        -p.base_duration_days,
        p.base_start.toordinal(),
    )


def select_primary(patterns: Sequence[VCPPattern]) -> list[VCPPattern]:
    """Mark the section 61A primary (``is_primary``); every candidate is kept, order unchanged."""
    if not patterns:
        return []
    best = min(range(len(patterns)), key=lambda i: _primary_key(patterns[i]))
    return [replace(p, is_primary=(i == best)) for i, p in enumerate(patterns)]
