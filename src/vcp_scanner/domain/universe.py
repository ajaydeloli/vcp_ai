"""Universe domain models (PROJECT_DESIGN section 67)."""

from dataclasses import dataclass
from datetime import date, datetime

from vcp_scanner.domain.enums import SurvivorshipStatus


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


@dataclass(frozen=True, slots=True)
class UniverseCandidate:
    """Everything the universe rules need about one instrument at an as-of date.

    Prices are the prices that actually traded (a provider-adjusted close has its provider's
    split/bonus factors undone). Traded values are raw close x raw volume averages.
    """

    instrument_id: str
    last_price: float | None
    avg_traded_value_20d: float | None
    avg_traded_value_50d: float | None
    days_history: int | None
    last_trade_date: date | None
    series: str | None
    exchange: str | None
    asm_flag: str | None
    gsm_flag: str | None
    t2t_flag: str | None
