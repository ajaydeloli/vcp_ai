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

# A demerger that leaves less than 2 % of the parent's value is treated as a bad open price
# rather than applied. (0.05 rejected KESORAMIND 2025-03-10: its cement business went to
# UltraTech, 204.72 -> 10.23 = 0.04997; owner decision 2026-10-01.)
_MIN_DEMERGER_FACTOR = 0.02
# An ex-date open at or up to 1 % above the prior close means the special pre-open found no
# measurable value leaving: factor 1.0 (DALMIASUG 2025-10-31 opened exactly at 346.20).
_DEMERGER_NO_CHANGE_BAND = 0.01


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


def rescale_prior_close(prices: ExDatePrices, price_factor: float) -> ExDatePrices:
    """``prices`` with the prior close put on the scale after same-day ratio actions.

    A split or bonus on the same ex-date as a rights issue or demerger already explains part of
    the ex-date drop; deriving the second factor from the unscaled prior close would count it
    twice.
    """
    if price_factor == 1.0 or prices.prior_close is None:
        return prices
    return ExDatePrices(prices.prior_close * price_factor, prices.ex_open, prices.raw)


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
      base price). The share count does not change, so the volume factor is 1.0. An open at
      or up to 1 % above ``P`` gives factor 1.0 (nothing measurable left); an open further
      above ``P``, or a factor under 0.02, means the open is not usable. A factor entered by
      hand (manual override, stored as the ratio ``f:1``) is used as given.
    """
    if resolution.action_type is CorporateActionType.DEMERGER and has_usable_ratio(
        resolution.ratio_numerator, resolution.ratio_denominator
    ):
        # A demerger price factor entered by hand (manual overrides, stored as ratio f:1)
        # wins over the open price, which may be missing or unusable (UEL-type cases).
        assert resolution.ratio_numerator is not None and resolution.ratio_denominator
        manual = resolution.ratio_numerator / resolution.ratio_denominator
        return (manual, 1.0) if 0 < manual <= 1 else None
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
        if o is None or not math.isfinite(o) or o <= 0:
            return None
        if o >= p:
            return (1.0, 1.0) if o <= p * (1 + _DEMERGER_NO_CHANGE_BAND) else None
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


# -- Fix C11 (2026-10-04): split/bonus factors must be visible in the raw prices ---------------

#: A split/bonus factor this far from 1 must show up as a raw gap on its ex-date; it equals
#: the gap detector's default ``gap_pct`` (30 %), so the two checks agree on what "visible" is.
RATIO_CONFIRM_GAP = 0.30


def ratio_unconfirmed(
    price_factor: float, prices: ExDatePrices | None, gap: float = RATIO_CONFIRM_GAP
) -> bool:
    """True when a split/bonus price factor large enough to be seen (<= 1 - gap, or >= 1 /
    (1 - gap)) is *not* seen in the raw prices: the ex-date open is not within ``gap`` of the
    prior close times the factor. Then the action is probably wrong (a non-equity bonus read
    as an equity one, a wrong ratio or a wrong ex-date) and must not rescale history.

    Not checkable (False): no raw prices for the ex-date, the stock did not trade on it, or
    the factor is too small to separate from an ordinary day's move.
    """
    if prices is None or not prices.raw or prices.prior_close is None or prices.ex_open is None:
        return False
    if prices.prior_close <= 0 or prices.ex_open <= 0 or price_factor <= 0:
        return False
    if (1 - gap) < price_factor < 1 / (1 - gap):
        return False
    return abs(prices.ex_open / (prices.prior_close * price_factor) - 1.0) >= gap


def superseded_by_unmodelled(
    resolution: CorporateActionResolution, resolutions: list[CorporateActionResolution]
) -> bool:
    """A split/bonus whose ex-date also carries an UNMODELLED or CAPITAL_REDUCTION reading for
    the same instrument. Raw provider rows are immutable, so after a parser fix the *same*
    NSE record exists twice: its old reading (here, an equity BONUS with a ratio) and its new
    one (UNMODELLED, e.g. a bonus of preference shares). The newer reading wins: the old one
    must no longer rescale prices (Fix C11; the event layer already reports such a record
    once, as the UNMODELLED warning)."""
    if resolution.action_type not in PRICE_SCALING_ACTIONS or resolution.ex_date is None:
        return False
    return any(
        r.instrument_id == resolution.instrument_id
        and r.ex_date == resolution.ex_date
        and r.action_type in (CorporateActionType.UNMODELLED, CorporateActionType.CAPITAL_REDUCTION)
        for r in resolutions
    )
