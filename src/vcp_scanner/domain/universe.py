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
    #: Why the status is not POINT_IN_TIME_COMPLETE ("; "-joined reasons), None when it is.
    survivorship_detail: str | None = None


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


@dataclass(frozen=True, slots=True)
class SurvivorshipEvidence:
    """What the stored data can prove about one universe snapshot (audit P0-4).

    ``missing_price_days``: calendar days in the look-back window with no settled NSE
    bhavcopy entry (OK or NO_SESSION); ``None`` when no bhavcopy data exists at all.
    ``flag_history_start``: first date each surveillance list (ASM, GSM) was collected;
    ``None`` when never collected. ``delistings``: delisting records in the security master.
    """

    window_start: date
    missing_price_days: int | None
    first_price_day: date | None
    flag_history_start: dict[str, date | None]
    delistings: int
