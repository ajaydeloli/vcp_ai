"""Builders for data-quality events derived from other stored facts (audit finding P0-2)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from vcp_scanner.domain.corporate_actions import (
    PRICE_SCALING_ACTIONS,
    CorporateActionResolution,
    CorporateActionStatus,
    ratio_unknown,
)
from vcp_scanner.domain.enums import CorporateActionType, DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity, make_event_id
from vcp_scanner.domain.market import IdentifierPeriod


def corporate_action_events(
    instrument_id: str,
    resolutions: Sequence[CorporateActionResolution],
    detected_at: datetime,
    *,
    conflict_blocks_signals: bool = True,
) -> list[DataQualityEvent]:
    """CORPORATE_ACTION_UNRESOLVED events for the instrument's current resolutions.

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
