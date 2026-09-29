"""Domain models for system and data quality events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from vcp_scanner.domain.enums import DataQualityFlag


class EventSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class DataQualityEvent:
    """Represents an anomaly or issue found in data."""

    event_id: str
    instrument_id: str
    flag: DataQualityFlag
    severity: EventSeverity
    detected_at: datetime
    description: str
    context: dict[str, str | float | int | None] | None = None
