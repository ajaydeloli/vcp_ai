"""NSE capital-market bhavcopy: the raw daily price source of truth (audit step 2, D1).

A bhavcopy is NSE's end-of-day file for one trading session: every security that traded,
with unadjusted OHLC, volume, series and ISIN. Prices here are exactly as traded; the only
adjustments ever applied to them come from reconciled corporate actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

# Series kept by the ingest (owner decision, step 2): normal equity (EQ), trade-for-trade
# (BE, BZ) and SME (SM, ST). Debt, ETF-like and government series are dropped at parse time.
EQUITY_SERIES: frozenset[str] = frozenset({"EQ", "BE", "BZ", "SM", "ST"})
# Company equity ISINs start with INE. ETFs and other fund units also trade in series EQ but
# carry INF... ISINs; the scanner is for stocks, so they are skipped (2.2 real-data check).
EQUITY_ISIN_PREFIX = "INE"


class BhavcopyFormat(StrEnum):
    """File layout. NSE switched from the legacy CSV to the UDiFF layout on 2024-07-08."""

    LEGACY = "CM_LEGACY"
    UDIFF = "CM_UDIFF"


class BhavcopyFileStatus(StrEnum):
    """What the manifest knows about one calendar date."""

    OK = "OK"  # file downloaded and parsed
    NO_SESSION = "NO_SESSION"  # no file, and none is expected (holiday / weekend)
    PENDING = "PENDING"  # no file yet, but one may still be published
    ERROR = "ERROR"  # download or parse failed


@dataclass(frozen=True)
class BhavcopyRow:
    """One security's session in a bhavcopy, unadjusted."""

    trade_date: date
    symbol: str
    series: str
    isin: str
    open: float
    high: float
    low: float
    close: float
    last: float | None
    prev_close: float | None  # NSE's previous close; NOT adjusted for corporate actions
    volume: int
    turnover: float | None
    trades: int | None


@dataclass(frozen=True)
class RejectedBhavcopyRow:
    """A row that failed validation. Kept (never silently dropped) for the quality log."""

    trade_date: date
    symbol: str
    series: str
    reason: str


@dataclass(frozen=True)
class ParsedBhavcopy:
    """The parsed content of one bhavcopy file."""

    trade_date: date
    file_format: BhavcopyFormat
    rows: list[BhavcopyRow]
    rejected: list[RejectedBhavcopyRow] = field(default_factory=list)
    skipped_series: dict[str, int] = field(default_factory=dict)  # series -> row count


@dataclass(frozen=True)
class BhavcopyFileRecord:
    """One manifest entry (table ``bhavcopy_files``)."""

    trade_date: date
    status: BhavcopyFileStatus
    url: str
    file_format: BhavcopyFormat
    sha256: str | None = None
    row_count: int = 0
    rejected_count: int = 0
    cache_path: str | None = None
    detail: str | None = None
