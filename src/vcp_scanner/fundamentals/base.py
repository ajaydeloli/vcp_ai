"""Fundamentals: provider and repository Protocols and the filing record (F1).

Only the data-gathering side exists so far (FUNDAMENTALS_SPECIFICATION §13, step F1): list the
filings a source has published, store each raw file unchanged, record what was seen. Nothing here
is read by the scan, the score, the backtest or the paper ledger (spec §2, freeze rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

FEED_OLD = "NSE_FINANCIAL_RESULTS"  # api/corporates-financial-results (filings to Jan 2025)
FEED_INTEGRATED = "NSE_INTEGRATED_FILING"  # api/integrated-filing-results (from Feb 2025)

STATUS_OK = "OK"
STATUS_FETCH_ERROR = "FETCH_ERROR"
STATUS_PARSE_ERROR = "PARSE_ERROR"
STATUS_NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class FilingRef:
    """One filing as a source lists it (no file content yet)."""

    source_feed: str
    source_record_id: str  # the feed's own id (seqNumber / seq_Id)
    symbol: str
    isin: str | None
    period_end: date
    period_type: str | None  # QUARTER | ANNUAL; None when the feed does not say (parsed later)
    statement_basis: str  # CONSOLIDATED | STANDALONE | UNKNOWN
    audited: bool | None
    broadcast_at: datetime  # timezone-aware (IST): when the market could first see it
    url: str  # the XBRL file that is fetched and cached
    revision_flags: str = ""  # the feed's own revision hints, kept verbatim for F2
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def filing_id(self) -> str:
        """Deterministic id: the same listed filing always gets the same id."""
        return f"{self.source_feed}:{self.source_record_id}"


@dataclass(frozen=True)
class FilingListing:
    """What a listing call returned."""

    filings: list[FilingRef]
    #: True when the source may have returned fewer rows than exist (the integrated feed
    #: returns at most 20 rows per query, verified 2026-10-03): the caller must narrow the query.
    truncated: bool = False


class FundamentalProvider(Protocol):
    """A source of financial-result filings."""

    def list_filings(
        self,
        *,
        symbol: str | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> FilingListing:
        """Filings broadcast in ``[start, end]`` (all dates when omitted) for one symbol or all."""

    def download(self, url: str) -> bytes:
        """The raw bytes of a filing file. Raises ``ProviderError`` when unreachable."""


@dataclass(frozen=True)
class StoredFiling:
    """A manifest row (table ``fundamental_filings``)."""

    filing_id: str
    status: str
    sha256: str | None
    cache_path: str | None


@dataclass(frozen=True)
class FilingRow:
    """A manifest row as the parser needs it."""

    filing_id: str
    instrument_id: str
    period_end: date
    period_type: str | None
    statement_basis: str
    revision_number: int
    broadcast_at: datetime
    cache_path: str | None
    sha256: str | None


class FilingManifest(Protocol):
    """The manifest of filings seen, one row per filing (spec §4)."""

    def get(self, filing_id: str) -> StoredFiling | None: ...

    def record(
        self,
        ref: FilingRef,
        *,
        instrument_id: str,
        status: str,
        sha256: str | None,
        cache_path: str | None,
        fetched_at: datetime,
    ) -> None:
        """Insert or update the row of ``ref`` and renumber the revisions of its period."""
