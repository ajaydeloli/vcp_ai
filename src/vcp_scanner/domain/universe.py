"""Universe domain models (PROJECT_DESIGN section 67)."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class SurvivorshipStatus(StrEnum):
    POINT_IN_TIME_COMPLETE = "POINT_IN_TIME_COMPLETE"
    PARTIAL = "PARTIAL"
    BIASED = "BIASED"


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    """A point-in-time snapshot of the eligible universe."""

    universe_snapshot_id: str
    universe_name: str
    as_of_date: date
    created_at: datetime
    config_hash: str
    method_version: str
    survivorship_status: SurvivorshipStatus


@dataclass(frozen=True, slots=True)
class UniverseMembership:
    """A single instrument's membership status in a universe snapshot."""

    universe_snapshot_id: str
    instrument_id: str
    eligible: bool
    exclusion_reason: str | None = None
    avg_traded_value: float | None = None
    price: float | None = None
    instrument_type: str | None = None
    series: str | None = None
    asm_flag: str | None = None
    gsm_flag: str | None = None
    t2t_flag: str | None = None
