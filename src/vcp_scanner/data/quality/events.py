"""Builders for data-quality events derived from other stored facts (audit finding P0-2)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from vcp_scanner.domain.corporate_actions import CorporateActionResolution, CorporateActionStatus
from vcp_scanner.domain.enums import DataQualityFlag
from vcp_scanner.domain.events import DataQualityEvent, EventSeverity, make_event_id


def corporate_action_events(
    instrument_id: str,
    resolutions: Sequence[CorporateActionResolution],
    detected_at: datetime,
    *,
    conflict_blocks_signals: bool = True,
) -> list[DataQualityEvent]:
    """One CORPORATE_ACTION_UNRESOLVED event per current PROVIDER_CONFLICT resolution.

    DATABASE_SCHEMA 17A: a ``PROVIDER_CONFLICT`` row blocks signals for the instrument "until
    superseded by a new row". The event is keyed by (instrument, action type, ex-date), the
    same key reconciliation uses, so when the conflict becomes CONFIRMED / MANUAL_OVERRIDE the
    key disappears from this set and ``sync_events`` closes the event.

    The block starts on the ex-date: bars before it are unaffected by the disputed action.
    ``conflict_blocks_signals`` (config ``corporate_actions.conflict_blocks_signals``) turns
    the event into a warning when an operator has chosen not to block.
    """
    events: list[DataQualityEvent] = []
    for r in resolutions:
        if r.instrument_id != instrument_id:
            continue
        if r.status is not CorporateActionStatus.PROVIDER_CONFLICT:
            continue
        ex = r.ex_date.isoformat() if r.ex_date else "unknown"
        fields = r.conflict_fields or "unspecified"
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
                severity=EventSeverity.CRITICAL,
                detected_at=detected_at,
                description=(
                    f"{r.action_type.value} ex-date {ex}: sources disagree ({fields}); "
                    "adjustment withheld until resolved"
                ),
                context={
                    "resolution_id": r.resolution_id,
                    "action_type": r.action_type.value,
                    "conflict_fields": r.conflict_fields,
                    "nse_action_id": r.nse_action_id,
                    "upstox_action_id": r.upstox_action_id,
                },
                trade_date=r.ex_date,
                blocks_signal=conflict_blocks_signals,
                dataset="corporate_actions",
            )
        )
    return events
