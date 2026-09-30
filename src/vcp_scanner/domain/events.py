"""Domain models for system and data quality events."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from vcp_scanner.domain.enums import DataQualityFlag


class EventSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EventStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


#: ``resolved_by`` value for events the system closed itself because the underlying condition
#: cleared. Any other value is a human decision, which the system never overrides or reopens.
SYSTEM_RESOLVER = "SYSTEM"


def make_event_id(flag: DataQualityFlag, instrument_id: str, *key_parts: str) -> str:
    """Deterministic id: the same condition always maps to the same event.

    Re-running a detector therefore updates the existing row instead of piling up duplicates,
    and a human resolution stays attached to the condition it was made for.
    """
    raw = "|".join([flag.value, instrument_id, *key_parts])
    return "dq-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class DataQualityEvent:
    """Represents an anomaly or issue found in data (DATABASE_SCHEMA section 19).

    ``blocks_signal`` events stop production signals for the instrument from ``trade_date``
    onward until they are resolved (AGENTS.md rule 4; DATA_SPECIFICATION 18A).
    """

    event_id: str
    instrument_id: str
    flag: DataQualityFlag
    severity: EventSeverity
    detected_at: datetime
    description: str
    context: dict[str, str | float | int | None] | None = None
    #: First date the condition affects; NULL means it affects every date.
    trade_date: date | None = None
    blocks_signal: bool = False
    dataset: str = "daily_prices"
    status: EventStatus = EventStatus.OPEN
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None
