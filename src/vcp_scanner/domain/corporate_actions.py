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

    A split or bonus explains a gap only if it actually feeds an adjustment factor: its status
    allows adjustment and its ratio is usable. A dividend, a conflict, or a split whose ratio
    could not be read leaves the raw gap unadjusted, so it must not silence the safety net.

    A rights issue or demerger with an adjustable status also explains its ex-date gap: its
    factor comes from the ex-date prices (:func:`derived_factor`), and when that fails a
    separate blocking event (cause ``factor_unknown``) is raised instead (audit step 2.4).
    """
    if resolution.ex_date is None or not status_allows_adjustment(resolution.status):
        return False
    if resolution.action_type in PRICE_DERIVED_ACTIONS:
        return True
    return resolution.action_type in PRICE_SCALING_ACTIONS and has_usable_ratio(
        resolution.ratio_numerator, resolution.ratio_denominator
    )


#: Actions whose factor is derived from prices around the ex-date rather than from a ratio
#: alone (audit step 2.4). With raw (bhavcopy) prices their ex-date gaps are visible.
PRICE_DERIVED_ACTIONS: frozenset[CorporateActionType] = frozenset(
    {CorporateActionType.RIGHTS, CorporateActionType.DEMERGER}
)

# A demerger that takes more than 95 % of the parent's value is implausible; treat the open
# price as unusable rather than apply it.
_MIN_DEMERGER_FACTOR = 0.05


@dataclass(frozen=True, slots=True)
class ExDatePrices:
    """Raw prices around an ex-date: the last close before it and the ex-date open.

    ``raw`` is True only when both bars come from the raw price source of truth (NSE
    bhavcopy). Bars from a provider-adjusted source (Kite) may already include the action, so
    no factor is derived from them.
    """

    prior_close: float | None
    ex_open: float | None
    raw: bool


def derived_factor(
    resolution: CorporateActionResolution, prices: ExDatePrices
) -> tuple[float, float] | None:
    """(price_factor, volume_factor) of a rights issue or demerger, or None if underivable.

    * RIGHTS ``a:b`` at issue price ``S`` (``cash_amount`` = face value + premium) with prior
      close ``P``: theoretical ex-rights price ``TERP = (b*P + a*S) / (a + b)``. The price factor
      is ``TERP / P`` and the volume factor its inverse (the bonus element adds shares). If
      ``S >= P`` the rights have no bonus element and the factor is 1.0. BHARTIARTL 2021-09-27
      (1:14 at Rs 535, P = 739.40) gives 0.98157, the same as Kite's 0.98156.
    * DEMERGER: NSE runs a special pre-open session on the ex-date that discovers the parent's
      price without the demerged business, so the ex-date open ``O`` over ``P`` is the factor
      (RELIANCE 2023-07-20: 2580 / 2841.85 = 0.9079; the 261.85 difference is JIOFIN's listing
      base price). The share count does not change, so the volume factor is 1.0. ``O >= P``
      or an implausibly small factor means the open is not usable.
    """
    p = prices.prior_close
    if p is None or not math.isfinite(p) or p <= 0:
        return None
    if resolution.action_type is CorporateActionType.RIGHTS:
        a, b, s = resolution.ratio_numerator, resolution.ratio_denominator, resolution.cash_amount
        if not has_usable_ratio(a, b) or s is None or not math.isfinite(s) or s < 0:
            return None
        assert a is not None and b is not None
        if s >= p:
            return 1.0, 1.0
        pf = (b * p + a * s) / ((a + b) * p)
        return pf, 1.0 / pf
    if resolution.action_type is CorporateActionType.DEMERGER:
        o = prices.ex_open
        if o is None or not math.isfinite(o) or o <= 0 or o >= p:
            return None
        pf = o / p
        if pf < _MIN_DEMERGER_FACTOR:
            return None
        return pf, 1.0
    return None


def factor_unknown(resolution: CorporateActionResolution, prices: ExDatePrices | None) -> bool:
    """A rights issue or demerger that should adjust raw prices but whose factor cannot be
    derived. Only raw prices count: a provider-adjusted series already contains the action.
    """
    return (
        resolution.action_type in PRICE_DERIVED_ACTIONS
        and resolution.ex_date is not None
        and status_allows_adjustment(resolution.status)
        and prices is not None
        and prices.raw
        and derived_factor(resolution, prices) is None
    )
