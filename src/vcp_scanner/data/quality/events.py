"""Builders for data-quality events derived from other stored facts (audit finding P0-2)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta

from vcp_scanner.domain.corporate_actions import (
    PRICE_SCALING_ACTIONS,
    CorporateActionResolution,
    CorporateActionStatus,
    ExDatePrices,
    factor_unknown,
    ratio_unknown,
)
from vcp_scanner.domain.enums import CorporateActionType, DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity, make_event_id
from vcp_scanner.domain.market import IdentifierPeriod


def unmodelled_action_events(
    instrument_id: str,
    resolutions: Sequence[CorporateActionResolution],
    detected_at: datetime,
) -> list[DataQualityEvent]:
    """Warnings for price-affecting actions NSE lists but this scanner does not model.

    Audit P1-10: a capital reduction, merger, scheme of arrangement, a bonus of debentures or
    rights in non-equity securities used to be dropped with only a log line. Each now raises a
    non-blocking ``CORPORATE_ACTION_UNMODELLED`` warning on its ex-date; the gap detector still
    blocks if the action left a split-like jump. The warning clears when a hand-entered action
    (``MANUAL_OVERRIDE``) exists for the same ex-date, and a person can resolve it as harmless
    with ``vcp quality resolve``.
    """
    handled = {r.ex_date for r in resolutions if r.status is CorporateActionStatus.MANUAL_OVERRIDE}
    events: list[DataQualityEvent] = []
    for r in resolutions:
        if r.instrument_id != instrument_id or r.action_type is not CorporateActionType.UNMODELLED:
            continue
        if r.ex_date is None or r.ex_date in handled:
            continue
        ex = r.ex_date.isoformat()
        events.append(
            DataQualityEvent(
                event_id=make_event_id(
                    DataQualityFlag.CORPORATE_ACTION_UNMODELLED, instrument_id, ex
                ),
                instrument_id=instrument_id,
                flag=DataQualityFlag.CORPORATE_ACTION_UNMODELLED,
                severity=EventSeverity.WARNING,
                detected_at=detected_at,
                description=(
                    f"NSE lists a price-affecting action on {ex} that this scanner does not "
                    "model (capital reduction, merger, scheme, or a non-equity bonus/rights); "
                    "prices are not adjusted for it. Check it: add a manual override if it "
                    "rescales prices, or resolve this warning."
                ),
                context={"ex_date": ex, "resolution_id": r.resolution_id},
                trade_date=r.ex_date,
                blocks_signal=False,
                dataset="corporate_actions",
            )
        )
    return events


def corporate_action_events(
    instrument_id: str,
    resolutions: Sequence[CorporateActionResolution],
    detected_at: datetime,
    *,
    conflict_blocks_signals: bool = True,
    ex_prices: Mapping[date, ExDatePrices] | None = None,
) -> list[DataQualityEvent]:
    """CORPORATE_ACTION_UNRESOLVED events for the instrument's current resolutions.

    A third cause (audit step 2.4): a rights issue or demerger on raw prices whose factor cannot
    be derived from ``ex_prices`` (:func:`factor_unknown`). It always blocks from the ex-date,
    like an unknown split ratio, because the adjusted series would keep the raw gap.

    Two causes, one event each:

    * a ``PROVIDER_CONFLICT`` resolution (below), and
    * a split/bonus whose status allows adjustment but whose ratio is missing or unusable
      (audit P0-3). The adjustment engine applies factor 1.0 to such an action, so the
      adjusted series keeps the raw price jump. This event always blocks, whatever
      ``conflict_blocks_signals`` says, because it is not a disagreement between sources but
      a known-wrong adjusted series. It clears when a later resolution carries a usable ratio
      (or a MANUAL_OVERRIDE supplies one) and cannot be closed by hand.

    DATABASE_SCHEMA 17A: a ``PROVIDER_CONFLICT`` row blocks signals for the instrument "until
    superseded by a new row". The event is keyed by (instrument, action type, ex-date), the
    same key reconciliation uses, so when the conflict becomes CONFIRMED / MANUAL_OVERRIDE the
    key disappears from this set and ``sync_events`` closes the event.

    The block starts on the ex-date: bars before it are unaffected by the disputed action.
    ``conflict_blocks_signals`` (config ``corporate_actions.conflict_blocks_signals``) turns
    the event into a warning when an operator has chosen not to block. Only split/bonus
    conflicts can block: a dividend or rights disagreement never rescales prices, so it is
    recorded as a WARNING that does not gate (audit P0-2 policy, 2026-09-30).
    """
    events: list[DataQualityEvent] = []
    for r in resolutions:
        if r.instrument_id != instrument_id:
            continue
        if ratio_unknown(r):
            events.append(_ratio_unknown_event(instrument_id, r, detected_at))
            continue
        if r.ex_date is not None and factor_unknown(r, (ex_prices or {}).get(r.ex_date)):
            events.append(_factor_unknown_event(instrument_id, r, detected_at))
            continue
        if r.status is not CorporateActionStatus.PROVIDER_CONFLICT:
            continue
        ex = r.ex_date.isoformat() if r.ex_date else "unknown"
        fields = r.conflict_fields or "unspecified"
        price_scaling = r.action_type in PRICE_SCALING_ACTIONS
        events.append(
            DataQualityEvent(
                event_id=make_event_id(
                    DataQualityFlag.CORPORATE_ACTION_UNRESOLVED,
                    instrument_id,
                    r.action_type.value,
                    ex,
                ),
                instrument_id=instrument_id,
                flag=DataQualityFlag.CORPORATE_ACTION_UNRESOLVED,
                severity=EventSeverity.CRITICAL if price_scaling else EventSeverity.WARNING,
                detected_at=detected_at,
                description=(
                    f"{r.action_type.value} ex-date {ex}: sources disagree ({fields}); "
                    + (
                        "adjustment withheld until resolved"
                        if price_scaling
                        else "no price adjustment involved, warning only"
                    )
                ),
                context={
                    "resolution_id": r.resolution_id,
                    "action_type": r.action_type.value,
                    "conflict_fields": r.conflict_fields,
                    "nse_action_id": r.nse_action_id,
                    "upstox_action_id": r.upstox_action_id,
                },
                trade_date=r.ex_date,
                blocks_signal=conflict_blocks_signals and price_scaling,
                dataset="corporate_actions",
            )
        )
    return events


def _ratio_unknown_event(
    instrument_id: str, r: CorporateActionResolution, detected_at: datetime
) -> DataQualityEvent:
    """Blocking event for an adjustable split/bonus that has no usable ratio (audit P0-3)."""
    ex = r.ex_date.isoformat() if r.ex_date else "unknown"
    return DataQualityEvent(
        event_id=make_event_id(
            DataQualityFlag.CORPORATE_ACTION_UNRESOLVED,
            instrument_id,
            r.action_type.value,
            ex,
            "ratio_unknown",
        ),
        instrument_id=instrument_id,
        flag=DataQualityFlag.CORPORATE_ACTION_UNRESOLVED,
        severity=EventSeverity.CRITICAL,
        detected_at=detected_at,
        description=(
            f"{r.action_type.value} ex-date {ex} ({r.status.value}) has no usable ratio "
            f"({r.ratio_numerator}:{r.ratio_denominator}); prices are left unadjusted"
        ),
        context={
            "resolution_id": r.resolution_id,
            "action_type": r.action_type.value,
            "status": r.status.value,
            "cause": "ratio_unknown",
            "nse_action_id": r.nse_action_id,
            "upstox_action_id": r.upstox_action_id,
        },
        trade_date=r.ex_date,
        blocks_signal=True,
        dataset="corporate_actions",
    )


def _factor_unknown_event(
    instrument_id: str, r: CorporateActionResolution, detected_at: datetime
) -> DataQualityEvent:
    """Blocking event for a rights issue / demerger whose factor cannot be derived (step 2.4)."""
    ex = r.ex_date.isoformat() if r.ex_date else "unknown"
    return DataQualityEvent(
        event_id=make_event_id(
            DataQualityFlag.CORPORATE_ACTION_UNRESOLVED,
            instrument_id,
            r.action_type.value,
            ex,
            "factor_unknown",
        ),
        instrument_id=instrument_id,
        flag=DataQualityFlag.CORPORATE_ACTION_UNRESOLVED,
        severity=EventSeverity.CRITICAL,
        detected_at=detected_at,
        description=(
            f"{r.action_type.value} ex-date {ex} ({r.status.value}): no factor can be derived "
            "(missing issue price / ratio, or no usable ex-date open); prices are left unadjusted"
        ),
        context={
            "resolution_id": r.resolution_id,
            "action_type": r.action_type.value,
            "status": r.status.value,
            "cause": "factor_unknown",
            "ratio_numerator": r.ratio_numerator,
            "ratio_denominator": r.ratio_denominator,
            "cash_amount": r.cash_amount,
        },
        trade_date=r.ex_date,
        blocks_signal=True,
        dataset="corporate_actions",
    )


# A face-value split's new ISIN appears on the ex-date or the next session (2.0 spike), so a
# split explains an ISIN change that starts within this window around its ex-date.
_ISIN_CHANGE_WINDOW_BEFORE = timedelta(days=7)
_ISIN_CHANGE_WINDOW_AFTER = timedelta(days=1)


def identity_events(
    instrument_id: str,
    periods: Sequence[IdentifierPeriod],
    resolutions: Sequence[CorporateActionResolution],
    detected_at: datetime,
) -> list[DataQualityEvent]:
    """SYMBOL_MAPPING_UNCERTAIN warnings for ISIN changes no split explains (audit step 2.2).

    Identity links a new ISIN to the old instrument when the symbol and issuer are the same.
    That is normally a face-value split. Without a split near the change, the link is still
    the best reading but deserves a look (e.g. a capital reduction). The event never blocks:
    a price effect, if any, is caught separately by the gap detector.
    """
    split_dates = [
        r.ex_date
        for r in resolutions
        if r.action_type is CorporateActionType.SPLIT and r.ex_date is not None
    ]
    events: list[DataQualityEvent] = []
    previous: IdentifierPeriod | None = None
    for p in sorted(periods, key=lambda x: x.valid_from):
        if previous is not None and previous.isin != p.isin:
            # The new ISIN starts on the split's ex-date or shortly after it.
            explained = any(
                p.valid_from - _ISIN_CHANGE_WINDOW_BEFORE
                <= ex
                <= p.valid_from + _ISIN_CHANGE_WINDOW_AFTER
                for ex in split_dates
            )
            if not explained:
                events.append(
                    DataQualityEvent(
                        event_id=make_event_id(
                            DataQualityFlag.SYMBOL_MAPPING_UNCERTAIN,
                            instrument_id,
                            "isin_change",
                            p.valid_from.isoformat(),
                        ),
                        instrument_id=instrument_id,
                        flag=DataQualityFlag.SYMBOL_MAPPING_UNCERTAIN,
                        severity=EventSeverity.WARNING,
                        detected_at=detected_at,
                        description=(
                            f"ISIN changed {previous.isin} -> {p.isin} on {p.valid_from} "
                            f"({previous.symbol} -> {p.symbol}) with no split nearby; "
                            "linked by same symbol and issuer"
                        ),
                        context={
                            "cause": "isin_change_unexplained",
                            "old_isin": previous.isin,
                            "new_isin": p.isin,
                            "old_symbol": previous.symbol,
                            "new_symbol": p.symbol,
                        },
                        trade_date=p.valid_from,
                        blocks_signal=False,
                        dataset="instruments",
                    )
                )
        previous = p
    return events
