"""Turn stored filings into snapshots and facts (F2; FUNDAMENTALS_SPECIFICATION §5).

Input: manifest rows with status OK whose cached file has not been turned into a snapshot yet.
Output per filing: one row in ``fundamental_snapshots`` and its numbers in
``fundamental_facts`` (scope QUARTER, YTD, ANNUAL or INSTANT). A filing that cannot be read is
marked PARSE_ERROR in the manifest and is not retried until it is reset.

Nothing is computed from other companies or from prices; growth rates and margins are F3.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from vcp_scanner.fundamentals.base import FilingRow
from vcp_scanner.fundamentals.fetch import sha256_hex
from vcp_scanner.fundamentals.xbrl import (
    ANNUAL_DAYS,
    FilingParseError,
    ParsedFiling,
    parse_xbrl,
)

logger = logging.getLogger(__name__)

PROVIDER = "NSE"
EPS_LIMIT = 10_000.0
QUARTER_MAX_DAYS = 105

STATUS_OK = "OK"
STATUS_ESTIMATED = "ESTIMATED"  # the quarter was derived from year-to-date values
STATUS_INVALID = "INVALID"  # failed a sanity check: stored, never used


@dataclass(frozen=True)
class Fact:
    scope: str  # QUARTER | YTD | ANNUAL | INSTANT
    item: str
    value: float
    derived: bool = False
    period_start: date | None = None
    period_end: date | None = None


@dataclass(frozen=True)
class SnapshotRow:
    snapshot_id: str
    instrument_id: str
    period_end: date
    period_type: str
    statement_basis: str
    revision_number: int
    filing_date: date
    available_at: datetime
    currency: str | None
    data_status: str
    source_record_id: str


class SnapshotStore(Protocol):
    def unparsed_filings(self) -> list[FilingRow]: ...

    def mark_parse_error(self, filing_id: str) -> None: ...

    def set_parsed_attributes(self, filing_id: str, period_type: str, basis: str) -> None:
        """Set a filing's period type and basis from the file, then renumber its revisions."""

    def filing(self, filing_id: str) -> FilingRow | None: ...

    def prior_ytd(
        self, instrument_id: str, basis: str, ytd_start: date, before: date, available_at: datetime
    ) -> tuple[date, dict[str, float]] | None:
        """Latest earlier snapshot of the same fiscal year and basis: its period end and YTD."""

    def add_snapshot(self, row: SnapshotRow, facts: list[Fact]) -> None:
        """Insert the snapshot and facts; the previous revision becomes superseded."""


@dataclass
class ParseReport:
    parsed: int = 0
    errors: int = 0
    estimated: int = 0
    invalid: int = 0
    messages: list[str] = field(default_factory=list)


def snapshot_id(row: FilingRow, period_type: str, basis: str) -> str:
    key = f"{row.instrument_id}|{row.period_end}|{period_type}|{basis}|{row.revision_number}"
    return "fs_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _facts(
    parsed: ParsedFiling, period_type: str, derived_quarter: dict[str, float] | None
) -> list[Fact]:
    out: list[Fact] = []
    p = parsed.period
    if period_type == "ANNUAL":
        # The old feed's annual filing repeats the last quarter in OneD and carries the
        # year in FourD (verified RELIANCE 2024-03-31); a year-long OneD is used as it is.
        year = parsed.ytd if parsed.ytd is not None and p.days < ANNUAL_DAYS[0] else p
        out += [Fact("ANNUAL", k, v, False, year.start, year.end) for k, v in year.items.items()]
    else:
        quarter = derived_quarter if derived_quarter is not None else p.items
        out += [Fact("QUARTER", k, v, derived_quarter is not None, p.start, p.end)
                for k, v in quarter.items()]  # fmt: skip
        if parsed.ytd is not None:
            y = parsed.ytd
            out += [Fact("YTD", k, v, False, y.start, y.end) for k, v in y.items.items()]
    out += [Fact("INSTANT", k, v, False, None, p.end) for k, v in parsed.instant.items()]
    if parsed.is_financial:
        out.append(Fact("INSTANT", "is_financial", 1.0, False, None, p.end))
    return out


def _sane(parsed: ParsedFiling, row: FilingRow) -> str | None:
    """The first failed sanity check (spec §5.6), or None."""
    if parsed.currency not in (None, "INR"):
        return f"currency {parsed.currency}"
    if parsed.period.end != row.period_end:
        return f"period end {parsed.period.end} differs from the listing {row.period_end}"
    if parsed.period.end > row.broadcast_at.date():
        return "period end after the broadcast date"
    revenue = parsed.period.items.get("revenue")
    if revenue is not None and revenue < 0:
        return "negative revenue"
    eps = parsed.period.items.get("eps")
    if eps is not None and abs(eps) > EPS_LIMIT:
        return f"EPS {eps} outside +-{EPS_LIMIT:.0f}"
    return None


def _derive_quarter(
    parsed: ParsedFiling, store: SnapshotStore, row: FilingRow, basis: str
) -> tuple[dict[str, float] | None, str | None]:
    """A quarter from year-to-date values when the filing has no quarter column (§5.1).

    Returns (items, None) or (None, reason).
    """
    ytd = parsed.ytd
    if ytd is None:
        return None, "no quarter values and no year-to-date values"
    if ytd.days <= QUARTER_MAX_DAYS:
        return dict(ytd.items), None  # a first quarter: year to date is the quarter
    prior = store.prior_ytd(row.instrument_id, basis, ytd.start, row.period_end, row.broadcast_at)
    if prior is None:
        return None, "no earlier filing of the year to subtract"
    _end, before = prior
    return {k: v - before[k] for k, v in ytd.items.items() if k in before}, None


def parse_filings(
    store: SnapshotStore,
    cache_root: Path,
    *,
    only: Callable[[FilingRow], bool] | None = None,
) -> ParseReport:
    """Create snapshots for every stored filing that has none (idempotent)."""
    report = ParseReport()
    rows = [r for r in store.unparsed_filings() if only is None or only(r)]

    parsed_by_id: dict[str, ParsedFiling] = {}
    for row in rows:
        try:
            data = (cache_root / (row.cache_path or "")).read_bytes()
            if row.sha256 and sha256_hex(data) != row.sha256:
                raise FilingParseError("cached file does not match its SHA-256")
            parsed = parse_xbrl(data)
            period_type = parsed.period_type
            if period_type is None:
                raise FilingParseError(f"unsupported period length {parsed.period.days} days")
            if (
                row.period_type == "ANNUAL"
                and period_type == "QUARTER"
                and parsed.ytd is not None
                and ANNUAL_DAYS[0] <= parsed.ytd.days <= ANNUAL_DAYS[1]
            ):
                period_type = "ANNUAL"  # annual statement; its OneD is the last quarter
            basis = parsed.basis or (
                row.statement_basis if row.statement_basis != "UNKNOWN" else None
            )
            if basis is None:
                raise FilingParseError("statement basis unknown")
        except (FilingParseError, OSError) as exc:
            store.mark_parse_error(row.filing_id)
            report.errors += 1
            report.messages.append(f"{row.filing_id}: {exc}")
            logger.warning("fundamentals parse failed for %s: %s", row.filing_id, exc)
            continue
        store.set_parsed_attributes(row.filing_id, period_type, basis)
        parsed_by_id[row.filing_id] = parsed

    # Second pass in time order, so a derived quarter finds its earlier filings.
    fresh = [store.filing(r.filing_id) for r in rows if r.filing_id in parsed_by_id]
    ordered = sorted(
        (f for f in fresh if f is not None), key=lambda f: (f.period_end, f.broadcast_at)
    )
    for row in ordered:
        parsed = parsed_by_id[row.filing_id]
        period_type = row.period_type or parsed.period_type or "QUARTER"
        basis = row.statement_basis
        derived: dict[str, float] | None = None
        status = STATUS_OK
        if period_type == "QUARTER" and not parsed.period.items:
            derived, reason = _derive_quarter(parsed, store, row, basis)
            if derived is None:
                store.mark_parse_error(row.filing_id)
                report.errors += 1
                report.messages.append(f"{row.filing_id}: {reason}")
                continue
            status = STATUS_ESTIMATED
            report.estimated += 1
        problem = _sane(parsed, row)
        if problem:
            status = STATUS_INVALID
            report.invalid += 1
            report.messages.append(f"{row.filing_id}: INVALID, {problem}")
        snap = SnapshotRow(
            snapshot_id(row, period_type, basis), row.instrument_id, row.period_end, period_type,
            basis, row.revision_number, row.broadcast_at.date(), row.broadcast_at,
            parsed.currency or "INR", status, row.filing_id,
        )  # fmt: skip
        try:
            store.add_snapshot(snap, _facts(parsed, period_type, derived))
        except Exception as exc:  # a key collision from an out-of-order restatement
            store.mark_parse_error(row.filing_id)
            report.errors += 1
            report.messages.append(f"{row.filing_id}: {type(exc).__name__}: {exc}")
            continue
        report.parsed += 1
    return report
