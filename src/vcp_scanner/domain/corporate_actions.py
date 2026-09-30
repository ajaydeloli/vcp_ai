"""Corporate Action domain models and logic (PROJECT_DESIGN section 67)."""

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from vcp_scanner.domain.enums import CorporateActionType


class CorporateActionStatus(StrEnum):
    """Reconciliation status of a corporate action."""

    CONFIRMED = "CONFIRMED"
    SINGLE_SOURCE = "SINGLE_SOURCE"
    PROVIDER_CONFLICT = "PROVIDER_CONFLICT"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


def status_allows_adjustment(status: CorporateActionStatus) -> bool:
    """Whether a resolution status may feed adjustment factors (DATABASE_SCHEMA 17A).

    Only CONFIRMED, SINGLE_SOURCE and MANUAL_OVERRIDE do. PROVIDER_CONFLICT blocks.
    """
    return status in (
        CorporateActionStatus.CONFIRMED,
        CorporateActionStatus.SINGLE_SOURCE,
        CorporateActionStatus.MANUAL_OVERRIDE,
    )


#: Action types whose ratio changes the price scale (and therefore feed adjustment factors).
PRICE_SCALING_ACTIONS: frozenset[CorporateActionType] = frozenset(
    {CorporateActionType.SPLIT, CorporateActionType.BONUS}
)


def has_usable_ratio(numerator: float | None, denominator: float | None) -> bool:
    """True when both ratio parts are present, finite and positive.

    A split or bonus without such a ratio gets factor 1.0 from the adjustment engine, i.e. it
    is *not* applied to prices (audit P0-3).
    """
    if numerator is None or denominator is None:
        return False
    return (
        math.isfinite(numerator)
        and math.isfinite(denominator)
        and numerator > 0
        and denominator > 0
    )


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """Raw corporate action from a provider."""

    corporate_action_id: str
    instrument_id: str
    action_type: CorporateActionType
    source: str
    created_at: datetime

    isin: str | None = None
    action_date: date | None = None
    announcement_date: date | None = None
    ex_date: date | None = None
    record_date: date | None = None

    ratio_numerator: float | None = None
    ratio_denominator: float | None = None
    cash_amount: float | None = None
    old_symbol: str | None = None
    new_symbol: str | None = None
    source_record_id: str | None = None
    ingested_at: datetime | None = None  # When we first saw this; for grace period


@dataclass(frozen=True, slots=True)
class CorporateActionResolution:
    """A reconciled corporate action spanning multiple providers."""

    resolution_id: str
    instrument_id: str
    action_type: CorporateActionType
    status: CorporateActionStatus

    isin: str | None = None
    ex_date: date | None = None
    ratio_numerator: float | None = None
    ratio_denominator: float | None = None
    cash_amount: float | None = None

    nse_action_id: str | None = None
    upstox_action_id: str | None = None
    conflict_fields: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CorporateActionAdjustment:
    """The derived adjustment factors computed from a resolved action."""

    resolution_id: str
    instrument_id: str
    effective_date: date
    price_factor: float
    volume_factor: float
    cumulative_price_factor: float
    cumulative_volume_factor: float
    source: str
    calculation_version: str


def ratio_unknown(resolution: CorporateActionResolution) -> bool:
    """A price-scaling action that *should* adjust prices but cannot: status permits
    adjustment, yet the ratio is missing or unusable, so the engine silently applies 1.0.
    """
    return (
        resolution.action_type in PRICE_SCALING_ACTIONS
        and status_allows_adjustment(resolution.status)
        and not has_usable_ratio(resolution.ratio_numerator, resolution.ratio_denominator)
    )


def explains_price_gap(resolution: CorporateActionResolution) -> bool:
    """Whether a resolution accounts for a raw overnight gap on its ex-date (audit P0-3).

    Only a split or bonus that actually feeds an adjustment factor explains a gap: its status
    allows adjustment and its ratio is usable. A dividend, a conflict, or a split whose ratio
    could not be read leaves the raw gap unadjusted, so it must not silence the safety net.
    """
    return (
        resolution.action_type in PRICE_SCALING_ACTIONS
        and resolution.ex_date is not None
        and status_allows_adjustment(resolution.status)
        and has_usable_ratio(resolution.ratio_numerator, resolution.ratio_denominator)
    )
