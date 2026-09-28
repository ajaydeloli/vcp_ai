"""Corporate Action domain models and logic (PROJECT_DESIGN section 67)."""

from dataclasses import dataclass
from datetime import date, datetime

from vcp_scanner.domain.enums import CorporateActionType, StrEnum


class CorporateActionStatus(StrEnum):
    """Reconciliation status of a corporate action."""

    CONFIRMED = "CONFIRMED"
    SINGLE_SOURCE = "SINGLE_SOURCE"
    PROVIDER_CONFLICT = "PROVIDER_CONFLICT"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


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
